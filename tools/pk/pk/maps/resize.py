"""pk map resize / extend-to (SPEC §5.7): grow or crop a map and fix everything that
depends on its coordinates in one transaction."""
import re

from ..core.errors import PkError
from ..core.out import Result
from ..core.repo import JsonDocument
from .grid import encode, decode
from .layers import Grid, render
from .tiles import Pair
from .edit import default_bits

MAX_MAP_DATA = 10240          # include/fieldmap.h MAX_MAP_DATA_SIZE, MAP_OFFSET_W=15, MAP_OFFSET_H=14
OPPOSITE = {'up': 'down', 'down': 'up', 'left': 'right', 'right': 'left'}
ANCHORS = {'tl': (0, 0), 't': (.5, 0), 'tr': (1, 0), 'l': (0, .5), 'c': (.5, .5), 'r': (1, .5),
           'bl': (0, 1), 'b': (.5, 1), 'br': (1, 1)}
LOCAL_COORD_CMDS = re.compile(r'^(\s*)(setmetatile|setobjectxyperm|setobjectxy)(\s+)(.*)$')
WARP_CMDS = re.compile(r'^(\s*)(warp\w*|setwarp|setdynamicwarp|setdivewarp|setholewarp|setescapewarp)(\s+)(MAP_\w+)\s*,(.*)$')


def parse_growth(opts, w, h):
    if opts.get('add') and opts.get('to'):
        raise PkError('PARSE', 'use --add or --to, not both')
    if opts.get('add'):
        grow = dict(top=0, left=0, right=0, bottom=0)
        for part in opts['add'].split(','):
            m = re.fullmatch(r'(top|left|right|bottom)[=:](-?\d+)', part.strip())
            if not m:
                raise PkError('PARSE', '--add top=n,left=n,right=n,bottom=n (negative crops)')
            grow[m[1]] = int(m[2])
        return grow
    m = re.fullmatch(r'(\d+)x(\d+)', opts.get('to', ''))
    if not m:
        raise PkError('PARSE', 'pk map resize MAP --add top=n,... | --to WxH [--anchor tl|t|tr|l|c|r|bl|b|br]')
    nw, nh = int(m[1]), int(m[2])
    ax, ay = ANCHORS.get(opts.get('anchor', 'tl'), (None, None))
    if ax is None:
        raise PkError('PARSE', '--anchor tl|t|tr|l|c|r|bl|b|br')
    left, top = round((nw - w) * ax), round((nh - h) * ay)
    return dict(left=left, top=top, right=nw - w - left, bottom=nh - h - top)


def shift_args(args, dx, dy, positions):
    """Shift numeric args at the given positions; None if any is not a plain integer."""
    parts = [p.strip() for p in args.split(',')]
    out = list(parts)
    for i, delta in zip(positions, (dx, dy)):
        if i >= len(parts) or not re.fullmatch(r'-?\d+', parts[i]):
            return None
        out[i] = str(int(parts[i]) + delta)
    return ', '.join(out)


