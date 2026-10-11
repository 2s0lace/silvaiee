pk tileset build tools/pk/art/SET/tileset.pkt [--dry-run]
pk tileset export MAP|PRIMARY,SECONDARY name [--which secondary|primary] -> tools/pk/art/name/*.px + tileset.pkt
.pkt lines: name X | kind primary|secondary | version frlg|emerald | pair_with gTileset_General_Frlg
metatile line: NAME FILE.px[@x,y] BEHAVIOR LAYER [tags=a,b] [sym=X] [attr=0xRAW]
 BEHAVIOR: NORMAL, TALL_GRASS, ... or full MB_FRLG_*; LAYER: NORMAL | COVERED | SPLIT
 @x,y picks a 16x16 cell of the drawing; its bottom layer -> tile slots 0-3, top layer -> 4-7.
The compiler: quantises colours, packs palettes (6 secondary slots on FRLG), reuses primary palettes and
tiles when colours/pixels match, dedupes with flips, writes tiles.png, palettes, metatiles.bin,
metatile_attributes.bin, registers in src/data/tilesets/*.h and include/tilesets.h (once), and writes
metatile names/tags for pk meta/terrain. Metatile ids stay stable per name (tools/pk/data/tilesets/).
Errors: LIMIT = too many colours per 8x8, palettes, tiles or metatiles.
Round trip: export then build reproduces vanilla pixels and attributes (tested on Pallet Town).
pk tileset lint SET.pkt: new metatiles only (lines without attr=). Flags near-copies of vanilla tiles,
 hand-drawn copies of ANIMATED art (sea/shore 416-481, flowers 508-511 on General_Frlg: copy them pixel-exact
 in the bottom layer), bottom-layer holes, pure black/white, flat 8x8 fills, colours outside 4-12/metatile.
pk tileset preview SET.pkt [--scale 3] -> .pk/render/<set>_preview.png: each new drawing on plain ground, sand, water.
build runs lint first and prints its warnings.
