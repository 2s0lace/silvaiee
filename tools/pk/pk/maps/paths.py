"""Winding, vanilla-bounded town paths for ``pk terrain path``.

Routes are deterministic A* walks.  The state includes direction and straight-run length, so the
measured vanilla p95 is a hard ceiling rather than a post-hoc warning.  Painting is delegated to
``terrain.apply``; a second constrained pass cleans the safe one-cell join ring.
"""
from collections import Counter, deque
import hashlib
import heapq

from ..core.errors import PkError
from ..core.out import Result
from .edit import load, save
from .grid import decode
from .layers import events
from .lint import physical_class
from . import lint, meta, objects, reach, terrain


VANILLA_WIDTH_MEDIAN = 2
VANILLA_WIDTH_P95 = 6
VANILLA_STRAIGHT_P95 = 8
VANILLA_WIDTH_CHANGE = 0.354

STEPS = ((1, 0), (0, 1), (-1, 0), (0, -1))


class _Scratch:
    """The small part of Transaction needed by terrain.apply; never reaches the real transaction."""
    def __init__(self):
        self.writes = {}

    def stage(self, path, data):
        self.writes[path] = data


def _clone(grid):
    copy = type(grid).__new__(type(grid))
    copy.repo, copy.name, copy.data, copy.layout = grid.repo, grid.name, grid.data, grid.layout
    copy.w, copy.h, copy.cells = grid.w, grid.h, list(grid.cells)
    return copy


def _parse_points(words, grid):
    from .edit import point
    if len(words) < 2:
        raise PkError('PARSE', 'usage: pk terrain path MAP x1,y1 x2,y2 [x3,y3 ...]')
    return [point(word, grid) for word in words]


def _footprint(anchor, width):
    x, y = anchor
    return {(x + dx, y + dy) for dy in range(width) for dx in range(width)}


def _protected(glue):
    """Cells occupied by, or supplying a constrained side of, a tree/rock/cliff object."""
    protected = set()
    for y in range(glue.grid.h):
        for x in range(glue.grid.w):
            cell = glue.grid.at(x, y)
            if not glue.objectish(cell):
                continue
            protected.add((x, y))
            rec = glue.raw.get(cell[0])
            if not rec:
                continue
            for side, (dx, dy) in objects.SIDES.items():
                if objects.constrained(Counter(rec['nmt'][side])) is not None:
                    protected.add((x + dx, y + dy))
    return protected


def blocked_cells(repo, grid, pair):
    """Conservative route blockers: physical obstacles, every event, and object glue."""
    blocked = set(events(grid.data)) | _protected(objects.Glue(repo, grid, pair))
    for y in range(grid.h):
        for x in range(grid.w):
            mt, coll, _ = grid.at(x, y)
            cls = physical_class(pair.behavior_name(mt), coll)
            if cls in ('wall', 'door', 'surf') or cls.startswith('ledge'):
                blocked.add((x, y))
    return blocked


def _jitter(seed, x, y, option):
    raw = f'{seed}|{option}|{x},{y}'.encode('utf-8')
    return int.from_bytes(hashlib.sha256(raw).digest()[:2], 'big') / 65535.0


def _anchors_near(point, allowed, width):
    """Exact anchor, or edge-cap anchors adjacent to a blocked door/warp destination."""
    px, py = point
    containing = {a for a in allowed if point in _footprint(a, width)}
    if containing:
        return containing
    return {a for a in allowed if min(abs(x - px) + abs(y - py) for x, y in _footprint(a, width)) == 1}


