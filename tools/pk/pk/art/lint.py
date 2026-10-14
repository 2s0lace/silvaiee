"""pk tileset lint / preview: catch the mistakes a reviewer would otherwise send back.

lint (also run by every tileset build) checks the NEW metatiles of a .pkt (lines without attr=,
i.e. not copied by `pk tileset export`):
  - near-copies of vanilla 8x8 tiles (75-99% identical): copy exactly or change clearly; if the
    vanilla tile is ANIMATED (water, shores, flowers) a non-exact copy will never animate in game;
  - transparent pixels in the bottom layer (the backdrop colour shows through in game);
  - pure black / pure white, flat single-colour 8x8 tiles, colour counts outside 4..12 per metatile.
preview renders each new drawing on plain ground, sand and water of the paired tilesets.
"""
import io
import re
import struct

from PIL import Image

from ..core.errors import PkError
from ..core.out import Result
from ..maps.meta import pal_file
from ..maps.tiles import tileset, LIMITS
from .compiler import Spec, gba
from .pixeldsl import parse, crop

NEAR = 0.75


def animated_ranges(repo, symbol):
    """[(first_tile, count)] animated for a tileset: follow TilesetAnim_<Base>() to the
    QueueAnimTiles_* functions it calls (their names do not always contain <Base>)."""
    base = symbol[len('gTileset_'):]
    text = repo.read('src/tileset_anims.c').decode('utf-8', 'replace')
    m = re.search(r'static void TilesetAnim_%s\([^)]*\)\s*\{(.*?)\n\}' % re.escape(base), text, re.S)
    if not m:
        return []
    out = []
    for fn in sorted(set(re.findall(r'(QueueAnimTiles_\w+)\(', m[1]))):
        body = re.search(r'static void %s\([^)]*\)\s*\{(.*?)\n\}' % re.escape(fn), text, re.S)
        if not body:
            continue
        for t in re.finditer(r'TILE_OFFSET_4BPP\((NUM_TILES_IN_PRIMARY(?:_FRLG)?\s*\+\s*)?(\d+)\)\),\s*(\d+)\s*\*\s*TILE_SIZE_4BPP', body[1]):
            first = int(t[2]) + (LIMITS['frlg']['tiles'] if t[1] else 0)
            out.append((first, int(t[3])))
    return out


