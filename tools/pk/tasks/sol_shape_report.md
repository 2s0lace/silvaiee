# Source-bank preservation: five-pond rerun

Ponds now copy every source water tuple and full eight-neighbour bank ring unchanged. Only the outer
destination-lawn ring is solved. Secondary source tiles produce an atomic NOT PLACEABLE refusal with
tile IDs/coordinates; no bank re-solving fallback remains. The outline check requires a non-water,
non-lawn bank neighbour and rejects every exposed lawn-facing side.

Catalogue: 45 ponds, 46 coves, 903 grove candidates, 44 islands, 0 sandbars, 46 beaches, 239 tallgrass.
Four ponds have wholly portable patches.

Same five IDs, sizes and positions:

| Pond suffix | Size | Secondary water / bank cells | Result | Auto baseline seams |
|---|---|---:|---|---:|
| 792c2959c3 | 3x3 | 0 / 4 | NOT PLACEABLE | 13 |
| 9ed7d101ba | 6x6 | 6 / 20 | NOT PLACEABLE | 17 |
| 9c8a08d481 | 10x4 | 10 / 20 | NOT PLACEABLE | 22 |
| 29b1fa8baf | 9x7 | 5 / 3 | NOT PLACEABLE | 24 |
| d5d3f3dadd | 10x7 | 16 / 2 | NOT PLACEABLE | 35 |

Refusals preserve pending writes/map cells; added objects 0, reach unchanged. Placement seams, fidelity
and bank-completeness results are N/A. Five-placement acceptance remains unmet; no shapes or thresholds
were substituted. 21 checks pass; the five-placement acceptance test fails.

Commands:
`C:/Python314/python.exe -B -c "from tools.pk.tests.test_shapes import benchmark; benchmark(render=True)"`;
`C:/Python314/python.exe -B -m unittest tools.pk.tests.test_shapes -v`.

`.pk/render/shape_ponds.png` explicitly shows SOURCE / NOT PLACEABLE previews; magenta marks secondary
bank tiles. Exact directional water-shape flips are refused.
