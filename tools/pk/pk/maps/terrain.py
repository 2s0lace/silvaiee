"""pk terrain: paint *materials*, let learned vanilla adjacency choose the metatiles.

Model (DECISIONS.md, milestone 4b): every cell has a material = the class view label
(ground, solid, water, grass, sand, ledge_*, door, ...) or the first tag of a named metatile
(tree, path, flowers, cliff ...). Brushes set the material of cells; a solver then picks, for
every touched cell plus a 1-cell margin (margin cells keep their material), the metatile of
that material that best agrees with its 4 neighbours according to how often each pair of
metatiles sits side by side in vanilla maps that use the same tileset pair.
"""
import math
import re

from ..core.errors import PkError
from ..core.out import Result, page
from .grid import decode
from .layers import Grid, class_of, render
from .edit import load, save, default_bits, area, point, changed_view
from .tiles import Pair


class Rules:
    """Adjacency statistics of one tileset pair, learned from every vanilla layout using it."""
    def __init__(self, repo, pair, include_pk=True):
        self.pair = pair
        key = (pair.primary['symbol'], pair.secondary['symbol'])
        self.h, self.v, self.freq, self.by_mat = {}, {}, {}, {}
        # Diagonal pair tables (NE/NW/SE/SW), keyed (cell, diagonal neighbour in that compass
        # direction). They are only scored when solve(diag_weight=...) is non-zero; the default
        # weight is 0 until the acceptance test proves them. Corners (outer/inner) show up here,
        # where 4-neighbour scoring cannot see them.
        self.ne, self.nw, self.se, self.sw = {}, {}, {}, {}
        self.layouts = 0
        for layout in repo.layouts().values():
            if (layout.get('primary_tileset'), layout.get('secondary_tileset')) != key:
                continue
            if not include_pk and str(layout.get('id', '')).startswith('LAYOUT_PK_'):
                continue    # Pk* drafts are never reference data (project rule)
            w, h = layout['width'], layout['height']
            try:
                cells = decode(repo.read(layout['blockdata_filepath'])[:w * h * 2])
            except PkError:
                continue
            self.layouts += 1
            for y in range(h):
                for x in range(w):
                    a = cells[y * w + x]
                    self.freq[a[0]] = self.freq.get(a[0], 0) + 1
                    self.by_mat.setdefault(self.material(a), set()).add(a[0])
                    if x + 1 < w:
                        k = (a[0], cells[y * w + x + 1][0])
                        self.h[k] = self.h.get(k, 0) + 1
                    if y + 1 < h:
                        k = (a[0], cells[(y + 1) * w + x][0])
                        self.v[k] = self.v.get(k, 0) + 1
                    if x + 1 < w and y > 0:
                        k = (a[0], cells[(y - 1) * w + x + 1][0])
                        self.ne[k] = self.ne.get(k, 0) + 1
                    if x > 0 and y > 0:
                        k = (a[0], cells[(y - 1) * w + x - 1][0])
                        self.nw[k] = self.nw.get(k, 0) + 1
                    if x + 1 < w and y + 1 < h:
                        k = (a[0], cells[(y + 1) * w + x + 1][0])
                        self.se[k] = self.se.get(k, 0) + 1
                    if x > 0 and y + 1 < h:
                        k = (a[0], cells[(y + 1) * w + x - 1][0])
                        self.sw[k] = self.sw.get(k, 0) + 1
        # Named metatiles join their tag's material even if vanilla never used them.
        for ts in (pair.primary, pair.secondary):
            for hexid, info in pair.names(ts).items():
                if info.get('tags'):
                    self.by_mat.setdefault(info['tags'][0], set()).add(int(hexid, 16))

    def material(self, cell):
        info = self.pair.info(cell[0])
        if info.get('tags'):
            return info['tags'][0]
        return class_of(self.pair, cell[0], cell[1])[1]

    def pair_score(self, table, a, b):
        return math.log(table.get((a, b), 0) + 0.01)


