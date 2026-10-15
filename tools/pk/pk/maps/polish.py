"""pk map polish: copy MAP into NEW_MAP and improve it with the critic, zero tokens (round 2 B3c).

Names must never decide anything; the sketch's physical classes and vanilla 3x3 class-pattern
vocabulary decide what may go where:

* Physical classes come from game data only (`lint.physical_class`: walk, surf, ledge_*, wall,
  grass_enc, door, plus elevation). Name-based materials (`rules.material` / sidecar tags) are
  never consulted by polish.
* Over all vanilla outdoor layouts (never `Pk*`), the snapshot records for each 3x3 physical-class
  pattern the exact centre metatiles vanilla used, with counts (`stats.vocab`).
* In polish, a changed cell may only take a metatile that vanilla used for the sketch's 3x3
  physical-class pattern at that cell (or after an explicit shape move).
* Physical class lock (separate from the vocabulary): a changed cell must keep the original cell's
  physical class and elevation, and its dominant vanilla physical class must match.
* Odd-tile score: each candidate is compared (mean RGB distance over 16x16 pixels) against the
  weighted average render of the vanilla metatiles used for that 3x3 class pattern; candidates
  above the vanilla leave-one-out p95 threshold (`stats.odd_p95`) are rejected.
* If the pattern never occurs in vanilla, or no allowed tile removes the seam, polish prints
  `INFO NOFIT x,y pattern` and leaves the cell unchanged.
"""
import re

from ..core.errors import PkError
from ..core.out import Result
from . import lint, reach, stamps
from .edit import usage
from .grid import encode
from .layers import Grid, events
from .meta import draw
from .paintover import copy_map
from .tiles import Pair

VOCAB_MIN_COUNT = 5      # B3c: a candidate must be used >= 5 times in vanilla for this pattern
VOCAB_MIN_SHARE = 0.02   # ... and be >= 2% of the pattern's vanilla usage
BUDGET_FRAC = 0.1      # at most this share of the map's cells may change in total
FROZEN_PATTERN = re.compile(r'DOOR|WARP|SIGNPOST|WINDOW')
SOFT_CLASSES = {'walk', 'surf', 'grass_enc'}


def reach_sets(repo, name, grid, pair, data):
    """{(state, entrance): frozenset of reachable cells} for the current grid."""
    try:
        scripts = repo.read(f'data/maps/{name}/scripts.inc').decode('utf-8')
    except Exception:
        scripts = ''
    world = reach.World(grid, pair, data, scripts)
    out = {}
    for state, abilities in reach.STATES.items():
        for key, cells in world.entrances().items():
            out[(state, key)] = frozenset(world.reach(cells, abilities))
    return out


def problem_boxes(warns):
    """WARN rows -> boxes: SEAM/RARE are @x,y (1x1); STAIR/RECT/EMPTY print a box."""
    boxes = []
    for line in warns['seams'] + warns['rare']:
        m = re.search(r'@(\d+),(\d+)', line)
        if m:
            boxes.append((int(m.group(1)), int(m.group(2)), 1, 1))
    for key in ('stairs', 'rects', 'empty'):
        for line in warns[key]:
            m = re.search(r'(\d+),(\d+)-(\d+),(\d+)', line)
            if m:
                x0, y0, x1, y1 = map(int, m.groups())
                boxes.append((min(x0, x1), min(y0, y1), abs(x1 - x0) + 1, abs(y1 - y0) + 1))
    return boxes


def cell_physical(pair, cell):
    """(physical_class, elevation) of an in-grid cell (mt, coll, elev) from game data only."""
    return lint.physical_class(pair.behavior_name(cell[0]), cell[1]), cell[2]


def movable(grid, pair, boxes, ring, data):
    """Editable cells: within `ring` of a problem box, minus event/structure/wall/door cells."""
    allowed = set()
    for (x0, y0, w, h) in boxes:
        for y in range(y0 - ring, y0 + h + ring):
            for x in range(x0 - ring, x0 + w + ring):
                if grid.inside(x, y):
                    allowed.add((x, y))
    frozen = set()
    for (ex, ey) in events(data):
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                frozen.add((ex + dx, ey + dy))
    try:
        _, stamped = stamps.instances(grid, stamps.load_stamps(grid.repo, pair))
        frozen |= stamped
    except Exception:
        pass
    for y in range(grid.h):
        for x in range(grid.w):
            cell = grid.at(x, y)
            behavior = pair.behavior_name(cell[0])
            pcls = lint.physical_class(behavior, cell[1])
            if pcls in ('wall', 'door') or FROZEN_PATTERN.search(behavior):
                frozen.add((x, y))
    return sorted(allowed - frozen)


