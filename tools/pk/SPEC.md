# `pk` — Token-Efficient ROM-Hacking Interface for LLM Agents

**Target repo:** pokeemerald-expansion 1.17.1, built as **FireRed** (`make firered`, see `build_fr.sh`).
**Audience of this document:** the LLM that will implement `pk`.
**Audience of `pk` itself:** other LLM agents that will edit maps, tilesets, events and dialogue.

---

## 0. Mission

LLM agents currently burn thousands of tokens on this repo because the data is binary (`map.bin`,
`metatiles.bin`), verbose (`map.json`), or tangled (dialogue mixed into 14 KB `scripts.inc` files),
and because they must re-derive engine rules (bit layouts, line-width limits, palette limits) every
session.

`pk` is a single CLI program that:

1. **Reads** project data and prints *compact, semantic, text-only views*.
2. **Writes** project data through *small operations* (not whole-file rewrites).
3. **Owns all engine knowledge** (bit packing, tile limits, text wrapping, header registration), so
   the calling LLM never has to.
4. **Validates** every write and reports errors in a fixed, terse grammar.

Success metric: typical tasks cost **≤10%** of the tokens they cost today (see §12 for budgets).

### Hard rules

- **The repo files stay the single source of truth.** `pk` reads/writes the existing formats in place
  (`map.json`, `layouts.json`, `map.bin`, `border.bin`, `scripts.inc`, `data/text/*.inc`, tileset
  folders, `src/data/tilesets/*.h`). Porymap must keep working on anything `pk` produces.
  `pk`'s own sidecar data (semantic names, stamps, index cache) lives under `tools/pk/data/` and
  `.pk/` and is never required by the build.
- **The repo is NOT a git repository.** `pk` must provide its own undo (§3.4).
- **Never print a whole file.** Every command has bounded output; large results are paginated.
- **Deterministic output.** Same input → byte-identical output (sorted keys, stable ordering).
  This lets LLMs diff results and lets tests use golden files.
- Language: **Python 3.12+**, dependency: **Pillow 12** (already
  installed). No other third-party deps. Must run from PowerShell and Git Bash on Windows.

---

## 1. Ground truth: data formats in this repo

Verify each item against the source before relying on it; file/line references are given.

### 1.1 Maps and layouts
- `data/maps/<MapName>/map.json` — header, connections, `object_events`, `warp_events`,
  `coord_events`, `bg_events`. `header.inc`, `events.inc`, `connections.inc` next to it are
  **generated** by `tools/mapjson` (`map_data_rules.mk`) — never edit them, never show them.
- `data/maps/<MapName>/scripts.inc` — event scripts **and** most map-local text.
- `data/maps/map_groups.json` — map group membership.
- `data/layouts/layouts.json` — `{id, name, width, height, border_width, border_height,
  primary_tileset, secondary_tileset, border_filepath, blockdata_filepath, layout_version}`.
  `layout_version` is `"frlg"` or emerald; it selects the attribute format and tileset limits.
- `map.bin` / `border.bin`: little-endian `u16` per block, row-major, `width*height` entries
  (`include/global.fieldmap.h:7-12`):
  - bits 0-9 metatile id (`0x3FF` = undefined)
  - bits 10-11 collision (0 passable, 1-3 impassable; 3 = `MAPGRID_IMPASSABLE`)
  - bits 12-15 elevation (0-15)

### 1.2 Tilesets
Folder `data/tilesets/{primary|secondary}/<name>/` containing:
- `tiles.png` — indexed 4bpp image, 8×8 tiles left-to-right, top-to-bottom, width 128 px
  (16 tiles). Only pixel *indices* matter; colours come from palettes.
- `palettes/NN.pal` — JASC-PAL, 16 colours each (`JASC-PAL\n0100\n16\n r g b ...`). Colour
  index 0 is transparent. Colours are quantised to 5 bits/channel on GBA (`c & 0xF8`).
- `metatiles.bin` — 8 × `u16` per metatile (2 layers × 4 tiles; order per layer TL, TR, BL, BR;
  first 4 = bottom layer, next 4 = top layer). Each `u16`: bits 0-9 tile index, bit 10 hflip,
  bit 11 vflip, bits 12-15 palette slot.
- `metatile_attributes.bin`
  - **FRLG layouts: `u32` per metatile** (`src/fieldmap.c:83-104`): behavior bits 0-8,
    terrain 9-13, attr2 14-17, attr3 18-23, encounter type 24-26, attr5 27-28,
    **layer type 29-30**, bit 31.
  - Emerald layouts: `u16` per metatile: behavior 0-7, layer type 12-15.
  - Layer types (`include/global.fieldmap.h:54-56`): 0 NORMAL (middle+top), 1 COVERED
    (bottom+middle), 2 SPLIT (bottom+top).
  - Behaviors: `include/constants/metatile_behaviors_frlg.h` (FRLG) and
    `include/constants/metatile_behaviors.h` (Emerald).
- Limits (`include/fieldmap.h:4-14`):

  | | FRLG | Emerald |
  |---|---|---|
  | tiles in primary | 640 | 512 |
  | metatiles in primary | 640 | 512 |
  | palettes in primary | 7 | 6 |
  | totals (tiles / metatiles / palettes) | 1024 / 1024 / 13 | same |

  Secondary tilesets start at index = primary limit for both tile ids and metatile ids, and their
  palettes occupy slots `NUM_PALS_IN_PRIMARY .. 12`.
- Registration (three files, all must be updated when a tileset is created):
  - `src/data/tilesets/graphics.h` — `gTilesetTiles_X` (`INCGFX_U32(".../tiles.png",
    ".4bpp.fastSmol")`) and `gTilesetPalettes_X[][16]` (one `INCGFX_U16(".../NN.pal",
    ".gbapal")` per palette file).
  - `src/data/tilesets/metatiles.h` — `gMetatiles_X`, `gMetatileAttributes_X` (`INCBIN_U16`).
  - `src/data/tilesets/headers.h` — `const struct Tileset gTileset_X = { .isCompressed = TRUE,
    .isSecondary = ..., .tiles, .palettes, .metatiles, .metatileAttributes, .callback = NULL }`.
  Copy the exact style of the neighbouring FRLG entry (e.g. `gTileset_PalletTown`).

