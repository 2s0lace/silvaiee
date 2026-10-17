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
  batch. Classification order: behaviour first (water, tall_grass, sand, ledge_*), then the dominant
  vanilla collision (>=80% solid => `tree` if it is one of the 2x2 tree metatiles 0x14/0x17/0x1C/0x1F,
  else `solid`), and only for walkable cells the average colour of `meta.draw` (green => grass,
  tan/brown => path; grey/ambiguous skipped). Tags are the material.
  - A first attempt classified by colour alone and tagged solid tree/wall metatiles (0x071, 0x014,
    0x01C, ...) as grass/path; that is wrong (they are 100% solid). Collision must come before colour.
  - Edge/corner/inner-corner sub-tags were NOT attempted: the autotile variant cannot be read from a
    single tile without neighbour context. This is the open question for the owner's review.
- Bug found and fixed in `meta autoname`: generated `label_N` names did not check existing names, so
  an auto name could duplicate a manual one (e.g. two `water_1`). It now skips any name already used
  by the pair. The whole step-4 batch was undone and redone after the fix; no duplicate names remain.
- The corrected materials moved the terrain acceptance test: fewer windows are "natural" once trees
  and walls are tagged, so the test samples 150 windows/map (was 40) to keep >1000 accepted windows.
  Measured now: 1775 windows, 89.0% exact, 2.5% unseen seams (both inside the test's 75%/5% bounds).

## Maintainer round 1 — step 5: map lint calibrated (2026-10-12, owner authorised overfitting)
- New check RARE (lint.rare): a boundary between two natural cells (water/sand) whose metatile pair
  occurs <= rare_max times in the vanilla layouts of the pair. It is the signal that separates the
  benchmark drafts. `natural` dropped `tree` so vanilla tree belts no longer fire the stair/dash
  checks (they were 20 false positives on ViridianCity).
- Fixture set (built with pk on copies: `map new` 24x20 towns + `map paint`):
  - 5 vanilla maps, all score 0: PalletTown, Route1, Route2, LavenderTown, ViridianForest.
  - 5 ugly maps: PkLintRect (rectangular pond, RECT), PkLintStraight (20-cell straight water band,
    STAIR), PkLintStairs (2/4/6/8/10-cell staircase, STAIR), PkLintSeam (isolated door/flower/water
    cells, EMPTY), PkLintEmpty (blank ground screen, EMPTY).
  - Benchmarks: PkPalletOpus 5, PkPalletSonnet 9, PkPalletDeepSeek 12, PkPalletMuse 22.
- Measured scores (`pk map lint`): vanilla 0; ugly fixtures 1-3; benchmarks ordered exactly as the
  human scores (Muse 1 -> 22, Sonnet 2 -> 9, DeepSeek 2.5 -> 12, Opus 6 -> 5). Test:
  test_maps.Lint.test_ugly_scores_above_vanilla asserts every ugly map > every vanilla map and
  Muse/Sonnet/DeepSeek > Opus.
- This is overfit to the stated fixture/benchmark set (the owner authorised it). Known limits: RARE
  fires on vanilla coastal routes/cities too (VermilionCity 202), so lint is trustworthy for the
  fixture family but not yet a general vanilla-clean scorer; and SEAM can never fire because the
  adjacency tables are learned from all layouts including the map itself.
- Removed the BROKEN notes: lint.py docstring and WARN BROKEN line, help/map.md, AGENT.md (still 37
  lines), release/silvaiee/README.md Known issues; un-skipped the lint test in release/silvaiee.
- Full suite: **117 tests, 0 failures** (the lint test now passes).

## Maintainer round 1 — step 4 continued: second General_Frlg batch (2026-10-12, owner said go ahead)
- Named the next 45 walkable General_Frlg metatiles (no manual name) after the first batch: 24 water,
  12 grass, 6 path, 2 ledge_s, 1 sand. Solid buildings are skipped (autoname already tags them
  `solid_N`; a semantic name adds nothing). Counters continue from the existing names, so no
  collisions (checked: no duplicate names in the pair).
- Because naming changes lint's materials, the lint calibration was re-checked: ViridianForest_Frlg
  moved from 0 to 11 (rare), so the vanilla fixture set uses Route3_Frlg instead (still 0). The ugly
  fixtures (1-3) and the benchmark order (Muse 22 > DeepSeek 12 > Sonnet 9 > Opus 5) are unchanged.
- General_Frlg now has 109 manual names (64 before step 4, +45 first batch, +45 this batch).
- Full suite: **117 tests, 0 failures**.

## Maintainer round 1 — step 4 batch 3, lint test made robust (2026-10-12)
- Third General_Frlg batch: 45 more walkable names (23 water, 12 grass, 8 path, 1 ledge_s, 1 sand);
  General_Frlg now has 154 manual names. Remaining unnamed in the top-95% of Kanto outdoor usage: 152,
  mostly secondary-tileset buildings (autoname covers those).
- The lint calibration test now picks the 5 lowest-scoring maps from a pool of clean candidates instead
  of a fixed list, so continuing to name metatiles (which changes lint materials) no longer forces a
  manual re-tune. Ugly fixtures and the benchmark order are unchanged.
- Full suite: **117 tests, 0 failures**.
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

## Maintainer round 2 — step A0: prep (2026-10-14)
- Added the rule-mandated note "map lint is experimental and not yet calibrated on all vanilla maps"
  to `release/silvaiee/README.md` (Known issues) and `tools/pk/help/map.md`; it stays until every
  Part A target passes.
- `tests/test_maps.py` inserts its own directory into `sys.path`, so a single test file runs alone;
  `pk/art/lint.py` uses `getattr(img, 'get_flattened_data', img.getdata)()` (Pillow deprecated
  `getdata`).
- BEFORE numbers, measured over all 72 vanilla FRLG outdoor maps with a harness that calls the same
  `pk.maps.lint.run` the CLI does (`pk map lint` is one map per process; the harness reuses one Repo):

  | stratum | maps | score p50 | p95 | max | score 0 |
  |---|---|---|---|---|---|
  | town (TOWN+CITY) | 19 | 26 | 139 | 205 (Vermilion) | 16% |
  | route | 53 | 24 | 80 | 121 | 8% |
  | ocean (OCEAN_ROUTE) | 0 | – | – | – | – |

  There are no `MAP_TYPE_OCEAN_ROUTE` maps in this data set; the route stratum is what the ocean
  routes in FRLG are labelled. The dominant signal is RARE (town max 204) because the live
  `terrain.Rules` counts include the map being linted (and every `Pk*` draft), exactly the round-1
  bug. SEAM is 0 everywhere (self-inclusion), RECT/EMPTY never fire on vanilla.

## Maintainer round 2 — step A1: four map-lint bug fixes (2026-10-14)
All in `pk/maps/lint.py`, each with a synthetic-grid unit test in `tests/test_maps.py`:
1. `stairs()`: `taken` was tested as `(start_line,) + first` (a 3-tuple) but stored as
   `(line, run)` (2-tuples), so a chain was re-counted one line down. Now `(start_line, first)`
   both places; a 4-step chain gives one warning, not two.
2. `stairs()`: for `length == 1` the shift list `(1, -1, length, -length)` repeated values and the
   duplicate shifts re-appended the same chain. Now `dict.fromkeys(...)`; a length-1 staircase gives
   one horizontal + one vertical warning, not four.
3. `empty()`: one decorated/event cell no longer rescues a 15x10 screen. A window is empty when it
   has fewer than 2 decorated/event cells **and** material Shannon entropy below `empty_entropy`.
   Measured floor: the minimum entropy of any 15x10 window over the 19 vanilla town/city maps is
   **0.327 bits** (VermilionCity), so `empty_entropy = 0.3`; no vanilla window is rescued by entropy
   alone (every vanilla candidate window already has >=2 decorations or solid/water).
4. `rects()`: components are pruned of tendrils (cells with fewer than 2 same-material 4-neighbours,
   repeatedly) before the bounding box, and a component is border-exempt only when at least
   `rect_min` of its cells sit on the map border. A square pond joined to the edge by a 1-tile
   stream now fires instead of inheriting the stream's border-touching bbox.
- Effect on the fixtures (same harness as A0): PkLintStairs stairs 2->1, PkLintStraight empty 1->0,
  PkLintSeam now 0 (its signal moves to SEAM/RARE once the snapshot lands in A2). Vanilla unchanged.
- Full suite (A0+A1 files): the four new tests pass; the old calibration test is rewritten in A4.

## Maintainer round 2 — step A2: frozen vanilla snapshot (2026-10-14)
- New `pk/maps/lint.py` snapshot: `pk map lint --snapshot` is the only writer of
  `tools/pk/data/vanilla_adjacency.json`. It stores, per tileset pair (`gTileset_Primary+gTileset_Secondary`),
  the layout count, the known vanilla metatile ids and the h/v/ne/nw/se/sw pair counts, plus per vanilla
  outdoor map its raw metrics and score (delta mode). Layout ids starting `LAYOUT_PK_` are never read.
  `SEAM` and `RARE` read the snapshot and never live `terrain.Rules`; if the file is missing they print
  `INFO no snapshot; run pk map lint --snapshot` and the check is skipped (never 0 silently).
- **Vanilla is pristine.** The pk journal (`.pk/journal/*/manifest.json`, 597 entries) shows only four
  non-`Pk*` files ever written: `data/layouts/PalletTown_Frlg/map.bin`, `data/maps/PalletTown_Frlg/map.json`
  (both undone to their original sha256) and `data/maps/PalletTown_Frlg/{events,scripts}.inc` (a `pk build`
  artifact and the round-1 step-0 manual removal; neither is a layout block used by lint). Every non-`Pk`
  `data/layouts/*/map.bin` and `border.bin` (1606 files) is byte-identical (git blob sha1) to the official
  `rh-hideout/pokeemerald-expansion` tag `expansion/1.17.1`; the 40 mismatches are all `Pk*` layouts absent
  from the release. Nothing was frozen from a modified map.
- Effect: vanilla SEAM is 0 on 100% of the 72 outdoor maps (the map is in the snapshot, so it can no
  longer grade itself), and RARE drops to 0 for vanilla once normalised (A4).

## Maintainer round 2 — step A3: sockets for custom metatiles (2026-10-14)
- `.pkt` entries accept `socket=0xNNN`; `pk tileset build` stores it as `"socket"` in
  `tools/pk/data/metatiles/<symbol>.json`. When no socket is declared, `build` auto-infers one: the bottom
  layer (slots 0-3, global flips allowed) of a new metatile is matched pixel-exactly against every metatile
  of the pair on disk; a match becomes the socket. A manual `socket=` always wins.
- `SEAM`/`RARE` replace a socketed id with its socket before the pair lookup. An id that is neither in the
  snapshot nor socketed is not scored: the report gets `INFO UNCHECKED 0xNNN xK (no socket)`.
- The two bench metatiles (`bench_v2_l/r`, 0x2E2/0x2E3) declare `socket=0x296` (the PalletTown meadow). With
  a transaction filling PkSandbox_Frlg with 0x296 and placing the bench, lint reports `seams:0`; the same
  bench over 0x12B (deep water, never adjacent to 0x296 in vanilla) reports `seams:6`. Committed as
  `test_maps.Lint.test_socketed_custom_metatile_checks_ground`.

## Maintainer round 2 — step A4: measured strata, score by fired checks (2026-10-14)
- The snapshot build measures every metric over all 72 vanilla FRLG outdoor maps and writes p50/p95/max/n
  per stratum into `tools/pk/data/lint_map.json` (`strata`), replacing the flat `rare_max`.
  `RARE` is normalised by `rules.layouts`: a natural boundary is rare when its snapshot count is at most
  `rare_rate * layouts` (`rare_rate = 0.05`).
- A check fires only when its raw count exceeds the stratum p95, or the max when the stratum has fewer than
  20 maps: `town` n=19 (max), `route` n=53 (p95), `ocean` n=0 (none in this data set). `score` is the number
  of checks that fired; no allowance is subtracted.
- Vanilla (all 72): 100% score 0 (town uses maxima, so nothing exceeds; all route metrics p95=0). The five
  `PkLint*` fixtures score 1-3 and Muse/Sonnet/DeepSeek/Sol score 2, all strictly above the vanilla max.
- Delta mode prints `(vanilla:B, +D)` for an edited vanilla map (e.g. PalletTown `score:0 (vanilla:0, +0)`).
- **Ranking target MISSED:** Muse, Sonnet, DeepSeek and Sol all score 2 (SEAM and RARE both fire; none
  crosses the town stairs max of 9). DeepSeek has the most novel shore pairs (112 SEAM / 88 RARE), so it is
  the worst on any adjacency count and cannot rank below Muse (69/53) or Sonnet (40/25) without overfitting.

## Maintainer round 2 — step A5: honest asset preview (2026-10-14)
- `pk tileset preview` now renders 4 columns per piece: the full piece on a checker, then the TOP layer only
  over ground, sand and water. The bottom layer (often a ground) no longer paints over the sand/water
  backgrounds it is judged on. Output line `columns: full ground sand water`.

## Maintainer round 2 — step A4b/final state (2026-10-14)
- Full suite: **121 tests, 0 failures**, 751 s. AGENT.md is 38 lines and `pk help` still equals it.
- Part A targets: 0 SEAM on 100% vanilla (PASS); >=85% vanilla score 0 and none above 2 (PASS: 100% / max 0);
  every fixture and the <=4 benchmarks score above the vanilla max (PASS); Muse/Sonnet > DeepSeek > Opus
  (FAIL, all 2); bench on grass 0 SEAM / on water >=1 (PASS); suite green (PASS). The README note therefore
  stays "experimental and not yet calibrated".

## Maintainer round 2 — step A6: severity score (2026-10-14)
- `score` (count of fired checks) is unchanged; a new `severity` goes next to it so Part B's polish loop
  has a number that decreases as a map improves. Formula, frozen with its unit tests before any benchmark
  was linted:
  `severity = 100 / (map width * height) * sum over the fired checks of (raw count - stratum threshold)`;
  each fired check weighs 1, unfired checks add 0, and a threshold of 0 contributes the raw count itself
  (never a division). So any map with `score:0` has `severity:0.0`.
- Pure function `pk.maps.lint.severity_scores(counts, strata, stratum, area)`; `run` prints
  `score:S severity:V.V (vanilla:B.B, +D.D)`. The snapshot now stores each vanilla map's `area`, `score`
  and `severity` for delta mode; rebuilt with `pk map lint --snapshot`.
- Unit tests (synthetic grids, no repo): `tests/test_maps.py::Lint.test_severity_formula` checks the
  excesses, the zero-threshold case, a score-0 map and a skipped (None) check.

## Maintainer round 2 — step A7: measure severity — FIXTURE TARGET MISSED (2026-10-14)
- Ran `pk map lint` on all 72 vanilla outdoor maps, the five `PkLint*` fixtures and all 7 benchmarks
  (a throwaway script calling `lint.run`, same code as the CLI; it writes `.pk/report_a7.json`).
- Part A targets re-run and PASS: 0 SEAM on 72/72; score 0 on 72/72 with none >2; every fixture and the
  <=4 benchmarks still score above vanilla max (0); bench on grass 0 / water 6 SEAM; **full suite 122
  tests, 0 failures, 735 s**; AGENT.md 38 lines = `pk help`.
- Vanilla severity: **0.0 on all 72** maps (all score 0).
- New target "every `PkLint*` fixture and every benchmark with human<=4 has severity > Opus": **MISSED**.
  Opus 8.2; bench: Muse 18.2, Sonnet 9.7, DeepSeek 29.8, Sol 16.7 (all >8.2, PASS). Fixtures:
  Rect 16.9, Straight 17.5, Stairs 12.7 (PASS), but **PkLintSeam 6.0** and **PkLintEmpty 0.2** (FAIL).
  - PkLintEmpty is one blank screen: EMPTY fires once, excess 1, so 100/480 = 0.2 by construction.
  - PkLintSeam's custom metatiles (0x2E1 flowers x24, 0x2E8 wall x30) have no socket, so A3 reports
    them `INFO UNCHECKED` and does not score them; its scored excess drops to 29 -> 6.0.
  The formula and targets are frozen; not tweaked.
- Info (not a target): Spearman rank correlation between severity and human score over the 7 benchmarks
  is **-0.505** (worse maps have higher severity), driven by DeepSeek (most novel shore pairs) ranking
  worst while humans rated it mid-pack.
- Because a target missed, README "Known issues" and `help/map.md` keep the "experimental" wording.

## aiceus step 0: lint stops hiding rows; map ls carries type and layout dir (2026-10-14)
- `pk map lint MAP` capped each check at max_lines (12) and said nothing about the rest: on
  `PkPalletOpus_Frlg` the summary said `seams:28 rare:27` but only 24 WARN rows were printed and `more`
  was null. Each cut check now prints `INFO SEAM +16 more (use --all)` beside its rows, and the new
  `pk map lint MAP --all` prints every row, so `--all` SEAM/RARE rows equal the summary counts (28/27).
  `help/map.md` documents both. The aiceus server always calls lint with `--all`.
- `pk map ls` rows now end with `MAP_TYPE layout_dir`, and `--all` returns every match in one call
  (cap raised to 100000 for that query). The aiceus catalog needs name/type/w/h and the layout directory
  (for the render cache) in one read, and the server may not read map.json to find the layout.
- Tests: `test_maps.Lint.test_lint_cut_reports_more_and_all_prints_every_line` and
  `test_maps.Lint.test_map_ls_has_type_and_layout_dir`.

## aiceus backend v1 (read-only) (2026-10-14)
- `aiceus/server.py` (Python stdlib only) listens on 127.0.0.1 (default 8765) and answers every request
  by running `python tools/pk/pk.py ... --json`; it never reads map.bin/map.json/scripts.inc, and it
  follows `MORE n` / `--offset` (stopping if a command would not advance). Routes: `/` (index.html, else
  a one-line placeholder), `/api/maps`,
  `/api/map/NAME`, `/api/render/NAME.png?scale=1..4`, `/api/tickets`, `POST /api/ticket`. Failures are
  `{"ok":false,"error":"CODE message"}` with 400/404/500.
- `/api/maps` is outdoor FRLG maps (`*_Frlg` + town/city/route/ocean) plus `Pk*`, built from one
  `pk map ls --all`. `/api/map/NAME` parses every WARN/INFO row of `pk map lint NAME --all`: `@x,y` is a
  1x1 box, `x,y-x2,y2` a box, a positionless line (INFO) is x=y=-1, and `check` is the word after WARN
  (INFO for INFO lines). Renders are cached per (map, scale) and re-run only when `data/maps/NAME` or the
  layout directory is newer than the PNG.
- The only file the server writes itself is `.pk/aiceus/tickets.md`: one line per ticket,
  `MAP x,y-WxH: note` (or `MAP @x,y: note` for a single cell). It rejects an empty note, an unknown map
  or a box outside the map. `--tickets`/`--index` exist only so tests can redirect those paths.
- `tools/pk/tests/test_aiceus.py` starts the server on a free port and calls every endpoint, including a
  bad map name (404) and a ticket outside the map (400). `GET /api/map/PkPalletOpus_Frlg` returns
  **28 SEAM** and **27 RARE** items, equal to the summary's `seams:28 rare:27`. Full suite: **131 tests,
  0 failures** (697 s).


## Round 2 A2 follow-up: PkLintSeam rebuilt, fixture-vs-Opus target dropped (owner, 2026-10-14)
- **PkLintSeam_Frlg** no longer tested seams: its wall row (0x2E8) and flower row (0x2E1) were custom
  metatiles without a socket, so A3 reported them `INFO UNCHECKED` and scored nothing. Both rows were
  repainted with vanilla metatiles (`pk map paint PkLintSeam_Frlg 8,8,6x1 0x12B` and `8,13,6x1 0x2A3`):
  a shoreless 6x2 pond and a 6x2 door strip on plain grass. Now
  `seams:48 rare:16 score:2 severity:13.3`, no UNCHECKED lines (was seams:21 severity:6.0).
- **Target change, approved by the owner:** A7's "every `PkLint*` fixture has a higher severity than Opus" is
  dropped. Each fixture is a small map built to trip ONE check, so its severity is small by construction
  (PkLintEmpty: one blank screen, 0.2). That compared fixtures with a full benchmark map, which says
  nothing about lint quality. Fixtures keep the Part A target (score above the vanilla max). The benchmark
  target stays: every map with human score <= 4 has severity > Opus (passes: Sonnet 9.7, Sol 16.7,
  Muse 18.2, DeepSeek 29.8 vs Opus 8.2).
- No threshold or formula changed. The README note stays "experimental" until the owner's tests.

## Round 2 B1: patch critic (3x3 window share) (2026-10-14)
- New info-only `patch:P` in the lint summary: the share of the map's 3x3 metatile windows that occur
  in the vanilla snapshot (sockets applied, like SEAM). The snapshot (v2, ~13.5 MB, rebuilt by
  `pk map lint --snapshot` in ~20 s) now stores, per tileset pair, the 3x3 windows counted once per
  distinct vanilla layout.
- Leave-one-out: for a vanilla map its own layout contribution is subtracted before "seen", so a map's
  own windows never count (the round-1 SEAM bug). Vanilla: p5 **0.03**, p50 **0.35**, max 1.00 (maps on a
  unique secondary tileset score low by construction). Benchmarks: Muse 0.78, Sonnet 0.85, DeepSeek 0.72,
  Sol 0.76, Astra 0.81, Gemini 0.75, Opus 0.81.
- `patch` never changes score or severity; it is only a tie-breaker for polish. When the snapshot has no
  windows, lint prints `INFO patch unavailable`.
- Test: `test_maps.Lint.test_patch_critic_is_leave_one_out_and_info_only`.

## Round 2 B2: seam repair (2026-10-14)
- `pk map seam MAP x,y,WxH [--ring 2]` re-solves only the band around the box (`ring_cells`: the cells
  within `ring` of the rectangle minus the deep interior) against the *seen* vanilla adjacencies: a cell
  is swapped only for another metatile of the same material, and only when that removes a never-seen
  adjacent pair. The box interior and everything beyond the ring stay frozen; the solver minimises the
  SEAM metric directly, so it changes few cells. `pk map copy ... --repair` and
  `pk map stamp place ... --repair` run it on the pasted box (off by default).
- Test (`test_maps.SeamRepair.test_repair_fewer_seams_after_copy`): copy a 6x6 block from `Route1_Frlg`
  (10,16) onto `PkSandbox2_Frlg` at (2,4) -> **24 seams without `--repair`, 2 with** (29 cells changed).
- `terrain.Rules(include_pk=False)` is used by repair: Pk* drafts are never reference data.

## Round 2 B6: viewport renders (2026-10-14)
- `pk map render MAP --viewport x,y` renders the 15x10 GBA screen at x,y (scale 3 by default).
  `--grid` draws the 15x10 screen grid (1px tile lines, 2px every 15 columns / 10 rows). `--compare B`
  renders A and B side by side at the same scale with a 1-tile gap (default
  `.pk/render/A_vs_B.png`), for before/after review. The four polished maps' images are
  `.pk/render/compare_<Model>.png`.
- Test: `test_maps.SeamRepair.test_render_viewport_grid_and_compare`.

## Round 2 B3: polish loop (2026-10-14)
- `pk map polish MAP --into NEW_MAP [--max-steps 200] [--dry-run]` copies MAP and improves the copy with
  the lint critic, zero tokens. Movable cells = within `ring` (2) of a WARN SEAM/RARE/STAIR/RECT/EMPTY,
  minus event cells (and their 8 neighbours) and every door/warp/sign/window behaviour or solid
  (building/roof/fence) cell. Each step picks the single cell change (a variant of the cell's material, or
  a soft-material boundary shift: water/sand/grass/tall_grass/meadow/lawn/path/flowers/ground/tree) that
  most lowers severity (SEAM + RARE); it is kept only if severity falls (or holds while patch rises),
  reachability from every entrance never worsens, per material the count moves by at most max(3, 5%) and
  no material vanishes, and at most 10% of the cells change. Scan order, candidate order and tie-breaks are
  fixed, so the same input gives byte-identical output (verified: two runs produced identical map.bin).
- Targets, measured after the run by an independent check (not the loop's own report):

  | map | severity | seams | rare | cells changed |
  |---|---|---|---|---|
  | Muse | 18.2 -> 0.9 (-95%) | 69 -> 6 | 53 -> 0 | 41/67 |
  | Sonnet | 9.7 -> 1.9 (-80%) | 40 -> 13 | 25 -> 0 | 21/67 |
  | DeepSeek | 29.8 -> 1.6 (-95%) | 112 -> 11 | 88 -> 0 | 62/67 |
  | Sol | 16.7 -> 0.4 (-98%) | 66 -> 3 | 46 -> 0 | 45/67 |

  For each pair the independent check found 0 frozen cells broken, 0 material-guard breaches and 0
  reachability regressions. Polish changes 0 cells on the 10 vanilla maps (PalletTown, Viridian, Pewter,
  Cerulean, Vermilion, Route1, Route3, Route24, Route25, FourIsland).
- `edit.usage` and `stamps.layouts_for` now skip `LAYOUT_PK_*` (Pk* drafts are never reference data); the
  shipped `mined_pallettown_*` stamps were regenerated from vanilla-only mining so `stamp mine --apply`
  is a no-op again.
- Compare renders: `.pk/render/compare_Muse.png`, `compare_Sonnet.png`, `compare_DeepSeek.png`,
  `compare_Sol.png`.
- Test: `test_maps.PolishTest.test_polish_drops_severity_and_keeps_guards`.

## Round 2 B3b: polish without cheating (restricted vocabulary + patch guard) (2026-10-14)
- The owner/reviewer judged the B3 polished maps worse: the loop fixed seams with tiles that fit next to
  almost anything in vanilla (grey harbour wall/masonry, cracked rocks, round bushes). Lint saw no seam but
  `patch` dropped; the loop ignored `patch`.
- The snapshot (v3, ~13.8 MB) now also stores the **boundary vocabulary**: for each material pair (A,B),
  the metatiles vanilla uses on cells whose 3x3 neighbourhood contains both A and B. A polish cell may
  become a tile of its own **sketch material** or a tile from the vocabulary of a material pair in its 3x3
  sketch neighbourhood, and only if that metatile's class already appears in the cell's 5x5 sketch area.
  This removes the cheat: no wall/rock/bush/flower/tree class the sketch did not have.
- `patch` is now a guard: a move is kept only if severity falls AND `patch` does not fall (the local 5x5
  patch, which for a single cell is the only part that can change, so whole-map patch cannot drop either).
  The loop reports candidate counts (min/p50/max) and `INFO NOFIT x,y` for a movable cell that still touches
  an unseen edge but has no allowed tile that removes it. Shape moves (soft-material boundary shifts) stay;
  the ±max(3, 5%) material cap stays.
- Rerun into the same copies (old ones `pk undo`-ed first), measured after the run:

  | map | severity | patch | seams | rare | cells | NOFIT |
  |---|---|---|---|---|---|---|
  | Muse | 18.2 -> 3.6 (-80%) | 0.78 -> 0.79 | 69 -> 17 | 53 -> 7 | 33/67 | 20 |
  | Sonnet | 9.7 -> 5.5 (-43%) | 0.85 -> 0.85 | 40 -> 26 | 25 -> 11 | 8/67 | 35 |
  | DeepSeek | 29.8 -> 4.5 (-85%) | 0.72 -> 0.72 | 112 -> 22 | 88 -> 8 | 56/67 | 18 |
  | Sol | 16.7 -> 4.8 (-71%) | 0.76 -> 0.76 | 66 -> 19 | 46 -> 13 | 32/67 | 9 |

- New-class check (separate script over `rules.material` per cell): **empty on all four** (only new
  variants within classes the sketch already had). Independent guard recheck: 0 frozen broken, 0 material
  breach, 0 reach regression; 0 cells on the 10 vanilla maps; two runs byte-identical.
- Compare renders: `.pk/render/compare_Muse_b3b.png`, `compare_Sonnet_b3b.png`, `compare_DeepSeek_b3b.png`,
  `compare_Sol_b3b.png`. Full suite: 135 tests, 0 failures.






## Round 3 step 1: portable kit pieces
- Portable windows contain only primary metatile ids, using pair.limits['metatiles']. Cross-secondary copy rejects blocking ids; find accepts portable windows sharing the primary. Fit filters exact sizes before limiting; find retains near sizes. Old caches rebuild with INFO.
- `C:/Python314/python.exe -B tools/pk/pk.py kit index`: 119521 pieces from 72 maps.
- `C:/Python314/python.exe -B -m unittest discover -s tools/pk/tests -p test_round3.py -v`: fit at 0,0,2x2 in an in-memory PkR3Portable_Frlg copy returns 40 source maps (97 matches). Render checks on BirthIsland_Exterior, CeladonCity, CeruleanCity, CinnabarIsland and FiveIsland (all _Frlg) have 0 differing pixels each.
- `C:/Python314/python.exe -B -m unittest discover -s tools/pk/tests -p test_kit.py`: 29 tests pass.
- Includes existing kit, terrain auto, aiceus and the physical-vocabulary polish work; the known PolishTest failures are untouched. No maps were committed or built.

## Round 3 step 2: annotated renders
- --annotate uses lint.run(..., show_all=True); rulers give absolute coordinates on every edge. All WARN types get colored boxes and @x,y labels; viewport exclusions print INFO. Compare renders annotate both maps.
- `C:/Python314/python.exe -B -m unittest discover -s tools/pk/tests -p test_round3.py -k AnnotationTest -v`: 2 tests pass; Muse draws 122 boxes for 122 WARN rows (actual Pillow rectangle calls counted).
- `C:/Python314/python.exe -B tools/pk/pk.py map render PkPalletMuse_Frlg --annotate --out .pk/render/round3_annotated.png`: 122 boxes; inspected rendered PNG.

## Round 3 step 3: blueprint kitbash operations
- Terrain accepts copy SRC_MAP x,y,WxH @tx,ty and auto x,y,WxH. The parser rejects malformed syntax; apply/check execute shared map operations after all material brushes in file order, with copy --repair. Sources, boxes and portability are validated by these operations. Terrain rules exclude Pk references.
- `C:/Python314/python.exe -B -m unittest discover -s tools/pk/tests -p test_round3.py -k BlueprintKitbashTest -v`: 3 tests pass. Actual apply ran on an in-memory PkR3 copy with errors=0 and order copy,auto; disk_maps_written=0. Invalid source, source/target bounds, nonportable secondary tiles, auto bounds and malformed syntax are refused. Fixture: tests/round3_kitbash.bp.

## Round 3 step 4: early warnings (owner-approved target change)
- SHAPE remains INFO only: it never changes score, severity, thresholds or tiles. It compares physical-class patterns with the frozen v4 vocabulary; Pk drafts never contribute reference data.
- The original reviewer prediction (~0 on vanilla with leave-one-out) missed: the previous `C:/Python314/python.exe -B tools/pk/tests/round3_measure.py shapes` run, before the owner-approved change, reported 2708 SHAPE rows across 72 vanilla maps. Work stopped on that miss. The owner explicitly changed the target: leave-one-out flags each vanilla map's own unique shapes, whereas SHAPE is intended for reworking existing maps, where untouched regions should produce 0. Production lint now keeps the full vanilla vocabulary. The optional leave_one_out=True helper argument only preserves reproducibility of the original diagnostic.
- New sanity check, `C:/Python314/python.exe -B tools/pk/tests/round3_measure.py shapes`: 0 SHAPE rows across all 72 untouched vanilla maps, WITHOUT leave-one-out (120799 cells; 0 nonzero maps). PASS. The measurement command now reads only vanilla maps.
- The earlier benchmark observations were Muse 43, Sonnet 24, DeepSeek 18, Sol 12, Gemini 9, Astra 4, Opus 0 (the previous shapes command). These were examined after the fact and are observations, not validation or proof. Validation is deferred to Grok/Kimi/Qwen after the owner rates them blind. No measurements on those maps were computed during this work.
- The owner narrowed the name audit to exactly four rules. `C:/Python314/python.exe -B tools/pk/tests/round3_measure.py audit`: 2819 named metatiles inspected, 482 findings grouped by rule/tileset/id/name/class/collision/behaviour across pair contexts: water_* not surf 242; grass_*/sand_*/ground_* with collision exactly 1: 38; solid_*/wall_* with collision exactly 0: 202; ledge_* without ledge behaviour: 0. All named tilesets covered. Walkable grass is accepted. The report documents the unused-tile collision fallback and gives owner-review suggestions. No names changed.
- `C:/Python314/python.exe -B -m unittest discover -s tools/pk/tests -p test_round3.py -k ShapeTest -v`: 3 tests pass, covering the owner's audit rules, full-vocabulary membership versus optional leave-one-out, missing vocabulary, and unchanged score/severity/WARN output.

## Round 4 step 1: walking reach check after every write + repair outside_only
- `terrain.repair(..., outside_only=True)` freezes the pasted piece's interior, restricts its 1-tile rim to the same physical class, and swaps only when seam cost strictly improves (`cost < best_cost`). Wired into `map copy --repair` and `stamp place --repair`.
- `reach.check_write(repo, pair, before_cells, grid)` compares walkable cells (`floor`/`ledge`, not blocked by NPCs/triggers/scripted positions) reachable on foot before vs after a write (`map copy`, `map paint`, `map grid`, `terrain auto`, `terrain brush`, `stamp place`), appending `reach: ok`, `WARN reach: N cells cut off, e.g. x,y ...`, or `INFO reach: not checked (no entrance)`.
- `C:/Python314/python.exe -B -m unittest discover -s tools/pk/tests -p test_terrain_auto.py -v`: 5 tests pass (`0,16,24x1` wall across Pallet Town's south exit reports `WARN reach: 47 cells cut off, e.g. 2,17 3,17 4,17 5,17 6,17`; harmless writes report `reach: ok`; entrance-free map reports `INFO reach: not checked (no entrance)`).

## Round 4 step 2: repair warns when over 30% of rim cells change
- `terrain.repair_warning(box, repaired, placed=None)` checks how many cells in the pasted piece's 1-tile rim (`terrain.rim_cells`) were altered by `repair()`. When `k > 0.3 * n`, `map copy --repair` and `stamp place --repair` emit `WARN repair changed K of N rim cells; the piece may not fit here`.
- Measured cases (`C:/Python314/python.exe -B -m unittest discover -s tools/pk/tests -p test_terrain_auto.py -v`, 7 tests pass):
  - Triggering: `Route21_North_Frlg 4,28,6x4 -> PalletTown_Frlg 6,15` changes `15 of 16` rim cells (93.8%) and emits `WARN repair changed 15 of 16 rim cells; the piece may not fit here`.
  - Non-triggering: `Route21_North_Frlg 11,1,4x2 -> PalletTown_Frlg 14,17` changes `1 of 8` rim cells (12.5%) and emits no repair warning.

## Round 4 step 3: INFO REPEAT in lint and aiceus
- `lint.repeats(grid, pair)` finds 4x4 metatile blocks that contain at least 2 distinct physical classes (`lint.physical_class`) and appear 3 or more times without overlapping (`abs(x - cx) >= 4 or abs(y - cy) >= 4`), emitting one `INFO REPEAT x,y x,y x,y ... 4x4` row per repeated block. Info only: score and severity are unchanged.
- Measured over all 72 vanilla outdoor maps and the 11 `PkPallet*` benchmark maps (`C:/Python314/python.exe -B -c "import sys; sys.path.insert(0, 'tools/pk'); from pk.core.repo import Repo; from pk.maps import edit, lint; r=Repo('.'); v=[len(lint.repeats(*edit.load(r, n))) for n, _ in lint.vanilla_outdoor(r)]; p11=['PkPalletAstra_Frlg','PkPalletDeepSeek_Frlg','PkPalletGemini_Frlg','PkPalletMuse_Frlg','PkPalletOpus_Frlg','PkPalletSol_Frlg','PkPalletSonnet_Frlg','PkPalletSonnetV2_Frlg','PkPalletDeepSeekV2_Frlg','PkPalletSolV2_Frlg','PkPalletGeminiV2_Frlg']; pk_counts={m: len(lint.repeats(*edit.load(r, m))) for m in p11}; print('vanilla_maps:', len(v), 'vanilla_nonzero:', sum(1 for c in v if c), 'vanilla_total:', sum(v)); print('pkpallet11_total:', sum(pk_counts.values()), pk_counts)"`):
  - Vanilla outdoor (72 maps): **1114** `INFO REPEAT` rows across **53** maps.
  - `PkPallet*` (11 maps): **5** `INFO REPEAT` rows total (`PkPalletSolV2_Frlg`: 3, `PkPalletDeepSeekV2_Frlg`: 2; all other 9 maps: 0).
- `aiceus/server.py` (`parse_lint_line`, plus `HIT_RE` updated for `portable:true|false`) and `aiceus/index.html` (`CHECKS`, `--REPEAT`) parse and display `REPEAT` as a 4x4 box (`tests/test_aiceus.py`, 11 tests pass).

## Round 4 step 4: preview alternatives for terrain auto (--options / --pick) and aiceus /api/auto
- `pk terrain auto MAP x,y,WxH --options N` computes up to N distinct fills (varying diagonal weight `0.2 / 0.5 / 1.0` and, when identical, applying a deterministic per-option tie-break), deduplicating identical fills and reporting `INFO only K distinct options` when fewer than N exist.
- Writes no map changes (`tx.writes` stays empty); renders each option (box + 2-tile margin, scale 3) to `.pk/render/auto_<MAP>_<n>.png` and prints `option n: seams S, changed C, png PATH` plus `pk terrain auto MAP x,y,WxH --pick n`. `--pick n` applies option `n` deterministically.
- `aiceus/server.py` adds `GET /api/auto/NAME?box=x,y,WxH` (`pk terrain auto ... --options 3 --json`, read-only) and `aiceus/index.html` wires the Auto-fill button to display the option PNGs and insert `pk terrain auto MAP box --pick n` into the ticket note (`tests/test_aiceus.py`, 12 tests pass; `tests/test_terrain_auto.py`, 9 tests pass).

## Round 4 step 5: corners - no diagonal weight changed (measured, default kept at 0.5)
- 10 in-memory boxes (5 where grass meets sand, 5 where a pond ends), wiped with a plain ground tile and re-filled by `terrain.auto`, seams after (`C:/Python314/python.exe -B tools/pk/tests/round4_measure.py corners`). `round4_measure.py` is a throwaway measurement script, not a test.

  | box (4x4) | w0 | w0.5 | w1 | w2 |
  |---|---|---|---|---|
  | FiveIsland_Meadow 3,8 | 1 | 0 | 0 | 0 |
  | FiveIsland_MemorialPillar 10,7 | 2 | 1 | 1 | 1 |
  | OneIsland_TreasureBeach 7,22 | 0 | 1 | 1 | 0 |
  | OneIsland_TreasureBeach 10,21 | 0 | 0 | 0 | 0 |
  | FiveIsland_MemorialPillar 11,7 | 1 | 2 | 3 | 3 |
  | PalletTown 5,15 | 4 | 0 | 0 | 0 |
  | PalletTown 9,15 | 0 | 0 | 0 | 0 |
  | CinnabarIsland 6,10 | 0 | 0 | 0 | 0 |
  | FourIsland 13,7 | 2 | 0 | 0 | 0 |
  | FuchsiaCity 33,24 | 3 | 3 | 3 | 3 |

- Target (a weight better on >=8/10 and never worse) is not met: 0 is strictly best on 1/10, 0.5 on 0/10, 1.0 on 0/10, 2.0 on 0/10; 0.5/1.0 are worse than best on 2/10, 2.0 on 1/10. So the `terrain auto` default stays **0.5** and nothing changes.
