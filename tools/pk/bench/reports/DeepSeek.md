# Benchmark report: DeepSeek

Map: **PkPalletDeepSeek_Frlg** (24x28, gTileset_General_Frlg + gTileset_PalletTown, type town).
Goal: extend Pallet Town south with a small park and a beach, mostly by copying vanilla chunks.

## What I built

- Rows 0-19: verbatim copy of Pallet Town (`PalletTown_Frlg 0,0,24x20 -> 0,0`).
- Rows 20-21/22: a **park** just below town.
- Rows 22-25: a **sandy beach**, irregular top edge where the park dips to y22.
- Rows 25-27 + inlets: the **sea** along the whole bottom edge, with sand spits and a rounded bay/cove on the right.
- The vanilla pond (x7-10, y17-19) is **connected to the sea** by a short meandering stream that runs from the pond straight down through the park/beach and out into the sea.

## Chunks copied (pk map copy)

| Source | Region | Destination |
|---|---|---|
| PalletTown_Frlg | 0,0,24x20 | 0,0 (whole town) |
| Route1_Frlg | 2,2,20x3 | 2,20 (lawn pattern, flower cluster, walk path) |
| PalletTown_Frlg | 0,17,2x2 | 0,20 (left tree border continued into park) |
| PalletTown_Frlg | 22,17,2x2 | 22,20 (right tree border continued into park) |
| PkPalletDeepSeek_Frlg (self) | 4,11,6x3 | 2,20 (the town's fenced flower bed, reused as the park flower bed) |

Painted/derived with `pk terrain brush` + the vanilla-adjacency solver (no single-tile painting was needed for the shore):
sand base on y22-24, water on y23-27, a `path` brush for the pond->sea stream, four overlapping sand
circles for the wavy beach top, a water circle for the right cove, and sand circles for spits at the bottom edge.

## Renders I looked at

6 total: 5 at `--scale 1` (Route21_North_Frlg, Route1_Frlg used to find chunks; the map 3x while iterating)
plus the mandated final `--scale 2`. I could **see every PNG** (the model is vision-capable) and used them to
reject a first, very blocky rect-brush attempt and rebuild with organic circle/path shapes.

## Problems hit

- **Two failed attempts / rework.** The first beach (rectangular `rect` brushes) looked like a checkerboard of
  sand and water; I reset the extension (`map paint ... 0x296`) and rebuilt with `circle`/`path` brushes.
- **Lock contention.** Another `pk` run was writing a different map (`PkPalletSol_Frlg`) at the same time; many
  commands returned `ERR CONFLICT another pk transaction is running`. I added sleep-and-retry loops and retried
  every affected write.
- **No beach exists in these tilesets.** `terrain learn` shows only 4 sand metatiles and sand is only ever adjacent
  to water in vanilla (tiny sandbars in Route21_North). A large land-meets-sand beach therefore cannot be built from
  pure vanilla-valid adjacencies; the park<->sand border produces "seams never seen in vanilla" warnings. `pk map lint`
  is documented as uncalibrated (scores 0) so I judged by renders instead.
- The sand/shore boundary is still somewhat chunky, a consequence of the 4-metatile sand set.

## Honest rating

**5/10** against vanilla. It is functional and legible: town stays walkable to the beach, the park (fenced flower
bed, path, tree-framed border) reads clearly, the pond genuinely reaches the sea, and no straight shoreline edge
runs longer than ~6 tiles. But it is clearly not hand-authored: the sand coastline has blocky 90-degree steps and
visible repeat, the beach is a bit large and flat, and the green park runs straight into the sand with a hard
material change. `pk check` = 0 errors / 0 warnings.

## Deviations / notes

- Did not connect the map to Pallet Town (that would require editing PalletTown_Frlg, which the rules forbid), so
  the extension currently has no in-game entrance.
- Report file only; no other non-map files created.
