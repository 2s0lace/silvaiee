"""pk tileset build|export, pk px show|render|check."""
import io
import re

from PIL import Image

from ..core.errors import PkError
from ..core.out import Result
from ..maps.meta import pair_for, all_ids, draw
from .compiler import build, LAYER_TYPES, camel
from .pixeldsl import parse

ART = 'tools/pk/art'
SYMBOLS = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@$%&*+=<>~^'


def hexc(c):
    return f'#{c[0]:02x}{c[1]:02x}{c[2]:02x}'


def export(repo, tx, word, which, out_name):
    """Decompile one tileset of a pair into tools/pk/art/<out>/: one .px per metatile + tileset.pkt."""
    pair = pair_for(repo, word)
    ts = pair.secondary if which == 'secondary' else pair.primary
    base = pair.limits['metatiles'] if which == 'secondary' else 0
    ids = [m for m in all_ids(pair) if (m >= base) == (which == 'secondary')]
    folder = f'{ART}/{out_name}'
    lines = [f'name {out_name}', f'kind {which}', f'version {pair.version}',
             f'pair_with {pair.primary["symbol"]}' if which == 'secondary' else '',
             '# exported from ' + ts['symbol'] + ' by pk tileset export']
    attrs = pair.attributes(ts)
    from ..maps.tiles import behaviors
    names = behaviors(repo, pair.version)
    for mt in ids:
        layers = {}
        for layer, slots in (('bottom', range(0, 4)), ('top', range(4, 8))):
            layers[layer] = draw_layer(repo, pair, mt, slots)
        colours = sorted({c for px in layers.values() for row in px for c in row if c})
        if len(colours) > len(SYMBOLS):
            raise PkError('LIMIT', f'metatile 0x{mt:03X} has too many colours to export')
        sym = {c: SYMBOLS[i] for i, c in enumerate(colours)}
        text = ['size 16x16', 'pal . ' + ' '.join(f'{sym[c]}={hexc(c)}' for c in colours)]
        for layer in ('bottom', 'top'):
            px = layers[layer]
            if not any(c for row in px for c in row):
                continue
            text += [f'layer {layer}', 'grid'] + [''.join(sym[c] if c else '.' for c in row) for row in px] + ['']
        name = pair.info(mt).get('name') or f'm{mt:03x}'
        tx.stage(f'{folder}/{name}.px', ('\n'.join(text) + '\n').encode('utf-8'))
        local = mt - base
        raw = attrs[local] if local < len(attrs) else 0
        behavior = names.get(raw & (0x1FF if pair.version == 'frlg' else 0xFF), hex(raw & 0x1FF))
        layer_type = (raw >> 29) & 3 if pair.version == 'frlg' else (raw >> 12) & 0xF
        layer_name = {v: k for k, v in LAYER_TYPES.items()}.get(layer_type, 'NORMAL')
        tags = pair.info(mt).get('tags') or []
        lines.append(f'{name} {name}.px {behavior} {layer_name} attr=0x{raw:X}' + (f' tags={",".join(tags)}' if tags else ''))
    tx.stage(f'{folder}/tileset.pkt', ('\n'.join(l for l in lines if l) + '\n').encode('utf-8'))
    return Result(f'tileset export {ts["symbol"]} -> {folder} {len(ids)} metatiles',
                  [f'edit the .px files, then: pk tileset build {folder}/tileset.pkt (rename "name" to make a new tileset)'])


def draw_layer(repo, pair, mt, slots):
    """One layer of a metatile: slots 0-3 = bottom, 4-7 = top."""
    return draw(repo, pair, mt, only=set(slots))


