# Astra benchmark report

Map: PkPalletAstra_Frlg, 24x28. All map changes used pk commands. No build or blueprint application was run, and I edited no other map.

## Chunks used
- PalletTown_Frlg 0,0,24x20 -> 0,0: original town, retained unchanged.
- Route21_North_Frlg 6,19,6x3 -> 6,20: initial outlet water; upper rows subsequently replaced with a copy of the target town banks (6,18,6x2 -> 6,20).
- Route21_North_Frlg 8,33,16x5 -> 8,23 and 8,33,8x5 -> 0,23: sea and occasional rocks across the bottom.
- Route1_Frlg 2,27,12x3 -> 12,20: flower park and sandy approach.
- Route21_North_Frlg 4,29,7x4 -> 12,23 and 17,23: joined island sections forming the main beach. A 5,29,4x2 -> 18,23 copy and two sand tiles removed interior seams.
- Route1_Frlg 0,27,6x2 -> 0,20 and 2,29,6x1 -> 0,22: western flower garden and approach.
- Route21_North_Frlg 4,31,7x2 -> 0,23: smaller western shore. This replaced an earlier 14,25,4x3 -> 1,22 experiment.
- Route1_Frlg 8,25,2x2 -> 22,20: tree boundary termination.
- Single-tile bank repairs at 6,22 and 11,22; sand repair at 17,23,1x2.

## Visual review
Five successful renders, all five opened and visible: both source routes at scale 1, two target iterations at scale 1, and the final target at scale 2. The explicit final scale-2 instruction was treated as the exception to the opening scale-1 instruction. A failed Route1 render returned a transaction conflict and produced no additional render.

## Result and limitations
The town remains intact. Flower gardens extend it south, with a broad accessible sandy beach to the east and a smaller western shore. The original pond opens into the sea; the entire bottom edge is water, with a few vanilla rocks. Copied land tiles retain their original collision/elevation; the extension collision grid was inspected. No in-game traversal test was performed.

The sea-facing sandy edge has uneven lobes rather than a regular staircase. However, the eastern grass/sand boundary is still straight for more than six tiles: the strict natural-edge requirement is not fully met. The two beach lobes look repetitive, the park is shallow, and the mouth transitions remain less polished than vanilla. No claim of full benchmark compliance.

Several commands encountered another pk transaction's lock. Required operations were retried or replaced; the failed 0,24,6x3 park copy was abandoned. The final pk check was inconclusive: it returned a conflict because PkPalletMuse_Frlg/map.bin changed during its read. I did not modify that map. The final render succeeded afterward.

Honest rating against vanilla: **6/10**.
