"""pk map show / paint / fill / line / grid / copy / border (milestone 3)."""
import re
import struct

from ..core.errors import PkError
from ..core.out import Result
from .grid import encode, decode
from .layers import Grid, parse_rect, render, render_by_class, exact_legend
from .tiles import Pair

SHOW_MAX_W = 64


def load(repo, query):
    name, data, layout = repo.map(query)
    grid = Grid(repo, name, data, layout)
    return grid, Pair(repo, layout)


def point(text, grid):
    m = re.fullmatch(r'(\d+),(\d+)', text)
    if not m:
        raise PkError('PARSE', f'coordinate {text!r} must be x,y')
    x, y = int(m[1]), int(m[2])
    if not grid.inside(x, y):
        raise PkError('BOUNDS', f'{x},{y} outside {grid.w}x{grid.h}')
    return x, y


def area(text, grid):
    """x,y or x,y,WxH -> (x, y, w, h)"""
    m = re.fullmatch(r'(\d+),(\d+)(?:,(\d+)x(\d+))?', text)
    if not m:
        raise PkError('PARSE', f'area {text!r} must be x,y or x,y,WxH')
    x, y = int(m[1]), int(m[2])
    w, h = (int(m[3]), int(m[4])) if m[3] else (1, 1)
    if w < 1 or h < 1 or not grid.inside(x, y) or not grid.inside(x + w - 1, y + h - 1):
        raise PkError('BOUNDS', f'area {text} outside {grid.w}x{grid.h}')
    return x, y, w, h


def usage(repo, pair):
    """{metatile: {(coll, elev): count}} over every vanilla layout using the same tileset pair.

    Pk* drafts are excluded: they are never reference data, and including them would make the
    collision/elevation a painted cell gets depend on which drafts happen to exist (non-reproducible
    polish output)."""
    cache = getattr(repo, '_pk_usage', {})
    key = (pair.primary['symbol'], pair.secondary['symbol'])
    if key not in cache:
        stats = {}
        for layout in repo.layouts().values():
            if (layout.get('primary_tileset'), layout.get('secondary_tileset')) != key:
                continue
            if str(layout.get('id', '')).startswith('LAYOUT_PK_'):
                continue
            try:
                raw = repo.read(layout['blockdata_filepath'])
            except PkError:
                continue
            for mt, coll, elev in decode(raw[:layout['width'] * layout['height'] * 2]):
                d = stats.setdefault(mt, {})
                d[(coll, elev)] = d.get((coll, elev), 0) + 1
        cache[key] = stats
        repo._pk_usage = cache
    return cache[key]


def default_bits(repo, pair, metatile, old, coll=None, elev=None):
    """Collision/elevation for a painted cell: explicit > dominant vanilla usage (>=80%) > old cell."""
    stats = usage(repo, pair).get(metatile, {})
    total = sum(stats.values())
    if coll is None:
        by = {}
        for (c, _), n in stats.items():
            by[c] = by.get(c, 0) + n
        best = max(by, key=by.get) if by else None
        coll = best if best is not None and by[best] >= 0.8 * total else old[1]
    if elev is None:
        by = {}
        for (_, e), n in stats.items():
            by[e] = by.get(e, 0) + n
        best = max(by, key=by.get) if by else None
        elev = best if best is not None and by[best] >= 0.8 * total else old[2]
    return metatile, coll, elev


def save(tx, grid):
    tx.stage(grid.layout['blockdata_filepath'], encode(grid.cells))


def changed_view(grid, pair, cells, layer):
    if not cells:
        return []
    xs, ys = [c[0] for c in cells], [c[1] for c in cells]
    x0, y0 = max(min(xs) - 1, 0), max(min(ys) - 1, 0)
    x1, y1 = min(max(xs) + 1, grid.w - 1), min(max(ys) + 1, grid.h - 1)
    if x1 - x0 + 1 > SHOW_MAX_W or y1 - y0 + 1 > 40:
        return [f'changed area {x0},{y0},{x1-x0+1}x{y1-y0+1} too large to print; use pk map show --rect']
    return render(grid, pair, (x0, y0, x1 - x0 + 1, y1 - y0 + 1), layer)


def bits_opts(opts):
    def num(key, top):
        if key not in opts:
            return None
        v = int(opts[key], 0)
        if not 0 <= v <= top:
            raise PkError('BOUNDS', f'--{key} 0..{top}')
        return v
    return num('coll', 3), num('elev', 15)


