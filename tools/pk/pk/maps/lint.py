"""pk map lint: visual ugliness so cheap models can fix maps without judging images.

Reuses the terrain model (DECISIONS.md 4b): the material of a cell is the class-view label
(pk map show) or the first tag of a named metatile, and SEAM compares metatile pairs against
the same vanilla adjacency statistics the terrain solver learned (terrain.Rules). Thresholds
live in tools/pk/data/lint_map.json; calibrated so every deliberately ugly map (rectangular
ponds, staircase shores, unseen seams, empty screens, and the weaker PkPallet benchmark drafts)
scores above the clean vanilla maps (test_maps.Lint.test_ugly_scores_above_vanilla).
"""
import json
import math

from ..core.errors import PkError
from ..core.out import Result
from .edit import load
from .layers import events
from .terrain import Rules

LINT_JSON = 'tools/pk/data/lint_map.json'

CHECKS = [('natural', list), ('decor_tags', list), ('stair_min_steps', int), ('stair_min_run', int),
          ('straight_max', int), ('straight_types', list), ('dash_min_run', int), ('dash_min_gap', int),
          ('dash_types', list),
          ('rare_mats', list), ('rare_max', int),
          ('rect_min', int), ('rect_fill', (int, float)), ('rect_border', bool),
          ('empty_w', int), ('empty_h', int), ('empty_margin', int), ('empty_types', list),
          ('empty_solid', bool), ('empty_water_frac', (int, float)), ('empty_entropy', (int, float)),
          ('max_lines', int)]


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
    return cfg


def seams(grid, rules):
    rows = []
    for y in range(grid.h):
        for x in range(grid.w - 1):
            if (grid.at(x, y)[0], grid.at(x + 1, y)[0]) not in rules.h:
                rows.append(f'WARN SEAM h 0x{grid.at(x, y)[0]:03X}-0x{grid.at(x + 1, y)[0]:03X} @{x},{y}')
    for y in range(grid.h - 1):
        for x in range(grid.w):
            if (grid.at(x, y)[0], grid.at(x, y + 1)[0]) not in rules.v:
                rows.append(f'WARN SEAM v 0x{grid.at(x, y)[0]:03X}-0x{grid.at(x, y + 1)[0]:03X} @{x},{y}')
    return rows


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


def rare(meta, grid, cfg):
    """Rare shore adjacencies: a boundary between two natural cells whose metatile pair occurs
    at most rare_max times in the vanilla layouts of the pair. Hand-made shores pair their tiles
    with whatever vanilla put there; a generated shore often pairs tiles that never meet."""
    mats = set(cfg['rare_mats'])
    rules = meta['rules']
    mat = meta['mat']
    rows, counted = [], 0
    for y in range(grid.h):
        for x in range(grid.w - 1):
            if not ({mat[(x, y)], mat[(x + 1, y)]} & mats):
                continue
            a, b = grid.at(x, y)[0], grid.at(x + 1, y)[0]
            if rules.h.get((a, b), 0) <= cfg['rare_max']:
                rows.append(f'WARN RARE h 0x{a:03X}-0x{b:03X} @{x},{y}')
                counted += 1
    for y in range(grid.h - 1):
        for x in range(grid.w):
            if not ({mat[(x, y)], mat[(x, y + 1)]} & mats):
                continue
            a, b = grid.at(x, y)[0], grid.at(x, y + 1)[0]
            if rules.v.get((a, b), 0) <= cfg['rare_max']:
                rows.append(f'WARN RARE v 0x{a:03X}-0x{b:03X} @{x},{y}')
                counted += 1
    return rows, counted


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


def run(repo, query):
    grid, pair = load(repo, query)
    rules = pair_rules(repo, pair)
    cfg = thresholds(repo)
    mat = {(x, y): rules.material(grid.at(x, y)) for y in range(grid.h) for x in range(grid.w)}
    meta = dict(cfg, mat=mat, marks=events(grid.data), rules=rules,
                map_type=grid.data.get('map_type'))
    cap = cfg['max_lines']
    seam_rows = seams(grid, rules)
    stair_rows, stair_n = stairs(meta, grid, cfg)
    rare_rows, rare_n = rare(meta, grid, cfg)
    rect_rows, rect_n = rects(meta, grid, cfg)
    empty_rows, empty_n = empty(meta, grid, pair, cfg)
    counts = f'seams:{len(seam_rows)} stairs:{stair_n} rare:{rare_n} rects:{rect_n} empty:{empty_n}'
    rows = sorted(seam_rows + stair_rows)[:cap] + rare_rows[:cap] + \
        sorted(rect_rows, key=lambda r: r[9:])[:cap] + empty_rows[:cap]
    return Result(f'lint {grid.name} {counts} '
                  f'score:{len(seam_rows) + stair_n + rare_n + rect_n + empty_n}', rows)