def _astar(grid, starts, goals, allowed, seed, option, old_path, prior, danger=None):
    """Return anchor cells. State is (x, y, direction, current straight length)."""
    if not starts or not goals:
        raise PkError('CONFLICT', 'a destination has no safe path edge cap')
    goal_list = tuple(goals)
    heuristic = lambda x, y: min(abs(x - gx) + abs(y - gy) for gx, gy in goal_list)
    queue, distance, parent = [], {}, {}
    serial = 0
    for x, y in sorted(starts):
        state = (x, y, -1, 0)
        distance[state] = 0.0
        heapq.heappush(queue, (heuristic(x, y), 0.0, serial, state))
        serial += 1
    final = None
    while queue:
        _, cost, _, state = heapq.heappop(queue)
        if cost != distance.get(state):
            continue
        x, y, direction, run = state
        if (x, y) in goals:
            final = state
            break
        for nd, (dx, dy) in enumerate(STEPS):
            if direction >= 0 and nd == (direction + 2) % 4:
                continue
            if direction >= 0 and nd != direction and run < 2:
                continue
            nxt = (x + dx, y + dy)
            if nxt not in allowed:
                continue
            nrun = run + 1 if nd == direction else 1
            if nrun > VANILLA_STRAIGHT_P95:
                continue
            bend = 3.0 if direction >= 0 and nd != direction else 0.0
            merge = 0.0 if nxt in old_path else 0.35
            diversity = 1.8 if nxt in prior else 0.0
            step = (1.0 + bend + 0.42 * _jitter(seed, *nxt, option) + merge + diversity
                    + 12.0 * (danger or {}).get(nxt, 0))
            ns = (nxt[0], nxt[1], nd, nrun)
            nc = cost + step
            if nc < distance.get(ns, float('inf')):
                distance[ns], parent[ns] = nc, state
                heapq.heappush(queue, (nc + heuristic(*nxt), nc, serial, ns))
                serial += 1
    if final is None:
        raise PkError('CONFLICT', 'no safe route between destinations without cutting an object or water')
    route = []
    while True:
        route.append((final[0], final[1]))
        if final not in parent:
            break
        final = parent[final]
    route.reverse()
    return route


def route(repo, grid, pair, waypoints, width=2, option=1, prior=frozenset()):
    if not 1 <= width <= VANILLA_WIDTH_P95:
        raise PkError('BOUNDS', f'--width must be 1..{VANILLA_WIDTH_P95} (vanilla p95)')
    blocked = blocked_cells(repo, grid, pair)
    allowed = {(x, y) for y in range(grid.h - width + 1) for x in range(grid.w - width + 1)
               if not (_footprint((x, y), width) & blocked)}
    rules = terrain.Rules(repo, pair)
    old_path = {(x, y) for y in range(grid.h) for x in range(grid.w)
                if rules.material(grid.at(x, y)) in ('path', 'sand')}
    seed = grid.name + '|' + '|'.join(f'{x},{y}' for x, y in waypoints)
    path_choices, _ = road_family(repo, pair, rules)
    encounter = {(x, y) for y in range(grid.h) for x in range(grid.w)
                 if physical_class(pair.behavior_name(grid.at(x, y)[0]), grid.at(x, y)[1]) == 'grass_enc'}

    def incompatible_edges(anchor):
        footprint = _footprint(anchor, width)
        bad = 0
        for x, y in footprint:
            for dx, dy, table, reverse in ((-1, 0, rules.h, True), (1, 0, rules.h, False),
                                           (0, -1, rules.v, True), (0, 1, rules.v, False)):
                neighbour = (x + dx, y + dy)
                if neighbour in footprint or neighbour not in blocked or not grid.inside(*neighbour):
                    continue
                fixed = grid.at(*neighbour)[0]
                if not any(((fixed, m) in table if reverse else (m, fixed) in table)
                           for m in path_choices):
                    bad += 1
        # Prefer a route around encounter patches. If an exit itself is grass, painting supplies a
        # lawn verge instead of leaving sand directly against the encounter tiles.
        near = {(x + dx, y + dy) for x, y in footprint for dx, dy in STEPS}
        return bad + 2 * len(near & blocked) + 2 * len(footprint & encounter) + 0.25 * len(near & encounter)

    danger = {anchor: incompatible_edges(anchor) for anchor in allowed}
    result = []
    start_set = _anchors_near(waypoints[0], allowed, width)
    for destination in waypoints[1:]:
        goals = _anchors_near(destination, allowed, width)
        segment = _astar(grid, start_set, goals, allowed, seed, option, old_path, set(prior), danger)
        result.extend(segment if not result else segment[1:])
        start_set = {segment[-1]}
    return result


