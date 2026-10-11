.px = text pixel art. One op per line, # comments ('#' is not a colour symbol).
size WxH (multiples of 16) | use pal.px (shared pal lines) | pal a=#40b088 b=#70c8a0 ('.' = transparent)
from other.px (start from another drawing) | layer bottom|top (ops apply to that layer; default bottom)
fill c | rect x,y WxH c | hline x,y len c | vline x,y len c | dot x,y c
noise c,d 0.15 [seed=3] (c->d with probability) | dither b,c x,y WxH (checkerboard)
outline b (around non-transparent pixels) | mirror x|y | copy x,y WxH -> x2,y2 | recolor c=d e=f
grid [x,y] then rows of symbols until a blank line (' ' or '?' keeps the pixel, '.' transparent)
pk px show FILE.px (text echo + legend) | pk px check FILE.px (colours per 8x8, max 15)
pk px render FILE.px [--scale 4] [--out path.png] (default .pk/render/)
Colours are stored 15-bit on GBA (channels rounded down to multiples of 8).
pk px import IMAGE.png OUT.px --size WxH [--grid N] [--palette primary|keep|pal.px] [--colors N] [--key #rrggbb]
 [--layer top]: external/AI images -> .px (grid detection, dominant colour per cell, mapped to game palette).
 Raw images go in tools/pk/art/_inbox/<source>/ (see its README). Always review and fix the result by hand.