class Vanilla:
    """Every 8x8 RGB tile used by vanilla metatiles of a tileset pair, with tile ids."""
    def __init__(self, repo, primary, secondary, version):
        # secondary=None: only the primary (from disk); exported vanilla secondary metatiles are
        # added from the .pkt itself (see lint), so a rebuilt tileset never compares to itself.
        lim = LIMITS[version]
        self.tiles = {}            # rgb tile -> (tile id, metatile id)
        self.animated = set()
        sheets, pals = {}, {}
        sources = [(primary, 0)] + ([(secondary, lim['tiles'])] if secondary else [])
        for sym, base in sources:
            ts = tileset(repo, sym)
            img = Image.open(io.BytesIO(repo.read(ts['tiles'])))
            sheets[base] = (img.width // 8, list(getattr(img, 'get_flattened_data', img.getdata)()), img.width)
            for first, count in animated_ranges(repo, sym):
                self.animated |= set(range(first, first + count))
        p_ts = tileset(repo, primary)
        s_ts = tileset(repo, secondary) if secondary else p_ts
        for slot in range(16):
            src = p_ts if slot < lim['pals'] else s_ts
            if slot < len(src['palettes']):
                pals[slot] = [gba(c) for c in pal_file(repo.read(src['palettes'][slot]))]

        def tile(word):
            t, hf, vf, slot = word & 0x3FF, word >> 10 & 1, word >> 11 & 1, word >> 12
            base = 0 if t < lim['tiles'] else lim['tiles']
            if base not in sheets:
                return None
            cols, data, width = sheets[base]
            n = t - base
            x0, y0 = (n % cols) * 8, (n // cols) * 8
            if y0 + 8 > len(data) // width or slot not in pals:
                return None
            rows = []
            for j in range(8):
                y = y0 + (7 - j if vf else j)
                row = []
                for i in range(8):
                    x = x0 + (7 - i if hf else i)
                    v = data[y * width + x]
                    row.append(pals[slot][v] if v else None)
                rows.append(tuple(row))
            return tuple(rows)
        self.anim_colours = set()
        anim_px, static_px = {}, {}
        for sym, meta_base in [(primary, 0)] + ([(secondary, lim['metatiles'])] if secondary else []):
            ts = tileset(repo, sym)
            raw = repo.read(ts['metatiles'])
            for k in range(len(raw) // 16):
                for word in struct.unpack('<8H', raw[k * 16:k * 16 + 16]):
                    rgb = tile(word)
                    if rgb and any(c for row in rgb for c in row):
                        self.tiles.setdefault(rgb, (word & 0x3FF, meta_base + k))
                        bucket = anim_px if (word & 0x3FF) in self.animated else static_px
                        for row in rgb:
                            for c in row:
                                if c:
                                    bucket[c] = bucket.get(c, 0) + 1
        # Colours of ANIMATED tiles used by water-behaviour metatiles (the sea): a new tile painted
        # mostly in these colours is hand-drawn water that will not animate.
        from ..maps.tiles import behaviors
        names = behaviors(repo, version)
        self.anim_colours = set()
        for sym, meta_base in [(primary, 0)]:
            ts = tileset(repo, sym)
            raw = repo.read(ts['metatiles'])
            attrs = repo.read(ts['attributes']) if ts['attributes'] else b''
            size = 4 if version == 'frlg' else 2
            for k in range(len(raw) // 16):
                if (k + 1) * size > len(attrs):
                    break
                value = int.from_bytes(attrs[k * size:(k + 1) * size], 'little') & (0x1FF if version == 'frlg' else 0xFF)
                if not re.search(r'OCEAN|POND|WATER', names.get(value, '')):
                    continue
                for word in struct.unpack('<8H', raw[k * 16:k * 16 + 16]):
                    if (word & 0x3FF) in self.animated:
                        rgb = tile(word)
                        if rgb:
                            self.anim_colours |= {c for row in rgb for c in row if c}
        # Shore tiles also carry grass/sand/white; keep only colours mostly drawn by animated tiles.
        self.anim_colours = {c for c in self.anim_colours
                             if anim_px.get(c, 0) >= 0.5 * (anim_px.get(c, 0) + static_px.get(c, 0))}

    def nearest(self, q):
        if q in self.tiles:
            return 1.0, self.tiles[q]
        best, where = 0, None
        flat = [c for row in q for c in row]
        colours = set(flat)
        for t, info in self.tiles.items():
            other = [c for row in t for c in row]
            if not colours & set(other):
                continue
            same = sum(a == b for a, b in zip(flat, other))
            if same > best:
                best, where = same, info
        return best / 64, where


def _symbols(repo):
    from ..maps.tiles import _registry
    return _registry(repo)


def new_entries(spec):
    return [e for e in spec.entries if e.get('attr') is None]


def quads(px):
    return [tuple(tuple(px[qy + j][qx + i] for i in range(8)) for j in range(8))
            for qy, qx in ((0, 0), (0, 8), (8, 0), (8, 8))]


def lint(repo, spec_path):
    spec = Spec(repo, spec_path)
    pair_with = spec.meta.get('pair_with')
    if spec.meta['kind'] == 'secondary' and not pair_with:
        raise PkError('PARSE', f'{spec_path}: lint needs pair_with for a secondary tileset')
    from ..core.repo import Repo
    disk = Repo(repo.root)
    if spec.meta['kind'] == 'secondary':
        vanilla = Vanilla(disk, pair_with, None, spec.meta['version'])
    else:
        vanilla = Vanilla(disk, spec.symbol, None, spec.meta['version']) if spec.symbol in _symbols(disk) else None
    warnings, drawings = [], {}
    # Exported vanilla metatiles of this tileset (lines with attr=) are part of the reference.
    for e in spec.entries:
        if e.get('attr') is None or vanilla is None:
            continue
        if e['file'] not in drawings:
            drawings[e['file']] = parse(repo, f"{spec.folder}/{e['file']}")
        for layer in ('bottom', 'top'):
            px = [[gba(c) for c in row] for row in crop(drawings[e['file']], layer, e['x'], e['y'])]
            for q in quads(px):
                if any(c for row in q for c in row):
                    vanilla.tiles.setdefault(q, (None, -1))
    for e in new_entries(spec):
        if e['file'] not in drawings:
            drawings[e['file']] = parse(repo, f"{spec.folder}/{e['file']}")
        d = drawings[e['file']]
        name = e['name']
        bottom = [[gba(c) for c in row] for row in crop(d, 'bottom', e['x'], e['y'])]
        top = [[gba(c) for c in row] for row in crop(d, 'top', e['x'], e['y'])]
        holes = sum(1 for row in bottom for c in row if c is None)
        if holes:
            warnings.append(f'{name}: bottom layer has {holes} transparent px (backdrop shows in game); fill it with ground/water')
        colours = {c for layer in (bottom, top) for row in layer for c in row if c}
        if not 4 <= len(colours) <= 12:
            warnings.append(f'{name}: {len(colours)} colours (vanilla median 7; target 6-9)')
        if {(0, 0, 0), (248, 248, 248)} & colours:
            warnings.append(f'{name}: pure black/white pixels; use the darkest/lightest tinted ramp colour')
        for layer_name, layer in (('bottom', bottom), ('top', top)):
            for n, q in enumerate(quads(layer)):
                cs = {c for row in q for c in row}
                if cs == {None}:
                    continue
                ratio, info = vanilla.nearest(q) if vanilla else (0, None)
                if ratio == 1.0:
                    continue
                if len(cs - {None}) == 1 and None not in cs:
                    warnings.append(f'{name} {layer_name} q{n}: flat single-colour 8x8; add texture like vanilla')
                if ratio >= NEAR and info:
                    tile_id, meta = info
                    anim = (' ANIMATED in game: copy it pixel-exact or it will stand still'
                            if tile_id is not None and tile_id in vanilla.animated else '')
                    where = f'tile {tile_id} (metatile 0x{meta:03X})' if tile_id is not None else 'an exported vanilla tile'
                    warnings.append(f'{name} {layer_name} q{n}: {round(ratio * 100)}% like vanilla {where}; '
                                    f'copy exactly or change clearly.{anim}')
                elif vanilla and sum(1 for row in q for c in row if c in vanilla.anim_colours) >= 12:
                    warnings.append(f'{name} {layer_name} q{n}: hand-drawn sea water (colours of the ANIMATED sea); '
                                    f'put a pixel-exact copy of the vanilla water tile in the bottom layer and draw '
                                    f'posts/shadows on the top layer, or it will not animate')
                elif info and info[0] is not None and info[0] in vanilla.animated and ratio >= 0.5:
                    warnings.append(f'{name} {layer_name} q{n}: resembles animated vanilla tile {info[0]} '
                                    f'(metatile 0x{info[1]:03X}); custom copies of water/shore/flowers do not animate')
    return spec, warnings


def preview(repo, tx, spec_path, opts):
    from ..maps.tiles import Pair
    from ..maps.meta import draw
    from ..maps.terrain import Rules
    spec = Spec(repo, spec_path)
    primary = spec.meta.get('pair_with') or spec.symbol
    layout = dict(id='preview', primary_tileset=primary, secondary_tileset=spec.symbol, layout_version=spec.meta['version'])
    try:
        pair = Pair(repo, layout)
        rules = Rules(repo, pair)
    except PkError:
        raise PkError('NOTFOUND', f'{spec.symbol} is not built yet; run pk tileset build first (or --dry-run fails too)')
    backgrounds = []
    for mat in ('ground', 'sand', 'water'):
        ids = rules.by_mat.get(mat)
        if ids:
            backgrounds.append((mat, draw(repo, pair, max(ids, key=lambda m: rules.freq.get(m, 0)))))
    files = list(dict.fromkeys(e['file'] for e in new_entries(spec)))
    if not files:
        raise PkError('NOTFOUND', f'{spec_path}: no new metatiles (lines without attr=)')
    pieces = [parse(repo, f'{spec.folder}/{f}') for f in files]
    cell_w = max(d.w for d in pieces) + 32
    cell_h = max(d.h for d in pieces) + 32
    sheet = Image.new('RGB', (cell_w * len(backgrounds), cell_h * len(pieces)), (0, 0, 0))
    for r, d in enumerate(pieces):
        for c, (mat, bg) in enumerate(backgrounds):
            x0, y0 = c * cell_w, r * cell_h
            for y in range(cell_h):
                for x in range(cell_w):
                    p = bg[y % 16][x % 16]
                    sheet.putpixel((x0 + x, y0 + y), p or (0, 0, 0))
            for layer in ('bottom', 'top'):
                for y, row in enumerate(d.layers[layer]):
                    for x, p in enumerate(row):
                        if p:
                            sheet.putpixel((x0 + 16 + x, y0 + 16 + y), gba(p))
    scale = int(opts.get('scale', 3))
    sheet = sheet.resize((sheet.width * scale, sheet.height * scale), Image.NEAREST)
    buf = io.BytesIO()
    sheet.save(buf, 'PNG')
    out = opts.get('out') or f'.pk/render/{spec.name}_preview.png'
    tx.stage(repo.path(out).relative_to(repo.root).as_posix(), buf.getvalue())
    return Result(f'tileset preview -> {out}',
                  [f'rows: {" ".join(files)}', f'columns: {" ".join(m for m, _ in backgrounds)}'])