def px_command(repo, tx, args, opts):
    verb, rest = args[0], args[1:]
    if verb == 'import' and len(rest) == 2:
        from .importer import run as import_image
        return import_image(repo, tx, rest[0], rest[1], opts)
    if verb in ('show', 'check', 'render') and len(rest) == 1:
        d = parse(repo, rest[0])
        if verb == 'show':
            colours = sorted({c for px in d.layers.values() for row in px for c in row if c})
            sym = {c: SYMBOLS[i % len(SYMBOLS)] for i, c in enumerate(colours)}
            rows = ['legend .=transparent ' + ' '.join(f'{sym[c]}={hexc(c)}' for c in colours)]
            for layer in ('bottom', 'top'):
                px = d.layers[layer]
                if any(c for row in px for c in row):
                    rows.append(f'layer {layer}')
                    rows += [''.join(sym[c] if c else '.' for c in row) for row in px]
            return Result(f'px {rest[0]} {d.w}x{d.h}', rows)
        if verb == 'check':
            rows, worst = [], 0
            for layer in ('bottom', 'top'):
                px = d.layers[layer]
                for ty in range(0, d.h, 8):
                    for tx_ in range(0, d.w, 8):
                        cs = {px[ty + j][tx_ + i] for j in range(8) for i in range(8)} - {None}
                        cs = {(c[0] & 0xF8, c[1] & 0xF8, c[2] & 0xF8) for c in cs}
                        worst = max(worst, len(cs))
                        if len(cs) > 15:
                            rows.append(f'{layer} tile {tx_},{ty} has {len(cs)} colours (max 15)')
            total = len({c for px in d.layers.values() for row in px for c in row if c})
            return Result(f'px check {rest[0]} colours:{total} max_per_8x8:{worst} errors:{len(rows)}', rows[:18])
        img = Image.new('RGBA', (d.w, d.h), (0, 0, 0, 0))
        for layer in ('bottom', 'top'):
            for y, row in enumerate(d.layers[layer]):
                for x, c in enumerate(row):
                    if c:
                        img.putpixel((x, y), (*c, 255))
        scale = int(opts.get('scale', 4))
        img = img.resize((d.w * scale, d.h * scale), Image.NEAREST)
        buf = io.BytesIO()
        img.save(buf, 'PNG')
        out = opts.get('out') or '.pk/render/' + rest[0].rsplit('/', 1)[-1].replace('.px', '.png')
        tx.stage(repo.path(out).relative_to(repo.root).as_posix(), buf.getvalue())
        return Result(f'px render {rest[0]} -> {out}')
    raise PkError('PARSE', 'usage: pk px show|check|render FILE.px')


def tileset_command(repo, tx, args, opts):
    verb, rest = args[0], args[1:]
    if verb == 'build' and len(rest) == 1:
        from .lint import lint
        _, warnings = lint(repo, rest[0])          # against the pre-build state
        result = build(repo, tx, rest[0], opts)
        result.data += [f'WARN {w}' for w in warnings[:15]] + ([f'WARN ... {len(warnings) - 15} more (pk tileset lint)'] if len(warnings) > 15 else [])
        return result
    if verb == 'lint' and len(rest) == 1:
        from .lint import lint
        _, warnings = lint(repo, rest[0])
        return Result(f'tileset lint {rest[0]} warnings:{len(warnings)}', warnings[:18], more=18 if len(warnings) > 18 else None)
    if verb == 'preview' and len(rest) == 1:
        from .lint import preview
        return preview(repo, tx, rest[0], opts)
    if verb == 'export' and len(rest) == 2:
        which = opts.get('which', 'secondary')
        if which not in ('primary', 'secondary'):
            raise PkError('PARSE', '--which primary|secondary')
        if not re.fullmatch(r'[a-z][a-z0-9_]*', rest[1]):
            raise PkError('PARSE', 'export name is snake_case (becomes tools/pk/art/<name>/)')
        return export(repo, tx, rest[0], which, rest[1])
    raise PkError('PARSE', 'usage: pk tileset build|lint|preview tools/pk/art/X/tileset.pkt | pk tileset export MAP|P,S name [--which secondary|primary]')
