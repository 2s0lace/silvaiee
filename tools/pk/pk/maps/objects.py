"""Broken multi-tile objects: a tree without its upper half, a rock face without its top, a cliff that
stops mid-edge. Learned from vanilla, never hand-coded.

For every cell whose metatile is a wall or ledge (trees, rocks, cliffs, fences, ledges), each cardinal side
is checked against the neighbours vanilla puts there (tile cards' raw counts: same pair plus portable
primary tiles, never Pk* layouts). A side is *constrained* when a few metatiles (at most CONSTRAINED_TOP)
cover CONSTRAINED_SHARE of its vanilla uses. If vanilla never puts the actual neighbour there, the object is
broken on that side.

Literal map edges: a side that leaves the map shows the border blocks (`GetBorderBlockAt` in
src/fieldmap.c: FRLG layouts use ((x mod borderWidth), (y mod borderHeight)) of the map coordinate), unless
the map has a connection on that side (then the connected map is drawn there and the side is not checked).
Vanilla puts tiles next to its border in combinations that never occur inside a map, so edge sides are
judged against vanilla *edge* statistics (`edge_stats`), learned from every vanilla outdoor map's
unconnected edges.
"""
from collections import Counter

from ..core.errors import PkError
from .grid import decode

SIDES = {'N': (0, -1), 'E': (1, 0), 'S': (0, 1), 'W': (-1, 0)}
WORD = {'N': 'above', 'E': 'right', 'S': 'below', 'W': 'left'}
CONNECTION_SIDE = {'up': 'N', 'down': 'S', 'left': 'W', 'right': 'E'}
MIN_USES = 8              # a side needs this many vanilla observations to be trusted
CONSTRAINED_TOP = 4       # ... and at most this many metatiles may cover ...
CONSTRAINED_SHARE = 0.95  # ... this share of them


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


def border_reader(repo, layout):
    """(x, y) outside the map -> border metatile, exactly as GetBorderBlockAt does for FRLG layouts."""
    path = (layout or {}).get('border_filepath')
    if not path:
        return None
    try:
        cells = decode(repo.read(path))
    except PkError:
        return None
    bw = int(layout.get('border_width', 2) or 2)
    bh = int(layout.get('border_height', 2) or 2)
    if len(cells) < bw * bh:
        return None
    return lambda x, y: cells[(y % bh) * bw + (x % bw)][0]


def connected_sides(data):
    return {CONNECTION_SIDE[c.get('direction')] for c in (data or {}).get('connections') or []
            if c.get('direction') in CONNECTION_SIDE}


_EDGE_CACHE = {}


def edge_stats(repo, pair):
    """{(metatile, side): Counter(border neighbour)} over the unconnected edges of every vanilla outdoor map
    whose ids are meaningful here (same pair, or both ids primary). Cached per pair for the process."""
    from .lint import vanilla_outdoor
    key = (pair.primary['symbol'], pair.secondary['symbol'])
    if key in _EDGE_CACHE:
        return _EDGE_CACHE[key]
    limit = pair.limits['metatiles']
    maps, layouts = repo.maps(), repo.layouts()
    stats = {}
    for name, _ in vanilla_outdoor(repo):
        data = maps[name]
        layout = layouts.get(data['layout']) or {}
        same = (layout.get('primary_tileset'), layout.get('secondary_tileset')) == key
        border = border_reader(repo, layout)
        if border is None:
            continue
        w, h = layout['width'], layout['height']
        try:
            cells = decode(repo.read(layout['blockdata_filepath']))[:w * h]
        except PkError:
            continue
        linked = connected_sides(data)
        for side, (dx, dy) in SIDES.items():
            if side in linked:
                continue
            if side in ('N', 'S'):
                coords = [(x, 0 if side == 'N' else h - 1) for x in range(w)]
            else:
                coords = [(0 if side == 'W' else w - 1, y) for y in range(h)]
            for x, y in coords:
                mt, nb = cells[y * w + x][0], border(x + dx, y + dy)
                if not same and (mt >= limit or nb >= limit):
                    continue
                stats.setdefault((mt, side), Counter())[nb] += 1
    _EDGE_CACHE[key] = stats
    return stats