def stamp_overlay(grid, pair, rect, rows, show_events):
    """Replace the class cells of recognised stamp instances with one symbol each and prepend
    a legend ("H house_small @12,4"). Opt-in via `map show --stamps`."""
    from . import stamps as S
    from .layers import events as event_marks
    library = S.load_stamps(grid.repo, pair)
    found, _ = S.instances(grid, library)
    if not found:
        return rows
    syms, used = {}, set()
    for stamp, _, _ in found:
        if stamp['name'] in syms:
            continue
        sym = stamp.get('sym') if stamp.get('sym') not in used else None
        sym = sym or next(c for c in S.SYMBOLS if c not in used)
        syms[stamp['name']] = sym
        used.add(sym)
    x0, y0, w, h = rect
    marks = {}
    for stamp, sx, sy in found:
        sw, sh = stamp['size']
        for j in range(sh):
            for i in range(sw):
                if stamp['cells'][j][i] is not None and x0 <= sx + i < x0 + w and y0 <= sy + j < y0 + h:
                    marks[(sx + i, sy + j)] = syms[stamp['name']]
    events_seen = event_marks(grid.data) if show_events else {}
    for k, line in enumerate(rows):
        m = re.fullmatch(r'(\d\d)\|(.*)', line)
        if not m:
            continue
        y, cells = int(m[1]), list(m[2])
        for i in range(min(w, len(cells))):
            p = (x0 + i, y)
            if p in marks and p not in events_seen:
                cells[i] = marks[p]
        rows[k] = f'{y:02d}|' + ''.join(cells)
    counts = {}
    for stamp, _, _ in found:
        counts[stamp['name']] = counts.get(stamp['name'], 0) + 1
    legend = ['stamps ' + ' '.join(f'{syms[n]}={n}x{counts[n]}' for n in syms)]
    return legend + rows


