# Parallel task D: tile cards

`pk meta card MAP MT`; `pk meta cards MAP [--role|--class|--limit|--sheet]`;
`pk map show MAP --roles [--rect x,y,WxH]`. Tests:
`C:/Python314/python.exe -B -m unittest tools.pk.tests.test_cards` (14 pass). Numbers from a
throwaway harness calling `pk.maps.cards`, the same code as the CLI.

| check | target | measured |
|---|---|---|
| objects vs stamps | >=90% of stamp tiles | **MISS 42.2%** (333/789; 43.5% of the 766 vanilla-metatile tiles; tree 3/4) |
| prediction open | >=85% | 94.8% (10599/11182) |
| prediction edge | >=85% | 89.0% (290/326) |
| prediction corner | >=85% | 93.0% (80/86) |
| coverage (non-mixed) | none | 25.1% (2593/10325) |
| name check | none | 2606/10300 pair rows; 272/2635 distinct; name_audit 482/2819 |
| known tiles | pass | 0x12B open surf; 0x045 walk+CONTRADICTS; 0x0D4 CONTRADICTS |

The objects target is **missed**; work stopped there and no threshold was loosened. `NEED_SHARE`
0.50 would give 91.5% over vanilla stamps, but 0.90 is kept. Sheet:
`.pk/render/cards_gTileset_General_Frlg+gTileset_PalletTown.png` (10 open/edge/corner, scale 3,
role under each).

Example cards:
- `0x12B surf open surf used 11708x` — open water.
- `0x12C surf edge: wall E (71%)` — shore.
- `0x125 walk inner corner: surf NW (100%)` — concave bay.
- `0x01C wall open wall needs 0x014 at N, 0x01D at W` — tree part.
- `0x045 walk mixed; water_151 (name contradicts)` — the walkable grey block.

Weak: the object rule needs one metatile to dominate a cardinal, but building walls are reused
across shapes, so most stamps are not objects. Inner corners and single-use tiles are noisy, so
roles need >=5 uses and a 0.90 diagonal. Coverage is low because most tiles appear in several
roles. The per-pair name count is inflated (primary ids repeat; an unused tile falls back to walk).