def solve(grid, rules, targets, margin=1, passes=6, local_weight=0.3, frozen=frozenset(),
          choices=None, unary=None, diag_weight=0.0, seed=None, freq_weight=0.05):
    """targets: {(x,y): material}. Returns {(x,y): metatile} for targets + margin ring.

    Prior = freq_weight*log(vanilla frequency) + local_weight*log(frequency in this map outside the
    targets): a new area then looks like the rest of the same map (Pallet's light grass, not the
    tufted forest-edge grass that dominates other layouts of the pair).

    choices replaces the material -> candidate set: {(x,y): [metatile,...]} for any cell.
    unary(m, p) -> float is added to every candidate score (used by map paintover, which scores
    -k * colour distance between the metatile's pixels and the painted image).

    seed starts the iterative pass from an existing assignment instead of a fresh greedy one, so
    seam repair only changes cells that actually improve the adjacency score (few-cell edits)."""
    local = {}
    for y in range(grid.h):
        for x in range(grid.w):
            if (x, y) not in targets:
                m = grid.at(x, y)[0]
                local[m] = local.get(m, 0) + 1
    prior = lambda m: (freq_weight * math.log(rules.freq.get(m, 0) + 1)
                       + local_weight * math.log(local.get(m, 0) + 1))
    cells = dict(targets)
    for (x, y) in list(targets):
        for dx in range(-margin, margin + 1):
            for dy in range(-margin, margin + 1):
                p = (x + dx, y + dy)
                if p not in cells and p not in frozen and grid.inside(*p):
                    cells[p] = rules.material(grid.at(*p))
    cand = {}
    for p, mat in cells.items():
        options = list(choices[p]) if choices is not None else rules.by_mat.get(mat)
        if not options:
            if choices is not None:
                raise PkError('NOTFOUND', f'cell {p} has no candidate metatiles')
            raise PkError('NOTFOUND', f'material {mat} has no metatiles in {rules.pair.secondary["symbol"]}; '
                                      f'known: {" ".join(sorted(rules.by_mat))}')
        cand[p] = options
    bonus = unary if unary else (lambda m, p: 0.0)
    def diag_score(m, x, y, get):
        """Diagonal (corner) agreement, weighted by diag_weight (0 = off, the default)."""
        if not diag_weight:
            return 0.0
        s = 0.0
        for dx, dy, table in ((1, -1, rules.ne), (-1, -1, rules.nw), (1, 1, rules.se), (-1, 1, rules.sw)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < grid.w and 0 <= ny < grid.h:
                n = get(nx, ny)
                if n is not None:
                    s += rules.pair_score(table, m, n)
        return diag_weight * s
    current = {}
    if seed is not None:
        for p in cells:
            current[p] = seed.get(p) if seed.get(p) is not None else grid.at(*p)[0]
    else:
        # Greedy initialisation from the outside in: each cell is chosen against the neighbours
        # that are already fixed (untouched map cells or cells assigned earlier). This avoids most
        # of the local optima a plain row-by-row start falls into.
        def known(x, y):
            if (x, y) in current:
                return current[(x, y)]
            if (x, y) not in cells and grid.inside(x, y):
                return grid.at(x, y)[0]
            return None
        def depth(p):
            d, frontier = 0, {p}
            while all(q in cells for q in frontier) and d < 50:
                d += 1
                frontier = {(x + dx, y + dy) for x, y in frontier for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))}
            return d
        for p in sorted(cells, key=lambda p: (depth(p), p[1], p[0])):
            x, y = p
            best, best_score = None, None
            for m in cand[p]:
                s = prior(m) + bonus(m, p)
                for (nx, ny), table, first in (((x - 1, y), rules.h, True), ((x + 1, y), rules.h, False),
                                               ((x, y - 1), rules.v, True), ((x, y + 1), rules.v, False)):
                    n = known(nx, ny)
                    if n is not None:
                        s += rules.pair_score(table, n, m) if first else rules.pair_score(table, m, n)
                s += diag_score(m, x, y, known)
                if best_score is None or s > best_score or (s == best_score and m < best):
                    best, best_score = m, s
            current[p] = best
    get = lambda x, y: current[(x, y)] if (x, y) in current else grid.at(x, y)[0]
    order = sorted(cells, key=lambda p: (p[1], p[0]))
    for _ in range(passes):
        changed = 0
        for (x, y) in order:
            best, best_score = current[(x, y)], None
            for m in cand[(x, y)]:
                s = prior(m) + bonus(m, (x, y))
                if x > 0:
                    s += rules.pair_score(rules.h, get(x - 1, y), m)
                if x + 1 < grid.w:
                    s += rules.pair_score(rules.h, m, get(x + 1, y))
                if y > 0:
                    s += rules.pair_score(rules.v, get(x, y - 1), m)
                if y + 1 < grid.h:
                    s += rules.pair_score(rules.v, m, get(x, y + 1))
                s += diag_score(m, x, y, get)
                if best_score is None or s > best_score or (s == best_score and m < best):
                    best, best_score = m, s
            if best != current[(x, y)]:
                current[(x, y)] = best
                changed += 1
        if not changed:
            break
    return current


