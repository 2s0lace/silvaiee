# Implementation decisions

## Milestone 1 (2026-10-11)
- Stop after core validation and build; milestone 2 requires owner OK. Blueprint parsing,
  check/plan/world are milestone 5b. No blueprint has been applied. VISION policy is binding.
- SPEC section 2 assigns tools/pk/pk to both a package directory and a shell file, which is
  impossible. Keep the package; put the POSIX shim in tools/pk/bin/pk. PowerShell uses pk.cmd.
- All edit handlers compute pending bytes against an in-memory transaction overlay. Only
  Transaction.commit installs files. Index refresh is journaled, including lazy refresh.
  Dry-run creates no cache or journal. Initial new implementation files bootstrapped the
  journal; subsequent code changes and this onboarding change use it.
- A filesystem cannot atomically rename multiple files. Journal before-images are fsynced
  before installation; individual replacements are atomic. Ordinary failures roll back.
  Interrupted prepared transactions block writes until pk undo N recovers them. External
  changes are never overwritten on undo. Undo itself is journaled, making redo possible.
- Batch supports up to 100 operations using the same overlay and one commit. Build, undo,
  nested batch and per-line protocol flags are rejected explicitly. In milestone 1 the
  available batch mutation is index; edit commands arrive later. There is no arbitrary
  file-write CLI. Transaction/batch APIs are tested with synthetic file operations.
- pk build stages the complete repository (excluding .pk and __pycache__) under .pk,
  runs bash build_fr.sh firered, validates the log EXIT marker and ROM BPRE game code,
  then journals and installs only changed files. Failed builds leave live project files
  untouched. This preserves undo even for generated build outputs. Staging costs time and
  disk space; it avoids running an external writer directly against this non-git project.
  Raw compiler output is retained in .pk/build-output.log; CLI errors are deduplicated,
  capped at 15 lines. Normal/release ROM names follow the actual Makefile (32 MB currently).
- build_fr.sh does not propagate make's status (its last command is tail). Shell status
  alone is insufficient. Clear /tmp/fr_build.log first, then require its final EXIT 0.
- The milestone 1 pre-build check covers active layouts, block lengths, layout references
  and map group membership. Full semantic check is milestone 3 (text fit after milestone 2).
  Do not claim full semantic validation yet; pk check labels its output structural.
- The actual project has 939 map JSON files and 785 layouts, not 945 maps. Twenty unused
  Emerald layouts have surplus u16 words. Preserve those bytes, test their round-trips,
  and validate dimensions only for layouts referenced by maps. No source data was repaired.
- Ninety-five map JSON files use multiline empty arrays; vanilla json.dumps(indent=2)
  is not byte-identical. JsonDocument preserves original bytes on no-op, key order and
  newline style, and replaces only changed top-level values on edits. All 939 files are
  tested both unchanged and with one field edited/restored, preserving unrelated bytes.
- Map lookup never selects among ambiguous names (including shared layout IDs). Exact
  map names or MAP_* IDs are preferred; fuzzy candidates use >=0.8 normalized similarity.
  The spec's example pallet is actually ambiguous because interiors share that substring.
- The index includes both define and enum constants (Emerald MB_* and object graphics are
  enums; FireRed behaviors use MB_FRLG_*). C strings are not editable/indexed as dialogue.
  Source mtimes/sizes and membership detect stale caches, even older replacement files.
  Reference lookup matches identifier boundaries and excludes definitions and generated
  map includes. It returns at most 18 matches + status + MORE = 20 lines.
- Text indexing is a lexical search aid only; byte-preserving text parse/emit and width
  calibration belong to milestone 2. Font limits are intentionally not guessed.
- Log times and build durations vary by execution, as required by their command formats;
  read views, search ranking and pagination are otherwise deterministic.
- Source format evidence and repository audit are stored in data/format_verification.json.

## Milestone 1 — status: COMPLETE (verified 2026-10-11)
- Tests: 31/31 pass (`python -m unittest discover tools/pk/tests`, ~15 s).
- `pk build`: staged FireRed build EXIT 0, ROM BPRE 32 MB installed, journaled as transaction 6.
  Memory: EWRAM 86.5%, IWRAM 87.4%, ROM 79.8%.
- Launchers: tools/pk/bin/pk (Git Bash) and tools/pk/pk.cmd (PowerShell) both work.
- Budgets measured: `map info PalletTown_Frlg` 441 chars; label lookup 116 chars; refs ≤20 lines.
- Known nit carried into milestone 2: `find --kind text` snippets dropped placeholders
  ("Hi, . I bet"). Fixed in milestone 2.
- .pk/ is ~116 MB after one build (journaled build outputs). Consider `pk gc --keep N` later.

## Milestone 2 — text (2026-10-11, implemented by Claude after Astra's milestone 1)
- Correction to the milestone 1 nit list: `find` snippets were already capped at 60 characters.
  The real bug was dropped placeholders. Index schema v3 keeps `{PLAYER}` etc. in text snippets.
