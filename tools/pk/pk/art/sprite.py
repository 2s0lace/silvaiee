"""pk sprite: overworld NPC sprites drawn as .px and registered by cloning a vanilla template.

The new sprite copies everything from the template (--like GFX): frame size and order, animations,
OAM, shadow and its palette tag. Its pixels must use the template's palette (no new palettes), so the
game and every vanilla sprite sharing that palette stay untouched.

Files touched (inserted next to the template's entries, so #if IS_FRLG context is kept):
  include/constants/event_objects.h                      OBJ_EVENT_GFX_<NAME> before NUM_OBJ_EVENT_GFX
  src/data/object_events/object_event_graphics.h         gObjectEventPic_<Camel> (INCGFX of the png)
  src/data/object_events/object_event_pic_tables.h       sPicTable_<Camel>
  src/data/object_events/object_event_graphics_info.h    gObjectEventGraphicsInfo_<Camel>
  src/data/object_events/object_event_graphics_info_pointers.h  extern + [OBJ_EVENT_GFX_<NAME>]
  graphics/object_events/pics/people/<name>.png           4-bit indexed, template palette
"""
import io
import re

from PIL import Image

from ..core.errors import PkError
from ..core.out import Result
from .compiler import camel, gba
from .ops import SYMBOLS, hexc
from .pixeldsl import parse

CONSTS = 'include/constants/event_objects.h'
GFX = 'src/data/object_events/object_event_graphics.h'
PICS = 'src/data/object_events/object_event_pic_tables.h'
INFO = 'src/data/object_events/object_event_graphics_info.h'
PTRS = 'src/data/object_events/object_event_graphics_info_pointers.h'
MOVEMENT = 'src/event_object_movement.c'
PNG_DIR = 'graphics/object_events/pics/people'
ART = 'tools/pk/art/sprites'


def text(repo, path):
    return repo.read(path).decode('utf-8')


def gfx_const(word):
    word = word.upper()
    return word if word.startswith('OBJ_EVENT_GFX_') else 'OBJ_EVENT_GFX_' + word


class Template:
    """Everything the game needs to know about one vanilla sprite, found by following its symbols."""

    def __init__(self, repo, word):
        ptrs = text(repo, PTRS)
        for const in (gfx_const(word) + '_FRLG', gfx_const(word)):   # FRLG first, like pk ev: WOMAN_1
            self.const = const
            m = re.search(rf'^\s*\[{const}\]\s*=\s*&(gObjectEventGraphicsInfo_\w+),', ptrs, re.M)
            if m:
                break
        if not m:
            raise PkError('NOTFOUND', f'{self.const} has no graphics info; pick a vanilla NPC (pk find --kind gfx)')
        self.info_sym = m[1]
        info = text(repo, INFO)
        m = re.search(rf'^const struct ObjectEventGraphicsInfo {self.info_sym} = \{{\n.*?^\}};\n', info, re.M | re.S)
        if not m:
            raise PkError('PARSE', f'{INFO}: struct {self.info_sym} not found')
        self.info_block = m[0]
        self.pic_table = re.search(r'\.images = (\w+),', self.info_block)[1]
        self.pal_tag = re.search(r'\.paletteTag = (\w+),', self.info_block)[1]
        pics = text(repo, PICS)
        m = re.search(rf'^static const struct SpriteFrameImage {self.pic_table}\[\] = \{{\n.*?^\}};\n', pics, re.M | re.S)
        if not m:
            raise PkError('PARSE', f'{PICS}: {self.pic_table} not found')
        self.pic_block = m[0]
        pic_syms = set(re.findall(r'\b(gObjectEventPic_\w+)\b', self.pic_block))
        if len(pic_syms) != 1:
            raise PkError('LIMIT', f'{self.const} uses {len(pic_syms)} pictures; pick a single-sheet NPC as template')
        self.pic_sym = pic_syms.pop()
        gfx = text(repo, GFX)
        m = re.search(rf'^const (u16|u32) {self.pic_sym}\[\] = INCGFX_U(16|32)\("([^"]+)"(.*)\);\n', gfx, re.M)
        if not m:
            raise PkError('PARSE', f'{GFX}: {self.pic_sym} not found')
        self.pic_line, self.ctype, self.bits, self.png, self.gfx_args = m[0], m[1], m[2], m[3], m[4]
        mov = text(repo, MOVEMENT)
        m = re.search(rf'\{{\s*(gObjectEventPal_\w+),\s*{self.pal_tag}\s*\}}', mov)
        if not m:
            raise PkError('NOTFOUND', f'palette for tag {self.pal_tag} not found in {MOVEMENT}')
        m = re.search(rf'^const u16 {m[1]}\[\] = INCGFX_U16\("([^"]+\.pal)"', gfx, re.M)
        if not m:
            raise PkError('NOTFOUND', f'palette file for tag {self.pal_tag} not found in {GFX}')
        self.pal_path = m[1]
        self.palette = read_pal(repo, self.pal_path)
        im = Image.open(io.BytesIO(repo.read(self.png)))
        self.size = im.size


