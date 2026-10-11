"""Pixel DSL (.px): text pixel art for LLM authors (SPEC §7.1).

    # comment
    size 32x16                 multiple of 16 (metatile grid)
    use palette.px             shared palette lines from another file in the same folder
    pal . a=#183018 b=#285830  '.' is always transparent; letters/digits map to colours
    from other.px              start from another drawing (then modify)
    layer bottom|top           following ops draw on that layer (default bottom)
    fill c
    rect x,y WxH c
    hline x,y len c | vline x,y len c | dot x,y c
    noise c,d 0.15 [seed=3]    recolour pixels that are c to d with probability p
    dither b,c x,y WxH         checkerboard of b and c
    outline b                  paint b around every non-transparent pixel of the layer
    mirror x|y [WxH]           mirror the left half onto the right (x) or top onto bottom (y)
    copy x,y WxH -> x2,y2
    recolor c=d e=f            whole layer
    grid [x,y]                 following rows are pixels; ' ' or '?' keep, '.' transparent; ends at blank line
"""
import random
import re

from ..core.errors import PkError

TRANSPARENT = None


def colour(text):
    m = re.fullmatch(r'#?([0-9A-Fa-f]{6})', text)
    if not m:
        raise PkError('PARSE', f'colour {text!r} must be #rrggbb')
    v = int(m[1], 16)
    return (v >> 16 & 255, v >> 8 & 255, v & 255)


class Drawing:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.layers = {'bottom': [[None] * w for _ in range(h)], 'top': [[None] * w for _ in range(h)]}

    def copy(self):
        d = Drawing(self.w, self.h)
        d.layers = {k: [row[:] for row in v] for k, v in self.layers.items()}
        return d


