"""Tileset compiler (SPEC §7.2-7.4): .pkt spec + .px drawings -> a registered game tileset.

.pkt format (one directive or metatile per line, # comments):
    name forest                      -> gTileset_Forest, data/tilesets/<kind>/forest/
    kind secondary|primary
    version frlg|emerald
    pair_with gTileset_General_Frlg  (secondary only; used for checks and previews)
    # metatile: NAME SOURCE[@x,y] BEHAVIOR LAYER [tags=a,b] [sym=X]
    grass ground.px@0,0 MB_FRLG_NORMAL NORMAL tags=ground sym=.
SOURCE is a .px file in the same folder; @x,y picks a 16x16 cell (pixels). Both of its layers are
used: 'bottom' -> first 4 tile slots, 'top' -> last 4 (as in vanilla metatiles.bin).
attr=0x... gives the raw attribute word instead of BEHAVIOR/LAYER (pk tileset export uses it).
LAYER: NORMAL | COVERED | SPLIT. BEHAVIOR: full MB_* name or short (NORMAL, TALL_GRASS ...).
"""
import io
import json
import re
import struct

from PIL import Image

from ..core.errors import PkError
from ..core.out import Result
from ..maps.tiles import LIMITS, TOTAL_TILES, TOTAL_METATILES, TOTAL_PALS, behaviors
from .pixeldsl import parse, crop

LAYER_TYPES = {'NORMAL': 0, 'COVERED': 1, 'SPLIT': 2}
IDS_DIR = 'tools/pk/data/tilesets'


def gba(c):
    return None if c is None else (c[0] & 0xF8, c[1] & 0xF8, c[2] & 0xF8)


def camel(name):
    return ''.join(p.capitalize() for p in re.split(r'[_\W]+', name) if p)


class Spec:
    def __init__(self, repo, path):
        self.path = path
        self.folder = path.rsplit('/', 1)[0]
        self.meta = dict(kind='secondary', version='frlg')
        self.entries = []
        for n, raw in enumerate(repo.read(path).decode('utf-8').splitlines(), 1):
            line = raw.split('#', 1)[0].strip()
            if not line:
                continue
            words = line.split()
            if words[0] in ('name', 'kind', 'version', 'pair_with') and len(words) == 2:
                self.meta[words[0]] = words[1]
                continue
            if len(words) < 4:
                raise PkError('PARSE', f'{path}:{n}: metatile line is NAME SOURCE[@x,y] BEHAVIOR LAYER [tags=..] [sym=X]')
            name, source, behavior, layer, *extra = words
            if not re.fullmatch(r'[a-z][a-z0-9_]*', name):
                raise PkError('PARSE', f'{path}:{n}: metatile name {name!r} must be snake_case')
            if any(e['name'] == name for e in self.entries):
                raise PkError('CONFLICT', f'{path}:{n}: duplicate metatile {name}')
            m = re.fullmatch(r'([\w.\-]+\.px)(?:@(\d+),(\d+))?', source)
            if not m:
                raise PkError('PARSE', f'{path}:{n}: source is file.px or file.px@x,y')
            if layer not in LAYER_TYPES:
                raise PkError('PARSE', f'{path}:{n}: layer NORMAL|COVERED|SPLIT')
            opts = dict(e.split('=', 1) for e in extra if '=' in e)
            self.entries.append(dict(name=name, file=m[1], x=int(m[2] or 0), y=int(m[3] or 0),
                                     behavior=behavior, layer=layer, line=n,
                                     tags=[t for t in opts.get('tags', '').split(',') if t], sym=opts.get('sym'),
                                     attr=int(opts['attr'], 0) if 'attr' in opts else None))
        if 'name' not in self.meta:
            raise PkError('PARSE', f'{path}: missing "name <tileset>"')
        if self.meta['kind'] not in ('primary', 'secondary') or self.meta['version'] not in LIMITS:
            raise PkError('PARSE', f'{path}: kind primary|secondary, version frlg|emerald')
        if not self.entries:
            raise PkError('PARSE', f'{path}: no metatiles')
        self.name = self.meta['name']
        self.symbol = 'gTileset_' + camel(self.name)
        suffix = '_frlg' if self.meta['version'] == 'frlg' and not self.name.endswith('_frlg') else ''
        self.dir = f"data/tilesets/{self.meta['kind']}/{self.name}{suffix}"


