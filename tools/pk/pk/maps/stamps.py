"""Stamp mining and instance detection (SPEC §5.3, maintainer round 1 step 3).

Mining counts repeated rectangles of (metatile, collision, elevation) across the vanilla
layouts of one tileset pair. It starts from every 2x2 window that appears at least `min_count`
times and grows each rectangle right and down while it stays frequent (the growth keeps only
the occurrences that share the new column/row), so the result is a set of maximal repeated
rectangles: trees, house walls, fences, signs. Nothing is written unless the caller applies.
"""
from collections import defaultdict

from ..core.errors import PkError
from .grid import decode

# One unique symbol per stamp name in map show --stamps. Uppercase letters (minus D, which the
# class view uses for doors) then digits; lowercase and W/#/~ are taken by classes and events.
SYMBOLS = ''.join(c for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789' if c != 'D')


def layouts_for(repo, pair):
    key = (pair.primary['symbol'], pair.secondary['symbol'])
    out = []
    for layout in repo.layouts().values():
        if (layout.get('primary_tileset'), layout.get('secondary_tileset')) != key:
            continue
        if str(layout.get('id', '')).startswith('LAYOUT_PK_'):
            continue        # Pk* drafts are never reference data, so mining ignores them
        w, h = layout['width'], layout['height']
        try:
            cells = decode(repo.read(layout['blockdata_filepath'])[:w * h * 2])
        except PkError:
            continue
        out.append((layout['id'], w, h, cells))
    return out


def mine(repo, pair, min_size=(2, 2), min_count=3, max_area=48, solid_only=True, maximal=True):
    """Repeated rectangles appearing >= min_count times. solid_only keeps trees, house walls,
    fences and signs (every cell solid) and drops the many random ground/solid patches that
    otherwise repeat three times by chance. Recognisable objects are the rectangles that cannot
    grow (maximal); their min-size sub-blocks are always kept too, because a house is detected by
    tiling its 2x2 blocks. maximal=False additionally keeps every frequent intermediate size."""
    min_w, min_h = min_size
    grids = layouts_for(repo, pair)
    occ = defaultdict(list)                     # content (tuple of rows) -> [(grid, x, y)]
    for gi, (_, W, H, cells) in enumerate(grids):
        for y in range(H - 1):
            for x in range(W - 1):
                content = ((cells[y * W + x], cells[y * W + x + 1]),
                           (cells[(y + 1) * W + x], cells[(y + 1) * W + x + 1]))
                occ[content].append((gi, x, y))
    queue = [c for c, ps in occ.items() if len(ps) >= min_count]
    seen, extended = set(), set()
    while queue:
        content = queue.pop()
        if content in seen:
            continue
        seen.add(content)
        w, h = len(content[0]), len(content)
        positions = occ[content]
        if w * h >= max_area:
            continue
        groups = defaultdict(list)              # grow right
        for gi, x, y in positions:
            W = grids[gi][1]
            if x + w < W:
                col = tuple(grids[gi][3][(y + j) * W + x + w] for j in range(h))
                groups[col].append((gi, x, y))
        for col, ps in groups.items():
            if len(ps) >= min_count:
                grown = tuple(row + (col[j],) for j, row in enumerate(content))
                extended.add(content)
                if grown not in occ:
                    occ[grown] = ps
                    queue.append(grown)
        groups = defaultdict(list)              # grow down
        for gi, x, y in positions:
            W, H = grids[gi][1], grids[gi][2]
            if y + h < H:
                row = tuple(grids[gi][3][(y + h) * W + x + i] for i in range(w))
                groups[row].append((gi, x, y))
        for row, ps in groups.items():
            if len(ps) >= min_count:
                grown = content + (row,)
                extended.add(content)
                if grown not in occ:
                    occ[grown] = ps
                    queue.append(grown)
    proposals = []
    for content, positions in occ.items():
        w, h = len(content[0]), len(content)
        if w < min_w or h < min_h:
            continue
        if maximal and content in extended and w * h > min_w * min_h:
            continue
        flat = [cell for row in content for cell in row]
        if not any(cell[1] for cell in flat):                     # no solid cell
            continue
        if solid_only and not all(cell[1] for cell in flat):      # mixed ground/solid patch
            continue
        proposals.append(dict(content=content, size=(w, h), count=len(positions),
                               source=grids[positions[0][0]][0], at=(positions[0][1], positions[0][2])))
    proposals.sort(key=lambda p: (-p['size'][0] * p['size'][1], -p['count'], p['size']))
    return proposals


def classify(content, tree_ids):
    w, h = len(content[0]), len(content)
    flat = [cell for row in content for cell in row]
    ids = {cell[0] for cell in flat}
    if ids and ids <= tree_ids:
        return 'tree'
    if all(cell[1] for cell in flat):           # every cell solid
        return 'building'
    if min(w, h) == 1:
        return 'fence'
    return 'patch'


SYM_OF_KIND = {'tree': 'T', 'building': 'H', 'fence': 'F', 'patch': 'P'}


def to_stamps(repo, pair, proposals, tree_ids):
    stamps, counters = [], {}
    tag = pair.secondary['symbol'].replace('gTileset_', '').lower()
    for p in proposals:
        content = p['content']
        kind = classify(content, tree_ids)
        n = counters.get(kind, 0)
        counters[kind] = n + 1
        name = f'mined_{tag}_{kind}_{n}'
        stamps.append(dict(name=name, size=[p['size'][0], p['size'][1]], sym=SYM_OF_KIND.get(kind, 'S'),
                           primary=pair.primary['symbol'], secondary=pair.secondary['symbol'],
                           source=f'{p["source"]} {p["at"][0]},{p["at"][1]} (x{p["count"]})',
                           cells=[[list(cell) for cell in row] for row in content], on='land'))
    return stamps


def tree_metatiles(repo, pair):
    """Metatile ids of the hand-made `tree` stamp when it fits this pair (else empty)."""
    import json
    try:
        data = json.loads(repo.read('tools/pk/data/stamps/tree.json'))
    except PkError:
        return set()
    if data['primary'] != pair.primary['symbol'] and data['secondary'] != pair.secondary['symbol']:
        if data['primary'] != pair.primary['symbol']:
            return set()
    return {cell[0] for row in data['cells'] for cell in row}


def load_stamps(repo, pair):
    """Every stamp that can stand in a map of this pair (same pair, or primary-only stamps)."""
    import json
    out = []
    root = repo.root / 'tools/pk/data/stamps'
    if not root.exists():
        return out
    for path in sorted(root.glob('*.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        same = (data['primary'], data['secondary']) == (pair.primary['symbol'], pair.secondary['symbol'])
        primary_only = data['primary'] == pair.primary['symbol'] and all(
            cell[0] < pair.limits['metatiles'] for row in data['cells'] for cell in row if cell)
        if same or primary_only:
            out.append(data)
    out.sort(key=lambda s: -s['size'][0] * s['size'][1])
    return out


def instances(grid, stamps):
    """Greedy largest-first exact matches. Returns (found, covered) where found is a list of
    (stamp, x, y) and covered maps (x, y) -> stamp. A null cell is a hole: matches anything and
    is not covered."""
    found, covered = [], {}
    for stamp in stamps:
        w, h = stamp['size']
        cells = stamp['cells']
        for y in range(grid.h - h + 1):
            for x in range(grid.w - w + 1):
                if any((x + i, y + j) in covered for j in range(h) for i in range(w) if cells[j][i]):
                    continue
                if all(cells[j][i] is None or grid.at(x + i, y + j) == tuple(cells[j][i])
                       for j in range(h) for i in range(w)):
                    for j in range(h):
                        for i in range(w):
                            if cells[j][i] is not None:
                                covered[(x + i, y + j)] = stamp
                    found.append((stamp, x, y))
    return found, covered


def _match_cells(W, H, cells, stamp):
    """Every cell of a map covered by some exact match of the stamp (matches may overlap)."""
    w, h = stamp['size']
    st = stamp['cells']
    cov = set()
    for y in range(H - h + 1):
        for x in range(W - w + 1):
            if all(st[j][i] is None or cells[(y + j) * W + x + i] == tuple(st[j][i])
                   for j in range(h) for i in range(w)):
                for j in range(h):
                    for i in range(w):
                        if st[j][i] is not None:
                            cov.add((x + i, y + j))
    return cov


def select(repo, pair, stamps, cap=16):
    """Greedily keep the stamps that cover the most still-uncovered structure cells of the
    pair's layouts, so map show --stamps and --apply get a small, useful set."""
    grids = layouts_for(repo, pair)
    matches = [[_match_cells(W, H, cells, st) for st in stamps] for (_, W, H, cells) in grids]
    covered = [set() for _ in grids]
    chosen, remaining = [], set(range(len(stamps)))
    while remaining and len(chosen) < cap:
        best_i, best_gain = None, 0
        for i in remaining:
            gain = sum(len(matches[gi][i] - covered[gi]) for gi in range(len(grids)))
            if gain > best_gain:
                best_i, best_gain = i, gain
        if best_i is None:
            break
        chosen.append(stamps[best_i])
        for gi in range(len(grids)):
            covered[gi] |= matches[gi][best_i]
        remaining.discard(best_i)
    return chosen


def coverage(grid, pair, stamps):
    """(covered structure cells, total structure cells). Structure = class-view solid '#' cells."""
    from .layers import class_of
    _, covered = instances(grid, stamps)
    total = {(x, y) for y in range(grid.h) for x in range(grid.w)
             if class_of(pair, *grid.at(x, y)[:2])[0] == '#'}
    return len(total & set(covered)), len(total)