- Field dialogue uses FONT_NORMAL (`src/menu.c` AddTextPrinterForMessage). FONT_MALE and
  FONT_FEMALE are both `FC 06 01` in charmap.txt, so the FRLG male/female colour fonts measure as
  FONT_NORMAL. Blocks that switch to any other font are listed with their font and not width-checked.
- Calibration (`pk text calibrate` writes data/text_limits.json):
  - 35,012 vanilla lines without placeholders were measured with gFontNormalLatinGlyphWidths.
  - The limit is the widest width occurring at least 5 times: **208 px** (27 lines).
  - Outliers: 209 px (debug.inc) and 210 px (move_relearner.inc).
  - Lines per box: 2.
- Placeholder widths are assumptions, stored in text_limits.json and editable:
  - PLAYER and RIVAL: 42 px (7 characters × 6 px),
  - STR_VAR_1..3: 60 px,
  - KUN: 0 px.
  Lines containing placeholders are therefore estimates. `text check` reports only definite
  overflows by default; `--estimates` adds placeholder lines (marked `~`, 128 of them, mostly Emerald
  Battle Frontier).
- SPEC §6.2/§11 "reproduce vanilla wrapping for ≥90%" was not achievable and has been replaced.
  - Exact greedy reproduction is ~10%, because vanilla breaks early for meaning
    ("Me?\nI'm the programmer!").
  - New acceptance, tested over all ~6,000 eligible vanilla blocks:
    - every wrapped line fits,
    - box count is the same,
    - per box, the wrap never uses more lines than vanilla,
    - exact matches stay above 5%.
  - Authors can force breaks with `|` (line) and `||` (box), or use `--keep-breaks` for raw control codes.
- Wrap style follows vanilla: line 2 of a box after `\n`, later lines scroll with `\l`, boxes split by `\p`.
- Emission puts one `.string` line per control code, which matches 11,403 of 11,416 vanilla blocks.
  The 13 others put two codes on one line; pk rewrites a block only when it is edited, and every
  other byte of the file is preserved (tested).
- Vanilla empty boxes (`...\p$`) cannot be written in plain input; use `--keep-breaks`.
- `text get` prints `[box.line]` prefixes because the output grammar compacts whitespace
  (indentation would be lost).
- Placeholders containing spaces (`{COLOR BLUE}`) count as one word when wrapping.
- Straight `"` is not in charmap.txt. The error suggests “ and ”.
- Out of scope, as in the SPEC: C strings `_("...")` in src/.
- pk build fix: launched from Git Bash, `bash build_fr.sh` resolved to Git's bash, whose MSYS
  runtime lacks the /opt/devkitpro mount ("arm-none-eabi-gcc: command not found"). The wrapper
  now runs "$BASH" (devkitPro MSYS2), puts its directory first on PATH, and drops
  MSYSTEM/SHELL/MAKE*.
- pk build no longer stages tools/pk/ and blueprints/: they are not build inputs, and excluding
  them lets pk and blueprints be edited while a build runs (they used to cause CONFLICT).

## Milestone 3 — maps, metatiles, events, check (2026-10-11, Claude)
- `map show` has two map views:
  - **class view** (default): one symbol per *meaning*, never more than ~15 symbols.
    - Sources, in priority order: semantic name sym, then behaviour class (grass, water, ledges, door,
      sand, waterfall), then the cell's collision (`#` solid, `.` ground).
    - Pallet Town costs ~630 characters (SPEC §12 budget: 900).
  - **exact view** (`--exact`): one symbol per metatile, with a legend.
  - Why two: FRLG towns use up to 319 distinct metatiles (Saffron), so the SPEC's "one symbol per
    metatile" view cannot fit a printable alphabet. Exact views therefore need a `--rect` when a
    map is large.
- Events are overlaid on the grid (W o i s h t); `--no-events` turns them off.
- Rows are `yy|cells` and rulers are `x|...`. The output grammar compacts whitespace, so no layout
  depends on spaces.
- Maps wider than 64 are banded; the summary names the next `--rect`.
- Painting without `--coll/--elev`:
  - collision and elevation come from that metatile's dominant vanilla usage (≥80%) across
    layouts with the same tileset pair;
  - otherwise the cell keeps its old bits.
  - Example: a door paints as coll 1, elev 0, exactly like vanilla doors.
- `map grid` symbols:
  - `def X <metatile>` lines, plus the exact-view legend of the patched rect;
  - `?` or space keeps a cell;
  - rows can be separated by `;` so no stdin is needed (works in batch).
- Additions to SPEC §5.4:
  - `map copy` (optionally `--from` another map on the same tileset pair),
  - `map at` (inspect one cell),
  - `map border show`.
- Metatile names live in `tools/pk/data/metatiles/<gTileset>.json`, keyed by global id hex
  (secondary ids start at 0x280 for FRLG).
  - `meta autoname` names only from behaviour or clear vanilla usage (solid ≥80% / ground ≤20%),
    marks those names `auto`, and never touches manual names.
  - Autoname has NOT been run over the project. Generic `solid_12` names add little; name
    metatiles on demand.
- `meta show` decodes the real tiles.png and JASC palettes. Bottom layer, then top layer;
  colour 0 is transparent. Output is a 16x16 text drawing; `--png` writes a 128x128 preview.