def route_options(repo, grid, pair, waypoints, width, count):
    routes, signatures, prior = [], set(), set()
    for option in range(1, count + 1):
        candidate = route(repo, grid, pair, waypoints, width, option, prior)
        signature = tuple(candidate)
        if signature not in signatures:
            routes.append(candidate)
            signatures.add(signature)
            prior.update(candidate[1:-1])
    return routes


def widened(route_cells, width):
    return set().union(*(_footprint(p, width) for p in route_cells))




def longest_straight(route_cells):
    best = run = 0
    previous = None
    for a, b in zip(route_cells, route_cells[1:]):
        direction = (b[0] - a[0], b[1] - a[1])
        run = run + 1 if direction == previous else 1
        previous, best = direction, max(best, run)
    return best


def road_family(repo, pair, rules, material='path'):
    """Include road transitions tagged grass, using the vanilla interior's dominant colour."""
    from .edit import usage
    used = usage(repo, pair)
    walk = [m for m, n in sorted(rules.freq.items()) if n and
            any(c == 0 for c, _ in used.get(m, {})) and
            physical_class(pair.behavior_name(m), 0) == 'walk']
    tagged = [m for m in walk if rules.material((m, 0, 3)) == material]
    if not tagged:
        raise PkError('NOTFOUND', f'no walkable vanilla {material} interior in this pair')
    pixels = {m: Counter(c for row in meta.draw(repo, pair, m) for c in row if c) for m in walk}
    interior = max(tagged, key=lambda m: (max(pixels[m].values(), default=0) / 256 >= 0.8,
                                          rules.freq.get(m, 0), -m))
    colour = pixels[interior].most_common(1)[0][0]
    coverage = {m: pixels[m].get(colour, 0) / 256 for m in walk}
    behavior = pair.behavior_name(interior)
    return {m for m in walk if coverage[m] >= 0.15 and pair.behavior_name(m) == behavior}, coverage


def lawn_tiles(grid, rules):
    """The destination's own lawn vocabulary, ordered by its local use, never a generic grass id."""
    counts = Counter(c[0] for c in grid.cells if rules.material(c) in ('lawn', 'meadow') and c[1] == 0)
    if not counts:
        counts = Counter(c[0] for c in grid.cells if rules.material(c) == 'grass' and c[1] == 0)
    if not counts:
        raise PkError('NOTFOUND', 'target map has no walkable lawn tile for a road verge')
    result = sorted(counts, key=lambda m: (-counts[m], m))
    # Some vanilla corner sockets use the map's plain lawn variation (tagged grass), between the
    # patterned lawn tiles. Learn that local variation too; never inject a fixed generic grass id.
    for cell in grid.cells:
        mt = cell[0]
        if mt in result or rules.material(cell) != 'grass' or cell[1]:
            continue
        colours = Counter(c for row in meta.draw(grid.repo, rules.pair, mt) for c in row if c)
        if max(colours.values(), default=0) >= .8 * 256:
            result.append(mt)
    return result