def ring_cells(grid, box, ring):
    """The editable band around `box`: cells within `ring` tiles of the box rectangle, minus the
    deep interior (the box shrunk by `ring`), so a boundary may move in or out by up to `ring`."""
    x0, y0, w, h = box
    ex0, ey0, ex1, ey1 = x0 - ring, y0 - ring, x0 + w - 1 + ring, y0 + h - 1 + ring
    sx0, sy0, sx1, sy1 = x0 + ring, y0 + ring, x0 + w - 1 - ring, y0 + h - 1 - ring
    cells = set()
    for y in range(max(ey0, 0), min(ey1, grid.h - 1) + 1):
        for x in range(max(ex0, 0), min(ex1, grid.w - 1) + 1):
            if sx0 <= x <= sx1 and sy0 <= y <= sy1:
                continue                    # deep interior stays frozen
            cells.add((x, y))
    return cells


def repair(repo, pair, rules, grid, box, ring=2, frozen=frozenset(), passes=16):
    """Re-solve only the band around `box` (ring_cells) against the *seen* vanilla adjacencies.

    A cell is only swapped for another metatile of the same material, and only when that lowers the
    number of adjacent metatile pairs that never occur in vanilla (the SEAM metric, sockets applied).
    Unlike solve(), this minimises the critic directly, so a repair changes few cells and still
    removes seams. Returns the list of changed cells; the rest of the map is untouched."""
    targets = ring_cells(grid, box, ring) - set(frozen)
    if not targets:
        return []
    known = set(rules.freq)
    sockets = {}

    def resolve(metatile):
        if metatile not in sockets:
            sock = (pair.info(metatile) or {}).get('socket')
            sockets[metatile] = int(sock) if sock is not None else metatile
        return sockets[metatile]

    def unseen(a, b, table):
        sa, sb = resolve(a), resolve(b)
        return 1 if (sa in known and sb in known and (sa, sb) not in table) else 0

    def cell_cost(x, y, metatile):
        cost = 0
        if x > 0:
            cost += unseen(grid.at(x - 1, y)[0], metatile, rules.h)
        if x + 1 < grid.w:
            cost += unseen(metatile, grid.at(x + 1, y)[0], rules.h)
        if y > 0:
            cost += unseen(grid.at(x, y - 1)[0], metatile, rules.v)
        if y + 1 < grid.h:
            cost += unseen(metatile, grid.at(x, y + 1)[0], rules.v)
        return cost

    before = {p: grid.at(*p) for p in targets}
    order = sorted(targets)
    for _ in range(passes):
        changed = 0
        for (x, y) in order:
            old = grid.at(x, y)
            material = rules.material(old)
            best, best_cost = old[0], cell_cost(x, y, old[0])
            for metatile in sorted(rules.by_mat.get(material, ())):
                if metatile == old[0]:
                    continue
                cost = cell_cost(x, y, metatile)
                if cost < best_cost or (cost == best_cost and metatile < best):
                    best, best_cost = metatile, cost
            if best != old[0]:
                grid.set(x, y, bits_for(repo, pair, rules, best, material, old))
                changed += 1
        if not changed:
            break
    return [p for p in targets if grid.at(*p) != before[p]]