- Events:
  - `ev add ... text="..."` appends `<Map>_EventScript_Auto_N` (msgbox MSGBOX_NPC/MSGBOX_SIGN,
    end) and a wrapped `<Map>_Text_Auto_N` to scripts.inc. The `_Frlg` suffix is dropped from
    labels, matching vanilla naming.
  - Short aliases: gfx resolves `_FRLG` variants first on FRLG layouts; `move=WANDER` means
    WANDER_AROUND.
  - `ev rm warp#n` renumbers every incoming warp from other maps. It refuses if a warp targets
    the removed one.
  - Edits go through JsonDocument, so only the changed value's bytes change (tested).
  - `when=night` is blueprint-level (SPEC §14) and comes with milestone 5b.
- `pk check` (full, ~30 s for 939 maps):
  - **Errors**: metatiles outside the tileset pair, unknown scripts, broken warps, connection
    offsets that don't mirror, tileset budgets, missing palettes.
  - **Warnings**: one-way warps, connections without a reciprocal, events outside the map
    (vanilla parks NPCs and warps off-map on purpose), definite text overflows.
  - Vanilla errors are recorded in `data/check_baseline.json` (3: the Route 6 ↔ Saffron
    connection quirk, and CableClub tiles.png padding). Only NEW errors fail.
    `pk check all --baseline` re-records the baseline.
  - The index now also reads `data/event_scripts.s`, where the Common_EventScript_* labels are
    defined (schema 4).
- `Repo.map` fast path: an exact directory name reads only its own map.json, not all 939
  (~1.3 s saved per command).

## Milestone 4 — resize, new maps, stamps (2026-10-11, Claude)
- `map resize` implements SPEC §5.7 in one transaction:
  - Block data and layouts.json. A shared layout needs `--fork-layout`, which copies it to
    `data/layouts/<Map>/` under a new LAYOUT_ id.
  - Every event is shifted. A crop that would cut a warp is refused, because other maps
    reference warps by index.
  - `setmetatile` / `setobjectxyperm` / `setobjectxy` literals in the map's own scripts.inc.
  - `warp*` / `set*warp` x,y literals in every map and common script that targets this map.
  - Connection offsets on both sides.
  - `src/data/heal_locations.json` x/y and respawn_x/respawn_y. Not in the SPEC, but whiteout
    and Fly coordinates break without it.
- Script literals use only the documented macro argument forms (`formatwarp`: when there are 2+
  args, x,y are the last two). Non-literal coordinates and `getplayerxy` comparisons become
  `WARN manual file:line`.
- Fill modes: `extend` (repeat the edge cells, all bits), `border` (border.bin pattern),
  `terrain` (extend materials, then the 4b solver), or a METATILE.
- Tested: grow, then crop back, restores every touched file byte-identically. Growing
  right/bottom touches no coordinates at all; VISION recommends it.
- `map extend-to MAP NEIGHBOUR` grows along the shared edge until the connection covers the
  neighbour. Example: Route 1 becomes 48 wide to span Viridian.
- `map new`:
  - creates the layout (map.bin, border.bin, layouts.json), map.json (header from `--like MAP`
    or type defaults), a minimal scripts.inc, and a map_groups.json entry;
  - the default group is `gMapGroup_PkNew`, appended last so existing group/map numbers (and
    saves) never shift;
  - map id `MAP_<SNAKE>` without _Frlg, layout id `LAYOUT_<SNAKE>_FRLG`, as in vanilla.
- Stamps (SPEC §5.3): `stamp extract/place/ls` in tools/pk/data/stamps (metatile + collision +
  elevation per cell, tied to the tileset pair).
  - Not done: stamp mining (§5.3) and recognising stamp instances in `map show`. They are
    optional, and class view plus render covers the need.
- `map render` (SPEC §7.5) draws real tiles from tiles.png and palettes into PNG, default
  `.pk/render/`. It is meant for vision-capable callers and for humans.
- Help: `pk help` is the 31-line AGENT.md index; `pk help <noun>` prints tools/pk/help/<noun>.md.

## Milestone 4b — terrain (2026-10-11, Claude)
- SPEC §5.6's heightmap model was replaced by a **material** model.
  - Why: FRLG outdoor maps keep almost all ground at elevation 3. Plateaus are separated by
    solid cliff tiles and one-way ledges, not by elevation bits, so a height layer cannot be
    inferred from vanilla data.
  - A cell's material is the first tag of its named metatile, else its class (ground, solid,
    water, grass, sand, ledges, door ...).
  - Brushes (`rect`, `circle`, `path`, `river`, `ledge`) set materials; `forest` tiles a stamp.
- Solver (instead of a hand-written rule table, SPEC §5.6 "learned, not hard-coded"):
  - Statistics: horizontal and vertical metatile adjacency counts from every vanilla layout with
    the same tileset pair. They are computed live in about 1 s, so there is no rules file to go
    stale.
  - Pass 1: greedy outside-in. Each cell takes the metatile of its material that best agrees
    with neighbours already fixed.
  - Pass 2: up to 6 ICM passes; score = sum of log(count + 0.01) + a small frequency prior.
  - Margin cells (1 ring) may switch to another metatile of the same material. That is how
    shore and edge variants appear around a painted area.