def run(repo, tx, query, opts):
    name, data, layout = repo.map(query)
    w, h = layout['width'], layout['height']
    g = parse_growth(opts, w, h)
    left, top = g['left'], g['top']
    nw, nh = w + g['left'] + g['right'], h + g['top'] + g['bottom']
    if nw < 1 or nh < 1:
        raise PkError('BOUNDS', f'resize would make {nw}x{nh}')
    if (nw + 15) * (nh + 14) > MAX_MAP_DATA:
        raise PkError('LIMIT', f'{nw}x{nh}: (w+15)*(h+14)={(nw+15)*(nh+14)} > {MAX_MAP_DATA} (MAX_MAP_DATA_SIZE)')
    maps = repo.maps()
    sharing = sorted(m for m, d in maps.items() if d['layout'] == layout['id'])
    warnings = []
    layouts_doc = JsonDocument(repo.read('data/layouts/layouts.json'))
    entry = next(x for x in layouts_doc.value['layouts'] if x.get('id') == layout['id'])
    if len(sharing) > 1:
        if not opts.get('fork-layout'):
            raise PkError('CONFLICT', f"{layout['id']} is shared by {len(sharing)} maps; add --fork-layout", sharing[:10])
        new_id = 'LAYOUT_' + re.sub(r'(?<!^)(?=[A-Z])', '_', name).upper().replace('__', '_')
        ids = {x.get('id') for x in layouts_doc.value['layouts']}
        while new_id in ids:
            new_id += '_PK'
        folder = f'data/layouts/{name}'
        entry = dict(entry, id=new_id, name=f'{name}_Layout',
                     blockdata_filepath=f'{folder}/map.bin', border_filepath=f'{folder}/border.bin')
        layouts_doc.value['layouts'].append(entry)
        tx.stage(entry['border_filepath'], repo.read(layout['border_filepath']))
        data = dict(data, layout=new_id)
        warnings.append(f'forked {layout["id"]} -> {new_id}')
    # --- blocks -----------------------------------------------------------------------
    grid = Grid(repo, name, maps[name], layout)
    pair = Pair(repo, layout)
    fill = opts.get('fill', 'extend')
    border = decode(repo.read(layout['border_filepath']))
    bw, bh = layout.get('border_width', 2), layout.get('border_height', 2)
    fill_mt = None if fill in ('extend', 'border', 'terrain') else pair.lookup(fill)
    cells = []
    for ny in range(nh):
        for nx in range(nw):
            ox, oy = nx - left, ny - top
            if 0 <= ox < w and 0 <= oy < h:
                cells.append(grid.at(ox, oy))
            elif fill in ('extend', 'terrain'):
                cells.append(grid.at(min(max(ox, 0), w - 1), min(max(oy, 0), h - 1)))
            elif fill == 'border':
                b = border[(ny % bh) * bw + nx % bw]
                cells.append(default_bits(repo, pair, b[0], b))
            else:
                cells.append(default_bits(repo, pair, fill_mt, (fill_mt, 0, 3)))
    if fill == 'terrain':
        # Extend materials, then let learned adjacency pick fitting metatiles for the new cells.
        from .terrain import Rules, solve
        tmp = Grid.__new__(Grid)
        tmp.w, tmp.h, tmp.cells = nw, nh, cells
        rules = Rules(repo, pair)
        fresh = {(x, y): rules.material(cells[y * nw + x]) for y in range(nh) for x in range(nw)
                 if not (0 <= x - left < w and 0 <= y - top < h)}
        for (x, y), mt in solve(tmp, rules, fresh, margin=0).items():
            cells[y * nw + x] = default_bits(repo, pair, mt, cells[y * nw + x])
    tx.stage(entry['blockdata_filepath'], encode(cells))
    entry['width'], entry['height'] = nw, nh
    tx.stage('data/layouts/layouts.json', layouts_doc.emit())
    # --- events -----------------------------------------------------------------------
    map_path = f'data/maps/{name}/map.json'
    doc = JsonDocument(repo.read(map_path))
    if 'layout' in data and data['layout'] != doc.value['layout']:
        doc.value['layout'] = data['layout']
    moved, dropped = 0, []
    for key in ('object_events', 'warp_events', 'coord_events', 'bg_events'):
        keep = []
        for n, e in enumerate(doc.value.get(key) or []):
            e['x'], e['y'] = e['x'] + left, e['y'] + top
            moved += bool(left or top)
            if not (0 <= e['x'] < nw and 0 <= e['y'] < nh) and (g['left'] < 0 or g['top'] < 0 or g['right'] < 0 or g['bottom'] < 0):
                if key == 'warp_events':
                    raise PkError('BOUNDS', f'crop would cut warp#{n}; warps are referenced by index elsewhere')
                if not opts.get('drop-out-of-bounds'):
                    raise PkError('BOUNDS', f'{key[:-7]}#{n} ends outside {nw}x{nh}; add --drop-out-of-bounds')
                dropped.append(f'{key[:-7]}#{n}')
                continue
            keep.append(e)
        if key in doc.value:
            doc.value[key] = keep
    # --- connections (both sides) -------------------------------------------------------
    conns = 0
    for c in doc.value.get('connections') or []:
        delta = top if c['direction'] in ('left', 'right') else left if c['direction'] in ('up', 'down') else 0
        if delta:
            c['offset'] = int(c['offset']) + delta
            conns += 1
            other = next((m for m, d in maps.items() if d['id'] == c['map']), None)
            if other:
                opath = f'data/maps/{other}/map.json'
                odoc = JsonDocument(repo.read(opath))
                for oc in odoc.value.get('connections') or []:
                    if oc.get('map') == doc.value['id'] and oc.get('direction') == OPPOSITE[c['direction']]:
                        oc['offset'] = int(oc['offset']) - delta
                tx.stage(opath, odoc.emit())
        if c['direction'] in ('up', 'down') and int(c['offset']) >= nw:
            warnings.append(f"connection {c['direction']} {c['map']} offset {c['offset']} is past the new width")
        if c['direction'] in ('left', 'right') and int(c['offset']) >= nh:
            warnings.append(f"connection {c['direction']} {c['map']} offset {c['offset']} is past the new height")
    tx.stage(map_path, doc.emit())
    # --- scripts with literal coordinates -----------------------------------------------
    scripts = 0
    if left or top:
        own = f'data/maps/{name}/scripts.inc'
        paths = sorted(p.relative_to(repo.root).as_posix() for pattern in ('data/maps/*/scripts.inc', 'data/scripts/*.inc')
                       for p in repo.root.glob(pattern))
        for path in paths:
            text = repo.read(path).decode('utf-8')
            lines = text.split('\n')
            changed = False
            for i, line in enumerate(lines):
                body = line.rstrip('\r')
                cr = line[len(body):]
                if path == own:
                    m = LOCAL_COORD_CMDS.match(body)
                    if m:
                        positions = (0, 1) if m[2] == 'setmetatile' else (1, 2)
                        new = shift_args(m[4], left, top, positions)
                        if new is None:
                            warnings.append(f'manual {path}:{i+1} {m[2]} has non-literal coordinates')
                        else:
                            lines[i] = f'{m[1]}{m[2]}{m[3]}{new}{cr}'
                            changed = True
                            scripts += 1
                        continue
                    if re.match(r'^\s*getplayerxy\b', body):
                        warnings.append(f'manual {path}:{i+1} getplayerxy comparisons may need +{left},+{top}')
                m = WARP_CMDS.match(body)
                if m and m[4] == doc.value['id']:
                    args = [a.strip() for a in m[5].split(',')]
                    if len(args) >= 2:
                        new = shift_args(m[5], left, top, (len(args) - 2, len(args) - 1))
                        if new is None:
                            warnings.append(f'manual {path}:{i+1} {m[2]} to {name} has non-literal coordinates')
                        else:
                            lines[i] = f'{m[1]}{m[2]}{m[3]}{m[4]}, {new}{cr}'
                            changed = True
                            scripts += 1
            if changed:
                tx.stage(path, '\n'.join(lines).encode('utf-8'))
        # heal / respawn locations (src/data/heal_locations.json)
        heal_path = 'src/data/heal_locations.json'
        heal = JsonDocument(repo.read(heal_path))
        touched = False
        for loc in heal.value.get('heal_locations', []):
            if loc.get('map') == doc.value['id']:
                loc['x'], loc['y'] = loc['x'] + left, loc['y'] + top
                touched = True
            if loc.get('respawn_map') == doc.value['id'] and 'respawn_x' in loc:
                loc['respawn_x'], loc['respawn_y'] = loc['respawn_x'] + left, loc['respawn_y'] + top
                touched = True
        if touched:
            tx.stage(heal_path, heal.emit())
            scripts += 1
    # --- report -------------------------------------------------------------------------
    new_grid = Grid(repo, name, doc.value, entry)
    rows = [f'WARN {x}' for x in warnings] + ([f'dropped {" ".join(dropped)}'] if dropped else [])
    seam = seam_rect(g, w, h, nw, nh)
    if seam and seam[2] <= 64 and seam[3] <= 30:
        rows += render(new_grid, pair, seam, 'class')
    return Result(f'resize {name} {w}x{h} -> {nw}x{nh} events:{moved} scripts:{scripts} conns:{conns} warn:{len(warnings)}', rows)