def unseen(grid, rules, cells):
    """Adjacent pairs touching the given cells that never occur in vanilla."""
    bad = set()
    for (x, y) in cells:
        for (ax, ay), (bx, by), table in (((x - 1, y), (x, y), rules.h), ((x, y), (x + 1, y), rules.h),
                                          ((x, y - 1), (x, y), rules.v), ((x, y), (x, y + 1), rules.v)):
            if grid.inside(ax, ay) and grid.inside(bx, by) and (grid.at(ax, ay)[0], grid.at(bx, by)[0]) not in table:
                bad.add((min(ax, bx), min(ay, by), 'h' if table is rules.h else 'v'))
    return sorted(bad)


def shape_cells(grid, words):
    """rect x,y,WxH | circle x,y,r | path x,y x,y ... [width=w] | line x,y x,y"""
    if not words:
        raise PkError('PARSE', 'shape: rect x,y,WxH | circle x,y,r | path x,y x,y ... [width=n]')
    kind, rest = words[0], words[1:]
    width = 1
    points = []
    for w in rest:
        m = re.fullmatch(r'width=(\d+)', w)
        if m:
            width = int(m[1])
        else:
            points.append(w)
    if kind == 'rect' and len(points) == 1:
        x, y, w, h = area(points[0], grid)
        return {(i, j) for j in range(y, y + h) for i in range(x, x + w)}
    if kind == 'circle' and len(points) == 1:
        m = re.fullmatch(r'(\d+),(\d+),(\d+)', points[0])
        if not m:
            raise PkError('PARSE', 'circle x,y,r')
        cx, cy, r = map(int, m.groups())
        return {(i, j) for j in range(cy - r, cy + r + 1) for i in range(cx - r, cx + r + 1)
                if grid.inside(i, j) and (i - cx) ** 2 + (j - cy) ** 2 <= r * r + r}
    if kind in ('path', 'line') and len(points) >= 2:
        pts = [point(p, grid) for p in points]
        cells = set()
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            n = max(abs(x2 - x1), abs(y2 - y1), 1)
            for k in range(n + 1):
                x, y = round(x1 + (x2 - x1) * k / n), round(y1 + (y2 - y1) * k / n)
                for dx in range(width):
                    for dy in range(width):
                        if grid.inside(x + dx, y + dy):
                            cells.add((x + dx, y + dy))
        return cells
    raise PkError('PARSE', f'bad shape {" ".join(words)}')


def bits_for(repo, pair, rules, mt, mat, old):
    """Collision/elevation that keep the cell in its material: the most common vanilla
    (coll, elev) of this metatile among usages of that material (a metatile used both as
    walkable and blocked must not silently turn 'ground' into 'solid')."""
    from .edit import usage
    stats = usage(repo, pair).get(mt, {})
    fitting = {k: n for k, n in stats.items() if rules.material((mt, k[0], k[1])) == mat}
    if fitting:
        coll, elev = max(fitting, key=lambda k: (fitting[k], -k[0]))
        return mt, coll, elev
    return default_bits(repo, pair, mt, old)


def apply(repo, tx, grid, pair, rules, targets, label, frozen=frozenset()):
    materials = {p: targets.get(p) or rules.material(grid.at(*p)) for p in targets}
    result = solve(grid, rules, targets, frozen=frozen)
    changed = []
    for p, mt in result.items():
        old = grid.at(*p)
        mat = materials.get(p) or rules.material(old)
        if old[0] != mt or rules.material(old) != mat:
            grid.set(*p, bits_for(repo, pair, rules, mt, mat, old))
            changed.append(p)
    save(tx, grid)
    bad = unseen(grid, rules, list(result))
    rows = changed_view(grid, pair, changed or list(targets), 'class')
    if bad:
        rows.insert(0, f'WARN {len(bad)} seams never seen in vanilla, e.g. ' +
                    ' '.join(f'{x},{y}{d}' for x, y, d in bad[:5]) + ' (check with pk map show --exact)')
    return Result(f'terrain {label} {grid.name} {len(targets)} cells, {len(changed)} metatiles changed', rows)


