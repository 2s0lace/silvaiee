"""pk map polish: copy MAP into NEW_MAP and improve it with the critic, zero tokens (round 2 B3b).

The sketch decides what may go where; the solver only picks the right variant.

* The critic is `lint` (SEAM/RARE against the frozen vanilla snapshot) and `patch` (3x3 windows seen in
  vanilla). Both are edges of the same snapshot.
* Only *problem cells* may change: cells within `ring` of a WARN SEAM/RARE/STAIR/RECT/EMPTY, minus every
  event cell (and its 8 neighbours) and every door/warp/sign/window behaviour or solid (building/roof/
  fence) cell. Everything else is frozen.
* A cell may become a tile of its own **sketch material** (the material it had in the copy before any
  move), or a tile from the **boundary vocabulary** of a material pair in its 3x3 sketch neighbourhood
  (the metatiles vanilla uses where those materials meet). A candidate is dropped unless its material
  already appears in the cell's 5x5 sketch area, so polish never introduces a class the sketch did not
  have (no masonry, rocks, bushes, flowers or trees where there were none).
* A change is kept only if severity falls (or holds while patch rises), `patch` does not fall over the
  whole map nor within the 5x5 around the move, reachability from every entrance never worsens, per
  material the count moves by at most max(3, 5%) and no material vanishes, and at most 10% of the cells
  change. A guard that rejects a change is counted; that exact change is never retried.
* A movable cell that still touches an unseen edge and has no allowed tile that removes it prints
  `INFO NOFIT x,y` and is left unchanged. The candidate count per cell is reported (min/p50/max).
* Scan order, candidate order and tie-breaks are fixed: the same input gives byte-identical output.
"""
import re

from ..core.errors import PkError
from ..core.out import Result
from . import lint, reach, terrain
from .grid import encode
from .layers import Grid, events
from .paintover import copy_map
from .tiles import Pair

BUDGET_FRAC = 0.1      # at most this share of the map's cells may change in total
FROZEN_PATTERN = re.compile(r'DOOR|WARP|SIGNPOST|WINDOW')


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


def movable(grid, pair, rules, boxes, ring, data):
    """Editable cells: within `ring` of a problem box, minus event/structure cells."""
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
    for y in range(grid.h):
        for x in range(grid.w):
            cell = grid.at(x, y)
            tags = pair.info(cell[0]).get('tags') or []
            behavior = pair.behavior_name(cell[0])
            if (rules.material(cell) == 'solid' or 'door' in tags
                    or FROZEN_PATTERN.search(behavior)):
                frozen.add((x, y))
    return sorted(allowed - frozen)


