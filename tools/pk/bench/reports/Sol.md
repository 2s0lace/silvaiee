# Benchmark run: Sol

Map: PkPalletSol_Frlg, 24x28.

## Chunks used
- PalletTown_Frlg: 0,0,24x20 -> 0,0, preserving the original town tiles.
- Route1_Frlg: 0,32,24x4, copied in three blocks to rows 20-23 as an initial extension; overwritten where the park and beach required it.
- Route1_Frlg: flower/tree/lawn chunks 2,27,5x2 -> 2,20 and 2,27,10x2 -> 11,20; a small lawn copy at 21,20.
- Route1_Frlg: path 11,32,4x3 -> 12,20; upper sand/path strips from 3,29 to row 22.
- Route21_North_Frlg: channel 7,5,4x4 -> 7,20, continuing the vanilla pond toward the sea.
- Route21_North_Frlg: sea blocks from 7,12 and 7,19 across rows 24-27.
- Route21_North_Frlg: island sand interiors from 5,29,5x1 across row 23 and at 15,24; lower shoreline pieces from 4,31, 5,32, 7,32 and 9,32 across rows 24-25.
- Route21_North_Frlg: tree bottoms 0,13,2x1 -> 0,23 and 20,9,2x1 -> 22,23.
- Single-tile painting repaired river mouths, sand corners, a path seam and clipped rock fragments accidentally included in sea copies.

## Visual review
I could see the images. Four renders were generated and viewed: Route21_North_Frlg at scale 1, Route1_Frlg at scale 1, the initial destination at scale 1, and the final destination at scale 2 as explicitly requested by the final step. No additional renders were generated.

## Outcome and limitations
The original town remains above a compact flower park. The right-hand path reaches the sand without crossing water. The original pond continues into the sea. The whole bottom row is sea. The shoreline uses unequal stretches and several shallow bays rather than a repeating staircase.

Some pk commands returned ERR CONFLICT because another transaction was running. Failed required copies were retried or replaced by subsequent copies; the first preview revealed unfilled ocean cells and clipped rock fragments, which were repaired. The first sea source selection included a rock fragment with collision 1; these isolated fragments were replaced with ordinary water at elevation 1 and collision 0.

The park is necessarily small within the eight-row extension. The upper beach boundary is fairly formal, and some lower shore corners remain more regular than the best vanilla layouts. This is a tile-layout benchmark: no events, entrances or gameplay connections were added, and no build was run.

Honest rating against vanilla: **7/10**.

Validation: pk check PkPalletSol_Frlg did not complete; it reported ERR CONFLICT because data/layouts/PkPalletDeepSeek_Frlg/map.bin changed during its read. This was an external concurrent-map change; no validation pass is claimed.