def constrained(side_counts):
    """(top metatiles, total) of a constrained side, or None if the side is free."""
    total = sum(side_counts.values())
    if total < MIN_USES:
        return None
    top = side_counts.most_common(CONSTRAINED_TOP)
    if sum(n for _, n in top) < CONSTRAINED_SHARE * total:
        return None
    return top, total


class Glue:
    """Vanilla object constraints for one map: which neighbours each object side accepts."""

    def __init__(self, repo, grid, pair, raw=None, edges=True):
        from .lint import physical_class
        self.repo, self.grid, self.pair = repo, grid, pair
        self.raw = raw if raw is not None else raw_counts(repo, pair)
        self.physical_class = physical_class
        self.border = border_reader(repo, getattr(grid, 'layout', None)) if edges else None
        self.linked = connected_sides(getattr(grid, 'data', None))
        self.edges = edge_stats(repo, pair) if self.border is not None else {}
        self._cls = {}

    def objectish(self, cell):
        key = (cell[0], cell[1])
        if key not in self._cls:
            cls = self.physical_class(self.pair.behavior_name(cell[0]), cell[1])
            self._cls[key] = cls == 'wall' or cls.startswith('ledge')
        return self._cls[key]

    def neighbour(self, x, y, side):
        """(metatile, at_edge) of the side's neighbour, or None when it is not checkable."""
        dx, dy = SIDES[side]
        nx, ny = x + dx, y + dy
        if self.grid.inside(nx, ny):
            return self.grid.at(nx, ny)[0], False
        if self.border is None or side in self.linked:
            return None
        return self.border(nx, ny), True

    def broken_side(self, x, y, side, cell=None):
        """None if fine, else (top, total, actual, at_edge) describing what vanilla puts there."""
        cell = cell or self.grid.at(x, y)
        rec = self.raw.get(cell[0])
        if rec is None or not self.objectish(cell):
            return None
        got = self.neighbour(x, y, side)
        if got is None:
            return None
        actual, at_edge = got
        inner = Counter(rec['nmt'][side])
        spec = constrained(inner)
        if spec is None:
            return None
        if inner.get(actual, 0):
            return None                                   # vanilla uses this neighbour inside maps
        if at_edge and self.edges.get((cell[0], side), {}).get(actual, 0):
            return None                                   # vanilla shows this tile against its border
        return spec[0], spec[1], actual, at_edge

    def rows(self):
        out = []
        for y in range(self.grid.h):
            for x in range(self.grid.w):
                cell = self.grid.at(x, y)
                for side in SIDES:
                    bad = self.broken_side(x, y, side, cell)
                    if not bad:
                        continue
                    top, total, actual, at_edge = bad
                    needs = ', '.join(f'0x{m:03X} {round(100 * n / total)}%' for m, n in top)
                    out.append(f'WARN OBJECT @{x},{y} 0x{cell[0]:03X} needs {WORD[side]}: {needs} '
                               f'(has 0x{actual:03X}{" map edge; set a matching border with pk map border" if at_edge else ""})')
        return out


def check(repo, grid, pair, raw=None, edges=True):
    """Rows `WARN OBJECT @x,y 0xMMM needs above: 0xAAA 98%, ... (has 0xBBB)`, one per broken side."""
    return Glue(repo, grid, pair, raw, edges).rows()


def count_in(glue, cells):
    """Broken sides touching any of the cells (each broken side counted once)."""
    seen = set()
    for (x, y) in cells:
        for side, (dx, dy) in SIDES.items():
            for cx, cy in ((x, y), (x - dx, y - dy)):
                if glue.grid.inside(cx, cy) and (cx, cy, side) not in seen and glue.broken_side(cx, cy, side):
                    seen.add((cx, cy, side))
    return len(seen)