### 1.3 Text
- Strings are `.string "...$"` directives, possibly spanning several consecutive `.string` lines,
  under a label `Label::`. Locations: `data/maps/*/scripts.inc`, `data/text/*.inc`,
  `data/scripts/*.inc`. (Also C strings `_("...")` in `src/` — out of scope for v1, read-only.)
- Control codes from `charmap.txt`: `\n` next line, `\l` scroll one line, `\p` new box, `$` end,
  `{PLAYER}`, `{RIVAL}`, `{STR_VAR_1..3}`, `{COLOR ...}`, `{PAUSE n}`, `{FONT_*}` etc.
  Placeholders must be measured with a configurable assumed width (`{PLAYER}` = 7 chars max).
- Glyph widths: `src/fonts.c` (`gFontNormalLatinGlyphWidths`, `gFontShortLatinGlyphWidths`, …).
  Parse these tables; map char → byte via `charmap.txt`.
- Field message box: 2 visible lines. **Max line width must be calibrated**, not guessed:
  compute pixel widths of every line of every existing map string, take the maximum seen per
  font (should be ~208 px for FONT_NORMAL); store in `tools/pk/data/text_limits.json`; allow
  override. The engine already has `BreakStringAutomatic` (`src/line_break.c`) — mirror its
  rules where sensible.

### 1.4 Build
`bash build_fr.sh firered` writes `/tmp/fr_build.log` and appends `EXIT <code>`. `pk build`
wraps this (§9).

---

## 2. Architecture

```
tools/pk/
  SPEC.md                 this file
  pk.py                   entry point (argparse-free tiny dispatcher is fine)
  pk.cmd / pk             shims: `python tools/pk/pk.py %*`
  pk/
    core/      repo.py (paths, layout_version), io_bin.py, jascpal.py, charmap.py,
               fonts.py, journal.py (undo), out.py (output grammar), errors.py
    index/     builder.py, query.py
    maps/      grid.py (decode/encode), view.py, ops.py, stamps.py, events.py
    text/      parse_inc.py, wrap.py, ops.py
    tiles/     pixeldsl.py, compiler.py, register.py, render.py
    batch.py   multi-op transactions
    mcp_server.py   (milestone 6, optional)
  data/        sidecar knowledge shipped with pk (see §5.2, §7.4)
  tests/       golden-file tests
.pk/           per-repo runtime state: index cache, undo journal (create on first run)
```

All modules are importable; the CLI is a thin layer. Every command is a pure function
`(repo_state, args) -> (result_lines, writes)`; `writes` are applied by a single transaction layer
(§3.4) so `--dry-run`, undo and batch work uniformly.

---

## 3. Protocol: how LLMs talk to `pk`

### 3.1 Invocation
```
pk <noun> <verb> [positional...] [--key value...] [--dry-run] [--json]
pk batch < ops.txt        # one command per line, applied atomically
pk help [noun]            # ≤40 lines, the LLM's only manual
```

### 3.2 Output grammar (default, line-oriented)
- First line is always a status:
  `OK <summary>` | `ERR <CODE> <message>` | `WARN <n>` (followed by OK/ERR line semantics).
- Following lines are data. Tables are space-separated with **no decorative characters**,
  no alignment padding beyond single spaces, no ANSI colours.
- Pagination: `MORE <next-offset>` as the last line when truncated; `--offset/--limit`.
- `--json` emits one JSON object (`{"ok":bool,"code":..,"data":..}`) for programmatic callers.

Example:
```
$ pk text set PalletTown_Text_OakDontGoOut "Hey! Wait! Don't go out!"
OK text PalletTown_Text_OakDontGoOut 1box 2lines
Hey! Wait! Don't go out!$
```

### 3.3 Error codes (stable, documented in `pk help errors`)
`NOTFOUND`, `AMBIGUOUS` (lists ≤10 candidates), `BOUNDS`, `LIMIT` (tile/palette/metatile budget),
`TEXTFIT` (cannot fit even after wrapping), `PARSE`, `CONFLICT` (file changed since read),
`BUILD`, `INTERNAL`. Every error message names the fix where possible:
`ERR LIMIT secondary tileset forest uses 391/384 tiles; reduce unique tiles or enable --dedupe-flip`.

### 3.4 Transactions & undo
- Each mutating command (or `pk batch`) is one transaction: compute all writes in memory,
  validate, then write. On any error nothing is written.
- Before writing, the previous content of every touched file is stored in
  `.pk/journal/<n>/` with a manifest. `pk undo [n]` restores; `pk log` lists
  (`<n> <time> <command>` one per line, last 20).
- `CONFLICT` detection: if a file's mtime/hash changed between read and write (e.g. Porymap open),
  abort.

### 3.5 Name resolution
Every identifier argument accepts exact names or unique case-insensitive substrings/fuzzy matches
(`pallet` → `PalletTown_Frlg`). Ambiguity returns `ERR AMBIGUOUS` with candidates, never a guess.
Map args accept map dir name, `MAP_*` id, or layout id.

---

## 4. Index & search (`pk find`, `pk index`)

- `pk index` (auto-run lazily when stale; staleness = any source mtime newer than cache) builds
  `.pk/index.sqlite` (stdlib `sqlite3`) with tables: maps, layouts, tilesets, texts (label, file,
  line, plain text), scripts (label, file, line), flags/vars (from `include/constants/flags.h`,
  `vars.h`), trainers, object gfx ids, metatile behaviors, music, warps graph.