def physical_bits(repo, pair, metatile, target_class, old):
    """(metatile, coll, elev) keeping `old`'s collision and elevation (physical class lock)."""
    coll, elev = old[1], old[2]
    if lint.physical_class(pair.behavior_name(metatile), coll) == target_class:
        return metatile, coll, elev
    stats = usage(repo, pair).get(metatile, {})
    fitting = {k: n for k, n in stats.items()
               if lint.physical_class(pair.behavior_name(metatile), k[0]) == target_class and k[1] == elev}
    if fitting:
        c, e = max(fitting, key=lambda k: (fitting[k], -k[0]))
        return metatile, c, e
    return metatile, coll, elev


class Critic:
    """3x3 physical-class pattern vocabulary, physical class lock, odd-tile filter and local delta."""

    def __init__(self, repo, grid, pair, cfg, snap):
        self.repo, self.grid, self.pair = repo, grid, pair
        stats = snap.pair(pair)
        self.known, self.h, self.v = stats.metatiles, stats.h, stats.v
        self.vocab = stats.vocab
        self.physical = stats.physical
        self.odd_p95 = stats.odd_p95
        self.windows = stats.windows
        self.limit = cfg['rare_rate'] * max(1, stats.layouts)
        self.sockets = {}
        self.sketch_cell = {(x, y): grid.at(x, y) for y in range(grid.h) for x in range(grid.w)}
        self.sketch_class = {(x, y): cell_physical(pair, self.sketch_cell[(x, y)])[0]
                             for y in range(grid.h) for x in range(grid.w)}
        self.sketch_elev = {(x, y): self.sketch_cell[(x, y)][2]
                            for y in range(grid.h) for x in range(grid.w)}
        self.patterns = {(x, y): lint.pattern_at(self.sketch_class, grid.w, grid.h, x, y)
                         for y in range(grid.h) for x in range(grid.w)}
        self.allowed = {}
        self._rgb = {}
        self._avg_rgb = {}

    def socket(self, metatile):
        if metatile not in self.sockets:
            sock = (self.pair.info(metatile) or {}).get('socket')
            self.sockets[metatile] = int(sock) if sock is not None else metatile
        return self.sockets[metatile]

    def dominant_class(self, metatile):
        info = self.physical.get(metatile)
        if info is not None:
            return info[0]
        return lint.metatile_physical(self.repo, self.pair, metatile)[0]

    def flat_rgb(self, metatile):
        if metatile not in self._rgb:
            self._rgb[metatile] = tuple(
                v for row in draw(self.repo, self.pair, metatile) for p in row for v in (p or (0, 0, 0)))
        return self._rgb[metatile]

    def pattern_avg_rgb(self, pat):
        if pat not in self._avg_rgb:
            counts = self.vocab.get(pat) or {}
            if not counts:
                self._avg_rgb[pat] = None
            else:
                total = sum(counts.values())
                self._avg_rgb[pat] = tuple(
                    sum(self.flat_rgb(mt)[i] * c for mt, c in counts.items()) / total for i in range(768))
        return self._avg_rgb[pat]

    def odd_score(self, metatile, pat):
        """Mean RGB distance (0..255) between `metatile` and the average vanilla render for `pat`."""
        avg = self.pattern_avg_rgb(pat)
        if avg is None:
            return None
        rgb = self.flat_rgb(metatile)
        return sum(abs(rgb[i] - avg[i]) for i in range(768)) / 768.0

    def candidates_for(self, x, y, pat=None, target_class=None):
        """Metatiles vanilla used for `pat`, filtered by physical class lock and odd-tile threshold."""
        pat = pat or self.patterns[(x, y)]
        target_class = target_class or self.sketch_class[(x, y)]
        elev = self.sketch_elev[(x, y)]
        counts = self.vocab.get(pat) or {}
        if not counts:
            return []
        out = []
        total = sum(counts.values())
        for mt in sorted(counts):
            # Common in this context only: a tile vanilla used once or twice here (stairs, dock
            # blocks) is a legal neighbour but reads as a random object.
            if counts[mt] < VOCAB_MIN_COUNT or counts[mt] < VOCAB_MIN_SHARE * total:
                continue
            if self.dominant_class(mt) != target_class:
                continue
            cell = physical_bits(self.repo, self.pair, mt, target_class, self.sketch_cell[(x, y)])
            if cell_physical(self.pair, cell) != (target_class, elev):
                continue
            score = self.odd_score(mt, pat)
            if score is not None and score <= self.odd_p95:
                out.append(mt)
        return out

    def prepare(self, work):
        """allowed[(x,y)] = metatiles permitted at this cell by the 3x3 class-pattern vocabulary."""
        for (x, y) in work:
            self.allowed[(x, y)] = self.candidates_for(x, y)

    def candidate_counts(self, work):
        sizes = sorted(len(self.allowed[p]) for p in work)
        if not sizes:
            return (0, 0, 0)
        pick = lambda q: sizes[min(len(sizes) - 1, int(round(q * (len(sizes) - 1))))]
        return sizes[0], pick(0.5), sizes[-1]

    def edge(self, a, b, table, cls_a, cls_b):
        sa, sb = self.socket(a), self.socket(b)
        if not (sa in self.known and sb in self.known):
            return 0
        cost = 0
        if (sa, sb) not in table:
            cost += 1
        if ('surf' in (cls_a, cls_b) or 'walk' in (cls_a, cls_b)) and table.get((sa, sb), 0) <= self.limit:
            cost += 1
        return cost

    def local(self, x, y, pcls, metatile):
        grid = self.grid
        cost = 0
        for (nx, ny), table, first in (((x - 1, y), self.h, True), ((x + 1, y), self.h, False),
                                       ((x, y - 1), self.v, True), ((x, y + 1), self.v, False)):
            if grid.inside(nx, ny):
                neighbour = grid.at(nx, ny)
                ncls = cell_physical(self.pair, neighbour)[0]
                cost += (self.edge(neighbour[0], metatile, table, ncls, pcls) if first
                         else self.edge(metatile, neighbour[0], table, pcls, ncls))
        return cost

    def options(self, x, y):
        """[(metatile, pcls, delta)] the 3x3 class-pattern vocabulary allows at this cell."""
        cell = self.grid.at(x, y)
        own = cell_physical(self.pair, cell)[0]
        base = self.local(x, y, own, cell[0])
        out = []
        for metatile in self.allowed.get((x, y), ()):
            if metatile == cell[0]:
                continue
            out.append((metatile, own, self.local(x, y, own, metatile) - base))
        return out, base

    def has_unseen(self, x, y):
        cell = self.grid.at(x, y)
        pcls = cell_physical(self.pair, cell)[0]
        for (nx, ny), table, first in (((x - 1, y), self.h, True), ((x + 1, y), self.h, False),
                                       ((x, y - 1), self.v, True), ((x, y + 1), self.v, False)):
            if self.grid.inside(nx, ny):
                neighbour = self.grid.at(nx, ny)
                ncls = cell_physical(self.pair, neighbour)[0]
                if (self.edge(neighbour[0], cell[0], table, ncls, pcls) if first
                        else self.edge(cell[0], neighbour[0], table, pcls, ncls)):
                    return True
        return False