def read_pal(repo, path):
    lines = text(repo, path).split()
    if lines[:2] != ['JASC-PAL', '0100']:
        raise PkError('PARSE', f'{path}: not a JASC palette')
    n = int(lines[2])
    vals = [int(v) for v in lines[3:3 + 3 * n]]
    return [tuple(vals[i:i + 3]) for i in range(0, 3 * n, 3)][:16]


def export(repo, tx, word, name):
    t = Template(repo, word)
    im = Image.open(io.BytesIO(repo.read(t.png)))
    if im.mode != 'P':
        raise PkError('PARSE', f'{t.png} is not an indexed png')
    w, h = im.size
    px = list(im.tobytes())
    sym = {i: SYMBOLS[i - 1] for i in range(1, len(t.palette))}
    out = [f'# {t.const} ({w}x{h}); frames of {frame_size(t)} left to right; palette {t.pal_tag} (do not add colours)',
           f'size {w}x{h}', 'pal . ' + ' '.join(f'{sym[i]}={hexc(t.palette[i])}' for i in sorted(sym)), 'grid']
    out += [''.join(sym.get(px[y * w + x], '.') for x in range(w)) for y in range(h)] + ['']
    path = f'{ART}/{name}.px'
    tx.stage(path, ('\n'.join(out) + '\n').encode('utf-8'))
    return Result(f'sprite export {t.const} -> {path} {w}x{h} frame {frame_size(t)} palette {t.pal_tag}',
                  [f'edit it, then: pk sprite build {path} NEW_NAME --like {t.const[14:]}'])


def frame_size(t):
    w = re.search(r'\.width = (\d+),', t.info_block)
    h = re.search(r'\.height = (\d+),', t.info_block)
    return f'{w[1]}x{h[1]}' if w and h else '?'


def to_indices(drawing, t):
    """Map the drawing's colours to template palette indices (15-bit compare); '.' -> 0."""
    lookup = {}
    for i, c in enumerate(t.palette[1:], 1):
        lookup.setdefault(gba(c), i)
    if drawing.layers['top'] and any(c for row in drawing.layers['top'] for c in row):
        raise PkError('PARSE', 'sprites have one layer; remove "layer top"')
    out, bad = [], set()
    for row in drawing.layers['bottom']:
        for c in row:
            if c is None:
                out.append(0)
            elif gba(c) in lookup:
                out.append(lookup[gba(c)])
            else:
                bad.add(hexc(c))
    if bad:
        allowed = ' '.join(hexc(c) for c in t.palette[1:])
        raise PkError('LIMIT', f'colours not in palette {t.pal_tag}: {" ".join(sorted(bad)[:8])}; allowed: {allowed}')
    return out


def png_bytes(indices, size, palette):
    im = Image.new('P', size)
    flat = [v for c in palette for v in c] + [0] * (768 - 3 * len(palette))
    im.putpalette(flat)
    im.putdata(indices)
    buf = io.BytesIO()
    im.save(buf, 'PNG')
    return buf.getvalue()