- Acceptance (replaces SPEC §11 "infer→bake ≥95%"): erase 5x5 windows of natural vanilla
  terrain on 7 maps, keep only their materials, and re-solve.
  - Before outside-in initialisation: 52% exact.
  - After: 81% exact; only 1.7% of adjacent pairs never occur in vanilla.
  - The test asserts ≥75% exact and ≤5% unseen seams.
  - The remaining misses are mostly "ground" variants (path vs grass) that coarse materials
    cannot tell apart. Tag metatiles (`meta name ... --tags path`) to make them separate
    materials.
- Every brush reports `WARN n seams never seen in vanilla` with coordinates.
- Not implemented from SPEC §5.6:
  - `terrain raise/lower/smooth` and the `.pk/terrain` sidecar. Use material brushes plus
    ledge/ramp materials instead.
  - `ramp`. Stairs need a named "stairs" material: `meta name ... --tags stairs`, then brush it.

## Milestone 5 — tilesets: pixel DSL, compiler, export (2026-10-11, Claude)
- `.px` DSL as in SPEC §7.1. `pal` uses explicit symbol=#rrggbb pairs, `use` shares palette files,
  `grid` rows end at a blank line. `#` is the comment character, so it cannot be a colour symbol.
- `.pkt` lines: `NAME FILE.px[@x,y] BEHAVIOR LAYER [tags=] [sym=] [attr=0xRAW]`. The drawing's bottom
  layer goes to tile slots 0-3 and its top layer to 4-7, exactly as vanilla metatiles.bin.
- Compiler steps:
  1. quantise colours to 15-bit;
  2. pack per-8x8 colour sets into the free palette slots (largest set first, fewest added colours);
  3. dedupe tiles with h/v flips;
  4. write tiles.png as 4-bit indexed with a grey ramp, like vanilla;
  5. write 16 JASC palettes, with own palettes in slots 7-12 (FRLG secondary);
  6. register the tileset idempotently: before `#endif // IS_FRLG` in graphics/metatiles/headers.h,
     plus `extern` in include/tilesets.h.
- **Primary sharing** (not in the SPEC, needed): a secondary tile whose colours are a subset of a
  paired-primary palette uses that slot, and pixel-identical primary tiles are reused.
  - Without it, vanilla Pallet Town needed 7 palettes (> 6 slots).
  - With it, `export` then `build` of gTileset_PalletTown is pixel-identical for every metatile
    (both layers, after quantisation), with identical attribute words (test_art).
- Metatile ids are stable per name (`tools/pk/data/tilesets/<name>.json`). Names, tags and syms go to
  `data/metatiles/<symbol>.json`, so terrain can use new materials at once.
- SPEC §7.4 "byte-identical metatiles.bin" was dropped: tile order legitimately differs.
  Pixel and attribute identity is the acceptance criterion.

## Milestone 5b — blueprints (2026-10-11, Claude)
- `pk bp plan|check|apply|diff|render|show|export|world`. apply/check/plan run the SAME code in one
  transaction; plan/check/render never commit.
- Size is absolute (`size WxH grow=...`): applying when the map already has that size does nothing.
- Terrain lines are collected into one material target set; `keep` and stamped forest cells are
  frozen.
  - Only cells whose material changes are re-solved, so vanilla detail inside an unchanged material
    survives.
  - Collision/elevation are picked so a cell stays in its material (a metatile used both open and
    blocked must not turn ground into solid).
  - The solver prior adds the map's own metatile frequencies, so new areas look like the rest of
    the map.
  - All 7 Kanto blueprints are idempotent: a second apply changes nothing (test_bp).
- Blueprint words: `grass` = plain grass ground, `tall_grass` = encounter grass, `rock` = solid.
  `flowers`/`path`/`stairs` need tagged metatiles and fall back to ground with a WARN.
  `h=` heights and `ramp` produce WARNs (see 4b).
