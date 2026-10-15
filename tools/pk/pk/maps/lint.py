"""pk map lint: visual ugliness so cheap models can fix maps without judging images.

Reuses the terrain model (DECISIONS.md 4b): the material of a cell is the class-view label
(pk map show) or the first tag of a named metatile. SEAM and RARE compare metatile pairs against a
FROZEN snapshot of the vanilla layouts (tools/pk/data/vanilla_adjacency.json, written only by
`pk map lint --snapshot`), never against live files, so an edited or generated map cannot grade
itself. Every other threshold (stairs/rects/empty) is measured per stratum (town/route/ocean) over
all vanilla FRLG outdoor maps and lives in tools/pk/data/lint_map.json. A check fires only when its
count exceeds the p95 (or the max when the stratum has fewer than 20 maps) of that stratum; the
score is the number of checks that fire.
"""
import json
import math
import re
from collections import Counter

from ..core.errors import PkError
from ..core.out import Result
from .edit import load, usage
from .grid import decode
from .layers import class_of, events
from .terrain import Rules
from .tiles import Pair

LINT_JSON = 'tools/pk/data/lint_map.json'
SNAPSHOT_JSON = 'tools/pk/data/vanilla_adjacency.json'
SNAPSHOT_VERSION = 4      # v4 adds physical classes, 3x3 class-pattern vocabulary and odd-tile p95
PK_LAYOUT = 'LAYOUT_PK_'
LEDGE_DIR = {'JUMP_SOUTH': 's', 'JUMP_NORTH': 'n', 'JUMP_EAST': 'e', 'JUMP_WEST': 'w'}


def physical_class(behavior, collision):
    """Physical class of a cell or metatile from game data only (never names or tags):
    walk | surf | ledge_<e|w|n|s> | wall | grass_enc | door."""
    for key, direction in LEDGE_DIR.items():
        if key in behavior:
            return f'ledge_{direction}'
    if re.search(r'DOOR|WARP|LADDER|STAIRS', behavior):
        return 'door'
    if collision:
        return 'wall'
    if re.search(r'WATER|OCEAN|POND|CURRENT|SEAWEED|PUDDLE', behavior) and 'SHALLOW' not in behavior:
        return 'surf'
    if 'GRASS' in behavior:
        return 'grass_enc'
    return 'walk'


def metatile_physical(repo, pair, metatile):
    """(physical_class, dominant_collision, dominant_elevation) of `metatile` from vanilla usage."""
    stats = usage(repo, pair).get(metatile, {})
    if stats:
        by_coll, by_elev = {}, {}
        for (c, e), n in stats.items():
            by_coll[c] = by_coll.get(c, 0) + n
            by_elev[e] = by_elev.get(e, 0) + n
        coll = max(by_coll, key=lambda c: (by_coll[c], -c))
        elev = max(by_elev, key=lambda e: (by_elev[e], -e))
    else:
        coll, elev = 0, 0
    return physical_class(pair.behavior_name(metatile), coll), coll, elev

CHECKS = [('natural', list), ('decor_tags', list), ('stair_min_steps', int), ('stair_min_run', int),
          ('straight_max', int), ('straight_types', list), ('dash_min_run', int), ('dash_min_gap', int),
          ('dash_types', list),
          ('rare_mats', list), ('rare_rate', (int, float)),
          ('rect_min', int), ('rect_fill', (int, float)), ('rect_border', bool),
          ('empty_w', int), ('empty_h', int), ('empty_margin', int), ('empty_types', list),
          ('empty_solid', bool), ('empty_water_frac', (int, float)), ('empty_entropy', (int, float)),
          ('max_lines', int)]

TOWN_TYPES = {'MAP_TYPE_TOWN', 'MAP_TYPE_CITY'}
STRATUM_TYPES = {'town': TOWN_TYPES, 'route': {'MAP_TYPE_ROUTE'}, 'ocean': {'MAP_TYPE_OCEAN_ROUTE'}}
OUTDOOR_TYPES = TOWN_TYPES | {'MAP_TYPE_ROUTE', 'MAP_TYPE_OCEAN_ROUTE'}


def stratum_of(map_type):
    if map_type in TOWN_TYPES:
        return 'town'
    if map_type == 'MAP_TYPE_OCEAN_ROUTE':
        return 'ocean'
    return 'route'


def pair_rules(repo, pair):
    cache = getattr(repo, '_pk_lint_rules', {})
    key = (pair.primary['symbol'], pair.secondary['symbol'])
    if key not in cache:
        cache[key] = Rules(repo, pair)
        repo._pk_lint_rules = cache
    return cache[key]