class Critic:
    """Sketch-driven candidates and the local delta of severity (SEAM + RARE, thresholds 0)."""

    def __init__(self, grid, pair, cfg, snap, rules):
        self.grid, self.pair, self.rules = grid, pair, rules
        stats = snap.pair(pair)
        self.known, self.h, self.v, self.vocab = stats.metatiles, stats.h, stats.v, stats.vocab
        self.windows = stats.windows
        self.rare_mats = set(cfg['rare_mats'])
        self.limit = cfg['rare_rate'] * max(1, stats.layouts)
        self.sockets = {}
        self.sketch = {(x, y): rules.material(grid.at(x, y))
                       for y in range(grid.h) for x in range(grid.w)}
        self.sketch_cell = {(x, y): grid.at(x, y) for y in range(grid.h) for x in range(grid.w)}
        self.allowed = {}

    def socket(self, metatile):
        if metatile not in self.sockets:
            sock = (self.pair.info(metatile) or {}).get('socket')
            self.sockets[metatile] = int(sock) if sock is not None else metatile
        return self.sockets[metatile]

    def material(self, metatile, x, y):
        """The material a candidate metatile would have at (x,y): tag first, else class of the
        sketch cell's collision (matches how Rules.by_mat was built)."""
        tags = (self.pair.info(metatile) or {}).get('tags')
        if tags:
            return tags[0]
        cell = self.sketch_cell[(x, y)]
        return self.rules.material((metatile, cell[1], cell[2]))

    def prepare(self, work):
        """allowed[(x,y)] = metatiles the sketch permits at this cell (B3b restricted vocabulary)."""
        for (x, y) in work:
            own = self.sketch[(x, y)]
            pairs3 = set()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    a = self.sketch.get((x + dx, y + dy))
                    if a is None:
                        continue
                    for dx2 in (-1, 0, 1):
                        for dy2 in (-1, 0, 1):
                            b = self.sketch.get((x + dx2, y + dy2))
                            if b is not None and a < b:
                                pairs3.add(f'{a}|{b}')
            classes5 = {self.sketch[(x + dx, y + dy)] for dx in range(-2, 3) for dy in range(-2, 3)
                        if (x + dx, y + dy) in self.sketch}
            tiles = set(self.rules.by_mat.get(own, ()))
            for pairkey in pairs3:
                for metatile in self.vocab.get(pairkey, ()):
                    if self.material(metatile, x, y) in classes5:
                        tiles.add(metatile)
            self.allowed[(x, y)] = sorted(tiles)

    def candidate_counts(self, work):
        sizes = sorted(len(self.allowed[p]) for p in work)
        if not sizes:
            return (0, 0, 0)
        pick = lambda q: sizes[min(len(sizes) - 1, int(round(q * (len(sizes) - 1))))]
        return sizes[0], pick(0.5), sizes[-1]

    def edge(self, a, b, table, mat_a, mat_b):
        sa, sb = self.socket(a), self.socket(b)
        if not (sa in self.known and sb in self.known):
            return 0
        cost = 0
        if (sa, sb) not in table:
            cost += 1
        if ({mat_a, mat_b} & self.rare_mats) and table.get((sa, sb), 0) <= self.limit:
            cost += 1
        return cost

    def local(self, x, y, material, metatile):
        grid, rules = self.grid, self.rules
        cost = 0
        for (nx, ny), table, first in (((x - 1, y), self.h, True), ((x + 1, y), self.h, False),
                                       ((x, y - 1), self.v, True), ((x, y + 1), self.v, False)):
            if grid.inside(nx, ny):
                neighbour = grid.at(nx, ny)
                nm = rules.material(neighbour)
                cost += (self.edge(neighbour[0], metatile, table, nm, material) if first
                         else self.edge(metatile, neighbour[0], table, material, nm))
        return cost

    def options(self, x, y):
        """[(metatile, material, delta)] the sketch allows at this cell."""
        cell = self.grid.at(x, y)
        own = self.rules.material(cell)
        base = self.local(x, y, own, cell[0])
        out = []
        for metatile in self.allowed.get((x, y), ()):
            if metatile == cell[0]:
                continue
            material = self.material(metatile, x, y)
            out.append((metatile, material, self.local(x, y, material, metatile) - base))
        return out, base

    def has_unseen(self, x, y):
        cell = self.grid.at(x, y)
        mat = self.rules.material(cell)
        for (nx, ny), table, first in (((x - 1, y), self.h, True), ((x + 1, y), self.h, False),
                                       ((x, y - 1), self.v, True), ((x, y + 1), self.v, False)):
            if self.grid.inside(nx, ny):
                neighbour = self.grid.at(nx, ny)
                nm = self.rules.material(neighbour)
                if (self.edge(neighbour[0], cell[0], table, nm, mat) if first
                        else self.edge(cell[0], neighbour[0], table, mat, nm)):
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


