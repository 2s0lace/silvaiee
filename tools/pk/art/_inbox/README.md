# Inbox for external art (Gemini and other image generators)

Put raw generated images here, one subfolder per source: `gemini/`, `other/`. Name each file
`<set>_<object>_v<N>.png`, for example `pallet_pond_pier_v1.png`. Never put them in `data/`.

Raw images are NOT game assets. Convert each one into a .px drawing inside its tileset folder:

    python tools/pk/pk.py px import tools/pk/art/_inbox/gemini/pallet_pond_pier_v1.png tools/pk/art/pallet_town/pier.px --size 32x48

- `--size` is the in-game size in pixels, a multiple of 16.
- The pixel grid is detected automatically; override it with `--grid N`.
- `--palette primary` (default) maps colours onto General_Frlg's palettes, which costs no palette budget.
  `--palette keep --colors 10` keeps the image's own colours; `--palette pal.px` uses a set palette.
- Transparent background: alpha in the PNG, or `--key #ff00ff` for a flat background colour.
- `--layer top` puts the drawing on the top layer (crowns, roofs).

After importing, the normal asset rules apply (tools/pk/agents/deepseek-assets.md):
1. `pk px render ... --scale 6`, then fix by hand in the .px (AI images get proportions, outlines and light
   direction wrong);
2. `pk tileset lint`, `pk tileset preview`, `pk tileset build`.

Keep the source PNG here: it records where the .px came from.

## Getting usable images from Gemini (prompt template)
Gemini draws at about 2x the game's pixel density with thick black outlines. Ask for the exact game size:

    Pixel art game asset in the exact style of Pokémon FireRed/LeafGreen (GBA, 2004) overworld tiles.
    Object: <wooden park bench, front view>. Canvas exactly <32x16> pixels; the whole object fits inside it.
    Output the image upscaled 16x with nearest neighbour (no smoothing, no anti-aliasing), so every game
    pixel is a crisp 16x16 square. Light from the top-left. Outlines 1 px in a dark tint of the object's own
    colour, never black. At most 8 colours, flat shading with 3-4 shades per material, no gradients, no
    texture noise. Plain flat magenta (#ff00ff) background, no grass, no shadow outside the object.
    3/4 top-down perspective like FRLG.

Then import with `--grid 16 --key #ff00ff --palette keep --colors 8`.
