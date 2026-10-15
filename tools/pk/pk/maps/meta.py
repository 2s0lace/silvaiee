"""pk meta: list, name, auto-name and draw metatiles of a tileset pair."""
import json
import re

from PIL import Image

from ..core.errors import PkError
from ..core.out import Result, page
from .edit import usage
from .layers import CLASSES
from .tiles import Pair, tileset, NAMES_DIR

SYMBOLS = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@#$%&*+=<>'


def pair_for(repo, word):
    """A tileset pair from a map name, or from 'gTileset_Primary,gTileset_Secondary'."""
    if ',' in word:
        p, s = word.split(',', 1)
        layout = {'primary_tileset': p, 'secondary_tileset': s,
                  'layout_version': 'frlg' if p.endswith('_Frlg') else 'emerald', 'id': word}
        tileset(repo, p), tileset(repo, s)
        return Pair(repo, layout)
    _, _, layout = repo.map(word)
    return Pair(repo, layout)


def all_ids(pair):
    n = pair.limits['metatiles']
    return list(range(pair.count(pair.primary))) + [n + i for i in range(pair.count(pair.secondary))]


def save_names(repo, tx, pair, ts):
    data = dict(sorted(pair.names(ts).items()))
    tx.stage(f'{NAMES_DIR}/{ts["symbol"]}.json', (json.dumps(data, indent=1, sort_keys=True) + '\n').encode('utf-8'))


def autoname(repo, tx, pair, overwrite=False):
    """Name metatiles from behaviours and vanilla usage; never replaces a manual name.
    Generated names never collide with an existing name (manual or auto) of the pair."""
    stats = usage(repo, pair)
    existing = {v['name'] for ts in (pair.primary, pair.secondary)
                for v in pair.names(ts).values() if v.get('name')}
    counters, named = {}, 0
    for mt in all_ids(pair):
        ts = pair.owner(mt)
        names = pair.names(ts)
        key = f'{mt:03X}'
        if key in names and not (overwrite and names[key].get('auto')):
            continue
        behavior = pair.behavior_name(mt)
        label = None
        for pattern, sym, cls in CLASSES:
            if re.search(pattern, behavior):
                label = cls
                break
        uses = stats.get(mt, {})
        total = sum(uses.values())
        if label is None:
            if not total:
                continue                      # unused and featureless: leave unnamed
            solid = sum(n for (c, _), n in uses.items() if c) / total
            label = 'solid' if solid >= 0.8 else 'ground' if solid <= 0.2 else None
            if label is None:
                continue
        while True:
            counters[label] = counters.get(label, 0) + 1
            name = f'{label}_{counters[label]}'
            if name not in existing:
                break
        existing.add(name)
        names[key] = dict(name=name, tags=[label], auto=True)
        named += 1
    for ts in (pair.primary, pair.secondary):
        save_names(repo, tx, pair, ts)
    return named


def pal_file(path):
    lines = path.decode('utf-8', 'replace').split()
    if lines[:1] != ['JASC-PAL']:
        raise PkError('PARSE', 'palette is not JASC-PAL')
    n = int(lines[2])
    return [tuple(int(v) for v in lines[3 + 3 * i: 6 + 3 * i]) for i in range(n)]


