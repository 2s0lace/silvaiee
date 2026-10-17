"""Composition stats: is a map laid out like vanilla, or empty / schematic? INFO only, never scored.

Two measures, both calibrated on every vanilla outdoor map (never Pk*), per stratum (town / route / ocean):
* open   - per 15x10 screen (sliding, stride 5): the share of *plain* cells, i.e. walkable ground/grass/sand
           whose whole 3x3 neighbourhood is the same metatile. A map's value is its emptiest screen; the
           threshold is the p95 of vanilla maps (per map, like every other lint check, so ~5% of vanilla
           maps fire). Benchmark maps with big empty lawns score high.
* mirror - the share of non-plain cells whose class equals the class of the mirrored cell (left-right and
           top-bottom, the higher one). Schematic layouts (a cross of paths, four equal quarters) score high.
A row fires when a map's value is above the vanilla per-map p95 of its stratum. Classes are the `pk map show` classes
(layers.class_of), so names never decide anything here.
"""
import json

from ..core.errors import PkError
from .layers import Grid, class_of

SCREEN_W, SCREEN_H, STRIDE = 15, 10, 5
CACHE = '.pk/layout/vanilla.json'
VERSION = 3
PLAIN_CLASSES = {'ground', 'grass', 'sand'}
FRAME_CLASSES = {'solid', 'water'}


def plain_mask(grid, pair):
    cls = {}
    for y in range(grid.h):
        for x in range(grid.w):
            cell = grid.at(x, y)
            cls[(x, y)] = class_of(pair, cell[0], cell[1])[1]
    plain = set()
    for y in range(grid.h):
        for x in range(grid.w):
            if cls[(x, y)] not in PLAIN_CLASSES:
                continue
            mt = grid.at(x, y)[0]
            if all(grid.at(nx, ny)[0] == mt for nx in range(max(0, x - 1), min(grid.w, x + 2))
                   for ny in range(max(0, y - 1), min(grid.h, y + 2))):
                plain.add((x, y))
    return cls, plain


def screens(grid):
    """15x10 windows, stride 5; a map smaller than a screen is one window."""
    w, h = min(SCREEN_W, grid.w), min(SCREEN_H, grid.h)
    xs = sorted(set(list(range(0, grid.w - w + 1, STRIDE)) + [grid.w - w]))
    ys = sorted(set(list(range(0, grid.h - h + 1, STRIDE)) + [grid.h - h]))
    return [(x, y, w, h) for y in ys for x in xs]


def measure(grid, pair):
    cls, plain = plain_mask(grid, pair)
    open_shares = []
    for (x0, y0, w, h) in screens(grid):
        n = sum(1 for y in range(y0, y0 + h) for x in range(x0, x0 + w) if (x, y) in plain)
        open_shares.append(((x0, y0, w, h), n / (w * h)))
    best = 0.0
    for mirror in ('lr', 'tb'):
        same = total = 0
        for y in range(grid.h):
            for x in range(grid.w):
                mx, my = (grid.w - 1 - x, y) if mirror == 'lr' else (x, grid.h - 1 - y)
                if (mx, my) <= (x, y):
                    continue                    # each pair once; the centre line never counts
                if (x, y) in plain and (mx, my) in plain:
                    continue                    # two empty lawns match trivially
                if cls[(x, y)] in FRAME_CLASSES and cls[(mx, my)] in FRAME_CLASSES:
                    continue                    # tree walls framing both sides match in every vanilla map
                total += 1
                same += cls[(x, y)] == cls[(mx, my)]
        if total:
            best = max(best, same / total)
    return open_shares, best


def _p95(values):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(0.95 * (len(values) - 1))))] if values else None


def vanilla_thresholds(repo):
    """{stratum: {'open': p95, 'mirror': p95, 'n_screens', 'n_maps'}}, cached in .pk/layout/."""
    path = repo.path(CACHE)
    try:
        if path.is_file():
            doc = json.loads(path.read_text('utf-8'))
            if doc.get('version') == VERSION:
                return doc['strata']
    except (OSError, ValueError, KeyError):
        pass
    from .lint import vanilla_outdoor, stratum_of
    from .tiles import Pair
    maps, layouts = repo.maps(), repo.layouts()
    opens, mirrors = {}, {}
    for name, _ in vanilla_outdoor(repo):
        try:
            data = maps[name]
            grid = Grid(repo, name, data, layouts[data['layout']])
            pair = Pair(repo, grid.layout)
        except (PkError, KeyError):
            continue
        stratum = stratum_of(data.get('map_type'))
        shares, mirror = measure(grid, pair)
        opens.setdefault(stratum, []).append(max(v for _, v in shares))   # per map: its emptiest screen
        mirrors.setdefault(stratum, []).append(mirror)
    strata = {s: dict(open=_p95(opens[s]), mirror=_p95(mirrors.get(s, [])), n_maps=len(opens[s])) for s in opens}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(version=VERSION, strata=strata), indent=1) + '\n', encoding='utf-8')
    except OSError:
        pass
    return strata


def rows(repo, grid, pair, stratum):
    """INFO LAYOUT rows for lint. Screens are written x,y-x2,y2 so aiceus draws them."""
    strata = vanilla_thresholds(repo)
    limits = strata.get(stratum) or strata.get('route')
    if not limits:
        return ['INFO LAYOUT not checked (no vanilla maps measured)']
    shares, mirror = measure(grid, pair)
    out = []
    flagged = [(box, v) for box, v in shares if limits['open'] is not None and v > limits['open']]
    for (x0, y0, w, h), v in sorted(flagged, key=lambda t: -t[1])[:3]:
        out.append(f'INFO LAYOUT open {x0},{y0}-{x0 + w - 1},{y0 + h - 1} {round(100 * v)}% plain lawn '
                   f'(vanilla {stratum} p95 {round(100 * limits["open"])}%)')
    if len(flagged) > 3:
        out.append(f'INFO LAYOUT open +{len(flagged) - 3} more screens')
    if limits['mirror'] is not None and mirror > limits['mirror']:
        out.append(f'INFO LAYOUT mirror {round(100 * mirror)}% of the layout mirrors itself '
                   f'(vanilla {stratum} p95 {round(100 * limits["mirror"])}%): break the symmetry')
    return out