def command(repo, tx, args, opts, pagination):
    verb, rest = args[0], args[1:]
    if verb == 'learn' and len(rest) == 1:
        grid, pair = load(repo, rest[0])
        rules = Rules(repo, pair)
        rows = [f'{m} {len(ids)} metatiles' for m, ids in sorted(rules.by_mat.items(), key=lambda kv: -len(kv[1]))]
        return page(f'terrain learn {pair.primary["symbol"]}+{pair.secondary["symbol"]} layouts:{rules.layouts} '
                    f'pairs:{len(rules.h) + len(rules.v)}', rows, *pagination(opts))
    if verb == 'show' and len(rest) == 1:
        grid, pair = load(repo, rest[0])
        from .layers import parse_rect
        rect = parse_rect(opts.get('rect'), grid.w, grid.h)
        return Result(f'terrain {grid.name} materials', render(grid, pair, rect, 'class', False))
    if verb == 'brush' and len(rest) >= 3:
        grid, pair = load(repo, rest[0])
        material = rest[-1]
        cells = shape_cells(grid, rest[1:-1])
        return apply(repo, tx, grid, pair, Rules(repo, pair), {p: material for p in cells}, f'brush {material}')
    if verb == 'river' and len(rest) >= 3:
        grid, pair = load(repo, rest[0])
        cells = shape_cells(grid, ['path'] + rest[1:])
        return apply(repo, tx, grid, pair, Rules(repo, pair), {p: 'water' for p in cells}, 'river')
    if verb == 'ledge' and len(rest) == 3:
        grid, pair = load(repo, rest[0])
        (x1, y1), (x2, y2) = point(rest[1], grid), point(rest[2], grid)
        direction = opts.get('dir', 's')
        cells = shape_cells(grid, ['line', rest[1], rest[2]])
        return apply(repo, tx, grid, pair, Rules(repo, pair), {p: f'ledge_{direction}' for p in cells}, f'ledge {direction}')
    if verb == 'forest' and len(rest) >= 3:
        grid, pair = load(repo, rest[0])
        import json
        from .create import stamp_path, stamp_fits
        stamp = json.loads(repo.read(stamp_path(opts.get('stamp', 'tree'))))
        if not stamp_fits(stamp, pair):
            raise PkError('CONFLICT', f"stamp {stamp['name']} is for another tileset pair")
        cells = shape_cells(grid, rest[1:])
        sw, sh = stamp['size']
        xs, ys = min(x for x, _ in cells), min(y for _, y in cells)
        placed = []
        for (x, y) in sorted(cells, key=lambda p: (p[1], p[0])):
            c = stamp['cells'][(y - ys) % sh][(x - xs) % sw]
            grid.set(x, y, tuple(c))
            placed.append((x, y))
        save(tx, grid)
        rows = changed_view(grid, pair, placed, 'class')
        return Result(f'terrain forest {grid.name} {len(placed)} cells tiled with stamp {stamp["name"]}', rows)
    if verb == 'bake' and len(rest) == 1:
        grid, pair = load(repo, rest[0])
        from .layers import parse_rect
        x0, y0, w, h = parse_rect(opts.get('rect'), grid.w, grid.h)
        rules = Rules(repo, pair)
        targets = {(x, y): rules.material(grid.at(x, y)) for y in range(y0, y0 + h) for x in range(x0, x0 + w)}
        return apply(repo, tx, grid, pair, rules, targets, 'bake')
    raise PkError('PARSE', 'usage: pk terrain learn|show|brush|river|ledge|forest|bake; see pk help terrain')