def seam_rect(g, w, h, nw, nh):
    """A small window around the first grown edge, for a quick visual check."""
    if g['right'] > 0:
        x = max(w + g['left'] - 3, 0)
        return x, 0, nw - x, min(nh, 20)
    if g['bottom'] > 0:
        y = max(h + g['top'] - 3, 0)
        return 0, y, min(nw, 64), nh - y
    if g['left'] > 0:
        return 0, 0, min(g['left'] + 3, nw), min(nh, 20)
    if g['top'] > 0:
        return 0, 0, min(nw, 64), min(g['top'] + 3, nh)
    return None


def extend_to(repo, tx, query, neighbour, opts):
    """Grow MAP along the edge it shares with NEIGHBOUR until the connection fully overlaps it."""
    name, data, layout = repo.map(query)
    other, odata, olayout = repo.map(neighbour)
    conn = next((c for c in data.get('connections') or [] if c.get('map') == odata['id']), None)
    if not conn:
        raise PkError('NOTFOUND', f'{name} has no connection to {other}')
    off = int(conn['offset'])
    if conn['direction'] in ('up', 'down'):
        size, length, grow = layout['width'], olayout['width'], ('left', 'right')
    else:
        size, length, grow = layout['height'], olayout['height'], ('top', 'bottom')
    before, after = max(0, -off), max(0, off + length - size)
    if not before and not after:
        return Result(f'extend-to {name} already covers {other}')
    opts = dict(opts, add=f'{grow[0]}={before},{grow[1]}={after}')
    opts.pop('to', None)
    return run(repo, tx, name, opts)
