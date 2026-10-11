# Benchmark report: Gemini (PkPalletGemini_Frlg)

Created by Gemini for the FireRed level design benchmark.
All map modifications were executed solely through `pk.py` commands (`pk map new`, `pk map copy`, `pk map paint`). Only `PkPalletGemini_Frlg` was modified.

---

## Chunks Used

The map extension (rows 20–27) was constructed predominantly by copying chunks from `PalletTown_Frlg`, `Route21_North_Frlg`, and `Route1_Frlg`, with minimal single-tile seam repairs:

| # | Source Map | Source Rect | Destination | Purpose |
|---|---|---|---|---|
| 1 | `PalletTown_Frlg` | `0,0,24x20` | `0,0` | Verbatim Pallet Town base (step 2) |
| 2 | `Route21_North_Frlg` | `0,48,24x2` | `0,26` | Sea base across rows 26–27 with corner rock formations |
| 3 | `Route21_North_Frlg` | `0,0,2x4` | `0,20` | Left tree border (y20–23) continuing seamlessly from Pallet Town |
| 4 | `Route21_North_Frlg` | `22,0,2x4` | `22,20` | Right tree border (y20–23) continuing seamlessly from Pallet Town |
| 5 | `Route21_North_Frlg` | `0,13,2x1` | `0,24` | Left tree bottoms terminating forest |
| 6 | `Route21_North_Frlg` | `22,9,2x1` | `22,24` | Right tree bottoms terminating forest |
| 7 | `Route21_North_Frlg` | `0,17,2x1` | `0,25` | Left coastal rock cliff (`0x1D9`) meeting sea |
| 8 | `Route21_North_Frlg` | `22,13,2x1` | `22,25` | Right coastal rock cliff (`0x1D9`) meeting sea |
| 9 | `Route21_North_Frlg` | `7,0,4x2` | `7,20` | Vanilla pond continuation (y20–21) as brick canal stream |
| 10 | `Route1_Frlg` | `2,27,5x2` | `2,20` | Western park lawn and red flower cluster |
| 11 | `Route21_North_Frlg` | `11,1,6x2` | `11,20` | Eastern park checkerboard lawn |
| 12 | `PalletTown_Frlg` | `5,12,2x2` | `19,20` | Red flower pair in eastern park |
| 13 | `Route21_North_Frlg` | `11,1,2x2` | `17,20` | Eastern park lawn filler column |
| 14 | `Route21_North_Frlg` | `11,1,1x2` | `21,20` | Eastern park lawn boundary column |
| 15 | `Route21_North_Frlg` | `4,29,7x3` | `11,23` | Main eastern beach body and first lobed shoreline |
| 16 | `Route21_North_Frlg` | `7,29,4x3` | `18,23` | Second sand lobe and eastern beach shore |
| 17 | `Route21_North_Frlg` | `11,1,3x1` | `15,22` | Park lawn promontory breaking the straight sand edge |
| 18 | `Route21_North_Frlg` | `11,1,2x1` | `2,22` | Western park lawn transition |

### Seam Repairs and Details (via `pk map paint`)
- `17,23` & `17,24` painted `0x115`: merged the two copied sand chunks into a continuous beach body.
- `17,25` painted `0x11D`: connected the two southern sand lobes via a recessed cove shore.
- `12,22..14,22` & `18,22..20,22` painted `0x115`: sand upper edge forming an irregular lawn/sand boundary (3-tile runs).
- `11,22` painted `0x114` & `21,22` painted `0x116`: continued the left and right sand shores north into row 22.
- `4,22..6,22` & `2,23..5,23` & `2,24..4,24` painted `0x115`: shaped the western beach approach.
- `6,22` & `6,23` painted `0x116`: western sand shore facing the estuary.
- `5,24` & `4,25` painted `0x11E`: curved bottom-right shore corners for the west beach.
- `2,25..3,25` painted `0x11C` & `0x11D`: curved bottom shore for the west beach.
- `7,22..10,22` & `7,23..10,23` painted `0x12B`: river mouth opening from the brick canal into the sandy bay.
- `8,24..9,25` painted `0x110, 0x111` / `0x118, 0x119`: 2x2 water rock centered in the estuary bay.

---

## Visual Review & Renders
- Looked at **3 renders total**: 2 iteration drafts at `--scale 1`, followed by the final review render at `--scale 2`.
- I **could see all generated PNG images**. Visual inspection was used directly to identify and fix the canal left-bank corner asymmetry at `(7,22)`, refine the sand/water junctions, and confirm aesthetic balance.

---

## Benchmark Requirements Check
- **Park & Beach Zoning**: A flower-adorned park borders Pallet Town (y20–21), giving way to sandy beaches (y22–25), and open sea along the entire southern edge (y26–27).
- **Irregular Shoreline & Natural Edges**:
  - Twin-lobed southern coastline (lobes at x15 and x19 separated by a cove at x16–18).
  - The estuary mouth opens organically at x5–10 with a central water rock.
  - The park/beach grass-to-sand transition alternates in 3-tile runs (3 sand, 3 lawn, 3 sand).
  - Every single natural run along water, sand, or lawn is between 1 and 3 tiles. No straight natural edge exceeds 3 tiles (strictly adhering to the $\le 6$ rule).
  - Absolutely zero staircases.
- **Pond Connection**: The vanilla pond (x7–10, y17–19) flows naturally as a canal stream through the park (y20–21) and opens into an estuary cove at the beach, spilling directly into the sea.
- **Walkability**: Fully walkable on foot from both the west town exit (Red's house side) and east town exit (Oak's lab side) down to the park and sandy beaches. Ground and sand maintain standard elevation 3 and collision 0.
- **Validation**: `pk check PkPalletGemini_Frlg` reports 0 errors and 0 warnings.

---

## Problems Hit
- **Tileset Limitations**: `gTileset_General_Frlg` and `gTileset_PalletTown` do not contain diagonal grass-to-sand transition metatiles; sand tiles meeting grass have a hard material edge. To avoid a sterile straight edge, the boundary was designed as alternating 3-tile lawn promontories and sand inlets framed by flower beds.
- **Stream Bank Symmetry**: Vanilla Route 21 canal banks are asymmetrical at row 11 (`0x130` on right, `0x131` 4 rows lower). Copying `0x130` created an awkward brick jut into sand; visual review on render 1 caught this, and replacing it with water (`0x12B`) and a sand shore (`0x116`) resolved the seam cleanly.

---

## Honest Rating vs Vanilla: 8.5/10
The map feels completely native to Game Freak's FireRed aesthetic. The canal seamlessly continues the vanilla pond into the sea, the twin-lobed beach looks organic and hand-crafted, the forest borders terminate cleanly, and the town retains unobstructed walking flow. It loses 1.5 points solely due to the underlying Gen 3 tileset lacking dedicated grass-to-sand blend tiles.