def behavior_value(repo, version, word, where):
    table = {v: k for k, v in behaviors(repo, version).items()}
    for candidate in (word, f'MB_FRLG_{word}' if version == 'frlg' else f'MB_{word}'):
        if candidate in table:
            return table[candidate]
    if re.fullmatch(r'0x[0-9A-Fa-f]+|\d+', word):
        return int(word, 0)
    raise PkError('NOTFOUND', f'{where}: behavior {word}; see include/constants/metatile_behaviors*.h')


def assign_palettes(tiles, slots):
    """tiles: list of colour sets (each <= 15). Greedy packing into <= slots palettes."""
    pals = []
    where = []
    order = sorted(range(len(tiles)), key=lambda i: -len(tiles[i]))
    result = [None] * len(tiles)
    for i in order:
        need = tiles[i]
        best, best_add = None, None
        for p, pal in enumerate(pals):
            add = len(need - pal)
            if len(pal | need) <= 15 and (best_add is None or add < best_add):
                best, best_add = p, add
        if best is None:
            pals.append(set(need))
            best = len(pals) - 1
        else:
            pals[best] |= need
        result[i] = best
    if len(pals) > slots:
        raise PkError('LIMIT', f'tiles need {len(pals)} palettes, only {slots} slots; reduce colours '
                               f'(each 8x8 tile <= 15 colours, few distinct colour sets)')
    return [sorted(p) for p in pals], result


