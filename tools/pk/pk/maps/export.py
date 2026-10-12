"""pk map export / import: one map's terrain as a lossless text file.

The text is the map itself, not a picture of it: the same export imported into the same
layout reproduces map.bin and border.bin byte for byte. Collision and elevation are stored
as a default per metatile symbol plus exceptions, the same default+exception idea as
`map show --layer all` (layers.compact_values), so an outdoor map's file stays short.
"""
from pathlib import Path
import re
import string

from ..core.errors import PkError
from ..core.out import Result
from .grid import decode, encode
from .layers import compact_values

FORMAT = 1
# One-character symbols first; if a map has more distinct metatiles than the pool, two-character
# symbols continue the same alphabet. Whitespace and '|' stay out (row prefixes and token splits).
POOL = [c for c in (string.ascii_letters + string.digits + '!#$%&()*+,-./:;<=>?@[]^_`{|}~')]


def symbols(n):
    if n <= len(POOL):
        return POOL[:n]
    out = list(POOL)
    for a in POOL:
        for b in POOL:
            if len(out) >= n:
                return out[:n]
            out.append(a + b)
    raise PkError('LIMIT', f'{n} distinct metatiles; the text alphabet holds {len(out)}')


def _border_cells(repo, layout):
    raw = repo.read(layout['border_filepath'])
    return decode(raw)


def export_text(repo, grid, pair):
    w, h = grid.w, grid.h
    counts = {}
    for cell in grid.cells:
        counts[cell[0]] = counts.get(cell[0], 0) + 1
    order = sorted(counts, key=lambda m: (-counts[m], m))
    syms = symbols(len(order))
    sym_of_mt = {mt: syms[i] for i, mt in enumerate(order)}
    sym_of = lambda x, y, c: sym_of_mt[c[0]]

    lines = [f'pkmap {FORMAT}',
             f'layout {grid.layout["id"]}',
             f'tilesets {pair.primary["symbol"]} {pair.secondary["symbol"]}',
             f'size {w}x{h}']
    lines.append('grid')
    for i in range(0, len(order), 8):
        chunk = ' '.join(f'{sym_of_mt[mt]} 0x{mt:03X}' for mt in order[i:i + 8])
        lines.append('legend ' + chunk)
    for y in range(h):
        lines.append(f'{y:02d}| ' + ' '.join(sym_of_mt[grid.at(x, y)[0]] for x in range(w)))
    for layer, fmt in (('coll', lambda v: str(v)), ('elev', lambda v: f'{v:X}')):
        default, exceptions = compact_values(grid, pair, (0, 0, w, h), layer, sym_of,
                                             show_events=False, cap=None)
        lines.append(f'{layer} ' + ' '.join(f'{sym_of_mt[mt]} {fmt(default[sym_of_mt[mt]])}' for mt in order))
        if exceptions:
            lines.append(f'{layer}! ' + ' '.join(f'{x},{y}={fmt(v)}' for x, y, v in exceptions))
    bw, bh = grid.layout.get('border_width', 2), grid.layout.get('border_height', 2)
    border = _border_cells(repo, grid.layout)
    lines.append(f'border {bw}x{bh}')
    for j in range(bh):
        lines.append(' '.join(f'0x{mt:03X},{coll},{elev}' for mt, coll, elev in border[j * bw:(j + 1) * bw]))
    return '\n'.join(lines) + '\n'


def _pairs(tokens, what):
    if len(tokens) % 2:
        raise PkError('PARSE', f'{what}: symbol/value pairs expected')
    return list(zip(tokens[0::2], tokens[1::2]))


def _exceptions(tokens, what):
    out = {}
    for token in tokens:
        m = re.fullmatch(r'(\d+),(\d+)=(\w+)', token)
        if not m:
            raise PkError('PARSE', f'{what}: exception x,y=value expected, got {token!r}')
        out[(int(m[1]), int(m[2]))] = m[3]
    return out


def export_map(repo, tx, name, opts):
    from .edit import load
    grid, pair = load(repo, name)
    text = export_text(repo, grid, pair)
    out = opts.get('out') or f'.pk/map/{grid.name}.map'
    tx.stage(repo.path(out).relative_to(repo.root).as_posix(), text.encode('utf-8'))
    return Result(f'map export {grid.name} {grid.w}x{grid.h} -> {out} {len(text)}b')