- `pk find <query> [--kind map|text|script|flag|var|trainer|gfx|behavior]`
  → `kind name location snippet(≤60 chars)` per line, ranked, max 20.
- `pk map ls [filter]` → `MapName WxH primary/secondary` one per line.
- `pk map info <map>` → ≤15 lines: id, layout, size, tilesets, music, weather, type, connections,
  counts of events, warp destinations.
- `pk refs <label|flag|var>` → every place it is used.

---

## 5. Maps

### 5.1 Grid view
```
pk map show <map> [--layer meta|coll|elev|all] [--rect x,y,w,h] [--raw]
```
Output:
```
OK PalletTown_Frlg 24x20 legend:9
legend . grass  T tree  " tall_grass  = path  ~ water  # fence  H house  ? 0x2A1  ...
   0         1         2
   012345678901234567890123
 0 TTTTTTTTTT==TTTTTTTTTTTT
 1 T........==..........T
 ...
```
- Each distinct metatile in view gets one printable ASCII symbol. Symbol choice: if the metatile
  has a semantic name (§5.2) with a preferred symbol, use it; else assign from a fixed pool in
  order of frequency. Unnamed metatiles appear in the legend as hex id.
- Multi-metatile structures recognised as stamp instances (§5.3) are shown with the stamp's letter
  on every covered cell and listed as `stamp house_small @10,4` under the grid.
- `--layer coll` prints `0-3` per cell, `--layer elev` prints `0-F`; `all` prints three grids.
- Coordinates rulers are mandatory (LLMs miscount otherwise). For maps >64 wide, auto-split into
  bands with `--rect`.
- `--raw` prints hex ids (debug only).

### 5.2 Semantic metatile names
`tools/pk/data/metatiles/<tileset>.json`: `{ "<id hex>": {"name": "tree_tl", "sym": "T",
"tags": ["tree","solid"]} }`.
- `pk meta autoname <tileset>` seeds names from behaviors (`MB_TALL_GRASS` → `tall_grass`,
  water behaviors → `water`, `MB_*_DOOR` → `door`, impassable-in-most-maps → `solid_N`).
- `pk meta name <tileset> <id> <name> [--sym X] [--tags a,b]`.
- `pk meta ls <tileset> [--unnamed]` → `id name sym behavior layer usage_count`.
- `pk meta show <tileset> <id|name>` → ASCII rendering of the 16×16 metatile using the pixel-DSL
  alphabet (§7.1) plus its palette — lets a text-only LLM "see" a tile.
- Default collision per name: tags `solid` → collision 1, otherwise 0; overridable per paint.
- A metatile name is resolved within the map's primary+secondary tileset pair.

### 5.3 Stamps (prefabs)
`tools/pk/data/stamps/<name>.json`: grid of metatile ids + collision + elevation, plus
`origin`, `tileset_pair`.
- `pk stamp extract <map> x,y,w,h <name>` — cut from an existing map.
- `pk stamp ls [--tileset X]` → `name WxH tilesets`.
- `pk stamp mine <tileset_pair>` (milestone 4) — find repeated rectangular patterns across all
  maps that use the pair (≥2 occurrences, ≥2×2, contains ≥1 non-ground tile) and propose stamps.

### 5.4 Edit operations
All take `<map>` first; coordinates are `x,y`; sizes `WxH`.
```
pk map paint <map> x,y[,WxH] <metatile|name> [--coll n] [--elev n]
pk map fill  <map> x,y <metatile|name>                  # flood fill same-metatile region
pk map line  <map> x1,y1 x2,y2 <name>                    # straight line (paths, fences)
pk map stamp <map> <stamp> x,y
pk map grid  <map> x,y  <<EOF                            # apply an ASCII patch using legend syms
TT..
T==.
EOF
pk map resize ...                                        # see §5.7 (smart resize)
pk map new <MapName> --size WxH --tilesets <primary>,<secondary> --fill grass
           [--type town|route|indoor] [--music MUS_X]    # creates layout + map + group entry
pk map border <map> <name|ids...>
```
- `grid` patch: `?` or space = keep existing cell. Symbols resolve via the map's current legend.
- After every edit, print only the **changed rect** (+1 cell margin) in grid form, never the
  whole map.
- Auto-tiling (milestone 4): names may be *tile families* (`path`, `water`, `ledge`) whose
  edges/corners are selected automatically from neighbour context using a rule table
  `tools/pk/data/autotile/<family>.json` (4-bit or 8-bit neighbour mask → metatile).
  `pk map paint ... path --auto`.

### 5.5 Events
```
pk ev ls <map>                      # one line per event
  obj#0 LOCALID_PALLET_SIGN_LADY @3,10 e3 WOMAN_1_FRLG WANDER 1x4 -> PalletTown_EventScript_SignLady
  warp#0 @6,7 e0 -> PalletTown_PlayersHouse_1F_Frlg#1
  coord#0 @12,1 VAR_MAP_SCENE_PALLET_TOWN_OAK=0 -> PalletTown_EventScript_OakTriggerLeft
  bg#0 sign @11,4 -> PalletTown_EventScript_OakLabSign
pk ev add <map> obj @x,y gfx=WOMAN_1 move=FACE_DOWN [script=Label|text="..."] [flag=...]
pk ev add <map> warp @x,y to=<map>#<warpIndex>
pk ev add <map> sign @x,y text="..."
pk ev set <map> obj#2 x=5 y=6 move=WANDER range=2x2
pk ev rm  <map> obj#2
```
- Short aliases: `gfx=WOMAN_1` → resolves `OBJ_EVENT_GFX_WOMAN_1_FRLG` (prefer `_FRLG` for frlg
  layouts); `move=WANDER` → `MOVEMENT_TYPE_WANDER_AROUND`.
- `text="..."` sugar on `obj`/`sign`: generates `Map_EventScript_Auto_N` (lock/faceplayer/msgbox
  MSGBOX_NPC or MSGBOX_SIGN/release/end) and `Map_Text_Auto_N` in `scripts.inc`, wrapped (§6.2).
