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
    """Name metatiles from behaviours and vanilla usage; never replaces a manual name."""
    stats = usage(repo, pair)
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
        counters[label] = counters.get(label, 0) + 1
        names[key] = dict(name=f'{label}_{counters[label]}', tags=[label], auto=True)
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


def render_png(repo, grid, pair, rect, scale=1):
    """RGBA image of the real tiles for a map rect (vision callers and humans)."""
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
    if scale != 1:
        img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()
