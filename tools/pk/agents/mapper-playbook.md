# Mapper playbook: building a FireRed map autonomously

You build or rework an outdoor map alone, with no human in the loop. The goal is a map that a player can't
tell apart from Game Freak's work: correct tiles, and also a layout that feels designed.

Read `tools/pk/AGENT.md` first. Every command below is real; check `pk help map|kit|terrain|meta` for details.
Write only through pk (journaled; `pk undo` is free). Run **one pk write at a time**.

## The rule that matters most
**Decide the layout before you place real tiles.** Every failed map so far failed on layout (where water, sand,
paths and trees go), and no tile-level fix could rescue it afterwards. Tiles are the easy part now; the tools do
them. Your job is composition.

## Phase 0: study (no writes)
1. Render 2-3 vanilla maps of the same kind as yours, and the maps that connect to it:
   `pk map render Route21_North_Frlg --scale 1`. Coastal town? Look at Vermilion, Cinnabar and the Sevii coasts.
2. Note what makes them feel designed:
   - open space vs decoration;
   - how coastlines bend;
   - how paths lead somewhere;
   - how flowers and trees cluster in groups, not single tiles.
3. Write a 5-line plan: what is where, why a player walks there, and what the one memorable spot is.

## Phase 1: sketch (materials only, on a scratch copy)
4. Work on your own `Pk*` map. Rough out areas with materials, not tiles:
   `pk terrain brush MAP rect x,y WxH water` (also `sand`, `grass`, `ground`; `pk terrain forest` for trees).
5. Check the sketch, and fix it before tiling:
   - `pk map lint MAP`: `INFO SHAPE` = a shape FireRed never uses (e.g. 1-tile diagonal water). Reshape it;
     don't hope tiles will hide it.
   - `pk map show MAP --rect x,y,WxH`: is every walkable area connected? Do paths lead to exits and doors?
   - Compare the proportions with your Phase 0 notes. Too empty? Too busy? Straight lines where vanilla curves?

## Phase 2: real tiles
6. Replace sketch areas with vanilla pieces where possible:
   - `pk kit fit MAP x,y,WxH` (pieces whose edges fit);
   - `pk kit find --feature shore|pond|ledge|tallgrass|cliff --pair MAP`;
   - paste with `pk map copy MAP x,y,WxH tx,ty --from SOURCE --repair`.

   Big pieces first, small ones after. Avoid piers/docks unless you want a pier.
7. Smooth the joins with small boxes (2-6 tiles): `pk terrain auto MAP x,y,WxH`. Use `--options 3` to compare
   variants (nothing is written), then `--pick n`.
8. After every write, read the extra lines:
   - `WARN reach` → you cut something off: `pk undo`.
   - `WARN repair changed K of N rim cells` → that piece doesn't fit there; try another.

## Phase 3: self-check with fresh eyes
9. `pk map lint MAP` until there's no WARN you can fix. `pk map render MAP --annotate --scale 2` shows every
   problem as a box with coordinates.
10. **You can't judge your own map** (every benchmark model overrated itself by 2-5 points). If you can
    start a subagent or a new session, give it ONLY:
    - your final render;
    - one vanilla reference render;
    - the question: "List up to 8 concrete defects with tile coordinates (x,y or x,y-WxH). No score, no praise."

    Fix what it lists, then repeat once.
11. If the subagent can't see images, give it the output of `pk map show MAP --roles` (once tile cards are
    installed; `pk help map` will list it) and `pk map lint MAP` instead.

## Phase 4: best of three (when the budget allows)
12. Build 3 variants of the risky part (e.g. the coastline) in 3 copies named `PkXxxA/B/C`. Make each copy
    with `pk map new PkXxxA_Frlg --size WxH --tilesets P,S --fill 0x... --like MAP`, then
    `pk map copy PkXxxA_Frlg 0,0,WxH 0,0 --from MAP`.
13. A fresh subagent sees the 3 renders, unlabelled and in random order, and picks the best with one
    sentence of reasons. Keep that one; delete the others with `pk map delete`.

## Report
- your 5-line plan;
- the pieces used (source + box);
- the final lint line;
- the critic's defect lists and what you fixed;
- a rating **only if a fresh critic gave one;** never rate your own map.