def import_map(repo, tx, name, file, opts):
    from .edit import load
    grid, pair = load(repo, name)
    path = Path(file)
    text = path.read_text(encoding='utf-8') if path.is_file() else repo.read(file).decode('utf-8')
    return import_text(repo, tx, grid.layout, text)


def import_text(repo, tx, layout, text):
    """Parse export_text output back into blockdata + border writes (one transaction)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != f'pkmap {FORMAT}':
        raise PkError('PARSE', f'not a pk map file (want "pkmap {FORMAT}")')
    fields, legend, grid_rows = {}, {}, []
    coll_def, elev_def, coll_exc, elev_exc = {}, {}, {}, {}
    border = []
    state = 'header'
    for lineno, raw in enumerate(lines[1:], 2):
        line = raw.rstrip()
        if state == 'header':
            if line == 'grid':
                state = 'grid'
                continue
            key, _, value = line.partition(' ')
            if key not in ('layout', 'tilesets', 'size'):
                raise PkError('PARSE', f'line {lineno}: unexpected header {line!r}')
            fields[key] = value
        elif state == 'grid':
            if line.startswith('legend '):
                for sym, hexid in _pairs(line.split()[1:], f'line {lineno} legend'):
                    if sym in legend or not re.fullmatch(r'0x[0-9A-Fa-f]+', hexid):
                        raise PkError('PARSE', f'line {lineno}: bad legend entry {sym} {hexid}')
                    legend[sym] = int(hexid, 16)
            elif line.startswith('coll! '):
                coll_exc = _exceptions(line.split()[1:], f'line {lineno}')
            elif line.startswith('elev! '):
                elev_exc = _exceptions(line.split()[1:], f'line {lineno}')
            elif line.startswith('coll '):
                coll_def = dict(_pairs(line.split()[1:], f'line {lineno}'))
            elif line.startswith('elev '):
                elev_def = dict(_pairs(line.split()[1:], f'line {lineno}'))
            elif line.startswith('border '):
                m = re.fullmatch(r'(\d+)x(\d+)', line.split()[1])
                if not m:
                    raise PkError('PARSE', f'line {lineno}: border needs WxH')
                bw, bh = int(m[1]), int(m[2])
                state = 'border'
            elif re.fullmatch(r'\d+\|.*', line):
                _, _, cells = line.partition('|')
                grid_rows.append(cells.split())
            elif line.strip():
                raise PkError('PARSE', f'line {lineno}: unexpected grid line {line!r}')
        else:
            for token in line.split():
                m = re.fullmatch(r'0x([0-9A-Fa-f]+),(\d+),(\d+)', token)
                if not m:
                    raise PkError('PARSE', f'line {lineno}: bad border cell {token!r}')
                border.append((int(m[1], 16), int(m[2]), int(m[3])))
    if set(fields) != {'layout', 'tilesets', 'size'}:
        raise PkError('PARSE', 'header needs layout, tilesets and size')
    m = re.fullmatch(r'(\d+)x(\d+)', fields['size'])
    w, h = int(m[1]), int(m[2])
    if fields['layout'] != layout['id'] or (w, h) != (layout['width'], layout['height']):
        raise PkError('CONFLICT', f"file is {fields['layout']} {fields['size']}, not {layout['id']} "
                                  f"{layout['width']}x{layout['height']}")
    if len(grid_rows) != h or any(len(r) != w for r in grid_rows):
        raise PkError('PARSE', f'grid is not {w}x{h}')
    if len(border) != layout.get('border_width', 2) * layout.get('border_height', 2):
        raise PkError('PARSE', 'border cell count does not match border size')
    cells = []
    for y, row in enumerate(grid_rows):
        for x, sym in enumerate(row):
            if sym not in legend:
                raise PkError('PARSE', f'grid {x},{y}: symbol {sym!r} is not in the legend')
            mt = legend[sym]
            coll = int(coll_exc.get((x, y), coll_def.get(sym, 0)))
            elev = int(elev_exc.get((x, y), elev_def.get(sym, '0')), 16)
            cells.append((mt, coll, elev))
    tx.stage(layout['blockdata_filepath'], encode(cells))
    tx.stage(layout['border_filepath'], encode(border))
    return Result(f'import {layout["id"]} {w}x{h} {len(cells)} cells, border {bw}x{bh}')
