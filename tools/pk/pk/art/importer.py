"""pk px import: turn an external image (e.g. Gemini-generated) into a .px drawing.

AI images are not game art: they are large, anti-aliased and use hundreds of colours. Import
  1. finds the pixel grid (--grid N, or auto: the largest N where N x N blocks are near-uniform),
  2. reduces to the target size (--size WxH, multiples of 16) by taking each block's dominant colour,
  3. maps colours to a palette: --palette primary (General_Frlg slots, costs no palette budget,
     default) | keep (median-cut to --colors N) | a .px file with pal lines,
  4. treats alpha < 128 (or --key #rrggbb) as transparent,
  5. writes a .px (layer bottom, or --layer top).
The result is a starting point: always review it with pk px render / tileset lint / preview.
"""
import io
import re

from PIL import Image

from ..core.errors import PkError
from ..core.out import Result
from ..maps.meta import pal_file
from ..maps.tiles import tileset
from .compiler import gba
from .pixeldsl import parse_palette, colour

SYMBOLS = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@$%&*+=<>~^'


def detect_grid(img, limit=64):
    """Largest block size whose blocks are mostly a single colour (nearest-neighbour upscales)."""
    rgb = img.convert('RGB')
    w, h = rgb.size
    px = rgb.load()
    best = 1
    for n in range(2, min(limit, w, h) + 1):
        if w % n and h % n:
            continue
        samples = total = 0
        for by in range(0, h - n + 1, max(n, h // 24)):
            for bx in range(0, w - n + 1, max(n, w // 24)):
                c = px[bx + n // 2, by + n // 2]
                same = sum(1 for dy in (0, n - 1) for dx in (0, n - 1) if _close(px[bx + dx, by + dy], c))
                samples += same
                total += 4
        if total and samples / total >= 0.85:
            best = n
    return best


def _close(a, b, tol=24):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def target_palette(repo, mode, folder):
    if mode == 'primary':
        ts = tileset(repo, 'gTileset_General_Frlg')
        colours = []
        for p in ts['palettes'][:7]:
            colours += [gba(c) for c in pal_file(repo.read(p))[1:]]
        return sorted({c for c in colours if c != (0xF8, 0, 0xF8)})          # drop the magenta placeholder
    if mode.endswith('.px'):
        return sorted(set(parse_palette(repo, mode if '/' in mode else f'{folder}/{mode}').values()))
    return None


def nearest(c, palette):
    return min(palette, key=lambda p: 2 * (p[0] - c[0]) ** 2 + 4 * (p[1] - c[1]) ** 2 + 3 * (p[2] - c[2]) ** 2)


def run(repo, tx, src, out, opts):
    try:
        img = Image.open(io.BytesIO(repo.read(src))).convert('RGBA')
    except PkError:
        raise
    except OSError as exc:
        raise PkError('PARSE', f'{src}: not an image ({exc})') from None
    if opts.get('crop'):
        c = re.fullmatch(r'(\d+),(\d+),(\d+)x(\d+)', opts['crop'])
        if not c:
            raise PkError('PARSE', '--crop x,y,WxH in source-image pixels')
        x, y, w, h = map(int, c.groups())
        img = img.crop((x, y, x + w, y + h))
    m = re.fullmatch(r'(\d+)x(\d+)', opts.get('size', ''))
    if not m:
        raise PkError('PARSE', 'pk px import IMAGE OUT.px --size WxH [--grid N] [--palette primary|keep|pal.px] '
                               '[--colors N] [--key #rrggbb] [--tolerance 24] [--crop x,y,WxH] [--layer bottom|top]')
    W, H = int(m[1]), int(m[2])
    if W % 16 or H % 16:
        raise PkError('PARSE', '--size must be multiples of 16 (metatiles)')
    grid = int(opts['grid']) if opts.get('grid') else 1
    if grid > 1:
        img = img.resize((img.width // grid, img.height // grid), Image.NEAREST)
    key = colour(opts['key']) if opts.get('key') else None
    tol = int(opts.get('tolerance', 24))
    mode = opts.get('palette', 'primary')
    folder = out.rsplit('/', 1)[0]
    palette = target_palette(repo, mode, folder)
    if palette is None:                                   # keep: median cut to N colours
        n = int(opts.get('colors', 12))
        q = img.convert('RGB').resize((W * 4, H * 4), Image.Resampling.BOX).quantize(colors=n, method=Image.Quantize.MEDIANCUT)
        pal = q.getpalette()[:3 * n]
        palette = sorted({gba(tuple(pal[i:i + 3])) for i in range(0, len(pal), 3)})
    # Majority vote per target pixel: every source pixel of the cell is mapped to the palette first,
    # then the most common result wins. Robust to JPEG noise and anti-aliased edges, unlike averaging.
    cache, pix = {}, img.load()
    def classify(p):
        r, g, b, a = p
        if a < 128 or (key and _close((r, g, b), key, tol)):
            return None
        k = (r >> 2, g >> 2, b >> 2)
        if k not in cache:
            cache[k] = nearest(gba((r, g, b)), palette)
        return cache[k]
    sw, sh = img.size
    mapped = []
    for y in range(H):
        row = []
        y0, y1 = y * sh // H, max((y + 1) * sh // H, y * sh // H + 1)
        for x in range(W):
            x0, x1 = x * sw // W, max((x + 1) * sw // W, x * sw // W + 1)
            # skip a 15% border of each cell: edges of upscaled pixels carry the anti-aliasing
            mx, my = (x1 - x0) * 15 // 100, (y1 - y0) * 15 // 100
            step = max(1, (x1 - x0) // 8)
            votes = {}
            for sy in range(y0 + my, y1 - my, step):
                for sx in range(x0 + mx, x1 - mx, step):
                    c = classify(pix[sx, sy])
                    votes[c] = votes.get(c, 0) + 1
            row.append(max(votes, key=votes.get) if votes else classify(pix[x0, y0]))
        mapped.append(row)
    used = sorted({c for row in mapped for c in row if c})
    if len(used) > len(SYMBOLS):
        raise PkError('LIMIT', f'{len(used)} colours after mapping; use --palette keep --colors 12')
    sym = {c: SYMBOLS[i] for i, c in enumerate(used)}
    layer = opts.get('layer', 'bottom')
    lines = [f'# imported from {src} (grid {grid}, palette {mode}) by pk px import; review before use',
             f'size {W}x{H}', 'pal . ' + ' '.join(f'{sym[c]}=#{c[0]:02x}{c[1]:02x}{c[2]:02x}' for c in used),
             f'layer {layer}', 'grid'] + [''.join(sym[c] if c else '.' for c in row) for row in mapped]
    tx.stage(out, ('\n'.join(lines) + '\n').encode('utf-8'))
    worst = 0
    for ty in range(0, H, 8):
        for tx_ in range(0, W, 8):
            worst = max(worst, len({mapped[ty + j][tx_ + i] for j in range(8) for i in range(8)} - {None}))
    return Result(f'px import {src} -> {out} {W}x{H} grid:{grid} colours:{len(used)} max_per_8x8:{worst}',
                  ['next: pk px render ' + out + ' --scale 6, fix by hand, then tileset lint/preview'] +
                  (['WARN more than 15 colours in an 8x8 tile; use --palette keep --colors 10'] if worst > 15 else []))