def insert_after(src, anchor, block, path):
    i = src.find(anchor)
    if i < 0:
        raise PkError('PARSE', f'{path}: anchor not found')
    i += len(anchor)
    return src[:i] + block + src[i:]


def build(repo, tx, px_path, name, like, opts):
    if not re.fullmatch(r'[a-z][a-z0-9_]*', name):
        raise PkError('PARSE', 'sprite NAME is lowercase snake_case (becomes OBJ_EVENT_GFX_NAME)')
    t = Template(repo, like)
    const, cam = gfx_const(name), camel(name)
    drawing = parse(repo, px_path)
    if (drawing.w, drawing.h) != t.size:
        raise PkError('BOUNDS', f'{px_path} is {drawing.w}x{drawing.h}; {t.const} sheets are {t.size[0]}x{t.size[1]}'
                                f' (start from pk sprite export {t.const[14:]} {name})')
    png_path = f'{PNG_DIR}/{name}.png'
    tx.stage(png_path, png_bytes(to_indices(drawing, t), t.size, t.palette))
    consts = text(repo, CONSTS)
    new = not re.search(rf'^\s*{const},', consts, re.M)
    if new:
        if re.search(rf'\b{const}\b', consts):
            raise PkError('CONFLICT', f'{const} already exists')
        m = re.search(r'^(\s*)NUM_OBJ_EVENT_GFX,', consts, re.M)
        consts = consts[:m.start()] + f'{m[1]}{const},\n' + consts[m.start():]
        tx.stage(CONSTS, consts.encode('utf-8'))
        pic_sym, info_sym, table = f'gObjectEventPic_{cam}', f'gObjectEventGraphicsInfo_{cam}', f'sPicTable_{cam}'
        tx.stage(GFX, insert_after(text(repo, GFX), t.pic_line,
                                   f'const {t.ctype} {pic_sym}[] = INCGFX_U{t.bits}("{png_path}"{t.gfx_args});\n', GFX).encode('utf-8'))
        pic_block = t.pic_block.replace(t.pic_table, table).replace(t.pic_sym, pic_sym)
        tx.stage(PICS, insert_after(text(repo, PICS), t.pic_block, '\n' + pic_block, PICS).encode('utf-8'))
        info_block = t.info_block.replace(t.info_sym, info_sym).replace(t.pic_table, table)
        tx.stage(INFO, insert_after(text(repo, INFO), t.info_block, '\n' + info_block, INFO).encode('utf-8'))
        ptrs = text(repo, PTRS)
        ext = f'extern const struct ObjectEventGraphicsInfo {t.info_sym};\n'
        ptrs = insert_after(ptrs, ext, f'extern const struct ObjectEventGraphicsInfo {info_sym};\n', PTRS)
        m = re.search(rf'^(\s*)\[{t.const}\]\s*=\s*&{t.info_sym},\n', ptrs, re.M)
        ptrs = ptrs[:m.end()] + f'{m[1]}[{const}] = &{info_sym},\n' + ptrs[m.end():]
        tx.stage(PTRS, ptrs.encode('utf-8'))
    verb = 'registered' if new else 'png updated (already registered)'
    return Result(f'sprite {const} {verb} like {t.const} {t.size[0]}x{t.size[1]} palette {t.pal_tag}',
                  [f'use it: pk ev add MAP obj @x,y gfx={name.upper()} ... ; then pk build'])


def command(repo, tx, args, opts):
    verb, rest = (args[0], args[1:]) if args else ('', [])
    if verb == 'export' and len(rest) == 2:
        return export(repo, tx, rest[0], rest[1])
    if verb == 'build' and len(rest) == 2 and opts.get('like'):
        return build(repo, tx, rest[0], rest[1], opts['like'], opts)
    if verb == 'show' and len(rest) == 1:
        t = Template(repo, rest[0])
        return Result(f'sprite {t.const} {t.size[0]}x{t.size[1]} frame {frame_size(t)} palette {t.pal_tag}',
                      [f'png {t.png}', 'colours ' + ' '.join(hexc(c) for c in t.palette[1:])])
    raise PkError('PARSE', 'usage: pk sprite show GFX | export GFX name | build FILE.px name --like GFX')
