"""Text views of a map grid: semantic classes, exact metatiles, collision, elevation."""
import re

from ..core.errors import PkError
from .grid import decode

# Behaviour-name patterns -> class symbol (first match wins). Names are MB_FRLG_* or MB_*.
CLASSES = [
    (r'WATERFALL', '|', 'waterfall'),
    (r'TALL_GRASS|LONG_GRASS|CYCLING_ROAD_PULL_DOWN_GRASS', '"', 'grass'),
    (r'JUMP_EAST', '>', 'ledge_e'), (r'JUMP_WEST', '<', 'ledge_w'),
    (r'JUMP_NORTH', '^', 'ledge_n'), (r'JUMP_SOUTH', 'v', 'ledge_s'),
    (r'WATER|OCEAN|POND|CURRENT|SEAWEED|PUDDLE', '~', 'water'),
    (r'SAND', ':', 'sand'),
    (r'DOOR|LADDER|STAIRS|WARP', 'D', 'door'),
    (r'COUNTER', '=', 'counter'),
    (r'ICE', '*', 'ice'),
]
EVENT_MARKS = [('W', 'warp'), ('o', 'npc'), ('i', 'item'), ('s', 'sign'), ('h', 'hidden'), ('t', 'trigger')]
# Exact-view symbols: letters first (punctuation reads like the class view), digits last.
POOL = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ' + r""".#,;:"~=+*%&@$!^<>/\|()[]{}`'-_""" + '0123456789'


class Grid:
    def __init__(self, repo, map_name, data, layout):
        self.repo, self.name, self.data, self.layout = repo, map_name, data, layout
        self.w, self.h = layout['width'], layout['height']
        self.cells = decode(repo.read(layout['blockdata_filepath']))
        if len(self.cells) != self.w * self.h:
            raise PkError('PARSE', f"{layout['blockdata_filepath']} size does not match {self.w}x{self.h}")

    def at(self, x, y):
        return self.cells[y * self.w + x]

    def set(self, x, y, cell):
        self.cells[y * self.w + x] = cell

    def inside(self, x, y):
        return 0 <= x < self.w and 0 <= y < self.h


def parse_rect(text, w, h):
    if not text:
        return 0, 0, w, h
    m = re.fullmatch(r'(\d+),(\d+)(?:,(\d+)(?:,|x)(\d+))?', text)
    if not m:
        raise PkError('PARSE', 'rect is x,y,w,h (or x,y,WxH)')
    x, y = int(m[1]), int(m[2])
    rw, rh = (int(m[3]), int(m[4])) if m[3] else (w - x, h - y)
    if rw <= 0 or rh <= 0 or x + rw > w or y + rh > h:
        raise PkError('BOUNDS', f'rect {text} outside map {w}x{h}')
    return x, y, rw, rh


def class_of(pair, metatile, collision):
    info = pair.info(metatile)
    if info.get('sym'):
        return info['sym'], info.get('name', '')
    name = pair.behavior_name(metatile)
    for pattern, sym, label in CLASSES:
        if re.search(pattern, name):
            return sym, label
    return ('#', 'solid') if collision else ('.', 'ground')


def events(data):
    marks = {}
    order = {s: i for i, (s, _) in enumerate(EVENT_MARKS)}
    def put(x, y, sym):
        if (x, y) not in marks or order[sym] < order[marks[(x, y)]]:
            marks[(x, y)] = sym
    for e in data.get('warp_events') or []:
        put(e['x'], e['y'], 'W')
    for e in data.get('object_events') or []:
        put(e['x'], e['y'], 'i' if 'ITEM_BALL' in str(e.get('graphics_id', '')) else 'o')
    for e in data.get('bg_events') or []:
        put(e['x'], e['y'], 'h' if e.get('type') == 'hidden_item' else 's')
    for e in data.get('coord_events') or []:
        put(e['x'], e['y'], 't')
    return marks


def rulers(x0, rw):
    tens = ''.join(str((x // 10) % 10) if x % 10 == 0 or x == x0 else '-' for x in range(x0, x0 + rw))
    units = ''.join(str(x % 10) for x in range(x0, x0 + rw))
    return [f'x|{tens}', f'x|{units}']


def exact_legend(grid, pair, rect):
    """Deterministic symbol per distinct metatile in rect (named syms preferred)."""
    x0, y0, rw, rh = rect
    counts = {}
    for y in range(y0, y0 + rh):
        for x in range(x0, x0 + rw):
            mt = grid.at(x, y)[0]
            counts[mt] = counts.get(mt, 0) + 1
    order = sorted(counts, key=lambda m: (-counts[m], m))
    legend, used = {}, set()
    for mt in order:
        sym = pair.info(mt).get('sym')
        if sym and len(sym) == 1 and sym not in used:
            legend[mt] = sym
            used.add(sym)
    pool = iter(c for c in POOL if c not in used)
    for mt in order:
        if mt not in legend:
            sym = next(pool, None)
            if sym is None:
                raise PkError('LIMIT', f'{len(order)} distinct metatiles in view; use a smaller --rect for --exact')
            legend[mt] = sym
    return legend


def compact_values(grid, pair, rect, layer, sym_of=None, show_events=True, cap=0.1):
    """(defaults, exceptions) for one coll/elev layer, or None when exceptions exceed cap.

    defaults: {symbol: dominant value}; exceptions: sorted [(x, y, value)] where a cell's
    value differs from its symbol's default. Event overlays hide the class in the class view,
    so their values are always exceptions. `sym_of(x, y, cell) -> symbol` lets callers key on
    something else than the class symbol (pk map export keys on the exact-metatile legend)."""
    if layer not in ('coll', 'elev'):
        raise PkError('PARSE', 'compact layer is coll|elev')
    x0, y0, rw, rh = rect
    idx = 1 if layer == 'coll' else 2
    if sym_of is None:
        def sym_of(x, y, c):
            return class_of(pair, c[0], c[1])[0]
    cells = {}
    for y in range(y0, y0 + rh):
        for x in range(x0, x0 + rw):
            c = grid.at(x, y)
            cells[(x, y)] = (sym_of(x, y, c), c[idx])
    counts = {}
    for sym, v in cells.values():
        counts.setdefault(sym, {}).setdefault(v, 0)
        counts[sym][v] += 1
    default = {sym: max(vs, key=lambda v: (vs[v], -v)) for sym, vs in counts.items()}
    marks = events(grid.data) if show_events else {}
    exceptions = [(x, y, v) for (x, y), (sym, v) in cells.items()
                  if v != default[sym] or (x, y) in marks]
    if cap is not None and len(exceptions) > rw * rh * cap:
        return None
    return default, exceptions


def render_by_class(grid, pair, rect, layer, show_events=True):
    """coll/elev as the dominant value per class symbol plus exceptions (lossless).
    Event overlays hide the class, so their values must be explicit exceptions.
    Returns None when exceptions exceed 10% of the visible cells."""
    compact = compact_values(grid, pair, rect, layer, show_events=show_events)
    if compact is None:
        return None
    default, exceptions = compact
    fmt = str if layer == 'coll' else (lambda v: f'{v:X}')
    rows = [f'layer {layer} by class: ' + ' '.join(f'{s}={fmt(default[s])}' for s in sorted(default))
            + f'; exceptions {len(exceptions)}']
    by_row = {}
    for x, y, v in sorted(exceptions, key=lambda e: (e[1], e[0])):
        by_row.setdefault(y, []).append(f'{x}={fmt(v)}')
    rows += [f'{y:02d}| ' + ' '.join(items) for y, items in by_row.items()]
    return rows


def render(grid, pair, rect, layer='class', show_events=True):
    x0, y0, rw, rh = rect
    marks = events(grid.data) if show_events and layer in ('class', 'exact') else {}
    rows, legend_rows = [], []
    if layer == 'class':
        seen = {}
        def cell(x, y):
            mt, coll, _ = grid.at(x, y)
            sym, label = class_of(pair, mt, coll)
            seen.setdefault(sym, label)
            return sym
    elif layer == 'exact':
        legend = exact_legend(grid, pair, rect)
        def cell(x, y):
            return legend[grid.at(x, y)[0]]
    elif layer == 'coll':
        def cell(x, y):
            return str(grid.at(x, y)[1])
    elif layer == 'elev':
        def cell(x, y):
            return f'{grid.at(x, y)[2]:X}'
    else:
        raise PkError('PARSE', 'layer is class|exact|coll|elev')
    body = []
    for y in range(y0, y0 + rh):
        line = ''.join(marks.get((x, y)) or cell(x, y) for x in range(x0, x0 + rw))
        body.append(f'{y:02d}|{line}')
    if layer == 'class':
        legend_rows.append('legend ' + ' '.join(f'{s}={seen[s] or "?"}' for s in sorted(seen)))
    elif layer == 'exact':
        items = [f'{s}=0x{mt:03X}' + (f'({pair.info(mt)["name"]})' if pair.info(mt).get('name') else '')
                 for mt, s in legend.items()]
        legend_rows += ['legend ' + ' '.join(items[i:i + 10]) for i in range(0, len(items), 10)]
    if marks:
        present = {marks[k] for k in marks if x0 <= k[0] < x0 + rw and y0 <= k[1] < y0 + rh}
        if present:
            legend_rows.append('events ' + ' '.join(f'{s}={n}' for s, n in EVENT_MARKS if s in present))
    return legend_rows + rulers(x0, rw) + body