def thresholds(repo):
    cfg = json.loads(repo.read(LINT_JSON))
    for key, kind in CHECKS:
        try:
            value = cfg[key]
        except KeyError:
            raise PkError('PARSE', f'{LINT_JSON}: missing {key}') from None
        if not isinstance(value, kind):
            raise PkError('PARSE', f'{LINT_JSON}: {key} has the wrong type')
    if not isinstance(cfg.get('strata'), dict):
        raise PkError('PARSE', f'{LINT_JSON}: missing strata (run pk map lint --snapshot)')
    return cfg


def metric_threshold(strata, stratum, metric):
    """A check fires above the stratum p95, or its max when the stratum has <20 maps."""
    stats = (strata.get(stratum) or {}).get(metric)
    if not stats:
        return None
    return stats['p95'] if stats.get('n', 0) >= 20 else stats['max']


def severity_scores(counts, strata, stratum, area):
    """(score, severity). score = number of checks whose value exceeds its stratum threshold.
    severity = 100 / area * sum over the fired checks of (value - threshold), each check weighted 1;
    a threshold of 0 contributes the value itself. Unfired checks add 0, so score-0 maps are 0.0."""
    score, excess = 0, 0
    for metric, value in counts.items():
        if value is None:
            continue
        threshold = metric_threshold(strata, stratum, metric)
        if threshold is not None and value > threshold:
            score += 1
            excess += value - threshold
    return score, round(excess * 100 / max(1, area), 1)


def socket_of(pair, mt):
    """A custom metatile declares a vanilla `socket` metatile: SEAM/RARE test the socket."""
    info = pair.info(mt) or {}
    sock = info.get('socket')
    return int(sock) if sock is not None else mt


def material_of(pair, metatile, collision):
    """The material a metatile belongs to: its first tag, else its behaviour class (Rules.material)."""
    tags = (pair.info(metatile) or {}).get('tags')
    if tags:
        return tags[0]
    return class_of(pair, metatile, collision)[1]


def window_key(ids):
    """Pack one 3x3 window (9 metatile ids, each < 4096) into a hex string for the snapshot."""
    value = 0
    for metatile in ids:
        value = (value << 12) | metatile
    return format(value, 'x')


def window_share(grid, pair, snap, name):
    """Share of the map's 3x3 metatile windows that occur in the vanilla snapshot (sockets applied).

    The snapshot stores, per window, how many *distinct* vanilla layouts contain it. For a vanilla
    map its own layout is one of them, so we subtract 1 before deciding "seen" (leave-one-out);
    otherwise a window only this map has would count as seen and every vanilla map would score 1.0."""
    if snap is None:
        return None
    stats = snap.pair(pair)
    if stats is None or not stats.windows:
        return None
    if grid.w < 3 or grid.h < 3:
        return 1.0
    table = stats.windows
    loo = name in snap.maps
    sockets = {}
    own, keys = set(), []
    for y in range(grid.h - 2):
        for x in range(grid.w - 2):
            ids = []
            for dy in range(3):
                for dx in range(3):
                    mt = grid.at(x + dx, y + dy)[0]
                    if mt not in sockets:
                        sockets[mt] = socket_of(pair, mt)
                    ids.append(sockets[mt])
            key = window_key(ids)
            keys.append(key)
            own.add(key)
    seen = 0
    for key in keys:
        count = table.get(key, 0)
        if loo and key in own:
            count -= 1
        if count > 0:
            seen += 1
    return seen / len(keys)


class PairStats:
    """Snapshot statistics of one tileset pair; h/v are tuple-keyed for membership tests."""

    def __init__(self, data):
        self.layouts = data['layouts']
        self.metatiles = set(data['metatiles'])
        self.h = {tuple(int(v) for v in k.split(',')): c for k, c in data['h'].items()}
        self.v = {tuple(int(v) for v in k.split(',')): c for k, c in data['v'].items()}
        self.windows = data.get('w') or {}          # 3x3 window key -> number of distinct vanilla layouts
        self.vocab = {pat: {int(m): c for m, c in d.items()}
                      for pat, d in (data.get('vocab') or {}).items() if isinstance(d, dict)}
        self.physical = {int(m): tuple(v) for m, v in (data.get('physical') or {}).items()}
        self.odd_p95 = float(data.get('odd_p95', 61.2))


class Snap:
    """The frozen vanilla snapshot; `.pair()` is None for pairs absent from it."""

    def __init__(self, doc):
        self.doc = doc
        self.maps = doc.get('maps', {})
        self._pairs = doc.get('pairs', {})
        self._cache = {}

    def pair(self, pair):
        key = pair.primary['symbol'] + '+' + pair.secondary['symbol']
        if key not in self._cache:
            data = self._pairs.get(key)
            self._cache[key] = PairStats(data) if data is not None else None
        return self._cache[key]