def draw(repo, pair, mt, only=None):
    """16x16 RGB pixels of a metatile: bottom layer then top layer, colour 0 transparent.
    only: subset of tile slots to draw (0-3 bottom layer, 4-7 top layer)."""
    import io
    ts = pair.owner(mt)
    raw = repo.read(ts['metatiles'])
    entry = raw[pair.local(mt) * 16: pair.local(mt) * 16 + 16]
    words = [entry[i] | entry[i + 1] << 8 for i in range(0, 16, 2)]
    sheets = {}
    def sheet(t):
        if t < pair.limits['tiles']:
            src, index = pair.primary, t
        else:
            src, index = pair.secondary, t - pair.limits['tiles']
        if src['symbol'] not in sheets:
            sheets[src['symbol']] = Image.open(io.BytesIO(repo.read(src['tiles'])))
        img = sheets[src['symbol']]
        cols = img.width // 8
        if index >= cols * (img.height // 8):
            return None
        return img.crop(((index % cols) * 8, (index // cols) * 8, (index % cols) * 8 + 8, (index // cols) * 8 + 8))
    pals = {}
    def palette(slot):
        if slot not in pals:
            src = pair.primary if slot < pair.limits['pals'] else pair.secondary
            files = src['palettes']
            pals[slot] = pal_file(repo.read(files[slot])) if slot < len(files) else [(255, 0, 255)] * 16
        return pals[slot]
    pixels = [[None] * 16 for _ in range(16)]
    for n, word in enumerate(words):
        if only is not None and n not in only:
            continue
        tile, hflip, vflip, slot = word & 0x3FF, word >> 10 & 1, word >> 11 & 1, word >> 12
        img = sheet(tile)
        if img is None:
            continue
        ox, oy = (n % 4 % 2) * 8, (n % 4 // 2) * 8
        for y in range(8):
            for x in range(8):
                index = img.getpixel((7 - x if hflip else x, 7 - y if vflip else y))
                if index:
                    pixels[oy + y][ox + x] = palette(slot)[index]
    return pixels


def command(repo, tx, args, opts, pagination):
    verb, rest = args[0], args[1:]
    if verb == 'ls' and len(rest) == 1:
        pair = pair_for(repo, rest[0])
        stats = usage(repo, pair)
        rows = []
        for mt in all_ids(pair):
            info = pair.info(mt)
            if opts.get('unnamed') and info.get('name'):
                continue
            uses = stats.get(mt, {})
            total = sum(uses.values())
            solid = round(100 * sum(n for (c, _), n in uses.items() if c) / total) if total else 0
            behavior = pair.behavior_name(mt).replace('MB_FRLG_', '').replace('MB_', '')
            rows.append(f'0x{mt:03X} {info.get("name", "-")} {behavior} L{pair.layer(mt)} used:{total} solid:{solid}%')
        return page(f'meta {pair.primary["symbol"]}+{pair.secondary["symbol"]} {len(rows)}', rows, *pagination(opts))
    if verb == 'name' and len(rest) == 3:
        pair = pair_for(repo, rest[0])
        mt = pair.lookup(rest[1])
        name = rest[2]
        if not re.fullmatch(r'[a-z][a-z0-9_]*', name):
            raise PkError('PARSE', 'metatile names are lowercase snake_case')
        for other in all_ids(pair):
            if other != mt and pair.info(other).get('name') == name:
                raise PkError('CONFLICT', f'name {name} already used by 0x{other:03X}')
        ts = pair.owner(mt)
        entry = dict(name=name, tags=[t for t in opts.get('tags', '').split(',') if t])
        if opts.get('sym'):
            if len(opts['sym']) != 1 or opts['sym'].isspace():
                raise PkError('PARSE', '--sym is one visible character')
            entry['sym'] = opts['sym']
        pair.names(ts)[f'{mt:03X}'] = entry
        save_names(repo, tx, pair, ts)
        return Result(f'meta name 0x{mt:03X} {name}' + (f' sym {entry["sym"]}' if 'sym' in entry else ''))
    if verb == 'autoname' and len(rest) == 1:
        pair = pair_for(repo, rest[0])
        n = autoname(repo, tx, pair, opts.get('overwrite', False))
        return Result(f'meta autoname {n} named in {pair.primary["symbol"]}+{pair.secondary["symbol"]}')
    if verb == 'show' and len(rest) == 2:
        pair = pair_for(repo, rest[0])
        mt = pair.lookup(rest[1])
        pixels = draw(repo, pair, mt)
        colours = sorted({p for row in pixels for p in row if p})
        if len(colours) > len(SYMBOLS):
            raise PkError('LIMIT', 'too many colours to draw as text')
        sym = {c: SYMBOLS[i] for i, c in enumerate(colours)}
        rows = [''.join(sym[p] if p else '.' for p in row) for row in pixels]
        legend = ' '.join(f'{sym[c]}=#{c[0]:02x}{c[1]:02x}{c[2]:02x}' for c in colours)
        info = pair.info(mt)
        if opts.get('png'):
            img = Image.new('RGBA', (16, 16))
            for y, row in enumerate(pixels):
                for x, p in enumerate(row):
                    img.putpixel((x, y), (*p, 255) if p else (0, 0, 0, 0))
            img = img.resize((16 * 8, 16 * 8), Image.NEAREST)
            out = repo.path(opts['png'])
            import io
            buf = io.BytesIO()
            img.save(buf, 'PNG')
            tx.stage(out.relative_to(repo.root).as_posix(), buf.getvalue())
        return Result(f'meta 0x{mt:03X} {info.get("name", "-")} {pair.behavior_name(mt)} L{pair.layer(mt)}',
                      [f'legend .=transparent {legend}'] + rows)
    raise PkError('PARSE', 'usage: pk meta ls|name|autoname|show <map|primary,secondary> ...; see pk help meta')


def render_png(repo, grid, pair, rect, scale=1, grid_lines=False):
    """RGBA image of the real tiles for a map rect (vision callers and humans).

    grid_lines draws the 15x10 screen grid: a 1px line on every tile boundary and a 2px line
    every 15 columns / 10 rows, so a reviewer can see where the GBA screen falls."""
    import io
    x0, y0, w, h = rect
    img = Image.new('RGBA', (w * 16, h * 16), (0, 0, 0, 255))
    cache = {}
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            mt = grid.at(x, y)[0]
            if mt not in cache:
                tile = Image.new('RGBA', (16, 16), (0, 0, 0, 255))
                if pair.valid(mt):
                    for j, row in enumerate(draw(repo, pair, mt)):
                        for i, p in enumerate(row):
                            if p:
                                tile.putpixel((i, j), (*p, 255))
                cache[mt] = tile
            img.paste(cache[mt], ((x - x0) * 16, (y - y0) * 16))
    if grid_lines:
        from PIL import ImageDraw
        pen = ImageDraw.Draw(img, 'RGBA')
        for gx in range(w + 1):
            major = gx % 15 == 0
            pen.line([(gx * 16, 0), (gx * 16, img.height)],
                     fill=(0, 0, 0, 170) if major else (255, 255, 255, 80), width=2 if major else 1)
        for gy in range(h + 1):
            major = gy % 10 == 0
            pen.line([(0, gy * 16), (img.width, gy * 16)],
                     fill=(0, 0, 0, 170) if major else (255, 255, 255, 80), width=2 if major else 1)
    if scale != 1:
        img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()


ANNOTATION_COLORS = {'SEAM': 'red', 'RARE': 'orange', 'STAIR': 'purple', 'RECT': 'teal', 'EMPTY': 'blue'}


def annotate_png(png, rect, scale, warns):
    """Add absolute-coordinate rulers and one box per visible WARN; return PNG, count, INFO notes."""
    import io
    from PIL import ImageDraw
    x0, y0, w, h = rect
    tile, margin = 16 * scale, 28
    image = Image.open(io.BytesIO(png)).convert('RGB')
    canvas = Image.new('RGB', (image.width + 2 * margin, image.height + 2 * margin), '#eeeeee')
    canvas.paste(image, (margin, margin))
    pen = ImageDraw.Draw(canvas)
    for x in range(x0, x0 + w):
        if x % 5 == 0:
            for y in (3, margin + image.height + 3):
                pen.text((margin + (x - x0) * tile, y), str(x), fill='black')
    for y in range(y0, y0 + h):
        if y % 5 == 0:
            for x in (2, margin + image.width + 2):
                pen.text((x, margin + (y - y0) * tile), str(y), fill='black')
    count, notes = 0, []
    for line in warns:
        kind = line.split()[1]
        point = re.search(r'@(\d+),(\d+)', line)
        span = re.search(r'(\d+),(\d+)-(\d+),(\d+)', line)
        if point:
            xa, ya = map(int, point.groups())
            xb, yb = xa, ya
            if re.search(r'WARN (?:SEAM|RARE) h ', line):
                xb += 1
            elif re.search(r'WARN (?:SEAM|RARE) v ', line):
                yb += 1
        elif span:
            xa, ya, xb, yb = map(int, span.groups())
        else:
            raise PkError('PARSE', f'cannot annotate lint row: {line}')
        xa, xb = sorted((xa, xb))
        ya, yb = sorted((ya, yb))
        if xb < x0 or yb < y0 or xa >= x0 + w or ya >= y0 + h:
            notes.append(f'INFO annotation outside render rect: {line}')
            continue
        box = (margin + (max(xa, x0) - x0) * tile, margin + (max(ya, y0) - y0) * tile,
               margin + (min(xb + 1, x0 + w) - x0) * tile - 1,
               margin + (min(yb + 1, y0 + h) - y0) * tile - 1)
        color = ANNOTATION_COLORS[kind]
        pen.rectangle(box, outline=color, width=2)
        pen.text((box[0] + 2, box[1] + 2), f'@{xa},{ya}', fill=color, stroke_width=1, stroke_fill='white')
        count += 1
    buf = io.BytesIO()
    canvas.save(buf, 'PNG')
    return buf.getvalue(), count, notes


def render_annotated(repo, grid, pair, rect, scale=1, grid_lines=False):
    from . import lint
    warns = [line for line in lint.run(repo, grid.name, show_all=True).data if line.startswith('WARN ')]
    return annotate_png(render_png(repo, grid, pair, rect, scale, grid_lines), rect, scale, warns)


def render_compare(repo, left, right, scale=1, grid_lines=False, gap=1, annotate=False):
    """Two maps' full images side by side (A left, B right) with a `gap`-tile gap at the same
    scale, for before/after review. Returns PNG bytes."""
    import io
    (grid_a, pair_a), (grid_b, pair_b) = left, right
    rect_a, rect_b = (0, 0, grid_a.w, grid_a.h), (0, 0, grid_b.w, grid_b.h)
    tile = 16 * scale
    render = (lambda *args: render_annotated(*args)[0]) if annotate else render_png
    a = Image.open(io.BytesIO(render(repo, grid_a, pair_a, rect_a, scale, grid_lines)))
    b = Image.open(io.BytesIO(render(repo, grid_b, pair_b, rect_b, scale, grid_lines)))
    img = Image.new('RGBA', (a.width + gap * tile + b.width, max(a.height, b.height)), (0, 0, 0, 255))
    img.paste(a, (0, 0))
    img.paste(b, (a.width + gap * tile, 0))
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()