def local_seen(critic, x, y):
    """Seen count of the 3x3 windows fully inside the 5x5 around (x,y): the local patch metric."""
    grid = critic.grid
    seen = 0
    for ty in range(y - 2, y + 1):
        for tx in range(x - 2, x + 1):
            if tx < 0 or ty < 0 or tx + 2 >= grid.w or ty + 2 >= grid.h:
                continue
            key = lint.window_key([critic.socket(grid.at(tx + dx, ty + dy)[0])
                                   for dy in range(3) for dx in range(3)])
            seen += 1 if critic.windows.get(key, 0) > 0 else 0
    return seen


def box_seen(critic, cells):
    """Seen count of all 3x3 windows touching any cell in `cells`."""
    grid = critic.grid
    tops = set()
    for (x, y) in cells:
        for ty in range(y - 2, y + 1):
            for tx in range(x - 2, x + 1):
                if 0 <= tx and tx + 2 < grid.w and 0 <= ty and ty + 2 < grid.h:
                    tops.add((tx, ty))
    seen = 0
    for (tx, ty) in tops:
        key = lint.window_key([critic.socket(grid.at(tx + dx, ty + dy)[0])
                               for dy in range(3) for dx in range(3)])
        seen += 1 if critic.windows.get(key, 0) > 0 else 0
    return seen