- New `local_id` constants: follow how the repo defines `LOCALID_*` for the map (check whether
  they're declared in map.json or a header) and generate accordingly.
- Warp add can optionally create the reciprocal warp (`--both`).

### 5.6 Terrain sculpting (heightmap + materials → baked metatiles)

LLMs should shape land *semantically* ("raise a plateau here, river there, forest edge along the
north") and let `pk` pick every cliff edge, corner, ledge, shoreline, collision bit and elevation.

**Terrain model** — a per-map semantic layer, stored in `.pk/terrain/<map>.json` (sidecar, never
needed by the build; regenerable from the map via `infer`):
- `height[y][x]`: integer level 0..7 (0 = water level, 1 = normal ground, 2+ = plateaus).
- `mat[y][x]`: material id from a small vocabulary: `grass`, `tall_grass`, `path`, `sand`,
  `water`, `deep_water`, `tree`, `rock`, `flowers`, `floor`, `wall`, `void`.
- `lock[y][x]`: true for cells belonging to hand-placed content (stamps, buildings, signs, doors,
  warps, cells under events). Baking never touches locked cells.

**Commands**
```
pk terrain infer <map>                     # reconstruct height/mat/lock from current metatiles
pk terrain show <map> [--rect] [--layer height|mat|lock|all]
  height grid: digits 0-7; mat grid: one letter per material (legend line first)
pk terrain raise  <map> x,y[,WxH] [+n]     # also: lower, set-height <n>
pk terrain brush  <map> <shape> <mat|h=n>  # shapes: rect x,y,WxH | circle x,y,r | poly x,y x,y ...
                                           #         | path x,y x,y ... width=w (polyline, for roads/rivers)
pk terrain river  <map> x,y x,y ... [width=2]   # carves water + shores
pk terrain forest <map> <shape> [density=1.0] [edge=soft|hard]  # fills with tree stamps aligned
                                                                 # to the tileset's 2x2 tree grid
pk terrain ramp   <map> x,y dir=n|s|e|w    # stairs/slope between two height levels
pk terrain ledge  <map> x1,y1 x2,y2        # one-way jump ledges (dir derived from heights)
pk terrain smooth <map> [<shape>] [iter=1] # removes 1-cell spikes/holes that cannot be tiled
pk terrain bake   <map> [--rect] [--dry-run]
```
- Every terrain edit marks a *dirty rect*; `bake` (run automatically at the end of a terrain
  command unless `--no-bake`) re-tiles only dirty cells + a 1-cell margin, so hand-made detail
  elsewhere survives.
- Output after an edit: the changed rect as metatile grid (§5.1) + `WARN` lines for cells the
  rules could not resolve (e.g. `WARN untileable 12,7 cliff inner-corner missing in tileset`).

**Bake rules (learned, not hard-coded)** — `tools/pk/data/terrain_rules/<primary>+<secondary>.json`:
- Key = (cell material, cell height, 8-neighbour signature of (material, height-delta)),
  value = metatile id + collision + elevation, with counts.
- `pk terrain learn <tileset_pair|all>` builds the table by running `infer` on every vanilla map
  that uses the tileset pair and counting which metatile appears in each context (945 maps give
  plenty of samples: cliffs, ledges, shores, tree canopies). Rare contexts fall back by relaxing
  the signature (8-neighbour → 4-neighbour → material only).
- `infer` needs each metatile's terrain meaning: derive from §5.2 names/tags, behaviors
  (`MB_*_LEDGE`/jump behaviors, water/shore behaviors, tall grass) and observed elevation bits;
  unresolved metatiles get `mat=void, lock=true` and are listed by `pk terrain learn --report`
  so a human/LLM can name them once via `pk meta name`.
- Collision/elevation are part of the rule output. Engine semantics (verify against vanilla
  statistics, store the result in the rules file): elevation 0 = transition/any, 15 = bridge /
  multi-level, water usually 1, ground usually 3, each plateau level higher; cliff faces and
  trees are collision 1.
- Rules files are versioned; `bake` refuses to run against a tileset whose metatile ids changed
  since `learn` (`ERR CONFLICT relearn rules for gTileset_X`).

### 5.7 Smart resize / expanding maps

```
pk map resize <map> --add top=4,left=6[,right=n,bottom=n]   # negative values crop
pk map resize <map> --to WxH --anchor tl|t|tr|l|c|r|bl|b|br
              [--fill extend|border|terrain|<metatile|name>] [--dry-run]
```
Everything that depends on coordinates must be updated in the **same transaction**:
1. **Block data** — new `map.bin`; `layouts.json` width/height. If the layout is shared by
   several maps (check `layout` field across all `map.json`), require `--fork-layout` (copies the
   layout under a new id for this map) or apply to all and report every affected map.
2. **Events in this map** — shift `x,y` of all object/warp/coord/bg events by (left, top); events
   that end up outside after a crop → `ERR BOUNDS` listing them unless `--drop-out-of-bounds`.
3. **Scripts with literal coordinates** — in this map's `scripts.inc` (and any script reachable
   from its events), patch coordinate arguments of: `setmetatile x, y, ...`,
   `setobjectxyperm id, x, y`, `setobjectxy id, x, y`. Patch coordinate arguments in **all maps'**
   scripts for commands targeting this map: `warp*`, `setwarp`, `setdynamicwarp`,
   `setescapewarp MAP_X, x, y` (forms with a warp-id instead of coords need no change).
   `getplayerxy` followed by comparisons is too semantic to auto-patch: list each occurrence as
   `WARN manual <file:line>` with the delta to apply. (Counts in this repo: ~736 setmetatile,
   ~194 setobjectxyperm, ~132 warp, ~40 setobjectxy, ~27 setdynamicwarp, ~25 getplayerxy.)
4. **Connections** — offsets in `map.json` `connections` are measured along the shared edge.
   When rows are added on top, every `left`/`right` connection's offset in this map and its
   reciprocal in the neighbour change by ∓top; columns added on the left do the same for
   `up`/`down` connections. Keep both sides consistent; verify reciprocity in `pk check`.
   Report connections whose overlap now ends past the edge (`WARN`).
5. **Terrain sidecar & stamps** — shift `.pk/terrain/<map>.json`; regenerate stamp instance
   positions.
6. **Fill of new area**:
   - `extend` (default): repeat the outermost row/column outward, then run `terrain smooth` +
     `bake` on the seam, so tree belts, fences, paths and shorelines continue naturally.
   - `border`: tile `border.bin` (matches what the player already sees beyond the edge).
   - `terrain`: extend the height/material grid by nearest-neighbour, then bake (best for cliffs).
   - `<name>`: flat fill with one metatile.
7. **Engine limits** — reject with `ERR LIMIT` when `(width + 15) * (height + 14) > 10240`
   (`MAX_MAP_DATA_SIZE`, `MAP_OFFSET` in `include/fieldmap.h:15-25`); also warn on maps narrower
   than the screen for outdoor maps.
8. Output: `OK resize Route1_Frlg 24x40 -> 30x44 events:12 scripts:4 conns:2 warn:1`, followed
   by the WARN lines and the seam region grid. Nothing else.

`pk map extend-to <map> <neighbour> --fit` (convenience): resize along the shared edge so the
connection fully overlaps the neighbour's edge, then fill with `extend`.

---

## 6. Dialogue

### 6.1 Read
```
pk text ls <map|file> [--grep word]   # label  first 50 chars (plain, codes stripped)
pk text get <label>                   # plain text with box/line structure shown:
  OK PalletTown_Text_OakDontGoOut file:data/maps/PalletTown_Frlg/scripts.inc:412 font:NORMAL
  [1] OAK: Hey! Wait!
      Don't go out!
pk text grep <regex>                  # across all strings, ≤20 hits
```

### 6.2 Write
```
pk text set <label> "<plain text>" [--speaker NAME] [--keep-breaks]
pk text add <map> <label> "<plain text>"
pk text replace <regex> <repl> [--scope map|all] [--dry-run]   # bulk, shows count + 3 samples
```
- Input is **plain text**. Blank line or `||` = force new box (`\p`); `|` = force new line;
  `{PLAYER}` etc. pass through.
- Wrapping algorithm: greedy word wrap by pixel width (§1.3); 2 lines per box; first line break
  `\n`; subsequent lines in the same "paragraph" use `\l` (scroll) like vanilla text; explicit box
  breaks `\p`. Must reproduce vanilla wrapping for ≥90% of existing strings when fed their own
  stripped text (use as a test, §11).
- Output emitted as `.string` lines split at each control code, matching repo style:
  ```
  PalletTown_Text_OakDontGoOut::
  	.string "OAK: Hey! Wait!\n"
  	.string "Don't go out!$"
  ```
- Reject (ERR PARSE) characters absent from `charmap.txt`; suggest replacements (`’`→`'`, `…`→`...`).
- Edits must preserve everything outside the label's string block byte-for-byte.

---

## 7. Tilesets — AI-drawn pixel art

### 7.1 Pixel DSL (`.px` files)
Plain text, designed for LLM authoring and minimal tokens.
```
# tools/pk/art/forest/tree_big.px
palette forest_main          # named palette defined in palettes.px or inline:
# pal forest_main: . transparent, a #183018, b #285830, c #48a048, d #a0d068, e #604020, f #302010
size 32x32                   # multiple of 16; becomes a 2x2 metatile block
layer top                    # top | bottom | both (two grids follow)
grid
................aabbbbaa........
...
```
- Alphabet: `.` = index 0 (transparent); `a-z`, `A-Z`, `0-9` map to palette entries per the
  palette declaration. One palette per `.px` block of 16×16 must resolve to ≤15 colours + transparent.
- **Generative ops** so the LLM does not have to type every pixel:
  ```
  fill c                          # whole canvas
  rect 0,0 16x4 b
  noise c,d 0.15 seed=3           # sprinkle d over c with probability
  dither b,c checker 0,8 16x8
  outline b                       # outline non-transparent region
  mirror x | mirror y
  copy tree_big 0,0 16x16 -> 16,0
  recolor c=d e=f                 # palette swap variant
  from grass_base                 # start from another px file then apply ops
  shade light=tl b>c>d            # simple directional ramp along the shape edge
  ```
  Ops run in order; `grid` (if present) is applied last, with ` ` = keep.
- Variants: `variant tree_big_autumn from tree_big recolor c=h d=i` in one line.
- `pk px render <file> [--scale 4]` → PNG preview (for multimodal callers) and
  `pk px show <file>` → ASCII echo with palette legend (for text-only callers).
- `pk px check <file>` → colour count per 8×8 tile, per 16×16 metatile, unique-tile count.

### 7.2 Tileset spec (`tileset.yaml`-like, use a simple line format, no YAML dependency)
```
# tools/pk/art/forest/tileset.pkt
name forest
kind secondary                # primary | secondary
version frlg                  # frlg | emerald
pair_with gTileset_General_Frlg
palettes palettes.px
# metatile entries: <name> <px file>[@x,y] <behavior> <layer_type> [tags] [sym]
grass        ground.px@0,0    MB_NORMAL          NORMAL   ground          .
tall_grass   ground.px@16,0   MB_TALL_GRASS      NORMAL   grass           "
tree_tl      tree_big.px@0,0  MB_NORMAL          COVERED  solid,tree      T
tree_tr      tree_big.px@16,0 MB_NORMAL          COVERED  solid,tree      T
...
stamp tree_big = tree_tl tree_tr / tree_bl tree_br
autotile path = path_*.px rules=blob4
```

### 7.3 Compiler (`pk tileset build <spec>`)
Pipeline, all deterministic:
1. Render each metatile's bottom and top 16×16 layers from `.px` sources.
2. Slice into 8×8 tiles. **Deduplicate** including hflip/vflip/both variants; fully transparent
   tile → reuse tile 0 (keep tile 0 blank as vanilla does).
3. **Palette assignment**: each 8×8 tile needs a palette slot with ≤15 colours + transparent.
   Treat as packing problem: build colour sets per tile, merge tiles into ≤N palettes
   (N = available slots: FRLG secondary 6 (slots 7-12), FRLG primary 7) via greedy
   largest-set-first + subset merging; if over budget, report `ERR LIMIT` with the colour sets
   that don't fit and the closest-colour merge suggestion (`--quantize` to auto-merge the
   perceptually closest colours, ΔE in Lab).
   Secondary tilesets may also reference the paired primary's palettes/tiles: reuse a primary
   tile if pixels+palette match exactly.