def command(repo, tx, query, opts):
    new_name = opts.get('into')
    if not new_name:
        raise PkError('PARSE', 'usage: pk map polish MAP --into NEW_MAP [--max-steps n] [--dry-run]')
    ring = int(opts.get('ring', 2))
    if ring < 1:
        raise PkError('BOUNDS', '--ring must be >= 1')
    max_steps = int(opts.get('max-steps', 200))

    source_name = repo.map(query)[0]
    name, data, layout = copy_map(repo, tx, query, new_name)
    grid = Grid(repo, name, data, layout)
    pair = Pair(repo, layout)
    cfg = lint.thresholds(repo)
    snap = lint.load_snapshot(repo)
    if snap is None:
        raise PkError('NOTFOUND', 'lint snapshot missing; run pk map lint --snapshot')
    rules = terrain.Rules(repo, pair, include_pk=False)
    stratum = lint.stratum_of(data.get('map_type'))
    area = grid.w * grid.h

    def measure():
        counts, warns, _ = lint.measure(grid, pair, cfg, snap, rules)
        _, severity = lint.severity_scores(counts, cfg['strata'], stratum, area)
        return severity, counts, warns, lint.window_share(grid, pair, snap, name)

    def materials():
        counts = {}
        for y in range(grid.h):
            for x in range(grid.w):
                m = rules.material(grid.at(x, y))
                counts[m] = counts.get(m, 0) + 1
        return counts

    severity0, counts0, warns0, patch0 = measure()
    work = movable(grid, pair, rules, problem_boxes(warns0), ring, data)
    budget = int(area * BUDGET_FRAC)
    original = {p: grid.at(*p) for p in work}
    material_orig = materials()
    material_min = {m: max(1, n - max(3, int(n * 0.05))) for m, n in material_orig.items()}
    material_max = {m: n + max(3, int(n * 0.05)) for m, n in material_orig.items()}
    material_now = dict(material_orig)
    reach0 = reach_sets(repo, name, grid, pair, data)

    critic = Critic(grid, pair, cfg, snap, rules)
    critic.prepare(work)
    cand = critic.candidate_counts(work)

    severity, changed = severity0, 0
    steps = kept = 0
    blocked = {'severity': 0, 'patch': 0, 'reachability': 0, 'materials': 0, 'edits': 0}
    banned = set()
    while kept < max_steps and changed < budget:
        steps += 1
        best = None
        for (x, y) in work:
            own = rules.material(grid.at(x, y))
            options, _base = critic.options(x, y)
            for metatile, material, delta in options:
                if delta >= 0 or (x, y, metatile) in banned:
                    continue
                if best is not None and (delta, x, y, metatile) >= best[:4]:
                    continue
                if material != own and (material_now.get(material, 0) + 1 > material_max.get(material, 0)
                                        or material_now.get(own, 0) - 1 < material_min.get(own, 1)):
                    blocked['materials'] += 1
                    continue
                old = grid.at(x, y)
                before = local_seen(critic, x, y)
                grid.set(x, y, terrain.bits_for(repo, pair, rules, metatile, material, old))
                after = local_seen(critic, x, y)
                grid.set(x, y, old)
                if after < before:
                    blocked['patch'] += 1        # the 5x5 patch (and so the whole-map patch) would drop
                    continue
                best = (delta, x, y, metatile, own, material)
        if best is None:
            break
        delta, x, y, metatile, own, material = best
        old = grid.at(x, y)
        grid.set(x, y, terrain.bits_for(repo, pair, rules, metatile, material, old))
        new_reach = reach_sets(repo, name, grid, pair, data)
        worse = any(not (new_reach[k] >= reach0[k]) for k in reach0)
        if worse or changed + 1 > budget:
            blocked['reachability' if worse else 'edits'] += 1
            banned.add((x, y, metatile))
            grid.set(x, y, old)
            continue
        if material != own:
            material_now[own] -= 1
            material_now[material] = material_now.get(material, 0) + 1
        severity += delta
        reach0 = new_reach
        changed += 1
        kept += 1
    severity, counts, _, patch = measure()
    # NOFIT: a cell that still touches an unseen edge but has no allowed tile that removes it.
    nofit = [p for p in work if critic.has_unseen(*p) and
             all(delta >= 0 for _m, _mat, delta in critic.options(*p)[0])]
    if changed:
        tx.stage(layout['blockdata_filepath'], encode(grid.cells))
    rows = [f'candidates min/p50/max {cand[0]}/{cand[1]}/{cand[2]} over {len(work)} movable',
            f'NOFIT {len(nofit)}' + (': ' + ' '.join(f'{x},{y}' for x, y in nofit[:12])
                                     + (' ...' if len(nofit) > 12 else '') if nofit else '')]
    rows += [f'guard {k}: {v}' for k, v in blocked.items() if v] or ['no guard blocked a kept move']
    rows.append(f'next: pk map render {source_name} --compare {new_name} --scale 2 '
                f'--out .pk/render/compare_{new_name.replace("PkPallet", "").replace("_Frlg", "")}_b3b.png')
    return Result(
        f'polish {new_name} <- {source_name} steps:{kept}/{steps} severity:{severity0:.1f}->{severity:.1f} '
        f'patch:{patch0:.2f}->{patch:.2f} seams:{counts0["seams"]}->{counts["seams"]} '
        f'rare:{counts0["rare"]}->{counts["rare"]} cells:{changed}/{budget}', rows)