def load_snapshot(repo):
    cached = getattr(repo, '_pk_lint_snapshot', None)
    if cached is not None:
        return cached
    try:
        raw = repo.read(SNAPSHOT_JSON)
    except PkError:
        return None
    try:
        snap = Snap(json.loads(raw))
    except (ValueError, KeyError, TypeError) as exc:
        raise PkError('PARSE', f'{SNAPSHOT_JSON}: {exc}; rerun pk map lint --snapshot') from None
    repo._pk_lint_snapshot = snap
    return snap


def seams(grid, pair, snap):
    """(WARN rows, {unchecked id: count}) of metatile pairs never seen in the vanilla snapshot.
    Custom metatiles are tested through their socket; one without a socket is reported, not scored."""
    if snap is None:
        return None
    stats = snap.pair(pair)
    if stats is None:
        return None
    known = stats.metatiles

    def resolve(mt):
        sock = (pair.info(mt) or {}).get('socket')
        if sock is not None:
            return int(sock), True
        return (mt, True) if mt in known else (mt, False)

    rows, unchecked = [], {}

    def check(a0, b0, table, tag, x, y):
        a, oka = resolve(a0)
        b, okb = resolve(b0)
        if not (oka and okb):
            for rid, ok in ((a0, oka), (b0, okb)):
                if not ok:
                    unchecked[rid] = unchecked.get(rid, 0) + 1
            return
        if (a, b) not in table:
            rows.append(f'WARN SEAM {tag} 0x{a:03X}-0x{b:03X} @{x},{y}')

    for y in range(grid.h):
        for x in range(grid.w - 1):
            check(grid.at(x, y)[0], grid.at(x + 1, y)[0], stats.h, 'h', x, y)
    for y in range(grid.h - 1):
        for x in range(grid.w):
            check(grid.at(x, y)[0], grid.at(x, y + 1)[0], stats.v, 'v', x, y)
    return rows, unchecked


def boundary_runs(mat, w, h, horizontal):
    """Maximal boundary segments of one material pair: along a line, the near side keeps one
    material and the far side another. Returns {(near, far): [(line, start, end)]}, the line
    is the near row, end inclusive."""
    along = w if horizontal else h
    across = h if horizontal else w
    runs = {}

    def near_far(there, line):
        if horizontal:
            return mat[(there, line)], mat[(there, line + 1)]
        return mat[(line, there)], mat[(line + 1, there)]

    for line in range(across - 1):
        here = 0
        while here < along:
            start = here
            key = near_far(here, line)
            while here + 1 < along and near_far(here + 1, line) == key:
                here += 1
            if key[0] != key[1]:
                runs.setdefault((key[0], key[1]), []).append((line, start, here))
            here += 1
    return runs


def stairs(meta, grid, cfg):
    """Stair-looking boundaries: chains of stair_min_steps runs of equal length whose joins
    shift by a constant one-tile step, plus any straight natural edge longer than straight_max.
    Paths, buildings and fences may be straight, so a run must border a natural material."""
    natural = set(meta['natural'])
    rows = []
    counted = 0
    straight_on = meta.get('map_type') in set(meta['straight_types'])
    dash_on = meta.get('map_type') in set(meta['dash_types'])
    for horizontal in (True, False):
        for (near, far), runs in boundary_runs(meta['mat'], grid.w, grid.h, horizontal).items():
            if not ({near, far} & natural):
                continue
            label = '|'.join(sorted((near, far)))
            by_line = {}
            for line, x1, x2 in runs:
                if x2 - x1 + 1 >= meta['stair_min_run']:
                    by_line.setdefault(line, []).append((x1, x2))
            for line, x1, x2 in runs:
                if straight_on and x2 - x1 + 1 > meta['straight_max']:
                    span = f'{x1},{line}-{x2},{line}' if horizontal else f'{line},{x1}-{line},{x2}'
                    rows.append(f'WARN STAIR {label} {span} run:{x2 - x1 + 1}')
                    counted += 1
            taken = set()
            for start_line in sorted(by_line):
                for first in sorted(by_line[start_line]):
                    if (start_line, first) in taken:
                        continue
                    length = first[1] - first[0] + 1
                    for shift in dict.fromkeys((1, -1, length, -length)):
                        chain = [(start_line, first)]
                        line, run = start_line, first
                        while line + 1 in by_line:
                            hit = None
                            for cand in by_line[line + 1]:
                                if cand[0] - run[0] == shift and cand[1] - cand[0] + 1 == length:
                                    hit = cand
                                    break
                            if hit is None:
                                break
                            chain.append((line + 1, hit))
                            line, run = line + 1, hit
                        if len(chain) < meta['stair_min_steps']:
                            continue
                        taken.update(chain)
                        xs = [x for _, (a, b) in chain for x in (a, b)]
                        span = (f'{min(xs)},{chain[0][0]}-{max(xs)},{chain[-1][0]}' if horizontal
                                else f'{chain[0][0]},{min(xs)}-{chain[-1][0]},{max(xs)}')
                        rows.append(f'WARN STAIR {label} {span} steps:{len(chain)}')
                        counted += 1

            def dash_emit(spans):
                if dash_on and len(spans) >= 2:
                    span = (f'{spans[0][0]},{line}-{spans[-1][1]},{line}' if horizontal
                            else f'{line},{spans[0][0]}-{line},{spans[-1][1]}')
                    rows.append(f'WARN STAIR {"|".join(sorted((near, far)))} {span} dash:{len(spans)}')
                    return 1
                return 0
            for line in sorted(set(by_line)):
                rr = sorted(set(by_line[line]))
                dash, counted_emit = [rr[0]], counted
                for nxt in rr[1:]:
                    if nxt[1] - nxt[0] + 1 == dash[-1][1] - dash[-1][0] + 1 \
                            and nxt[0] - dash[-1][1] >= meta['dash_min_gap']:
                        dash.append(nxt)
                        continue
                    counted += dash_emit(dash)
                    dash = [nxt]
                counted += dash_emit(dash)
                del counted_emit
    return rows, counted