- `forest` tiles the `tree` stamp (2x2, extracted from Pallet's border). Stamps made only of primary
  metatiles work for every map on the same primary.
- Events and identities:
  - NPCs get `LOCALID_BP_<MAP>_<ID>` (globally unique, as map_event_ids.h requires).
  - Scripts and texts get `<Map>_EventScript_BP_<id>` / `<Map>_Text_BP_<id>`.
  - Blueprint-made events whose id disappears are pruned.
  - `when=` NPCs: a free FLAG_TEMP_* hide flag, plus
    `<Map>_EventScript_BP_TimeOfDay` (gettimeofday + per-NPC goto_if_eq ... Common_EventScript_NopReturn
    / setflag). This is called at the top of the map's ON_TRANSITION script, or a new one is created.
    Times are numeric (0 morning, 1 day, 2 evening, 3 night) because the TimeOfDay enum is not
    visible to the assembler.
  - `text_night=` branches on gettimeofday inside the NPC script.
- Trainers (allowed since the 2026-10-11 policy update):
  - `trainers` lines generate a vanilla-style `trainerbattle_single TRAINER_X, Intro, Defeat` +
    `msgbox After, MSGBOX_AUTOCLOSE`, with trainer_type NORMAL and sight.
  - `TRAINER_X` must already exist (party added by the GLM tech agent); otherwise NOTFOUND with
    that hint.
- Policy file `data/policy.json` (from VISION): items and `declare var` are rejected, trainers
  allowed, quests postponed.
- **Reachability gates** (SPEC §14.3b), `maps/reach.py`:
  - Model: a cell is walkable when collision is 0. Water needs Surf, ledges are one-way.
    Cut/Smash/Strength objects block unless the ability is held. These are walls: var triggers,
    every other NPC, and every literal setobjectxy(perm) position in the map's scripts. Viridian's
    road is blocked by a scripted NPC (`setobjectxyperm LOCALID_TUTORIAL_MAN, 21, 11`), not by
    terrain.
  - Entrances: each warp, and each connected edge as one group.
  - For six ability states, every vanilla cell reachable in the new map must have been reachable
    from the same entrance in vanilla.
  - Vanilla compared with itself gives no errors; an opened Viridian forest wall is detected
    (test_bp).
- All 7 batch-1 blueprints pass `pk bp check` with errors:0. Remaining WARNs: untagged
  flowers/path/stairs, unmodelled heights, a few unseen seams. Muse's role file makes tagging the
  first job.

## Milestone 6 — MCP
- Not built in pk. The owner already runs an MCP server: the mGBA bridge
  (`tools/mgba-bridge/mcp_server.py` + `bridge.lua`, tools `mgba_*`), registered in `opencode.jsonc`.
  pk stays a CLI that the agents call through their shell.

## Agents (2026-10-11)
- Role files in `tools/pk/agents/`:
  - `glm-tech.md` — technical: C, build, trainer parties, pk, mGBA;
  - `muse-mapper.md` — terrain that looks hand-made and complex, with a checklist;
  - `deepseek-assets.md` — art at 0.9-1.0x of the original, based on measured vanilla statistics.
- Prompts in `opencode.jsonc` point to these files.
- `pk undo` skips `.pk/` files (index cache, renders). A refreshed index used to block undo of
  journals that also contained an index write.

## Token optimization (2026-10-11)
- `map show --layer all`: coll/elev defaults per class + coordinate exceptions, including
  event overlays (hidden classes); >10% exceptions falls back to a full grid. Ties use the
  lower value. Standalone coll/elev stays unchanged. Tests reconstruct the printed data.
- Load help on demand; read DECISIONS only before pk code edits. VISION is the policy source;
  proposals move to PARKED, rules remain in VISION. Batch edits, crop previews; mapper renders
  at scale 1, scale 2 only with a rect, 1–2 reference maps once/session. The human/complex
  checklist and DeepSeek style guidance stay unchanged.

## Objects and NPC sprites (2026-10-11, Claude)
- Blueprint `structures` (already in apply.py, SPEC §14) is now documented in `pk help bp`; tested.
  `door->Map#n` on a structure is still not compiled: doors use the `warps` section.
- `pk stamp from-tileset SET.pkt OBJ.px name`: an object drawn as one .px, listed per 16x16 cell in a
  built .pkt, becomes a stamp without painting a real map first. Ids come from
  `tools/pk/data/tilesets/<set>.json`. Cells default to coll 1 / elev 0 (measured on Pallet houses
  and doors); `--walk` cells get coll 0 / elev 3; unlisted cells are holes (`null`, skipped by place).
- `pk sprite`: NPC overworld sprites are cloned from a vanilla template (`--like`), so frame layout,
  animations, OAM and shadow are known-good. Pixels must use the template's palette tag: no new
  palettes, nothing else changes. The constant is appended before NUM_OBJ_EVENT_GFX (no existing id
  shifts; OBJ_EVENT_GFX_VAR_* move up by one, they are compile-time only). Entries are inserted next to
  the template, keeping its `#if IS_FRLG` context. Rebuilds only replace the png.
- Not verified by a real `pk build` yet: GLM's first sprite should be built and checked in mGBA.

## Blueprint raw patches and the beauty rule (2026-10-11, Claude)
- Section `raw` (SPEC §14) is now compiled, after structures: `raw x,y` / `at x,y`, `def X <metatile>
  [coll= elev=]`, rows. Symbols are defs or named `--sym` metatiles only, never the frequency legend of
  `map show --exact`, so applying twice gives the same map. Bits default like `map paint`.
- Owner's rule for Muse: the "human and complex" checklist is a guideline; beauty wins when a rule is
  broken on purpose (named in the hand-off). Content policy, gameplay safety and vanilla cores stay hard.
  Real-map `paint`/`grid` polish is allowed after a blueprint is applied (journaled).

## Asset review automation (2026-10-11, Claude)
- The first DeepSeek pipeline test (pier, bench, white flowers) worked end to end. Review found three classes of
  mistakes that a static PNG hides:
  - hand-drawn sea water inside the pier tiles (will not animate);
  - a recoloured flower (animated tiles 508-511; the variant will stand still);
  - "plain" context tiles picked by hand that were really a rock or a shore corner.
- `pk tileset lint` and `pk tileset preview` were added; build runs lint automatically.
  - Animated ranges are parsed from src/tileset_anims.c by following `TilesetAnim_<Base>()` to the QueueAnimTiles_*
    functions it calls. Their names are not consistent: General_Frlg calls QueueAnimTiles_General_Water_...
  - "Sea colours": colours of animated tiles used by OCEAN/POND/WATER-behaviour primary metatiles that are mostly
    drawn by animated tiles (≥50% of their pixels). This excludes the grass and sand inside shore tiles.
  - Lint compares new tiles against the primary plus the exported (attr=) metatiles of the same .pkt, never
    against the built tileset, which would contain the new art itself.
- `tools/pk/art/sprite.py` and test_art `test_sprite_export_build_register` were added by another agent. The
  test fails with AttributeError in sprite.build (a regex for the gfx constant list finds nothing) and needs GLM.

## Blueprint safeguards (2026-10-11, Claude)
- Found by the first Pallet trial (bench in the channel, sections pasted twice, 197 seams handed off).
- Errors in plan/check/apply: duplicate ids per section; a structure on water (stamp `on` is `land` by
  default; `--on water|any` on extract/from-tileset), on the keep area, over another structure, or with
  an NPC/trigger on one of its solid cells. Re-applying skips cells already holding the stamp.
- `--strict` turns "seams never seen in vanilla" into errors; Muse's "done" uses it.
- TRAP (reach.py): cells reachable from an entrance with no path back to any warp or connected edge,
  for no abilities and all abilities, compared with vanilla. It caught Route 22's basin (ledge in, the
  `ramp` out is ignored); the blueprint now uses a 2-cell gap until stairs exist.
