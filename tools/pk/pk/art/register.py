"""Register a tileset in src/data/tilesets/{graphics,metatiles,headers}.h (idempotent).

New FRLG entries go right before '#endif // IS_FRLG'; Emerald entries right before the '#else'
that separates the two halves, matching where vanilla keeps them."""
import re

from ..core.errors import PkError


def insert(text, block, frlg):
    if frlg:
        anchor = text.rfind('#endif // IS_FRLG')
    else:
        anchor = text.find('\n#else', text.find('#if !IS_FRLG')) + 1
    if anchor <= 0:
        raise PkError('PARSE', 'could not find the IS_FRLG section in a tileset header; register manually')
    return text[:anchor] + block + '\n' + text[anchor:]


def register(repo, tx, symbol, folder, secondary, version):
    base = symbol[len('gTileset_'):]
    frlg = version == 'frlg'
    files = {
        'src/data/tilesets/graphics.h': (
            f'gTilesetTiles_{base}[]',
            f'const u32 gTilesetTiles_{base}[] = INCGFX_U32("{folder}/tiles.png", ".4bpp.fastSmol");\n\n'
            f'const u16 gTilesetPalettes_{base}[][16] =\n{{\n'
            + ''.join(f'    INCGFX_U16("{folder}/palettes/{k:02d}.pal", ".gbapal"),\n' for k in range(16))
            + '};\n'),
        'src/data/tilesets/metatiles.h': (
            f'gMetatiles_{base}[]',
            f'const u16 gMetatiles_{base}[] = INCBIN_U16("{folder}/metatiles.bin");\n'
            f'const u16 gMetatileAttributes_{base}[] = INCBIN_U16("{folder}/metatile_attributes.bin");\n'),
        'src/data/tilesets/headers.h': (
            f'struct Tileset {symbol} ',
            f'const struct Tileset {symbol} =\n{{\n    .isCompressed = TRUE,\n'
            f'    .isSecondary = {"TRUE" if secondary else "FALSE"},\n    .tiles = gTilesetTiles_{base},\n'
            f'    .palettes = gTilesetPalettes_{base},\n    .metatiles = gMetatiles_{base},\n'
            f'    .metatileAttributes = gMetatileAttributes_{base},\n    .callback = NULL,\n}};\n'),
    }
    for path, (marker, block) in files.items():
        text = repo.read(path).decode('utf-8')
        if marker in text:
            continue                       # already registered; data files were rebuilt in place
        tx.stage(path, insert(text, block, frlg).encode('utf-8'))
    # headers.h tilesets must also be declared for layouts: include/tilesets.h
    decl_path = 'include/tilesets.h'
    try:
        decl = repo.read(decl_path).decode('utf-8')
    except PkError:
        return
    line = f'extern const struct Tileset {symbol};'
    if line not in decl and re.search(r'extern const struct Tileset gTileset_\w+;', decl):
        last = list(re.finditer(r'extern const struct Tileset gTileset_\w+;\n', decl))[-1]
        decl = decl[:last.end()] + line + '\n' + decl[last.end():]
        tx.stage(decl_path, decl.encode('utf-8'))