4. Quantise colours to GBA 15-bit (`& 0xF8` per channel) before comparison.
5. Emit `tiles.png` (indexed, width 128, palette = greyscale ramp or first palette — check what
   `gbagfx` expects), `palettes/00..15.pal` in JASC format (unused = black, padded to the count
   used by sibling tilesets), `metatiles.bin`, `metatile_attributes.bin` (u32 FRLG / u16 Emerald,
   behavior + layer type, other fields 0 unless spec sets `terrain=`/`encounter=`).
6. Register in `graphics.h`, `metatiles.h`, `headers.h` (idempotent — update in place if
   `gTileset_<Name>` exists). Name `gTileset_<CamelName>`.
7. Write `tools/pk/data/metatiles/<tileset>.json` (names/syms/tags) and stamps.
8. Report:
   ```
   OK tileset forest secondary frlg
   metatiles 48/384 tiles 211/384 palettes 4/6 dedup_saved 173
   ```
- Budgets checked against §1.2 limits. Metatile ids are stable across rebuilds: existing names keep
  their ids (store mapping), new names append; removed names leave a hole filled with an
  empty metatile only with `--compact` (which also remaps all maps using the tileset).

### 7.4 Decompiler (`pk tileset export <tileset>`)
The reverse: turns an existing vanilla tileset into `.px` + `.pkt` (one `.px` per metatile or
sheet, palettes extracted), so LLMs can create variants of vanilla art (`from` / `recolor`).
Must round-trip: `export` then `build` yields byte-identical `metatiles.bin`/attributes and a
pixel-identical rendered tileset (tile order may differ only with `--reorder`).