def load_primary(repo, symbol, lim):
    """Quantised palettes (slot -> 16 colours) and {indexed 8x8 tile: global index} of a primary."""
    from ..maps.tiles import tileset
    from ..maps.meta import pal_file
    ts = tileset(repo, symbol)
    pals = []
    for path in ts['palettes'][:lim['pals']]:
        pals.append([gba(c) for c in pal_file(repo.read(path))][:16])
    img = Image.open(io.BytesIO(repo.read(ts['tiles'])))
    cols = img.width // 8
    tiles = {}
    for n in range(min(cols * (img.height // 8), lim['tiles'])):
        x, y = (n % cols) * 8, (n // cols) * 8
        t = tuple(tuple(img.getpixel((x + i, y + j)) for i in range(8)) for j in range(8))
        tiles.setdefault(t, n)
    return pals, tiles


def build(repo, tx, spec_path, opts):
    spec = Spec(repo, spec_path)
    version, kind = spec.meta['version'], spec.meta['kind']
    lim = LIMITS[version]
    tile_base = 0 if kind == 'primary' else lim['tiles']
    pal_base = 0 if kind == 'primary' else lim['pals']
    max_tiles = lim['tiles'] if kind == 'primary' else TOTAL_TILES - lim['tiles']
    max_meta = lim['metatiles'] if kind == 'primary' else TOTAL_METATILES - lim['metatiles']
    pal_slots = lim['pals'] if kind == 'primary' else TOTAL_PALS - lim['pals']
    # Stable ids: names keep their metatile index across rebuilds; new names append.
    ids_path = f'{IDS_DIR}/{spec.name}.json'
    try:
        ids = json.loads(repo.read(ids_path))
    except PkError:
        ids = {}
    order = sorted(spec.entries, key=lambda e: ids.get(e['name'], 10 ** 6))
    nxt = max(ids.values(), default=-1) + 1
    for e in order:
        if e['name'] not in ids:
            ids[e['name']] = nxt
            nxt += 1
    count = max(ids[e['name']] for e in spec.entries) + 1
    if count > max_meta:
        raise PkError('LIMIT', f'{count} metatiles > {max_meta} for a {version} {kind} tileset')
    # 1. Render 8 tiles (4 bottom + 4 top) per metatile, colours quantised to 15-bit GBA.
    drawings = {}
    metas = {}
    for e in spec.entries:
        if e['file'] not in drawings:
            drawings[e['file']] = parse(repo, f"{spec.folder}/{e['file']}")
        d = drawings[e['file']]
        quads = []
        for layer in ('bottom', 'top'):
            px = crop(d, layer, e['x'], e['y'])
            for qy, qx in ((0, 0), (0, 8), (8, 0), (8, 8)):
                quads.append(tuple(tuple(gba(px[qy + j][qx + i]) for i in range(8)) for j in range(8)))
        metas[e['name']] = quads
    # 2. Palettes: every non-empty tile's colour set must fit one 15-colour palette.
    # Spec order (dict keeps insertion order) makes the output deterministic.
    tile_list = list(dict.fromkeys(q for e in spec.entries for q in metas[e['name']] if any(c for row in q for c in row)))
    colour_sets = [frozenset(c for row in q for c in row if c) for q in tile_list]
    for q, cs in zip(tile_list, colour_sets):
        if len(cs) > 15:
            raise PkError('LIMIT', f'an 8x8 tile uses {len(cs)} colours (max 15 + transparent)')
    # A secondary tileset may draw with the paired primary's palettes and tiles (vanilla does this
    # a lot): tiles whose colours fit a primary palette use that slot, and identical primary tiles
    # are reused instead of copied.
    primary_pals, primary_tiles = [], {}
    if kind == 'secondary' and spec.meta.get('pair_with'):
        primary_pals, primary_tiles = load_primary(repo, spec.meta['pair_with'], lim)
    shared = {}
    for q, cs in zip(tile_list, colour_sets):
        for p, pal in enumerate(primary_pals):
            if cs <= set(pal[1:]):
                shared[q] = p
                break
    own = [q for q in tile_list if q not in shared]
    palettes, tile_pal = assign_palettes([colour_sets[tile_list.index(q)] for q in own], pal_slots)
    # slot -> colour list (index 0 = transparent); own palettes after the primary ones
    slot_colours = {p: pal for p, pal in enumerate(primary_pals)}
    slot_colours.update({pal_base + k: [(0, 0, 0)] + pal for k, pal in enumerate(palettes)})
    slot_of = dict(shared)
    slot_of.update({q: pal_base + p for q, p in zip(own, tile_pal)})
    # 3. Index tiles per palette, dedupe with flips; tile 0 stays blank.
    sheet, index = [tuple(tuple(0 for _ in range(8)) for _ in range(8))], {}
    def indexed(q, slot):
        lut = {}
        for k, c in enumerate(slot_colours[slot]):
            if k and c not in lut:
                lut[c] = k
        return tuple(tuple(lut[c] if c else 0 for c in row) for row in q)
    def flips(t):
        return ((t, 0, 0), (tuple(r[::-1] for r in t), 1, 0), (t[::-1], 0, 1), (tuple(r[::-1] for r in t[::-1]), 1, 1))
    def place(q):
        if not any(c for row in q for c in row):
            return 0, 0, 0, pal_base
        slot = slot_of[q]
        t = indexed(q, slot)
        for v, hf, vf in flips(t):
            if v in primary_tiles:
                return primary_tiles[v] - tile_base, hf, vf, slot
            if v in index:
                return index[v], hf, vf, slot
        index[t] = len(sheet)
        sheet.append(t)
        return index[t], 0, 0, slot
    words = [0] * (count * 8)
    attrs = [0] * count
    seen_raw = len({indexed(q, slot_of[q]) for q in tile_list})
    for e in spec.entries:
        slot = ids[e['name']]
        for n, q in enumerate(metas[e['name']]):
            t, hf, vf, p = place(q)
            words[slot * 8 + n] = (tile_base + t) | hf << 10 | vf << 11 | p << 12
        if e.get('attr') is not None:
            attrs[slot] = e['attr']            # raw attribute word (export keeps terrain/encounter bits)
        else:
            b = behavior_value(repo, version, e['behavior'], f"{spec_path}:{e['line']}")
            layer = LAYER_TYPES[e['layer']]
            attrs[slot] = b | layer << 29 if version == 'frlg' else b | layer << 12
    if len(sheet) > max_tiles:
        raise PkError('LIMIT', f'{len(sheet)} unique tiles > {max_tiles}; simplify or reuse art')
    # 4. Files.
    rows = (len(sheet) + 15) // 16
    img = Image.new('P', (128, rows * 8), 0)
    grey = []
    for k in range(16):
        grey += [255 - k * 17] * 3
    img.putpalette(grey + [0] * (768 - len(grey)))
    for n, t in enumerate(sheet):
        for j, row in enumerate(t):
            for i, v in enumerate(row):
                img.putpixel(((n % 16) * 8 + i, (n // 16) * 8 + j), v)
    buf = io.BytesIO()
    img.save(buf, 'PNG', bits=4)
    tx.stage(f'{spec.dir}/tiles.png', buf.getvalue())
    for k in range(16):
        local = k - pal_base
        colours = [(0, 0, 0)] + (palettes[local] if 0 <= local < len(palettes) else [])
        colours += [(0, 0, 0)] * (16 - len(colours))
        text = 'JASC-PAL\r\n0100\r\n16\r\n' + ''.join(f'{r} {g} {b}\r\n' for r, g, b in colours)
        tx.stage(f'{spec.dir}/palettes/{k:02d}.pal', text.encode('ascii'))
    tx.stage(f'{spec.dir}/metatiles.bin', struct.pack(f'<{len(words)}H', *words))
    fmt = 'I' if version == 'frlg' else 'H'
    tx.stage(f'{spec.dir}/metatile_attributes.bin', struct.pack(f'<{len(attrs)}{fmt}', *attrs))
    tx.stage(ids_path, (json.dumps(ids, indent=1, sort_keys=True) + '\n').encode('utf-8'))
    # 5. Names for pk meta/terrain (global metatile ids).
    meta_base = 0 if kind == 'primary' else lim['metatiles']
    # Merge with existing names: tags/syms given in .pkt win; otherwise keep what `pk meta name` set
    # (another agent may tag metatiles while art is rebuilt; an exported m2xx line carries no tags).
    names_path = f'tools/pk/data/metatiles/{spec.symbol}.json'
    try:
        old_names = json.loads(repo.read(names_path))
    except PkError:
        old_names = {}
    names = {}
    for e in spec.entries:
        key = f"{meta_base + ids[e['name']]:03X}"
        old = old_names.get(key, {})
        exported = e.get('attr') is not None and re.fullmatch(r'm[0-9a-f]{3}', e['name'])
        entry = dict(old) if exported and old else {}
        entry.setdefault('name', e['name'])
        if e['tags'] or not exported:
            entry['tags'] = e['tags']
        entry.setdefault('tags', [])
        if e['sym']:
            entry['sym'] = e['sym']
        names[key] = entry
    tx.stage(f'tools/pk/data/metatiles/{spec.symbol}.json', (json.dumps(names, indent=1, sort_keys=True) + '\n').encode('utf-8'))
    # 6. C registration (idempotent).
    from .register import register
    register(repo, tx, spec.symbol, spec.dir, kind == 'secondary', version)
    return Result(f'tileset {spec.symbol} {kind} {version} -> {spec.dir}',
                  [f'metatiles {count}/{max_meta} tiles {len(sheet)}/{max_tiles} palettes {len(palettes)}/{pal_slots} '
                   f'dedup_saved {seen_raw + 1 - len(sheet)}'])
