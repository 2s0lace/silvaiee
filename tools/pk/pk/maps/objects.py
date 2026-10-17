"""Broken multi-tile objects: a tree without its upper half, a rock face without its top, a cliff that
stops mid-edge. Learned from vanilla, never hand-coded.

For every cell whose metatile is a wall or ledge (trees, rocks, cliffs, fences, ledges), each cardinal side
is checked against the neighbours vanilla puts there (tile cards' raw counts: same pair plus portable
primary tiles, never Pk* layouts). A side is *constrained* when a few metatiles (at most CONSTRAINED_TOP)
cover CONSTRAINED_SHARE of its vanilla uses. If vanilla never puts the actual neighbour there, the object
is broken on that side. Map-edge checks against the border blocks exist (EDGES) but are off: on vanilla
they fired 2138 times, so the border alignment is not yet right.
"""
from collections import Counter

from ..core.errors import PkError
from .grid import decode

SIDES = {'N': (0, -1), 'E': (1, 0), 'S': (0, 1), 'W': (-1, 0)}
WORD = {'N': 'above', 'E': 'right', 'S': 'below', 'W': 'left'}
MIN_USES = 8              # a side needs this many vanilla observations to be trusted
CONSTRAINED_TOP = 4       # ... and at most this many metatiles may cover ...
CONSTRAINED_SHARE = 0.95  # ... this share of them
EDGES = False             # map-edge (border) checks: off until the border phase is verified on vanilla


def raw_counts(repo, pair):
    """{metatile: {'uses', 'nmt': {dir: Counter}}} from the tile-card cache (built on demand)."""
    from . import cards
    try:
        path = cards._cache_path(repo, pair)
        if path.is_file():
            import json
            doc = json.loads(path.read_text('utf-8'))
            key = f"{pair.primary['symbol']}+{pair.secondary['symbol']}"
            if doc.get('version') == cards.CARDS_VERSION and doc.get('key') == key:
                return cards._load(doc['mt'])
    except (PkError, OSError, ValueError, KeyError, TypeError):
        pass
    cards.load_cards(repo, pair)            # writes the cache
    return cards.aggregate(repo, pair)


def _border(repo, grid):
    layout = grid.layout if hasattr(grid, 'layout') else None
    path = (layout or {}).get('border_filepath')
    if not path:
        return None
    try:
        cells = decode(repo.read(path))
    except PkError:
        return None
    bw = int((layout or {}).get('border_width', 2) or 2)
    bh = int((layout or {}).get('border_height', 2) or 2)
    if len(cells) < bw * bh:
        return None
    return lambda x, y: cells[(y % bh) * bw + (x % bw)][0]


def constrained(side_counts):
    """The few metatiles that make up a constrained side, or None if the side is free."""
    total = sum(side_counts.values())
    if total < MIN_USES:
        return None
    top = side_counts.most_common(CONSTRAINED_TOP)
    if sum(n for _, n in top) < CONSTRAINED_SHARE * total:
        return None
    return top, total


def check(repo, grid, pair, raw=None):
    """Rows `WARN OBJECT x,y 0xMMM needs above: 0xAAA 98%, ... (has 0xBBB)`, one per broken side."""
    from .lint import physical_class
    raw = raw if raw is not None else raw_counts(repo, pair)
    border = _border(repo, grid)
    cls_cache = {}

    def objectish(cell):
        mt, coll = cell[0], cell[1]
        key = (mt, coll)
        if key not in cls_cache:
            cls = physical_class(pair.behavior_name(mt), coll)
            cls_cache[key] = cls == 'wall' or cls.startswith('ledge')
        return cls_cache[key]

    rows = []
    for y in range(grid.h):
        for x in range(grid.w):
            cell = grid.at(x, y)
            rec = raw.get(cell[0])
            if rec is None or not objectish(cell):
                continue
            for side, (dx, dy) in SIDES.items():
                nx, ny = x + dx, y + dy
                if grid.inside(nx, ny):
                    actual = grid.at(nx, ny)[0]
                elif EDGES and border is not None:
                    actual = border(nx, ny)
                else:
                    continue
                spec = constrained(Counter(rec['nmt'][side]))
                if spec is None:
                    continue
                top, total = spec
                if rec['nmt'][side].get(actual, 0):
                    continue                     # vanilla uses this neighbour here: not broken
                needs = ', '.join(f'0x{m:03X} {round(100 * n / total)}%' for m, n in top)
                rows.append(f'WARN OBJECT @{x},{y} 0x{cell[0]:03X} needs {WORD[side]}: {needs} '
                            f'(has 0x{actual:03X}{"" if grid.inside(nx, ny) else " map edge"})')
    return rows