### 7.5 Previews
`pk render map <map> [--rect] [--out file.png]` and `pk render tileset <ts>` produce PNGs from the
actual binary data for vision-capable callers and humans. Text callers use `pk map show` /
`pk meta show`.

---

## 8. Scripts (light v1 scope)
- `pk script get <label>` — prints just that label's block (until next `::` label), ≤60 lines.
- `pk script set <label> <<EOF ... EOF` — replace one block; validate that referenced labels,
  flags, vars, `LOCALID_*` exist (via index).
- `pk script new <map> npc|sign|trigger|item ...` — templates mirroring vanilla FRLG patterns.
- Full script DSL is out of scope; the goal is surgical, label-sized reads/writes.

---

## 9. Build & check
```
pk check [map|all]   # static validation, no compile:
                     #   metatile ids within tileset pair, warps target existing warp index,
                     #   reciprocal warps, events inside bounds, scripts/labels exist,
                     #   texts fit, tileset budgets, palette files present
pk build [--release] # runs build_fr.sh (bash), returns:
                     #   OK build 41s pokefirered.gba 16MB
                     #   or ERR BUILD with ≤15 deduplicated error lines: file:line: message
```
`pk build` should run `pk check` first (skip with `--no-check`).

---

## 10. Agent onboarding files (deliverable)
1. `tools/pk/AGENT.md` — ≤60 lines: the command cheat-sheet and the rules "never open `.bin`,
   `map.json`, `scripts.inc`, `graphics.h` directly; use `pk`".
2. A short block appended to repo-root `CLAUDE.md` (create if missing) pointing to `AGENT.md`.
3. `pk help` prints the same content as AGENT.md.

---

## 11. Testing (required)
- `tools/pk/tests/` runnable with `python -m unittest` (stdlib only).
- **Round-trip tests** over *all* maps: decode→encode `map.bin` identical; `map.json` load→save
  identical (preserve key order and 2-space indent exactly like the repo; verify on all 945 maps).
- Text: parse→emit of every string block byte-identical; wrapping reproduces vanilla for ≥90%.
- Tileset: export→build round-trip on `pallet_town_frlg` and `general_frlg`.
- Golden output tests for `map show PalletTown_Frlg`, `ev ls`, `text get`.
- Terrain: for each of 20 vanilla outdoor maps, `terrain infer` then `terrain bake` over the whole
  map (ignoring locked cells) reproduces ≥95% of the original metatiles, collision and elevation.
- Resize: `resize --add top=3,left=3` then `--add top=-3,left=-3` restores every touched file
  byte-identically (map.bin, layouts.json, map.json of the map and its neighbours, scripts.inc).
- End-to-end: create a new secondary tileset from 10 `.px` metatiles, `map new` using it, add an
  NPC with text, `pk build` succeeds, ROM boots in mGBA (`tools/mgba` exists) — manual step OK.

---

## 12. Token budgets (acceptance criteria)
Measured as characters of tool output the calling LLM must read (≈ chars/4 tokens).