def command(repo, tx, args, opts):
    verb, rest = args[0], args[1:]
    layer = 'exact' if opts.get('exact') else 'class'
    if verb == 'show' and len(rest) == 1:
        grid, pair = load(repo, rest[0])
        rect = parse_rect(opts.get('rect'), grid.w, grid.h)
        note = []
        if not opts.get('rect') and grid.w > SHOW_MAX_W:
            rect = (0, 0, SHOW_MAX_W, grid.h)
            note = [f'band 0-{SHOW_MAX_W-1} of {grid.w}; next: --rect {SHOW_MAX_W},0,{grid.w-SHOW_MAX_W}x{grid.h}']
        layers = ['class', 'coll', 'elev'] if opts.get('layer') == 'all' else [opts.get('layer') or layer]
        show_events = not opts.get('no-events')
        rows = []
        for name in layers:
            compact = render_by_class(grid, pair, rect, name, show_events) if len(layers) > 1 and name != 'class' else None
            if compact is not None:
                rows += compact
                continue
            if len(layers) > 1:
                rows.append(f'layer {name}')
            rows += render(grid, pair, rect, name, show_events)
        x, y, w, h = rect
        if opts.get('stamps') and layers[0] == 'class':
            rows = stamp_overlay(grid, pair, rect, rows, show_events)
        return Result(f'{grid.name} {grid.w}x{grid.h} rect {x},{y},{w}x{h} layer:{"+".join(layers)}', note + rows)
    if verb == 'render' and len(rest) == 1:
        from .meta import render_png, render_compare
        grid, pair = load(repo, rest[0])
        viewport = opts.get('viewport')
        if viewport:
            m = re.fullmatch(r'(\d+),(\d+)', viewport)
            if not m:
                raise PkError('PARSE', '--viewport is x,y')
            vx, vy = int(m[1]), int(m[2])
            if vx + 15 > grid.w or vy + 10 > grid.h:
                raise PkError('BOUNDS', f'viewport {vx},{vy} needs 15x10 inside {grid.w}x{grid.h}')
            rect = (vx, vy, 15, 10)
        else:
            rect = parse_rect(opts.get('rect'), grid.w, grid.h)
        scale = int(opts['scale']) if opts.get('scale') else (3 if viewport else 1)
        if scale < 1:
            raise PkError('BOUNDS', '--scale must be >= 1')
        grid_lines = bool(opts.get('grid'))
        if opts.get('compare'):
            other, other_pair = load(repo, opts['compare'])
            data = render_compare(repo, (grid, pair), (other, other_pair), scale, grid_lines)
            out = opts.get('out') or f'.pk/render/{grid.name}_vs_{other.name}.png'
            tx.stage(repo.path(out).relative_to(repo.root).as_posix(), data)
            return Result(f'render compare {grid.name} vs {other.name} scale:{scale} '
                          f'{"grid " if grid_lines else ""}-> {out}',
                          [f'left {grid.w}x{grid.h} right {other.w}x{other.h} gap 1 tile'])
        out = opts.get('out') or f'.pk/render/{grid.name}.png'
        tx.stage(repo.path(out).relative_to(repo.root).as_posix(),
                 render_png(repo, grid, pair, rect, scale, grid_lines))
        return Result(f'render {grid.name} {rect[2]}x{rect[3]} scale:{scale}'
                      f'{" grid" if grid_lines else ""} -> {out}')
    if verb == 'seam' and len(rest) == 2:
        from . import terrain
        grid, pair = load(repo, rest[0])
        box = area(rest[1], grid)
        ring = int(opts.get('ring', 2))
        if ring < 1:
            raise PkError('BOUNDS', '--ring must be >= 1')
        rules = terrain.Rules(repo, pair, include_pk=False)
        changed = terrain.repair(repo, pair, rules, grid, box, ring)
        if changed:
            save(tx, grid)
        bad = terrain.unseen(grid, rules, changed)
        note = [f'WARN {len(bad)} seams still unseen after repair'] if bad else []
        return Result(f'seam {grid.name} box {box[0]},{box[1]},{box[2]}x{box[3]} ring:{ring} '
                      f'{len(changed)} metatiles changed', note + changed_view(grid, pair, changed, 'class'))
    if verb == 'at' and len(rest) == 2:
        grid, pair = load(repo, rest[0])
        x, y = point(rest[1], grid)
        mt, coll, elev = grid.at(x, y)
        info = pair.info(mt)
        return Result(f'at {grid.name} {x},{y}', [f'metatile 0x{mt:03X} {info.get("name", "-")} coll {coll} elev {elev}',
                                                   f'behavior {pair.behavior_name(mt)} layer {pair.layer(mt)} tileset {pair.owner(mt)["symbol"]}'])
    if verb in ('paint', 'fill', 'line') and len(rest) == (4 if verb == 'line' else 3):
        grid, pair = load(repo, rest[0])
        coll, elev = bits_opts(opts)
        mt = pair.lookup(rest[-1])
        if verb == 'paint':
            x, y, w, h = area(rest[1], grid)
            cells = [(i, j) for j in range(y, y + h) for i in range(x, x + w)]
        elif verb == 'fill':
            x, y = point(rest[1], grid)
            target = grid.at(x, y)[0]
            cells, todo, seen = [], [(x, y)], {(x, y)}
            while todo:
                i, j = todo.pop()
                cells.append((i, j))
                for n in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)):
                    if n not in seen and grid.inside(*n) and grid.at(*n)[0] == target:
                        seen.add(n)
                        todo.append(n)
            if len(cells) > 2000:
                raise PkError('LIMIT', f'fill would change {len(cells)} cells; paint an area instead')
        else:
            (x1, y1), (x2, y2) = point(rest[1], grid), point(rest[2], grid)
            cells, dx, dy = [], abs(x2 - x1), -abs(y2 - y1)
            sx, sy, err, x, y = (1 if x1 < x2 else -1), (1 if y1 < y2 else -1), dx + dy, x1, y1
            while True:
                cells.append((x, y))
                if (x, y) == (x2, y2):
                    break
                e2 = 2 * err
                if e2 >= dy:
                    err, x = err + dy, x + sx
                if e2 <= dx:
                    err, y = err + dx, y + sy
        for i, j in cells:
            grid.set(i, j, default_bits(repo, pair, mt, grid.at(i, j), coll, elev))
        save(tx, grid)
        sample = grid.at(*cells[0])
        return Result(f'{verb} {grid.name} {len(cells)} cells 0x{mt:03X} coll {sample[1]} elev {sample[2]}',
                      changed_view(grid, pair, cells, layer))
    if verb == 'grid' and len(rest) in (2, 3):
        grid, pair = load(repo, rest[0])
        x0, y0 = point(rest[1], grid)
        text = rest[2] if len(rest) == 3 else opts.get('stdin', '')
        rows, defs = [], {}
        for line in re.split(r'[;\n]', text):
            line = line.rstrip('\r')
            m = re.fullmatch(r'\s*def\s+(\S)\s+(\S+)\s*', line)
            if m:
                defs[m[1]] = pair.lookup(m[2])
            elif line.strip():
                rows.append(line)
        if not rows:
            raise PkError('PARSE', 'grid needs rows: inline "TT..;T==." or stdin lines; optional "def X <metatile>" lines')
        w, h = max(len(r) for r in rows), len(rows)
        if not grid.inside(x0 + w - 1, y0 + h - 1):
            raise PkError('BOUNDS', f'patch {w}x{h} at {x0},{y0} exceeds {grid.w}x{grid.h}')
        legend = {s: mt for mt, s in exact_legend(grid, pair, (x0, y0, w, h)).items()}
        legend.update(defs)
        coll, elev = bits_opts(opts)
        cells = []
        for j, row in enumerate(rows):
            for i, sym in enumerate(row):
                if sym in ' ?':
                    continue
                if sym not in legend:
                    raise PkError('PARSE', f'symbol {sym!r} at row {j} col {i} undefined; add "def {sym} <metatile>"')
                x, y = x0 + i, y0 + j
                grid.set(x, y, default_bits(repo, pair, legend[sym], grid.at(x, y), coll, elev))
                cells.append((x, y))
        save(tx, grid)
        return Result(f'grid {grid.name} {len(cells)} cells at {x0},{y0}', changed_view(grid, pair, cells, layer))
    if verb == 'copy' and len(rest) == 3:
        grid, pair = load(repo, rest[0])
        source, src_pair = (load(repo, opts['from']) if opts.get('from') else (grid, pair))
        sx, sy, w, h = area(rest[1], source)
        if (src_pair.primary['symbol'], src_pair.secondary['symbol']) != (pair.primary['symbol'], pair.secondary['symbol']):
            same_primary = src_pair.primary['symbol'] == pair.primary['symbol']
            blocked = sorted({source.at(sx + i, sy + j)[0] for j in range(h) for i in range(w)
                              if not same_primary or source.at(sx + i, sy + j)[0] >= src_pair.limits['metatiles']})
            if blocked:
                raise PkError('CONFLICT', 'copy needs a shared primary and portable box; blocked ids: '
                              + ', '.join(f'0x{mt:03X}' for mt in blocked))
        dx, dy = point(rest[2], grid)
        if not grid.inside(dx + w - 1, dy + h - 1):
            raise PkError('BOUNDS', f'copy target {dx},{dy},{w}x{h} outside {grid.w}x{grid.h}')
        block = [[source.at(sx + i, sy + j) for i in range(w)] for j in range(h)]
        cells = []
        for j in range(h):
            for i in range(w):
                grid.set(dx + i, dy + j, block[j][i])
                cells.append((dx + i, dy + j))
        repaired = []
        if opts.get('repair'):
            from . import terrain
            rules = terrain.Rules(repo, pair, include_pk=False)
            repaired = terrain.repair(repo, pair, rules, grid, (dx, dy, w, h), int(opts.get('ring', 2)))
        save(tx, grid)
        tail = f' repair:{len(repaired)}' if opts.get('repair') else ''
        return Result(f'copy {grid.name} {w}x{h} -> {dx},{dy}{tail}', changed_view(grid, pair, cells, layer))
    if verb == 'border' and len(rest) >= 2:
        grid, pair = load(repo, rest[0])
        bw, bh = grid.layout.get('border_width', 2), grid.layout.get('border_height', 2)
        path = grid.layout['border_filepath']
        old = decode(repo.read(path))
        if len(rest) == 2 and rest[1] == 'show':
            return Result(f'border {grid.name} {bw}x{bh}', [' '.join(f'0x{c[0]:03X}' for c in old[j * bw:(j + 1) * bw]) for j in range(bh)])
        ids = [pair.lookup(w) for w in rest[1:]]
        if len(ids) == 1:
            ids *= bw * bh
        if len(ids) != bw * bh:
            raise PkError('PARSE', f'border needs 1 or {bw*bh} metatiles ({bw}x{bh})')
        tx.stage(path, encode([(mt, c[1], c[2]) for mt, c in zip(ids, old)]))
        return Result(f'border {grid.name} {bw}x{bh}', [' '.join(f'0x{m:03X}' for m in ids)])
    raise PkError('PARSE', 'usage: pk map show|at|paint|fill|line|grid|copy|border; see pk help map')