def rare(meta, grid, cfg, snap):
    """Rare shore adjacencies: a water/sand boundary whose metatile pair is at most
    rare_rate * layouts occurrences in the vanilla snapshot of the pair. The rate normalisation
    keeps unique-tileset maps (Vermilion, Viridian Forest) from lighting up on every tile."""
    if snap is None:
        return None, None, None
    stats = snap.pair(meta['pair'])
    if stats is None:
        return None, None, None
    pair = meta['pair']
    known = stats.metatiles
    mats = set(cfg['rare_mats'])
    mat = meta['mat']
    limit = cfg['rare_rate'] * max(1, stats.layouts)
    rows, counted, unchecked = [], 0, {}

    def resolve(mt):
        sock = (pair.info(mt) or {}).get('socket')
        if sock is not None:
            return int(sock), True
        return (mt, True) if mt in known else (mt, False)

    def check(a0, b0, table, tag, x, y):
        nonlocal counted
        a, oka = resolve(a0)
        b, okb = resolve(b0)
        if not (oka and okb):
            for rid, ok in ((a0, oka), (b0, okb)):
                if not ok:
                    unchecked[rid] = unchecked.get(rid, 0) + 1
            return
        if table.get((a, b), 0) <= limit:
            rows.append(f'WARN RARE {tag} 0x{a:03X}-0x{b:03X} @{x},{y}')
            counted += 1

    for y in range(grid.h):
        for x in range(grid.w - 1):
            if {mat[(x, y)], mat[(x + 1, y)]} & mats:
                check(grid.at(x, y)[0], grid.at(x + 1, y)[0], stats.h, 'h', x, y)
    for y in range(grid.h - 1):
        for x in range(grid.w):
            if {mat[(x, y)], mat[(x, y + 1)]} & mats:
                check(grid.at(x, y)[0], grid.at(x, y + 1)[0], stats.v, 'v', x, y)
    return rows, counted, unchecked


def prune_tendrils(comp, mat, material):
    """Drop cells with fewer than 2 same-material 4-neighbours, repeatedly, so a 1-tile stream
    hanging off a square pond no longer drags the pond's bounding box to the map border."""
    comp = set(comp)
    while comp:
        remove = {(x, y) for (x, y) in comp
                  if sum(1 for n in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)) if n in comp) < 2}
        if not remove:
            return comp
        comp -= remove
    return comp


def rects(meta, grid, cfg):
    natural = set(meta['natural'])
    mat = meta['mat']
    rows, counted, seen = [], 0, set()
    for y0 in range(grid.h):
        for x0 in range(grid.w):
            if mat[(x0, y0)] not in natural or (x0, y0) in seen:
                continue
            frontier, comp, material = [(x0, y0)], {(x0, y0)}, mat[(x0, y0)]
            seen.add((x0, y0))
            while frontier:
                x, y = frontier.pop()
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if grid.inside(nx, ny) and (nx, ny) not in seen and mat[(nx, ny)] == material:
                        seen.add((nx, ny))
                        comp.add((nx, ny))
                        frontier.append((nx, ny))
            core = prune_tendrils(comp, mat, material)
            if not core:
                continue
            x1, y1 = max(x for x, _ in core), max(y for _, y in core)
            xa, ya = min(x for x, _ in core), min(y for _, y in core)
            w, h = x1 - xa + 1, y1 - ya + 1
            if w < cfg['rect_min'] or h < cfg['rect_min'] or len(core) < cfg['rect_fill'] * w * h:
                continue
            border = sum(1 for (x, y) in core
                         if x == 0 or y == 0 or x == grid.w - 1 or y == grid.h - 1)
            if cfg['rect_border'] and border >= cfg['rect_min']:
                continue    # a component pinned to the border can be a vanilla shoreline/forest edge
            rows.append(f'WARN RECT {material} {xa},{ya}-{x1},{y1} {round(len(core) / (w * h) * 100)}%')
            counted += 1
    return rows, counted