- Bug fix: `forest` used to win over every later brush (Route 22's whole basin stayed forest). Later
  lines now carve earlier forests; the tree grid stays aligned to the forest brush.
- `pk px import`: external and AI images go into `tools/pk/art/_inbox/<source>/`, which is outside data/ and
  not staged by the build. Import steps:
  1. detect the pixel grid;
  2. reduce to the target size with BOX;
  3. map each colour to the nearest one (weighted RGB) in General_Frlg's palettes (or `keep` = median cut,
     or a pal.px).
  - Tested: a vanilla tree upscaled 24x, blurred and noised imports pixel-exact (test_art Import).
  - Real generator output still needs hand fixes. The role file says so.
- Structure footprints are frozen for the terrain solver (the stamp owns them), so re-applying a pier
  over water is a no-op. Water under a structure is judged by the blueprint's intended material.
- TRAP treats warp cells as exits even though door metatiles are solid.
- Open: Pallet's second apply still changes ~14 margin cells around its flower beds. Cause: the new
  `lawn` tags (pk log 95-96) make the solver miss the target material on some cells, so the second
  run re-solves them. Fix in the solver (target material must be honoured or reported), not here.
- First real Gemini batch (6 JPGs, 2400x1792): grid auto-detection fails on JPEG noise, and Gemini draws at
  about 2x FRLG pixel density with thick near-black outlines and warm off-palette browns.
  - Import now always uses majority voting: map each source pixel of a cell to the palette, then pick the most
    common colour, ignoring a 15% cell border. `--crop x,y,WxH` selects the object. `--grid` is optional.
  - Result: a 2x1 bench is usable as a sketch with `--palette keep`. With `--palette primary` the wood maps to
    salmon/yellow, because General_Frlg has few browns.
  - Gemini output is concept art for DeepSeek, not source pixels, unless it was generated at the exact game
    size (see the prompt template in tools/pk/art/_inbox/README.md).

## pk map lint (2026-10-11, GLM tech)

- New read-only command pk map lint MAP: text-only visual-warning reports so cheap models can tidy
  maps without judging renders. Thresholds live in 	ools/pk/data/lint_map.json.
- It reuses the terrain model (sections above): cell material = class view or first tag of a named
  metatile, and SEAM reads the same vanilla adjacency tables as the terrain solver (	errain.Rules).
- Calibration over all 419 vanilla Kanto FRLG maps scored the whole set at 2 warnings total
  (CeruleanCity, FourIsland: their intentionally straight shore edges). PkPalletDraft_Frlg: 0;
  PkPalletDraft2_Frlg: 2 (dash warnings for the broken sand band inside its bay).
- Checks scoped to towns/cities (dash_types, straight_types, empty_types) because vanilla routes/
  caves legitimately keep long straight natural edges and blank blocks; RECT skips components that
  touch a map border (vanilla shorelines/ponds) and EMPTY skips windows that are mostly water/solid.

## Maintainer round 1 — step 0: clean baseline (2026-10-12)
- Removed the leftover TEST_LASS test NPC from the real PalletTown_Frlg: `pk ev rm PalletTown_Frlg obj#3`
  (it came from journal 59), and deleted the script/text block it left in scripts.inc
  (PalletTown_EventScript_Auto_1, PalletTown_Text_Auto_1) by hand — pk has no text rm. Nothing else in
  that map was touched.
