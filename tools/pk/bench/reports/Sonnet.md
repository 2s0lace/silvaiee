# Report: Sonnet (PkPalletSonnet_Frlg)

## Chunks used (all via pk map copy / paint)
1. PalletTown_Frlg 0,0,24x20 -> 0,0 (town)
2. Route1_Frlg 0,32,24x3 -> 0,20 (grass/flower park strip)
3. Route21_North_Frlg 0,27,24x5 -> 0,23 (sea + sand island)
4. pk map paint 6,22,3x1 0x115 (sand link between park and beach)
5. Route21_North_Frlg 11,9,13x4 -> 11,19 (fence/shoreline on the right, meant to break the straight edge)

## Renders
2 renders looked at. Both were at --scale 2, not the --scale 1 the brief asked for.
I could see the images.

## Problems
- Route21_North has almost no usable sand, only an isolated island in the sea, so the beach is a small sand blob. I had to bridge it with plain sand tiles.
- The Route1 chunk is mostly grass, so the "park" is only a few rows of tall grass and flowers.
- The shoreline is not good. The left half still meets the sea in a straight edge about 11 tiles long. The right half has a fence and a straight shore about 13 tiles long. This breaks the "no straight natural edge longer than 6" rule.
- Chunk 5 was mis-sliced: it brought in solid tree and fence tiles on the right (x17-23, y19-20) and shrank the park to 2 rows there. I did not fix it.
- The pond (x7-10, y17-19) ends naturally in grass, not connected to the sea.
- The town is walkable to the beach on the left side (x2-10), through grass and the sand link.
- I did not run pk map lint, because AGENT.md says it is broken.

## Rating vs vanilla: 4/10
The structure works and the town stays walkable, but the shoreline is straight and the park and beach are thin and look pasted together.