| Task | Today (approx.) | `pk` target |
|---|---|---|
| Change one NPC line | read 14 KB scripts.inc (~4k tok) | ≤ 300 chars |
| View a 24×20 town | not readable / custom script | ≤ 900 chars |
| Place 5 trees + path | write ad-hoc Python, read bin | ≤ 5 commands, ≤ 600 chars output |
| Add NPC with dialogue | edit map.json + scripts.inc (~6k tok) | 1 command, ≤ 300 chars |
| New 30-metatile tileset | many files, many errors | `.px` files + `.pkt` + 1 build cmd |
| Locate where a flag is used | grep noise | ≤ 20 lines |
| Add a plateau with cliffs + stairs | dozens of hand-picked metatiles | 2 commands (`raise`, `ramp`) |
| Widen a route by 6 columns | bin edit + manual event/script/connection fixes | 1 command, ≤ 800 chars output |

---

## 13. Milestones (implement in order, each shippable)
1. **Core**: repo paths, output grammar, journal/undo, batch, name resolution, index + `find`,
   `map ls/info`, `build`, AGENT.md.
2. **Text**: parse/emit, font widths + calibration, `text ls/get/set/add/replace`, tests.
3. **Map view & edit**: grid decode/encode, `map show`, `paint/fill/line/grid/border/resize`,
   `meta autoname/name/ls/show`, events `ev ls/add/set/rm`, `check`.
4. **Stamps, autotile & resize**: `stamp extract/ls/mine`, autotile families, `map new`,
   smart `map resize` / `extend-to` (§5.7).
4b. **Terrain**: `terrain infer/learn/show`, sculpt commands, `bake`, terrain tests (§5.6).
5. **Tilesets**: pixel DSL + ops, `px render/show/check`, compiler, registration, export
   (decompiler), round-trip tests.
5b. **Blueprints** (§14): parser, `check/plan/apply/diff/pull/export/world/render`, round-trip
   test (`export`→`apply` = no-op on 20 vanilla maps), example blueprint applies and builds.
6. **MCP server** (optional): expose every command as an MCP tool over stdio with the same output
   grammar, so agents can call `pk` without a shell. Tool list must stay small: one tool per noun
   with a `verb` argument.

## 14. Blueprints — declarative location designs

A **blueprint** (`.bp`) is a compact, text-only description of what a location *should be*:
size, terrain, structures, NPCs, dialogue, connections and design intent. It is the main exchange
format between LLMs (and humans) for "enhanced" locations: one LLM writes/reads a ~60-line
blueprint instead of thousands of tokens of maps, JSON and scripts; `pk` turns it into real data.

### 14.1 Location & role
- `blueprints/<Region>/<MapName>.bp` at repo root (one per location; interiors get their own file).
- `blueprints/world.bp` — region index: one line per location
  (`PalletTown_Frlg status=applied size=32x28 conns=Route1_Frlg,Route21_North_Frlg`).
- Blueprints are sidecar design sources: never needed by the build, but `pk` keeps map data and
  blueprint in sync (§14.4).

### 14.2 Format (line-based, sections, `#` comments, 2-space indent inside sections)
```
blueprint <MapName> v<N>
base <existing MapName> | new
size WxH [grow=top:n,left:n,right:n,bottom:n] [fill=extend|border|terrain]
tilesets <primary> <secondary>
header music=MUS_X weather=SUNNY type=TOWN flash=no bike=yes run=yes
intent "<one-line purpose>"          # design intent, read by LLMs, ignored by the compiler

terrain                              # applied in order, later lines override earlier
  fill grass h1
  rect x,y WxH <mat|forest|h=n> [edge=soft|hard]
  circle x,y r <mat|h=n>
  path x,y x,y ... width=w <mat>
  river x,y x,y ... width=w
  ramp x,y dir=n|s|e|w
  ledge x1,y1 x2,y2
  keep x,y WxH                       # never touched by blueprint terrain (hand-made area)

structures                           # stamps; ids are local names usable as anchors
  <id> <stamp> @x,y [door-><Map>#<warp>] [locked]
npcs
  <id> <gfx> @x,y <movement> [range=WxH] (text="..." | script=<Label>) [flag=FLAG_X] [elev=n]
       [when=morning|day|evening|night|dark]   # dark = evening+night. pk picks a FLAG_TEMP_* unused
                                               # in this map as the object's hide flag and appends to the
                                               # map's ON_TRANSITION script (creating it if missing):
                                               #   gettimeofday / goto_if_* VAR_0x8000 ... / setflag FLAG_TEMP_n
       [text_night="..."]                      # same NPC, different line after dark (gettimeofday in its script)
signs
  <id> @x,y (text="..." | script=<Label>)
warps
  <id> @x,y -> <Map>#<warp> [both]
triggers
  <id> @x,y[,WxH] var=VAR_X=value -> <Label>
connections
  up|down|left|right <MAP_ID> offset=<n|auto>
items
  <id> ball <ITEM> @x,y                # visible item ball (flag auto-allocated: FLAG_HIDE_<MAP>_<ID>)
  <id> hidden <ITEM> @x,y              # hidden item (FLAG_HIDDEN_ITEM_<MAP>_<ID>)
trainers                             # NPC trainers; party data lives in trainers.party (out of scope),
  <id> <gfx> @x,y <movement> sight=n trainer=TRAINER_X [intro="..." defeat="..." after="..."]
vanilla                              # existing content of the base map; default `keep all`
  keep all                           # every vanilla event survives and is auto-shifted by `grow`
  move <ref> @x,y                    # ref = script label | LOCALID_* | obj#n | warp#n | bg#n
  set  <ref> key=value ...           # e.g. move=WANDER range=2x2 gfx=...
  remove <ref>
  text <Label>: "..."                # rewrite a vanilla text (wrapped by pk)
declare                              # new save-data symbols; pk allocates from unused slots
  flag FLAG_<NAME>                   # (381 FLAG_UNUSED_* available — use freely)
  var  VAR_<NAME>                    # (only 29 VAR_UNUSED_* — max one per questline)
scripts                              # raw script blocks appended to the map's scripts.inc
  <Label>:
    <script commands, one per line, exactly as in scripts.inc>
texts                                # new labels, plain text, wrapped by pk
  <Label>: "..."
gates                                # progression blockers this design relies on (§14.3b)
  <kind cut|smash|strength|trigger|ledge|water> x,y [VAR=value] ["what it separates"]
raw x,y                              # escape hatch: exact metatile patch using map legend syms
  TT..
  T==.
notes                                # ≤10 lines of free-form context for LLMs
  ...
```
- **Coordinates are in the final (post-resize) space.** Anchors avoid arithmetic:
  `@lab.door+0,2`, `@players_house.sw-1,0` (stamp corners `nw ne sw se`, `door`, `center`).