def material_entropy(mat, window):
    """Shannon entropy (bits) of the material distribution of one window."""
    counts = {}
    for cell in window:
        counts[mat[cell]] = counts.get(mat[cell], 0) + 1
    n = len(window)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def empty(meta, grid, pair, cfg):
    margin = cfg['empty_margin']
    margin = min(margin, (cfg['empty_w'] - 1) // 2, (cfg['empty_h'] - 1) // 2)
    if meta['map_type'] not in set(cfg['empty_types']):
        return [], 0
    marks = meta['marks']
    decorated = {(x, y) for y in range(grid.h) for x in range(grid.w)
                 if pair.info(grid.at(x, y)[0]).get('tags') and
                 set(pair.info(grid.at(x, y)[0])['tags']) & set(meta['decor_tags'])}
    decorated |= set(marks)
    w, h = cfg['empty_w'], cfg['empty_h']
    rows, counted, taken = [], 0, set()
    for y0 in range(margin, grid.h - h - margin + 1):
        for x0 in range(margin, grid.w - w - margin + 1):
            window = {(x, y) for y in range(y0, y0 + h) for x in range(x0, x0 + w)}
            if len(window & decorated) >= 2 or window & taken:
                continue    # two decorations (or an event pair) are enough to rescue a screen
            if meta['empty_solid'] and any(meta['mat'][(x, y)] == 'solid' for (x, y) in window):
                continue
            water = len([1 for (x, y) in window if meta['mat'][(x, y)] == 'water'])
            if water > meta['empty_water_frac'] * w * h:
                continue
            if material_entropy(meta['mat'], window) >= meta['empty_entropy']:
                continue    # enough material variety: not a blank screen
            rows.append(f'WARN EMPTY {x0},{y0}-{x0 + w - 1},{y0 + h - 1}')
            taken |= window
            counted += 1
    return rows, counted


def measure(grid, pair, cfg, snap, rules):
    """Raw counts + WARN rows of every check for an in-memory grid. seams/rare are None when
    snap can't answer. Shared by the CLI (which loads first) and the polish loop (which works
    on a staged copy without re-reading the file)."""
    mat = {(x, y): rules.material(grid.at(x, y)) for y in range(grid.h) for x in range(grid.w)}
    meta = dict(cfg, mat=mat, marks=events(grid.data), rules=rules, pair=pair,
                map_type=grid.data.get('map_type'))
    counts, warns, unchecked = {}, {}, {}
    seam = seams(grid, pair, snap)
    counts['seams'] = None if seam is None else len(seam[0])
    warns['seams'] = seam[0] if seam else []
    if seam:
        unchecked.update(seam[1])
    stair_rows, counts['stairs'] = stairs(meta, grid, cfg)
    warns['stairs'] = stair_rows
    rare_rows, rare_n, rare_unchecked = rare(meta, grid, cfg, snap)
    counts['rare'], warns['rare'] = rare_n, rare_rows or []
    if rare_unchecked:
        for rid, n in rare_unchecked.items():
            unchecked[rid] = unchecked.get(rid, 0) + n
    rect_rows, counts['rects'] = rects(meta, grid, cfg)
    warns['rects'] = rect_rows
    empty_rows, counts['empty'] = empty(meta, grid, pair, cfg)
    warns['empty'] = empty_rows
    return counts, warns, unchecked


def analyze(repo, query, cfg, snap, pair_rules_fn=pair_rules):
    """Raw counts + WARN rows of every check. seams/rare are None when snap can't answer."""
    grid, pair = load(repo, query)
    counts, warns, unchecked = measure(grid, pair, cfg, snap, pair_rules_fn(repo, pair))
    return grid, pair, counts, warns, unchecked


def run(repo, query, show_all=False):
    cfg = thresholds(repo)
    snap = load_snapshot(repo)
    grid, pair, counts, warns, unchecked = analyze(repo, query, cfg, snap)
    stratum = stratum_of(grid.data.get('map_type'))
    score, severity = severity_scores(counts, cfg['strata'], stratum, grid.w * grid.h)
    patch = window_share(grid, pair, snap, grid.name)
    cap = cfg['max_lines']
    rows, cut = [], []
    for metric, check in (('seams', 'SEAM'), ('stairs', 'STAIR'), ('rare', 'RARE'),
                          ('rects', 'RECT'), ('empty', 'EMPTY')):
        value = counts.get(metric)
        threshold = metric_threshold(cfg['strata'], stratum, metric)
        if value is not None and threshold is not None and value > threshold:
            lines = sorted(warns[metric])
            if show_all:
                rows += lines
            else:
                rows += lines[:cap]
                if len(lines) > cap:
                    cut.append(f'INFO {check} +{len(lines) - cap} more (use --all)')
    infos = []
    if counts['seams'] is None or counts['rare'] is None:
        infos.append('INFO no snapshot; run pk map lint --snapshot')
    elif patch is None:
        infos.append('INFO patch unavailable; rerun pk map lint --snapshot for the 3x3 windows')
    infos += [f'INFO UNCHECKED 0x{rid:03X} x{n} (no socket)' for rid, n in sorted(unchecked.items())]
    infos += cut
    infos += shapes(grid, pair, snap)
    infos += repeats(grid, pair)
    header = (f'lint {grid.name} stratum:{stratum} '
              + ' '.join(f'{m}:{"-" if counts[m] is None else counts[m]}' for m in counts))
    delta = ''
    base = snap.maps.get(grid.name) if snap else None
    if base is not None:
        delta = f' (vanilla:{base.get("severity", 0.0):.1f}, {severity - base.get("severity", 0.0):+.1f})'
    patch_text = '' if patch is None else f' patch:{patch:.2f}'
    summary = f'{header} score:{score} severity:{severity:.1f}{delta}{patch_text}'
    return Result(summary, infos + rows)


# --- snapshot: the frozen vanilla adjacency statistics --------------------------------

def stats(values):
    values = sorted(values)
    n = len(values)
    if not n:
        return dict(p50=0, p95=0, max=0, n=0)
    pct = lambda p: values[min(n - 1, int(round(p * (n - 1))))]
    return dict(p50=pct(0.5), p95=pct(0.95), max=values[-1], n=n)


def pattern_at(cls, w, h, x, y):
    """3x3 physical-class pattern around (x,y), clamping out-of-bounds to the nearest edge cell."""
    return '|'.join(cls[(min(max(x + dx, 0), w - 1), min(max(y + dy, 0), h - 1))]
                    for dy in (-1, 0, 1) for dx in (-1, 0, 1))


def shapes(grid, pair, snap, leave_one_out=False):
    """Unseen physical 3x3 shapes, INFO only. Keep vanilla vocabulary for map reworking.

    leave_one_out is retained only for reproducing the original, rejected target.
    """
    stats = snap.pair(pair) if snap is not None else None
    if stats is None or not stats.vocab:
        return ['INFO SHAPE unavailable: snapshot has no physical vocabulary']
    classes = {(x, y): physical_class(pair.behavior_name(grid.at(x, y)[0]), grid.at(x, y)[1])
               for y in range(grid.h) for x in range(grid.w)}
    patterns = {(x, y): pattern_at(classes, grid.w, grid.h, x, y)
                for y in range(grid.h) for x in range(grid.w)}
    own = Counter((pat, grid.at(x, y)[0]) for (x, y), pat in patterns.items()) if leave_one_out and grid.name in snap.maps else Counter()
    seen = {pat for pat, counts in stats.vocab.items()
            if any(n > own[(pat, mt)] for mt, n in counts.items())}
    return [f'INFO SHAPE {x},{y} {pat}' for (x, y), pat in patterns.items() if pat not in seen]


def repeats(grid, pair):
    """4x4 metatile blocks appearing >= 3 times without overlap and containing >= 2 physical classes."""
    classes = [physical_class(pair.behavior_name(c[0]), c[1]) for c in grid.cells]
    by_block = {}
    for y in range(grid.h - 3):
        for x in range(grid.w - 3):
            w_cls = {classes[(y + dy) * grid.w + x + dx] for dy in range(4) for dx in range(4)}
            if len(w_cls) < 2:
                continue
            key = tuple(grid.at(x + dx, y + dy)[0] for dy in range(4) for dx in range(4))
            by_block.setdefault(key, []).append((x, y))
    rows = []
    for pos in by_block.values():
        if len(pos) < 3:
            continue
        chosen = []
        for x, y in pos:
            if all(abs(x - cx) >= 4 or abs(y - cy) >= 4 for cx, cy in chosen):
                chosen.append((x, y))
        if len(chosen) >= 3:
            rows.append((chosen[0], 'INFO REPEAT ' + ' '.join(f'{x},{y}' for x, y in chosen) + ' 4x4'))
    rows.sort(key=lambda t: (t[0][1], t[0][0]))
    return [r for _, r in rows]


def build_pairs(repo):
    """h/v/diagonal pair counts + known metatile ids + physical classes + 3x3 class-pattern
    vocabulary + leave-one-out odd-tile p95 per tileset pair, from vanilla layouts only."""
    from .meta import all_ids, draw
    pairs = {}
    pair_cache = {}
    for lid, layout in repo.layouts().items():
        if lid.startswith(PK_LAYOUT):
            continue
        key = layout['primary_tileset'] + '+' + layout['secondary_tileset']
        w, h = layout['width'], layout['height']
        try:
            cells = decode(repo.read(layout['blockdata_filepath'])[:w * h * 2])
        except PkError:
            continue
        entry = pairs.setdefault(key, dict(layouts=0, metatiles=set(),
                                           h={}, v={}, ne={}, nw={}, se={}, sw={}, w={},
                                           vocab={}, physical={}, odd_p95=61.2))
        entry['layouts'] += 1
        pair = None
        if isinstance(layout.get('primary_tileset'), str) and isinstance(layout.get('secondary_tileset'), str):
            if key not in pair_cache:
                try:
                    pair_cache[key] = Pair(repo, layout)
                except PkError:
                    pair_cache[key] = None    # a layout whose secondary is "0" or unregistered
            pair = pair_cache[key]
        layout_windows = set()
        for y in range(h):
            for x in range(w):
                a = cells[y * w + x][0]
                entry['metatiles'].add(a)
                if x + 1 < w:
                    k = f'{a},{cells[y * w + x + 1][0]}'
                    entry['h'][k] = entry['h'].get(k, 0) + 1
                if y + 1 < h:
                    k = f'{a},{cells[(y + 1) * w + x][0]}'
                    entry['v'][k] = entry['v'].get(k, 0) + 1
                if x + 1 < w and y > 0:
                    k = f'{a},{cells[(y - 1) * w + x + 1][0]}'
                    entry['ne'][k] = entry['ne'].get(k, 0) + 1
                if x > 0 and y > 0:
                    k = f'{a},{cells[(y - 1) * w + x - 1][0]}'
                    entry['nw'][k] = entry['nw'].get(k, 0) + 1
                if x + 1 < w and y + 1 < h:
                    k = f'{a},{cells[(y + 1) * w + x + 1][0]}'
                    entry['se'][k] = entry['se'].get(k, 0) + 1
                if x > 0 and y + 1 < h:
                    k = f'{a},{cells[(y + 1) * w + x - 1][0]}'
                    entry['sw'][k] = entry['sw'].get(k, 0) + 1
        # 3x3 windows counted once per distinct layout, so leave-one-out can subtract one map.
        for y in range(h - 2):
            for x in range(w - 2):
                layout_windows.add(window_key([cells[(y + dy) * w + x + dx][0]
                                               for dy in range(3) for dx in range(3)]))
        for k in layout_windows:
            entry['w'][k] = entry['w'].get(k, 0) + 1

    # Physical classes for every valid metatile of each pair.
    for key, entry in pairs.items():
        pair = pair_cache.get(key)
        if pair is None:
            continue
        for mt in all_ids(pair):
            pcls, coll, elev = metatile_physical(repo, pair, mt)
            entry['physical'][str(mt)] = [pcls, coll, elev]

    # 3x3 physical-class pattern vocabulary over all vanilla outdoor layouts (never Pk*),
    # plus leave-one-out odd-tile p95 threshold over vanilla outdoor maps.
    outdoor_layouts = {}
    maps_data = repo.maps()
    layouts_data = repo.layouts()
    for name, _ in vanilla_outdoor(repo):
        lid = maps_data[name]['layout']
        if lid not in outdoor_layouts:
            layout = layouts_data[lid]
            key = layout['primary_tileset'] + '+' + layout['secondary_tileset']
            pair = pair_cache.get(key)
            if pair is None:
                continue
            w, h = layout['width'], layout['height']
            cells = decode(repo.read(layout['blockdata_filepath'])[:w * h * 2])
            cls = {(x, y): physical_class(pair.behavior_name(cells[y * w + x][0]), cells[y * w + x][1])
                   for y in range(h) for x in range(w)}
            pats = {(x, y): pattern_at(cls, w, h, x, y) for y in range(h) for x in range(w)}
            outdoor_layouts[lid] = (key, pair, w, h, cells, pats)

    primary_vocab = {}    # (primary_symbol, pattern) -> {mt (<640): count}
    pair_vocab = {}       # (pair_key, pattern) -> {mt: count}
    layout_vocab = {}     # (lid, pattern) -> {mt: count}
    for lid, (key, pair, w, h, cells, pats) in outdoor_layouts.items():
        prim = pair.primary['symbol']
        limit = pair.limits['metatiles']
        for y in range(h):
            for x in range(w):
                mt = cells[y * w + x][0]
                pat = pats[(x, y)]
                d_pair = pair_vocab.setdefault((key, pat), {})
                d_pair[mt] = d_pair.get(mt, 0) + 1
                d_lay = layout_vocab.setdefault((lid, pat), {})
                d_lay[mt] = d_lay.get(mt, 0) + 1
                if mt < limit:
                    d_prim = primary_vocab.setdefault((prim, pat), {})
                    d_prim[mt] = d_prim.get(mt, 0) + 1

    for key, entry in pairs.items():
        pair = pair_cache.get(key)
        if pair is None:
            continue
        prim = pair.primary['symbol']
        limit = pair.limits['metatiles']
        vocab = {}
        for (p_sym, pat), counts in primary_vocab.items():
            if p_sym == prim:
                vocab[pat] = {str(mt): c for mt, c in counts.items()}
        for (p_key, pat), counts in pair_vocab.items():
            if p_key == key:
                dest = vocab.setdefault(pat, {})
                for mt, c in counts.items():
                    if mt >= limit or str(mt) not in dest:
                        dest[str(mt)] = c
        entry['vocab'] = vocab

    # Leave-one-out odd-tile score over all vanilla outdoor maps (mean RGB distance to average
    # vanilla render for the same 3x3 class pattern with the layout's own counts subtracted).
    render_cache = {}
    def flat_rgb(key, pair, mt):
        ck = (pair.primary['symbol'], mt) if mt < pair.limits['metatiles'] else (key, mt)
        if ck not in render_cache:
            render_cache[ck] = tuple(v for row in draw(repo, pair, mt) for p in row for v in (p or (0, 0, 0)))
        return render_cache[ck]

    loo_dists = []
    for lid, (key, pair, w, h, cells, pats) in outdoor_layouts.items():
        for mt in {c[0] for c in cells}:
            flat_rgb(key, pair, mt)
    for lid, (key, pair, w, h, cells, pats) in outdoor_layouts.items():
        seen_pats = {pats[(x, y)] for y in range(h) for x in range(w)}
        for pat in seen_pats:
            own = layout_vocab.get((lid, pat), {})
            full = pair_vocab.get((key, pat), {})
            rem = {mt: c - own.get(mt, 0) for mt, c in full.items() if c - own.get(mt, 0) > 0}
            if not rem:
                continue
            total = sum(rem.values())
            avg = tuple(sum(flat_rgb(key, pair, mt)[i] * c for mt, c in rem.items()) / total for i in range(768))
            for mt, c in own.items():
                rgb = flat_rgb(key, pair, mt)
                dist = sum(abs(rgb[i] - avg[i]) for i in range(768)) / 768.0
                loo_dists.extend([dist] * c)
    loo_dists.sort()
    odd_p95 = round(loo_dists[min(len(loo_dists) - 1, int(round(0.95 * (len(loo_dists) - 1))))], 2) if loo_dists else 61.2

    for entry in pairs.values():
        entry['metatiles'] = sorted(entry['metatiles'])
        entry['odd_p95'] = odd_p95
    return pairs


def vanilla_outdoor(repo):
    maps = repo.maps()
    layouts = repo.layouts()
    rows = []
    for name, data in maps.items():
        if name.startswith('Pk'):
            continue
        if data.get('map_type') not in OUTDOOR_TYPES:
            continue
        layout = layouts.get(data['layout'])
        if layout is None or layout.get('layout_version') != 'frlg':
            continue
        rows.append((name, stratum_of(data.get('map_type'))))
    return sorted(rows)


def derive_strata(baselines):
    strata = {name: {} for name in STRATUM_TYPES}
    for metric in ('seams', 'stairs', 'rare', 'rects', 'empty'):
        for name in STRATUM_TYPES:
            values = [b['counts'][metric] for b in baselines
                      if b['stratum'] == name and b['counts'][metric] is not None]
            strata[name][metric] = stats(values)
    return strata


def build_snapshot(repo, tx):
    cfg = thresholds(repo)
    pairs = build_pairs(repo)
    snap = Snap(dict(pairs=pairs, maps={}))
    baselines = []
    for name, stratum in vanilla_outdoor(repo):
        grid, _, counts, _, _ = analyze(repo, name, cfg, snap)
        baselines.append(dict(name=name, stratum=stratum, counts=counts, area=grid.w * grid.h))
    strata = derive_strata(baselines)
    maps = {}
    for base in baselines:
        score, severity = severity_scores(base['counts'], strata, base['stratum'], base['area'])
        maps[base['name']] = dict(stratum=base['stratum'], area=base['area'],
                                  **{k: v for k, v in base['counts'].items()},
                                  score=score, severity=severity)
    doc = dict(version=SNAPSHOT_VERSION, pairs=pairs, maps=maps)
    tx.stage(SNAPSHOT_JSON, (json.dumps(doc, sort_keys=True) + '\n').encode('utf-8'))
    cfg['strata'] = strata
    tx.stage(LINT_JSON, (json.dumps(cfg, indent=2, sort_keys=True) + '\n').encode('utf-8'))
    nonzero = sum(1 for m in maps.values() if m['score'])
    worst = max((m['severity'] for m in maps.values()), default=0.0)
    return Result(f'snapshot {SNAPSHOT_JSON} pairs:{len(pairs)} maps:{len(maps)} '
                  f'nonzero:{nonzero} max_severity:{worst:.1f}',
                  [f'thresholds written to {LINT_JSON}'])