def _road_patterns(repo, pair, interiors, candidates):
    """Vanilla 3x3 road-interior masks distinguish straight edges and real inner/outer corners."""
    key = pair.primary['symbol'], pair.secondary['symbol']
    patterns = {m: Counter() for m in candidates}
    offsets = [(dx, dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
    for layout in repo.layouts().values():
        if str(layout['id']).startswith('LAYOUT_PK_') or (
                layout.get('primary_tileset'), layout.get('secondary_tileset')) != key:
            continue
        w, h = layout['width'], layout['height']
        source = decode(repo.read(layout['blockdata_filepath'])[:w * h * 2])
        for y in range(1, h - 1):
            for x in range(1, w - 1):
                mt = source[y * w + x][0]
                if mt in patterns:
                    mask = tuple(source[(y + dy) * w + x + dx][0] in interiors for dx, dy in offsets)
                    patterns[mt][mask] += 1
    return patterns


def _road_cells(grid, pair, route_cells, width):
    """Keep ledge approaches capped; only a vertically traversed existing gap crosses its row."""
    cells = widened(route_cells, width)
    ledges = {(x, y) for y in range(grid.h) for x in range(grid.w)
              if physical_class(pair.behavior_name(grid.at(x, y)[0]), grid.at(x, y)[1]).startswith('ledge')}
    traversed = set(route_cells)
    gaps = set()
    for x, y in route_cells:
        if (x, y - 1) in traversed and (x, y + 1) in traversed:
            gaps |= _footprint((x, y), width)
    band = {(x + dx, y + dy) for x, y in ledges for dx, dy in STEPS}
    return cells - (band - gaps) - ledges


class _PathRules:
    """Local road material view; shared metatile names/defaults remain unchanged."""
    def __init__(self, base, material, family):
        self.__dict__.update(base.__dict__)
        self.by_mat = dict(base.by_mat)
        self.by_mat[material] = set(family)
        self.base = base

    def material(self, cell):
        return self.base.material(cell)

    def pair_score(self, table, a, b):
        import math
        return 0.01 * math.log(table.get((a, b), 0) + 1) if (a, b) in table else -100.0


def _paint(repo, grid, pair, route_cells, width, material):
    base = terrain.Rules(repo, pair)
    family, coverage = road_family(repo, pair, base, material)
    rules = _PathRules(base, material, family)
    before = list(grid.cells)
    blocked = blocked_cells(repo, grid, pair)
    cells = _road_cells(grid, pair, route_cells, width) - blocked
    # Leave room for an actual edge/cap tile beside immutable obstacles. A narrow ledge gap remains
    # a walkable lawn crossing, rather than acquiring an isolated sand stub against a cliff tile.
    cells = {p for p in cells if not any((p[0] + dx, p[1] + dy) in blocked for dx, dy in STEPS)}
    lawn = lawn_tiles(grid, base)
    fixed_grass = set()
    for y in range(grid.h):
        for x in range(grid.w):
            if physical_class(pair.behavior_name(grid.at(x, y)[0]), grid.at(x, y)[1]) != 'grass_enc':
                continue
            if (x, y) in blocked:
                fixed_grass.add((x, y))
            for dx, dy, table, reverse in ((-1, 0, base.h, True), (1, 0, base.h, False),
                                           (0, -1, base.v, True), (0, 1, base.v, False)):
                q = x + dx, y + dy
                if q not in blocked or not grid.inside(*q):
                    continue
                mt = grid.at(*q)[0]
                if not any(((mt, m) in table if reverse else (m, mt) in table) for m in lawn):
                    fixed_grass.add((x, y))
    cells = {p for p in cells if not any((p[0] + dx, p[1] + dy) in fixed_grass
                                       for dx in range(-2, 3) for dy in range(-2, 3))}
    if not cells:
        raise PkError('CONFLICT', 'path has no safe paint cells')
    all_cells = {(x, y) for y in range(grid.h) for x in range(grid.w)}
    terrain.apply(repo, _Scratch(), grid, pair, rules, {p: material for p in cells},
                  f'path {material}', frozen=all_cells - cells)
    _auto_ring(repo, grid, pair, rules, cells, material, family, coverage, blocked, before)
    # Surface edits preserve collision/elevation, including all entrance ability states.
    for y in range(grid.h):
        for x in range(grid.w):
            old = before[y * grid.w + x]
            grid.set(x, y, (grid.at(x, y)[0], old[1], old[2]))
    return cells, base


def _auto_ring(repo, grid, pair, rules, cells, material, family, coverage, blocked, before):
    """Two-tile road interior, vanilla corner/edge ring, and one tile of the destination lawn."""
    def neighbours(points):
        return {(x + dx, y + dy) for x, y in points for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                if grid.inside(x + dx, y + dy)}

    edge = neighbours(cells) - cells - blocked
    verge = neighbours(cells | edge) - cells - edge - blocked
    ring = edge | verge
    editable = cells | ring
    source = _clone(grid)
    source.cells[:] = before
    lawn = lawn_tiles(source, rules)
    walk = set(lawn) | family
    # Inner-corner tiles have ~88% road colour but are still boundary tiles, not solid interiors.
    interior = {m for m in family if coverage[m] >= .95}
    patterns = _road_patterns(repo, pair, interior, walk)
    offsets = [(dx, dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
    solid = max(interior, key=lambda m: coverage[m])
    road_palette = {c for row in meta.draw(repo, pair, solid) for c in row if c}
    side_fill = {}
    for m in walk:
        pixels = meta.draw(repo, pair, m)
        sides = (pixels[0], pixels[-1], [row[0] for row in pixels], [row[-1] for row in pixels])
        side_fill[m] = tuple(sum(c in road_palette for c in side) >= 15 for side in sides)

    def geometry(m, p):
        desired = tuple((min(max(p[0] + dx, 0), grid.w - 1),
                         min(max(p[1] + dy, 0), grid.h - 1)) in cells for dx, dy in offsets)
        scores = [sum((a != b) * (3 if i in (1, 3, 4, 5, 7) else 1)
                      for i, (a, b) in enumerate(zip(desired, mask))) for mask in patterns.get(m, {})]
        wanted_sides = ((True,) * 4 if p in cells else tuple(
            (p[0] + dx, p[1] + dy) in cells for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0))))
        side_error = sum(a != b for a, b in zip(wanted_sides, side_fill.get(m, (False,) * 4)))
        return 100 * side_error + min(scores, default=20)
    directions = ((-1, 0, rules.h, True), (1, 0, rules.h, False),
                  (0, -1, rules.v, True), (0, 1, rules.v, False))

    def compatible(a, b, table, reverse):
        return (b, a) in table if reverse else (a, b) in table

    domains, flexible = {}, {}
    for p in sorted(editable, key=lambda q: (q[1], q[0])):
        options = set(family if p in cells else family | set(lawn) if p in edge else lawn)
        if p in cells and any((p[0] + dx, p[1] + dy) in blocked
                              for dx in range(-2, 3) for dy in range(-2, 3)):
            options.update(lawn)
        old = before[p[1] * grid.w + p[0]][0]
        if p in ring and physical_class(pair.behavior_name(old), 0) == 'walk':
            options.add(old)
        fixed = [(grid.at(p[0] + dx, p[1] + dy)[0], table, reverse)
                 for dx, dy, table, reverse in directions
                 if grid.inside(p[0] + dx, p[1] + dy)
                 and (p[0] + dx, p[1] + dy) not in editable and p not in verge]
        fitting = {m for m in options if all(compatible(m, n, t, r) for n, t, r in fixed)}
        # Preserve existing walkable gaps where a ledge requires a grass cap.
        if not fitting and p in cells:
            fitting = {m for m in walk if all(compatible(m, n, t, r) for n, t, r in fixed)}
        # Preserve the requested outline even when a fixed boundary has no fully seen solution.
        best_shape = min(geometry(m, p) for m in options)
        shaped = {m for m in options if geometry(m, p) == best_shape}
        domains[p] = (fitting & shaped) or shaped
        flexible[p] = set(options) | (set(lawn) if p in cells else set())

    # A complete adjacency solution avoids the local minima of iterative single-cell updates.
    # Original tiles remain candidates for caps through narrow grass/ledge passages.
    exact = {}
    for p in sorted(editable):
        options = set(flexible[p])
        for dx, dy, table, reverse in directions:
            q = p[0] + dx, p[1] + dy
            if not grid.inside(*q) or q in editable or p in verge:
                continue
            fixed = grid.at(*q)[0]
            options = {m for m in options if compatible(m, fixed, table, reverse)}
        exact[p] = options

    arcs = [(p, (p[0] + dx, p[1] + dy), table, reverse)
            for p in sorted(editable) for dx, dy, table, reverse in directions
            if (p[0] + dx, p[1] + dy) in editable]

    def propagate(current):
        if any(not values for values in current.values()):
            return False
        queue = deque(arcs)
        while queue:
            a, b, table, reverse = queue.popleft()
            kept = {m for m in current[a] if any(compatible(m, n, table, reverse) for n in current[b])}
            if not kept:
                return False
            if kept == current[a]:
                continue
            current[a] = kept
            for dx, dy, other_table, other_reverse in directions:
                q = a[0] + dx, a[1] + dy
                if q in editable and q != b:
                    queue.append((q, a, other_table, not other_reverse))
        return True

    nodes = [0]

    def search(current):
        nodes[0] += 1
        if nodes[0] > 2000:
            return None
        p = min((p for p in sorted(editable) if len(current[p]) > 1),
                key=lambda p: (p not in cells, len(current[p]), p[1], p[0]), default=None)
        if p is None:
            return current
        old = before[p[1] * grid.w + p[0]][0]
        order = sorted(current[p], key=lambda m: (geometry(m, p),
                                                 -coverage.get(m, 0) if p in cells else m not in lawn,
                                                 m != old, lawn.index(m) if m in lawn else len(lawn), m))
        for m in order:
            branch = {q: set(values) for q, values in current.items()}
            branch[p] = {m}
            if propagate(branch):
                solution = search(branch)
                if solution is not None:
                    return solution
        return None

    solution = search(exact) if propagate(exact) else None
    if solution is not None and sum(next(iter(solution[p])) in family for p in cells) >= .8 * len(cells):
        for p, values in solution.items():
            old = grid.at(*p)
            grid.set(*p, (next(iter(values)), old[1], old[2]))
        return

    def unary(m, p):
        road = coverage.get(m, 0)
        if p in cells:
            return 4.0 * road - 4 * geometry(m, p)
        return -road - 4 * geometry(m, p) + (0.1 if m in lawn else 0)

    targets = {p: material if p in cells else rules.material(grid.at(*p)) for p in editable}
    seed = {p: min(domains[p], key=lambda m: (geometry(m, p),
                m != before[p[1] * grid.w + p[0]][0], -rules.freq.get(m, 0), m)) for p in editable}
    solved = terrain.solve(grid, rules, targets, margin=0, choices={p: sorted(v) for p, v in domains.items()},
                           unary=unary, passes=24, local_weight=0, freq_weight=0, diag_weight=0, seed=seed)
    for p, mt in solved.items():
        old = grid.at(*p)
        grid.set(*p, (mt, old[1], old[2]))


def _validate(repo, grid, pair, before, touched):
    old = _clone(grid)
    old.cells[:] = before
    before_objects = set(objects.check(repo, old, pair))
    after_rows = objects.check(repo, grid, pair)
    new_objects = set(after_rows) - before_objects
    reach_line = reach.check_write(repo, pair, before, grid)
    if new_objects:
        raise PkError('CONFLICT', f'path refused: would add {len(new_objects)} WARN OBJECT sides', sorted(new_objects))
    if reach_line.startswith('WARN reach'):
        raise PkError('CONFLICT', f'path refused: {reach_line[5:]}')
    try:
        scripts = repo.read(f'data/maps/{grid.name}/scripts.inc').decode('utf-8')
    except PkError:
        scripts = ''
    old_world = reach.World(old, pair, grid.data, scripts)
    new_world = reach.World(grid, pair, grid.data, scripts)
    for state, abilities in reach.STATES.items():
        for entrance, starts in old_world.entrances().items():
            lost = old_world.reach(starts, abilities) - new_world.reach(starts, abilities)
            if lost:
                raise PkError('CONFLICT', f'path refused: WARN reach: {len(lost)} cells lost '
                              f'from {entrance} with {state}')
    return after_rows, reach_line


def _preview(repo, base, pair, route_cells, width, material, index):
    grid = _clone(base)
    cells, rules = _paint(repo, grid, pair, route_cells, width, material)
    xs, ys = [p[0] for p in cells], [p[1] for p in cells]
    x0, y0 = max(0, min(xs) - 2), max(0, min(ys) - 2)
    x1, y1 = min(grid.w, max(xs) + 3), min(grid.h, max(ys) + 3)
    rel = f'.pk/render/path_{grid.name}_{index}.png'
    path = repo.path(rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(meta.render_png(repo, grid, pair, (x0, y0, x1 - x0, y1 - y0), scale=3))
    seams = len(terrain.unseen(grid, rules, cells))
    safety = ''
    try:
        _validate(repo, grid, pair, base.cells, cells)
    except PkError as exc:
        safety = f', REFUSED: {exc}'
    return rel, seams, len(cells), safety


def command(repo, tx, args, opts):
    if len(args) < 3:
        raise PkError('PARSE', 'usage: pk terrain path MAP x1,y1 x2,y2 [x3,y3 ...]')
    grid, pair = load(repo, args[0])
    points = _parse_points(args[1:], grid)
    width = int(opts.get('width', VANILLA_WIDTH_MEDIAN))
    material = opts.get('material', 'path')
    if material not in ('path', 'sand'):
        raise PkError('PARSE', '--material must be path or sand')
    options = int(opts['options']) if 'options' in opts else None
    pick = int(opts['pick']) if 'pick' in opts else None
    if options is not None and not 1 <= options <= 20:
        raise PkError('BOUNDS', '--options must be 1..20')
    if pick is not None and not 1 <= pick <= 20:
        raise PkError('BOUNDS', '--pick must be 1..20')
    if options is not None and pick is not None:
        raise PkError('PARSE', 'use either --options or --pick, not both')
    wanted = options if options is not None else max(3, pick or 1)
    if wanted < 1:
        raise PkError('BOUNDS', '--options/--pick must be >= 1')
    routes = route_options(repo, grid, pair, points, width, wanted)
    if options is not None:
        rows = []
        for i, candidate in enumerate(routes, 1):
            rel, seams, cells, safety = _preview(repo, grid, pair, candidate, width, material, i)
            rows.append(f'option {i}: seams {seams}, cells {cells}, straight {longest_straight(candidate)}, png {rel}{safety}')
            rows.append(f'pk terrain path {grid.name} ' + ' '.join(args[1:]) +
                        f' --width {width} --material {material} --pick {i}')
        if len(routes) < options:
            rows.append(f'INFO only {len(routes)} distinct options')
        return Result(f'terrain path {grid.name} options:{len(routes)}', rows)
    if pick is not None and pick > len(routes):
        raise PkError('BOUNDS', f'--pick {pick}; only {len(routes)} distinct options')
    chosen = routes[(pick or 1) - 1]
    before = list(grid.cells)
    cells, rules = _paint(repo, grid, pair, chosen, width, material)
    object_rows, reach_line = _validate(repo, grid, pair, before, cells)
    save(tx, grid)
    lint_result = lint.run(repo, grid.name, show_all=True)
    seams = len(terrain.unseen(grid, rules, cells))
    rows = [f'lint: {lint_result.summary}', *object_rows, reach_line]
    return Result(f'terrain path {grid.name} {len(chosen)} route cells, {len(cells)} painted '
                  f'width:{width} material:{material} straight:{longest_straight(chosen)} seams:{seams}', rows)