- Unknown aliases resolve as in §5.5 (`WOMAN_1` → `OBJ_EVENT_GFX_WOMAN_1_FRLG`, `WANDER` → …).
- Existing vanilla events/scripts are referenced by label (`script=PalletTown_EventScript_FatMan`)
  so complex vanilla logic is preserved untouched.

### 14.3 Commands
```
pk bp export <map> [--out file]   # generate a blueprint from an existing map: terrain via
                                  # `terrain infer` + greedy rect/path decomposition, stamp
                                  # recognition, events, texts by label, connections, notes empty
pk bp show <bp> [--section s]     # print (paginated) — usually the LLM just reads the file
pk bp check <bp>                  # static validation: names, labels, flags, bounds, limits,
                                  # overlapping structures, warps reachable, texts fit
pk bp plan <bp>                   # dry run: ordered op list + summary, like `terraform plan`
  OK plan PalletTown_Frlg resize 24x20->32x28 terrain:7 stamps:+1 npcs:+2 texts:3 conns:2
pk bp apply <bp> [--yes]          # compile to pk ops (§5.4-5.7, §6) in ONE transaction
pk bp diff <bp>                   # drift between map data and blueprint (e.g. after Porymap edits)
pk bp pull <bp>                   # update the blueprint from current map data (keeps notes/intent)
pk bp world                       # region overview from world.bp, 1 line per location
pk bp render <bp> [--out png]     # preview PNG without touching repo data
```

### 14.3b Progression gates & reachability (critical)
Enlarging maps can accidentally create bypasses around vanilla progression blockers (Cut trees,
the Viridian old man, Oak's Pallet trigger, the Pewter gym guide, ledges, water before Surf).
- Blueprint section `gates` lists the blockers the author relied on, e.g.
  `cut 18,26 separates north_zone` / `trigger 22,11 VAR_MAP_SCENE_VIRIDIAN_CITY_OLD_MAN=0`.
  `pk bp export` fills it automatically from cuttable-tree objects, rock-smash/strength objects,
  coord events with a var condition, ledges and water.
- `pk bp check` runs a **reachability diff**: flood-fill walkable cells (collision, elevation
  transitions, one-way ledges, water) from every map entrance (warps + connection edges) for each
  ability state `none, cut, surf, cut+surf, strength, ...`, treating gate triggers in their initial
  state as walls. For each state, cells/warps/connection-edges reachable in the new map that map to
  a *vanilla* region that was unreachable in that state → `ERR GATE <gate> bypassed via x,y`.
  New areas are allowed to be reachable only in states where their attachment point is reachable.
- Connections count: a new walkable cell on an edge that lines up with a walkable cell of the
  neighbour map is an entrance; check both maps.

### 14.4 Semantics
- **Declarative & idempotent:** `apply` twice = second run reports `OK nothing to do`.
- Compilation order: resize → terrain (bake) → structures → raw → warps → npcs/signs/triggers →
  texts/scripts → connections → `check`. Any failure rolls back everything (§3.4).
- **Ownership:** objects created by a blueprint carry its id (stored in `.pk/bp_state/<map>.json`:
  id → event index / script labels / generated text labels). Removing an id from the blueprint
  removes what it created; vanilla content not mentioned in the blueprint is left alone unless
  the blueprint says `prune unmanaged`.
- `export` must round-trip: `export` → `apply` on the same map = no changes.

### 14.5 LLM workflow
1. `pk bp export X` (or read existing `.bp`) → edit the blueprint text → `pk bp check` →
   `pk bp plan` → `pk bp apply` → `pk build`.
2. Blueprints are what LLMs pass to each other: a designer LLM writes them, an implementer LLM
   applies and fixes `check` errors, a reviewer LLM reads `bp diff` / `bp render`.

### 14.6 Region-level files
- `blueprints/VISION.md` — creative direction (read once per session by designer LLMs).
- `blueprints/world.bp` — one line per map: `<Map> <TYPE> <cur>-><target> status=<keep|plan|draft|applied|new> arcs=<ids> "<concept>"`.
  `pk bp world [--status s] [--arc A]` filters it.
- `blueprints/quests.bp` — every questline as ordered steps:
  ```
  quest A "Rocket Supply Line" var=VAR_QUEST_ROCKET_LINE
    1 CeruleanCity_Frlg  set=FLAG_A_MANIFEST_1  "Find manifest page in burgled house"
    2 VermilionCity_Frlg need=1 set=FLAG_A_MANIFEST_2 "Crate on the docks at night"
  ```
  `pk bp check --quests` verifies every `set=` symbol is set by some blueprint script, every
  `need=` gate is checked, and no step is unreachable (warp graph + required HMs/badges).

Writing rules for blueprints (all authors): only list *changes* to vanilla (use `vanilla keep all`);
coordinates are post-resize; prefer anchors and terrain verbs over `raw`; dialogue ≤3 boxes per NPC;
every new NPC adds lore/flavour or a gameplay hint. Project policy in `blueprints/VISION.md` overrides
this spec (currently: no new quests, no new items — `items`, `declare var` and `quests.bp` unused).

The current blueprints live in `blueprints/` (see `blueprints/VISION.md`).

## 15. Non-goals (v1)
Battle data, trainers' parties, species/move data, music, C-side strings, a full scripting
language, a GUI. These can be added later as new nouns using the same protocol.
