# Benchmark report: Opus (PkPalletOpus_Frlg)

Run by Claude Opus 5.5. All map edits were made with pk commands. I touched only PkPalletOpus_Frlg (source maps were read-only).

## Chunks used (all `pk map copy`, except where noted)
| Source | Rect | Target | Purpose |
|---|---|---|---|
| PalletTown_Frlg | 0,0,24x20 | 0,0 | town (step 2) |
| Route21_North_Frlg | 0,48,24x2 | 0,26 | open sea, corner rocks at x0-1 / x22-23 |
| Route21_North_Frlg | 0,0,7x2 | 0,20 | left tree tops plus fence corner (seamless under Pallet row 19) |
| Route21_North_Frlg | 0,12,8x4 | 0,22 | left trees → tree bottoms → fence → seawall, channel corner 0x131 |
| Route21_North_Frlg | 7,0,4x6 | 7,20 | pond continues as a brick-edged channel into the sea |
| Route21_North_Frlg | 11,1,6x3 | 11,20 | park lawn (checker lawn) |
| Route21_North_Frlg | 11,1,1x3 | 17,20 | lawn filler column |
| Route21_North_Frlg | 18,0,6x3 + 18,9,6x1 | 18,20 / 18,23 | right tree block plus tree bottoms |
| Route21_North_Frlg | 4,29,7x4 | 11,23 | sand island body plus lobed bottom edge (beach) |
| PalletTown_Frlg | 5,13,2x1 (twice) | 11,20 / 16,21 | flower pairs in the park |

Seam fixes, all by single-tile or small paint/grid edits:
- paint 7,25 0x131: channel/seawall corner, which the channel copy had overwritten.
- grid 11,23 13x4: extended the island tiles (body, 0x11C/0x11D/0x11E bottoms, 0x12D/0x12E notches) across to x23, giving two sand lobes.
- paint 13,22,3x1 0x115: sand bump into the park, so the park/beach boundary isn't one straight line.

## Renders
I looked at 4 renders and could see every image: one reference render of Route21_North (scale 1), two drafts (scale 1) and the final full map (scale 2).

## Result
- The pond flows straight down a canal and into the sea. Its banks are the same brick edging vanilla Route 21 uses.
- The park is a 7x3 lawn east of the canal with two flower pairs. Trees and a fence frame the west side, and a tree block frames the east side.
- The beach is sand from y22–24 down to y25–26. Its top edge has runs of 2/3/2/6 and its bottom edge has two lobes.
- The sea runs along the full bottom edge.
- You can walk from the town down to the beach: Pallet lawn (x11-21) leads to the park and then the sand. Collision checked.

## Problems hit
- The tileset pair only has sand as islands in water (edges have water on them). There is no grass→sand transition, so the park/beach boundary is a hard lawn/sand edge. It reads fine in the render but isn't vanilla-authentic.
- The canal banks are straight for about 9 tiles (y17-25), and the seawall on the left (y25, x0-6) is about 7 tiles. Both are copied straight from vanilla Route 21, which is built the same way. I didn't break them up because no bank-step metatile exists in these sources.
- The canal's east bank (0x12C) sits right next to the sand (x10/x11, y23-25). That's a small land/sand mismatch, but it isn't noticeable.
- Other benchmark agents were running at the same time, so I got `ERR CONFLICT` a few times and had to retry. `pk check` also failed once because of another agent's map.bin. My own map lints with 0 warnings (lint is uncalibrated).
- The park is small. The west side under the town is trees, not park.

## Rating vs vanilla: 6/10
It's clean, coherent and playable, and the north edge is seamless because Route21_North is the real southern neighbour. Against vanilla it loses on the hard lawn→sand edge, the long straight canal, and how thin the park is.