def parse(repo, path, seen=None):
    """Render a .px file to a Drawing whose pixels are RGB tuples or None."""
    seen = set() if seen is None else seen
    if path in seen:
        raise PkError('PARSE', f'{path}: circular from/use')
    seen = seen | {path}
    folder = path.rsplit('/', 1)[0]
    lines = repo.read(path).decode('utf-8').splitlines()
    pal = {'.': None}
    drawing, layer = None, 'bottom'
    i = 0

    def need():
        if drawing is None:
            raise PkError('PARSE', f'{path}:{i+1}: size or from must come first')
        return drawing.layers[layer]

    def sym(s):
        if s not in pal:
            raise PkError('PARSE', f'{path}:{i+1}: colour symbol {s!r} not in palette')
        return pal[s]

    def xy(text):
        m = re.fullmatch(r'(\d+),(\d+)', text)
        if not m:
            raise PkError('PARSE', f'{path}:{i+1}: expected x,y got {text!r}')
        return int(m[1]), int(m[2])

    def wh(text):
        m = re.fullmatch(r'(\d+)x(\d+)', text)
        if not m:
            raise PkError('PARSE', f'{path}:{i+1}: expected WxH got {text!r}')
        return int(m[1]), int(m[2])

    def put(px, x, y, value):
        if 0 <= x < drawing.w and 0 <= y < drawing.h:
            px[y][x] = value

    while i < len(lines):
        raw = lines[i]
        line = raw.split('#', 1)[0].strip() if not raw.lstrip().startswith('pal') else re.sub(r'\s+#\s.*$', '', raw).strip()
        words = line.split()
        if not words:
            i += 1
            continue
        op, args = words[0], words[1:]
        if op == 'size':
            w, h = wh(args[0])
            if w % 16 or h % 16:
                raise PkError('PARSE', f'{path}: size must be a multiple of 16 (metatiles)')
            drawing = Drawing(w, h)
        elif op == 'use':
            other = parse_palette(repo, f'{folder}/{args[0]}')
            pal.update(other)
        elif op == 'pal':
            for item in args:
                if item == '.':
                    continue
                m = re.fullmatch(r'(\S)=(#?[0-9A-Fa-f]{6})', item)
                if not m or m[1] == '.':
                    raise PkError('PARSE', f'{path}:{i+1}: palette entries look like a=#183018')
                pal[m[1]] = colour(m[2])
        elif op == 'from':
            base = parse(repo, f'{folder}/{args[0]}', seen)
            drawing = base.copy()
        elif op == 'layer':
            if args[:1] not in (['bottom'], ['top']):
                raise PkError('PARSE', f'{path}:{i+1}: layer bottom|top')
            layer = args[0]
        elif op == 'fill':
            px = need()
            for row in px:
                row[:] = [sym(args[0])] * drawing.w
        elif op == 'rect':
            px = need()
            (x, y), (w, h) = xy(args[0]), wh(args[1])
            for j in range(y, y + h):
                for k in range(x, x + w):
                    put(px, k, j, sym(args[2]))
        elif op in ('hline', 'vline'):
            px = need()
            (x, y), n = xy(args[0]), int(args[1])
            for k in range(n):
                put(px, x + k if op == 'hline' else x, y if op == 'hline' else y + k, sym(args[2]))
        elif op == 'dot':
            px = need()
            x, y = xy(args[0])
            put(px, x, y, sym(args[1]))
        elif op == 'noise':
            px = need()
            a, b = args[0].split(',')
            p = float(args[1])
            seed = int(args[2].split('=')[1]) if len(args) > 2 else 0
            rng = random.Random(seed)
            ca, cb = sym(a), sym(b)
            for row in px:
                for k, v in enumerate(row):
                    if v == ca and rng.random() < p:
                        row[k] = cb
        elif op == 'dither':
            px = need()
            a, b = (sym(s) for s in args[0].split(','))
            (x, y), (w, h) = xy(args[1]), wh(args[2])
            for j in range(y, y + h):
                for k in range(x, x + w):
                    put(px, k, j, a if (j + k) % 2 == 0 else b)
        elif op == 'outline':
            px = need()
            c = sym(args[0])
            solid = {(k, j) for j in range(drawing.h) for k in range(drawing.w) if px[j][k] is not None}
            for (k, j) in solid:
                for dk, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    n = (k + dk, j + dj)
                    if n not in solid and 0 <= n[0] < drawing.w and 0 <= n[1] < drawing.h:
                        px[n[1]][n[0]] = c
        elif op == 'mirror':
            px = need()
            if args[0] == 'x':
                for row in px:
                    for k in range(drawing.w // 2):
                        row[drawing.w - 1 - k] = row[k]
            elif args[0] == 'y':
                for j in range(drawing.h // 2):
                    px[drawing.h - 1 - j] = px[j][:]
            else:
                raise PkError('PARSE', f'{path}:{i+1}: mirror x|y')
        elif op == 'copy':
            px = need()
            if len(args) != 4 or args[2] != '->':
                raise PkError('PARSE', f'{path}:{i+1}: copy x,y WxH -> x2,y2')
            (x, y), (w, h), (tx, ty) = xy(args[0]), wh(args[1]), xy(args[3])
            block = [[px[y + j][x + k] for k in range(w)] for j in range(h)]
            for j in range(h):
                for k in range(w):
                    put(px, tx + k, ty + j, block[j][k])
        elif op == 'recolor':
            px = need()
            swaps = {}
            for item in args:
                a, b = item.split('=')
                swaps[sym(a)] = sym(b)
            for row in px:
                row[:] = [swaps.get(v, v) for v in row]
        elif op == 'grid':
            px = need()
            x0, y0 = xy(args[0]) if args else (0, 0)
            j = 0
            i += 1
            while i < len(lines) and lines[i].strip():
                for k, s in enumerate(lines[i].rstrip()):
                    if s in ' ?':
                        continue
                    put(px, x0 + k, y0 + j, sym(s))
                j += 1
                i += 1
            continue
        else:
            raise PkError('PARSE', f'{path}:{i+1}: unknown op {op!r}; see pk help px')
        i += 1
    if drawing is None:
        raise PkError('PARSE', f'{path}: empty drawing (needs size)')
    drawing.palette = pal
    return drawing


def parse_palette(repo, path):
    pal = {}
    for n, raw in enumerate(repo.read(path).decode('utf-8').splitlines(), 1):
        words = raw.split()
        if not words or words[0].startswith('#'):
            continue
        if words[0] != 'pal':
            raise PkError('PARSE', f'{path}:{n}: palette files contain only pal lines')
        for item in words[1:]:
            if item == '.':
                continue
            m = re.fullmatch(r'(\S)=(#?[0-9A-Fa-f]{6})', item)
            if not m:
                raise PkError('PARSE', f'{path}:{n}: bad palette entry {item}')
            pal[m[1]] = colour(m[2])
    return pal


def crop(drawing, layer, x, y, w=16, h=16):
    px = drawing.layers[layer]
    if x + w > drawing.w or y + h > drawing.h:
        raise PkError('BOUNDS', f'region {x},{y} {w}x{h} outside drawing {drawing.w}x{drawing.h}')
    return [row[x:x + w] for row in px[y:y + h]]