- The ~30 PalletTown metatiles named `meadowXXX` with tag `meadow` (journals 155-193, 222-223), and the
  General_Frlg `path_*`/`lawn_*` tags (journal 145, 95-96), are deliberate, not wrong: materials come
  from the first tag, and the tags separate flat-ground variants the solver otherwise cannot tell
  apart (DECISIONS milestone 4b says exactly this). Manual names were left alone.
- Two stale test assumptions were fixed (not the data):
  - test_solver_reproduces_natural_vanilla_terrain: the natural-material set lacked `meadow`, `lawn`
    and `path`. Adding them re-admits the tagged ground windows. Measured now (same seed 7, 7 maps,
    40 windows each): **1225 samples, 91.3% exact, 1.5% unseen seams (43/2940)** — better than the
    stale "81% / 1.7%", because the finer materials make the target metatile unambiguous.
  - test_autoname_does_not_override_manual: the PalletTown pair is now fully manually named, so
    autoname had nothing to name and the "at least one auto name" assertion failed. The test now
    trims the sidecar in the transaction first, then checks autoname names the unnamed cells and
    keeps the manual 0x2A3.
- test_core.RealFormats.test_structural_check hard-coded (939, 785); 12 benchmark PkPallet_* maps plus
  2 others were added since. It now derives the expected counts from the files.
- BASELINE full suite (`python -B -m unittest discover tools/pk/tests`): **110 tests, 1 failure**
  (test_vanilla_is_clean_and_draft2_is_worse — map lint, fixed in step 5), 764 s.
- `pk map lint` still prints WARN BROKEN; expected until step 5.

## Maintainer round 1 — step 1: full-map text round trip (2026-10-12)
- New `pk map export MAP [--out file]` / `pk map import MAP file [--dry-run]`, module
  pk/maps/export.py. Format (`pkmap 1`): layout id, tileset pair, size; `grid` then `legend SYM 0xID`
  and `yy| SYM SYM ...` rows; `coll`/`elev` defaults per metatile symbol plus `coll! x,y=v`
  exceptions; `border WxH` with `0xID,coll,elev` cells. Export stages the text (default
  .pk/map/MAP.map) through the journal, so --dry-run and undo work; import writes map.bin and
  border.bin in one transaction.
- layers.py: the default+exception computation of `map show --layer all` was extracted into
  compact_values(sym_of=...), so export keys the same structure on the exact-metatile legend
  instead of the class symbol. render_by_class output is unchanged (golden + compact tests pass).