def command(repo, tx, query, opts):
    new_name = opts.get('into')
    if not new_name:
        raise PkError('PARSE', 'usage: pk map polish MAP --into NEW_MAP [--max-steps n] [--dry-run]')
    ring = int(opts.get('ring', 2))
    if ring < 1:
        raise PkError('BOUNDS', '--ring must be >= 1')
    max_steps = int(opts.get('max-steps', 200))
    allow_shape = bool(opts.get('shape', False))

    source_name = repo.map(query)[0]
    name, data, layout = copy_map(repo, tx, query, new_name)
    grid = Grid(repo, name, data, layout)
    pair = Pair(repo, layout)
    cfg = lint.thresholds(repo)
    snap = lint.load_snapshot(repo)
    if snap is None:
        raise PkError('NOTFOUND', 'lint snapshot missing; run pk map lint --snapshot')
    rules = lint.pair_rules(repo, pair)
    stratum = lint.stratum_of(data.get('map_type'))
    area = grid.w * grid.h

    def measure():
        counts, warns, _ = lint.measure(grid, pair, cfg, snap, rules)
        _, severity = lint.severity_scores(counts, cfg['strata'], stratum, area)
        return severity, counts, warns, lint.window_share(grid, pair, snap, name)

    def class_counts():
        counts = {}
        for y in range(grid.h):
            for x in range(grid.w):
                c = cell_physical(pair, grid.at(x, y))[0]
                counts[c] = counts.get(c, 0) + 1
        return counts

    severity0, counts0, warns0, patch0 = measure()
    work = movable(grid, pair, problem_boxes(warns0), ring, data)
    work_set = set(work)
    budget = int(area * BUDGET_FRAC)
    class_orig = class_counts()
    class_min = {c: max(1, n - max(3, int(n * 0.05))) for c, n in class_orig.items()}
    class_max = {c: n + max(3, int(n * 0.05)) for c, n in class_orig.items()}
    class_now = dict(class_orig)
    reach0 = reach_sets(repo, name, grid, pair, data)

    critic = Critic(repo, grid, pair, cfg, snap)
    critic.prepare(work)
    cand = critic.candidate_counts(work)

    n_windows = max(1, (grid.w - 2) * (grid.h - 2))
    seen0 = int(round((patch0 or 0.0) * n_windows))
    seen_now = seen0

    changed_cells = set()
    steps = kept = 0
    blocked = {'severity': 0, 'patch': 0, 'reachability': 0, 'class_lock': 0, 'odd_tile': 0, 'edits': 0}
    banned = set()

    # Count candidates rejected by the odd-tile threshold or physical class lock during prepare.
    for (x, y) in work:
        pat = critic.patterns[(x, y)]
        tcls = critic.sketch_class[(x, y)]
        for mt in critic.vocab.get(pat, ()):
            if critic.dominant_class(mt) != tcls:
                blocked['class_lock'] += 1
            else:
                sc = critic.odd_score(mt, pat)
                if sc is not None and sc > critic.odd_p95:
                    blocked['odd_tile'] += 1

    while kept < max_steps and len(changed_cells) < budget:
        steps += 1
        best = None
        for (x, y) in work:
            if (x, y) not in changed_cells and len(changed_cells) + 1 > budget:
                continue
            old = grid.at(x, y)
            own_cls, own_elev = cell_physical(pair, old)
            options, _base = critic.options(x, y)
            for metatile, pcls, delta in options:
                if delta >= 0 or (x, y, metatile) in banned:
                    continue
                new_cell = physical_bits(repo, pair, metatile, pcls, old)
                # Separate physical class + elevation lock guard:
                if (cell_physical(pair, new_cell) != (critic.sketch_class[(x, y)], critic.sketch_elev[(x, y)])
                        or critic.dominant_class(metatile) != critic.sketch_class[(x, y)]):
                    blocked['class_lock'] += 1
                    continue
                before = local_seen(critic, x, y)
                grid.set(x, y, new_cell)
                after = local_seen(critic, x, y)
                grid.set(x, y, old)
                ds = after - before
                if seen_now + ds < seen0:
                    blocked['patch'] += 1
                    continue
                key = (delta, -ds, x, y, metatile)
                if best is None or key < best[0]:
                    best = (key, [(x, y, new_cell, metatile)], ds)

        # If no single-cell swap helps, try a simultaneous 2-cell swap on an adjacent movable pair
        # that shares an unseen seam (e.g. a 2-cell lawn transition or shore corner).
        if best is None:
            for (x, y) in work:
                for (nx, ny), is_h in (((x + 1, y), True), ((x, y + 1), False)):
                    if (nx, ny) not in work_set:
                        continue
                    if not (critic.has_unseen(x, y) or critic.has_unseen(nx, ny)):
                        continue
                    new_edits = (0 if (x, y) in changed_cells else 1) + (0 if (nx, ny) in changed_cells else 1)
                    if len(changed_cells) + new_edits > budget:
                        continue
                    old1, old2 = grid.at(x, y), grid.at(nx, ny)
                    cls1, elev1 = critic.sketch_class[(x, y)], critic.sketch_elev[(x, y)]
                    cls2, elev2 = critic.sketch_class[(nx, ny)], critic.sketch_elev[(nx, ny)]
                    tab = critic.h if is_h else critic.v
                    b0 = (critic.local(x, y, cls1, old1[0]) + critic.local(nx, ny, cls2, old2[0])
                          - critic.edge(old1[0], old2[0], tab, cls1, cls2))
                    for m1 in critic.allowed.get((x, y), ()):
                        for m2 in critic.allowed.get((nx, ny), ()):
                            if (m1, m2) == (old1[0], old2[0]) or (x, y, m1, nx, ny, m2) in banned:
                                continue
                            if (critic.socket(m1), critic.socket(m2)) not in tab:
                                continue
                            c1 = physical_bits(repo, pair, m1, cls1, old1)
                            c2 = physical_bits(repo, pair, m2, cls2, old2)
                            if (cell_physical(pair, c1) != (cls1, elev1)
                                    or cell_physical(pair, c2) != (cls2, elev2)):
                                blocked['class_lock'] += 1
                                continue
                            grid.set(x, y, c1)
                            grid.set(nx, ny, c2)
                            b1 = (critic.local(x, y, cls1, m1) + critic.local(nx, ny, cls2, m2)
                                  - critic.edge(m1, m2, tab, cls1, cls2))
                            grid.set(x, y, old1)
                            grid.set(nx, ny, old2)
                            delta = b1 - b0
                            if delta >= 0:
                                continue
                            before = box_seen(critic, ((x, y), (nx, ny)))
                            grid.set(x, y, c1)
                            grid.set(nx, ny, c2)
                            after = box_seen(critic, ((x, y), (nx, ny)))
                            grid.set(x, y, old1)
                            grid.set(nx, ny, old2)
                            ds = after - before
                            if seen_now + ds < seen0:
                                blocked['patch'] += 1
                                continue
                            key = (delta, -ds, x, y, m1, nx, ny, m2)
                            if best is None or key < best[0]:
                                best = (key, [(x, y, c1, m1), (nx, ny, c2, m2)], ds)

        # Optional shape moves (±1 tile on soft physical boundaries) when requested; after a shape
        # move, the moved cell gets its new physical class from the move itself and then follows
        # the same 3x3 class-pattern vocabulary rule.
        if best is None and allow_shape:
            for (x, y) in work:
                own = critic.sketch_class[(x, y)]
                if own not in SOFT_CLASSES or not critic.has_unseen(x, y):
                    continue
                if (x, y) not in changed_cells and len(changed_cells) + 1 > budget:
                    continue
                neigh_classes = {critic.sketch_class[(x + dx, y + dy)]
                                 for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))
                                 if (x + dx, y + dy) in critic.sketch_class} & SOFT_CLASSES
                for target_cls in sorted(neigh_classes - {own}):
                    if (class_now.get(target_cls, 0) + 1 > class_max.get(target_cls, 0)
                            or class_now.get(own, 0) - 1 < class_min.get(own, 1)):
                        blocked['class_lock'] += 1
                        continue
                    shifted = dict(critic.sketch_class, **{(x, y): target_cls})
                    pat = lint.pattern_at(shifted, grid.w, grid.h, x, y)
                    for mt in critic.candidates_for(x, y, pat, target_cls):
                        if (x, y, mt) in banned:
                            continue
                        old = grid.at(x, y)
                        delta = critic.local(x, y, target_cls, mt) - critic.local(x, y, own, old[0])
                        if delta >= 0:
                            continue
                        new_cell = physical_bits(repo, pair, mt, target_cls, old)
                        before = local_seen(critic, x, y)
                        grid.set(x, y, new_cell)
                        after = local_seen(critic, x, y)
                        grid.set(x, y, old)
                        ds = after - before
                        if seen_now + ds < seen0:
                            blocked['patch'] += 1
                            continue
                        key = (delta, -ds, x, y, mt)
                        if best is None or key < best[0]:
                            best = (key, [(x, y, new_cell, mt)], ds)

        if best is None:
            break
        key, edits, ds = best
        saved = [(x, y, grid.at(x, y)) for (x, y, _, _) in edits]
        for (x, y, new_cell, _) in edits:
            grid.set(x, y, new_cell)
        new_reach = reach_sets(repo, name, grid, pair, data)
        # Reach equal: the set of reachable cells must be identical (the sea must not become walkable).
        worse = any(new_reach[k] != reach0[k] for k in reach0)
        new_changed = changed_cells | {(x, y) for (x, y, _, _) in edits}
        if worse or len(new_changed) > budget:
            blocked['reachability' if worse else 'edits'] += 1
            if len(edits) == 1:
                banned.add((edits[0][0], edits[0][1], edits[0][3]))
            else:
                banned.add((edits[0][0], edits[0][1], edits[0][3], edits[1][0], edits[1][1], edits[1][3]))
            for (x, y, old_cell) in saved:
                grid.set(x, y, old_cell)
            continue
        for (x, y, new_cell, _), (_, _, old_cell) in zip(edits, saved):
            old_cls = cell_physical(pair, old_cell)[0]
            new_cls = cell_physical(pair, new_cell)[0]
            if new_cls != old_cls:
                class_now[old_cls] -= 1
                class_now[new_cls] = class_now.get(new_cls, 0) + 1
                critic.sketch_class[(x, y)] = new_cls
                for dy in range(-1, 2):
                    for dx in range(-1, 2):
                        p = (x + dx, y + dy)
                        if p in critic.patterns:
                            critic.patterns[p] = lint.pattern_at(critic.sketch_class, grid.w, grid.h, *p)
                            if p in work_set:
                                critic.allowed[p] = critic.candidates_for(*p)
        seen_now += ds
        reach0 = new_reach
        changed_cells = new_changed
        kept += 1

    changed = sum(1 for (x, y) in work if grid.at(x, y) != critic.sketch_cell[(x, y)])
    severity, counts, _, patch = measure()
    # NOFIT: a movable cell that still touches an unseen edge and has no allowed tile that removes it.
    nofit = []
    for (x, y) in work:
        if critic.has_unseen(x, y) and all(delta >= 0 for _m, _c, delta in critic.options(x, y)[0]):
            nofit.append((x, y, critic.patterns[(x, y)]))

    # Odd-tile score for every changed cell (worst 10 reported).
    odd_rows = []
    for (x, y) in sorted(work):
        if grid.at(x, y) != critic.sketch_cell[(x, y)]:
            mt = grid.at(x, y)[0]
            pat = critic.patterns[(x, y)]
            sc = critic.odd_score(mt, pat)
            odd_rows.append((sc if sc is not None else 0.0, x, y, mt))
    odd_rows.sort(key=lambda t: (-t[0], t[1], t[2]))
    worst_odd = odd_rows[0][0] if odd_rows else 0.0

    if changed:
        tx.stage(layout['blockdata_filepath'], encode(grid.cells))
    rows = [f'candidates min/p50/max {cand[0]}/{cand[1]}/{cand[2]} over {len(work)} movable',
            f'odd-tile threshold p95:{critic.odd_p95:.2f} worst:{worst_odd:.2f}'
            + (' (' + ' '.join(f'{x},{y}=0x{mt:03X}:{sc:.1f}' for sc, x, y, mt in odd_rows[:10]) + ')'
               if odd_rows else '')]
    rows += [f'INFO NOFIT {x},{y} {pat}' for x, y, pat in nofit]
    rows += [f'guard {k}: {v}' for k, v in blocked.items() if v] or ['no guard blocked a kept move']
    rows.append(f'next: pk map render {source_name} --compare {new_name} --scale 2 '
                f'--out .pk/render/compare_{new_name.replace("PkPallet", "").replace("Polish_Frlg", "").replace("_Frlg", "")}_b3c.png')
    return Result(
        f'polish {new_name} <- {source_name} steps:{kept}/{steps} severity:{severity0:.1f}->{severity:.1f} '
        f'patch:{patch0:.2f}->{patch:.2f} seams:{counts0["seams"]}->{counts["seams"]} '
        f'rare:{counts0["rare"]}->{counts["rare"]} cells:{changed}/{budget} nofit:{len(nofit)} '
        f'odd_max:{worst_odd:.1f}', rows)
