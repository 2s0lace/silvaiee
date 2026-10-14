"""pk map polish: copy MAP into NEW_MAP and improve it with the critic, zero tokens.

The critic is `lint` (SEAM/RARE against the frozen vanilla snapshot); the move is a re-solve of one
problem area. We only ever re-solve *problem cells*: cells within `ring` of a WARN SEAM/RARE/STAIR/
RECT location, minus every event cell (and its 8 neighbours) and every door/building/roof/fence/sign
metatile (tagged door, a door/warp/sign/window behaviour, or solid material). Everything else is
frozen, so only a ring around a real problem may change.

A step picks the single cell change (a metatile variant of the cell's material, or a fallback
boundary shift to a neighbour's material) that most lowers the critic, and keeps it only if:
  - severity falls (or stays equal while patch rises);
  - reachability from every entrance never gets worse (reach.py);
  - per material the cell count changes by at most max(3, 5%) and no material disappears;
  - at most 10% of the map's cells change in total.
A guard that rejects a move is recorded (and that exact change is never retried), so the report shows
whether the guards bite. The scan order and tie-breaks are fixed, so the same input gives
byte-identical output; no RNG, no LLM.
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
# A boundary shift may only move between soft natural materials; cliffs/piers/benches etc. would
# change the art direction (the owner judges the render), so they are never a cross-material target.
SOFT_MATERIALS = {'water', 'sand', 'grass', 'tall_grass', 'meadow', 'lawn', 'path', 'flowers',
                  'ground', 'tree'}


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
    """Local delta of severity (SEAM + RARE, both thresholds 0) and the candidate sets."""

    def __init__(self, grid, pair, cfg, snap, rules):
        self.grid, self.pair, self.rules = grid, pair, rules
        stats = snap.pair(pair)
        self.known, self.h, self.v = stats.metatiles, stats.h, stats.v
        self.rare_mats = set(cfg['rare_mats'])
        self.limit = cfg['rare_rate'] * max(1, stats.layouts)
        self.sockets = {}

    def socket(self, metatile):
        if metatile not in self.sockets:
            sock = (self.pair.info(metatile) or {}).get('socket')
            self.sockets[metatile] = int(sock) if sock is not None else metatile
        return self.sockets[metatile]

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
        """[(metatile, material, delta)] for a cell: its own material first, then neighbour ones."""
        grid, rules = self.grid, self.rules
        cell = grid.at(x, y)
        own = rules.material(cell)
        base = self.local(x, y, own, cell[0])
        neighbours = {rules.material(grid.at(nx, ny))
                      for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)) if grid.inside(nx, ny)}
        out = []
        for material in sorted({own} | (neighbours & SOFT_MATERIALS)):
            for metatile in sorted(rules.by_mat.get(material, ())):
                if metatile == cell[0]:
                    continue
                out.append((metatile, material, self.local(x, y, material, metatile) - base))
        return out


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
    severity, counts, patch, changed = severity0, counts0, patch0, 0
    steps = kept = 0
    blocked = {'severity': 0, 'reachability': 0, 'materials': 0, 'edits': 0}
    banned = set()
    while kept < max_steps and changed < budget:
        steps += 1
        best = None
        for (x, y) in work:
            own = rules.material(grid.at(x, y))
            for metatile, material, delta in critic.options(x, y):
                if (x, y, metatile) in banned:
                    continue
                if material != own and (material_now.get(material, 0) + 1 > material_max.get(material, 0)
                                        or material_now.get(own, 0) - 1 < material_min.get(own, 1)):
                    blocked['materials'] += 1
                    continue        # a boundary shift would break the per-material guard
                if best is None or (delta, x, y, metatile) < best:
                    best = (delta, x, y, metatile, own, material)
        if best is None or best[0] >= 0:
            break
        delta, x, y, metatile, own, material = best
        old = grid.at(x, y)
        grid.set(x, y, terrain.bits_for(repo, pair, rules, metatile, material, old))
        new_severity, new_counts, _, new_patch = measure()
        if new_severity > severity + 1e-9 or (new_severity == severity and new_patch <= patch):
            if new_severity > severity + 1e-9:
                blocked['severity'] += 1
            banned.add((x, y, metatile))
            grid.set(x, y, old)
            continue
        new_reach = reach_sets(repo, name, grid, pair, data)
        worse = any(not (new_reach[k] >= reach0[k]) for k in reach0)
        new_changed = changed + 1
        if worse or new_changed > budget:
            blocked['reachability' if worse else 'edits'] += 1
            banned.add((x, y, metatile))
            grid.set(x, y, old)
            continue
        if material != own:
            material_now[own] -= 1
            material_now[material] = material_now.get(material, 0) + 1
        severity, counts, patch = new_severity, new_counts, new_patch
        reach0 = new_reach
        changed = new_changed
        kept += 1
    if changed:
        tx.stage(layout['blockdata_filepath'], encode(grid.cells))
    rows = [f'guard {k}: {v}' for k, v in blocked.items() if v] or ['no guard blocked a move']
    rows.append(f'next: pk map render {source_name} --compare {new_name} --scale 2 --out .pk/render/compare.png')
    return Result(
        f'polish {new_name} <- {source_name} steps:{kept}/{steps} severity:{severity0:.1f}->{severity:.1f} '
        f'patch:{patch0:.2f}->{patch:.2f} seams:{counts0["seams"]}->{counts["seams"]} '
        f'rare:{counts0["rare"]}->{counts["rare"]} cells:{changed}/{budget} of {len(work)} movable',
        rows)