- Symbols: one char from a 93-char pool, two chars beyond that (a whole map can hold up to 1024
  distinct metatiles; `map show --exact`'s smaller pool would have raised LIMIT).
- Measured (uncommitted transactions, no disk writes): **all 319 FRLG layouts referenced by maps
  round-trip with byte-identical map.bin and border.bin, 0 failures**. The committed test
  test_export.py walks every vanilla FRLG outdoor layout (72 layouts, Pk* benchmark maps excluded)
  and also checks the header/legend/grid/border lines.
- AGENT.md gained one line (36 lines) and help/map.md documents the format; `pk help` still equals
  AGENT.md (test_help_matches_agent).
- Full suite: **112 tests, 1 failure** (map lint; step 5), 783 s.

## Maintainer round 1 — step 2: diagonal solver — TARGET MISSED (2026-10-12)
- Rules now learns four diagonal pair tables (ne/nw/se/sw), keyed (cell, diagonal neighbour in that
  compass direction). solve() takes `diag_weight` (default 0); it adds `diag_weight * log(count+0.01)`
  for the four in-bounds diagonal neighbours, in both the greedy init and the ICM passes.
- Acceptance test from the step-0 baseline (same maps, seed 7, natural materials incl. meadow/lawn/path),
  measured with a throwaway script calling the same code as test_solver_reproduces_natural_vanilla_terrain:

  | diag_weight | exact | unseen seams |
  |---|---|---|
  | 0.0 (baseline) | 91.27% | 1.46% (43/2940) |
  | 0.1 | 91.35% | 1.43% |
  | 0.2 | **92.24%** | 1.02% |
  | 0.3 | 92.08% | 0.99% |
  | 0.5 | 92.16% | 1.05% |
  | 0.75 | 92.16% | **0.85% (25/2940)** |
  | 1.0 | 91.35% | 1.19% |

- **The target is missed.** Target was at least +4 percentage points exact over the 91.27% baseline
  (i.e. >=95.27%); the best weight (0.2) reaches 92.24%, only +0.97 pp. Seams do improve (never worse),
  but exact does not come close. So the default stays **0** (diagonals off) and no threshold was
  loosened. The reason matches DECISIONS milestone 4b: the residual misses are ground-variant choices
  (path vs grass vs lawn), which a corner term cannot fix; corners were never the bottleneck at this
  material resolution.
- test_terrain.test_diagonal_pair_tables_learned asserts the four tables are learned and that
  diag_weight=0.5 is accepted. Full suite: **113 tests, 1 failure** (map lint; step 5), 769 s.
- Reported as a miss per the task rule ("If a number misses its target, STOP and report the miss"),
  rather than papering over it. The diagonal code is committed with the default weight 0; steps 3-5
  do not depend on it.

## Maintainer round 1 — step 3: stamp mining and detection (2026-10-12)
- New pk/maps/stamps.py and `pk stamp mine PAIR [--min-size 2x2] [--min-count 3] [--cap 32] [--apply]`.
  It seeds every 2x2 window of (metatile, coll, elev) in the vanilla layouts of the pair, then grows
  each frequent window right and down while it stays frequent (keeping only the occurrences that
  share the new column/row). Result: repeated rectangles — trees, house walls, fences, signs.
- Only all-solid rectangles are kept. Mixed ground/solid patches repeat three times by chance and
  produced ~7400 useless proposals; structure-only is what the task asks for and is what makes
  coverage measurable.
- A recognisable object is a maximal rectangle; the min-size (2x2) sub-blocks are kept too, because
  a house is covered by tiling its 2x2 blocks. `--apply` writes only a greedy subset (cap 32) chosen
  to cover the most still-uncovered structure cells, so the shipped library stays small.
- `pk map show --stamps` (opt-in) replaces each recognised instance's cells with one unique symbol
  and prepends a `stamps H=namexN ...` legend; events keep priority. The default `map show` output
  and the golden tests are unchanged.
- Acceptance target (>=80% of class-view solid '#' cells covered by a detected stamp), measured with
  the shipped library (load_stamps: hand-made + mined), union of all exact matches:

  | map | covered / structure | target |
  |---|---|---|
  | PalletTown_Frlg | 181/195 = **92.8%** | >=80% |
  | ViridianCity_Frlg | 826/1003 = **82.4%** | >=80% |
  | Route1_Frlg | 262/301 = **87.0%** | >=80% |

  All three pass. Commands: `pk stamp mine PalletTown_Frlg --apply`, `pk stamp mine ViridianCity_Frlg
  --apply` (32 stamps each, 64 shipped under tools/pk/data/stamps/mined_*). test_stamps.py checks
  mining, selection, the no-overwrite guard, the overlay, and the three coverage numbers.
- AGENT.md is 37 lines and `pk help` still equals it; help/map.md and help/stamp.md document the
  new flag and command. Full suite: **117 tests, 1 failure** (map lint; step 5), 763 s.

## Maintainer round 1 — step 4: catalog coverage — FIRST BATCH, PAUSED FOR REVIEW (2026-10-12)
- Kanto outdoor set = 72 vanilla FRLG layouts (TOWN/CITY/ROUTE/OCEAN_ROUTE, REGION_KANTO),
  120,799 cells, 16 secondary tilesets (incl. the three SeviiIslands sets, which carry
  REGION_KANTO in the data). The metatiles making up 95% of that usage number 319.
- Ran `pk meta autoname` for General_Frlg plus every one of the 16 secondaries (17 sidecar files).
  Names come from behavior and >=80% vanilla usage; manual names are never touched.
- Named the FIRST tileset (gTileset_General_Frlg), top 45 used metatiles with no manual name, in one
  batch: water (behavior), tall_grass, sand, ledge_s, and path vs grass split by the average colour
  of `meta.draw` (green => grass, tan/brown => path; grey/ambiguous skipped). Tags are the material.
  Edge/corner/inner-corner sub-tags were NOT attempted: the autotile variant cannot be read from a
  single tile without neighbour context. This is the open question for the owner's review.
- Per-tileset after this batch (used / manual-named / auto-named):

  | tileset | used | manual | auto |
  |---|---|---|---|
  | gTileset_General_Frlg | 511 | 64 | 447 |
  | gTileset_PalletTown | 78 | 78 | 0 |
  | gTileset_ViridianCity | 84 | 0 | 84 |
  | gTileset_PewterCity | 59 | 0 | 56 |
  | gTileset_CeruleanCity | 128 | 0 | 128 |
  | gTileset_VermilionCity | 112 | 0 | 105 |
  | gTileset_LavenderTown | 195 | 0 | 194 |
  | gTileset_CeladonCity | 224 | 0 | 224 |
  | gTileset_FuchsiaCity | 165 | 0 | 164 |
  | gTileset_SaffronCity | 190 | 0 | 188 |
  | gTileset_CinnabarIsland | 63 | 0 | 62 |
  | gTileset_IndigoPlateau | 165 | 0 | 165 |
  | gTileset_ViridianForest | 38 | 0 | 38 |
  | gTileset_BerryForest | 20 | 0 | 20 |
  | gTileset_SeviiIslands123 | 271 | 0 | 264 |
  | gTileset_SeviiIslands45 | 145 | 0 | 142 |
  | gTileset_SeviiIslands67 | 212 | 0 | 212 |

- Ten sample names with crops were reported to the owner (water_1 0x12B, water_2 0x1D9, path_1 0x071,
  tall_grass_1 0x00D, grass_1 0x01C, sand_1 0x115, ledge_s_1 0x087, path_2 0x0D9, grass_6 0x01F,
  water_7 0x110). Per the task, work STOPS after the first tileset until the owner checks quality.
- Full suite: **117 tests, 1 failure** (map lint; step 5), 762 s.
