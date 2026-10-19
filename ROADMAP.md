# Silvaiee roadmap

**Goal:** an AI agent rebuilds a whole region so it looks like Game Freak made it, only a little richer:
mountains, highlands, living animated towns and new art, all through `pk`.

**What we learned so far** (details in [DECISIONS.md](tools/pk/DECISIONS.md) and the
[benchmark](tools/pk/bench/results.md)):
- **Models are good at ideas and dialogue, and bad at spatial detail.** They cut trees in half, miss edges
  and can't judge their own maps.
- **Fixing a bad map afterwards doesn't work.** Three rounds of automatic "polish" passed every number and
  looked worse every time.
- **What works:** build from real vanilla shapes, let the tools place the tiles, and make the correct way
  the easy way.
- **Local plausibility is not composition.** A map made of vanilla-looking 4x4 pieces can still be nonsense
  as a whole, so layout needs its own level.

The plan:
- **the model (or a human art director) decides the composition;**
- **algorithms learned from vanilla do the tiles, edges and objects;**
- **cheap, measurable checks replace expensive vision judges.**

Every item has measurable targets, and an item is done only when its numbers pass.

**Art direction:** tastefully richer than vanilla, never far beyond it.
- Shade count, detail and colour may go a little above vanilla, up to the richest vanilla examples.
- Style rules stay strict: 1 px tinted outlines, light from the top-left, no noise or dithering, GBA limits.
- Ground stays calm; extra richness goes into objects.

---

## Done ✅
- **Full-map export/import as text:** byte-identical round trip on 319/319 vanilla FRLG layouts.
- **`map lint`, calibrated on vanilla:**
  - every vanilla outdoor map scores 0;
  - every deliberately ugly test map scores above vanilla;
  - every badly rated benchmark map scores worse than the best one.

  `INFO SHAPE` (3x3 shapes vanilla never uses) and `INFO LAYOUT` (empty lawns, mirror symmetry) are
  info-only hints on top.
- **Broken objects:** `WARN OBJECT` flags a tree without its upper half, a rock face without its top, or a
  ledge without its end. It's learned from vanilla neighbours, and is 0 on all 72 vanilla maps. It includes
  the map edge against the border, and skips sides that have a connection.
- **Tile cards:** what each metatile *is* (physical class, role such as edge, corner or open, the neighbours it
  needs), computed from vanilla instead of guessed names. Role predictions are 89-95% accurate in
  leave-one-out tests.
- **Kitbashing:**
  - `pk kit find/fit` searches vanilla pieces across about 40 maps (pieces made only of shared tiles are
    portable between tilesets);
  - `map copy --repair` stitches the joins without rewriting the piece;
  - `kit fit` only offers pieces that don't break objects (checked by pasting in memory).
- **`terrain auto`:** fills a box from its surroundings; `--options 3` previews variants without writing. It
  never breaks a tree or rock.
- **Checks after every write:** `reach` (nothing got cut off, per entrance, collision before water), the
  repair rim warning, and a matching vanilla border on `map new`.
- **Blockout tiler:** a map drawn as letters (`T` trees, `~` water, `s` sand, `p` path, ...) plus an optional
  elevation layer is turned into real tiles. It chooses tiles by letter context, solves forests, rocks and water
  as whole regions, and solves large maps in sections. Five vanilla maps (Route 1, Route 22, Pallet Town,
  Route 3, One Island Kindle Road) rebuild from their own letters, leave-one-out:
  - 99.6-100% letters;
  - 0 broken objects;
  - 0 cells cut off;
  - 0 open banks.
- **aiceus:** a local art-director view that shows lint and objects on the map. Drag a box to write a ticket,
  find vanilla pieces that fit it, or preview `terrain auto` variants. It never writes to maps itself.
- **Benchmarks:** level design (v1 → v2 → v3, several models per round) and dialogue (8 models), with blind scoring.
  - **v2 result:** the same model (Sol) built a clearly better map with the kitbash tools than without them.
  - **v3 result:** only the strongest model (Argon) composes well on its own; the others repeat the same
    layout mistakes.

## Replaced 🔁
Planned, then overtaken by something that works better:
- **Diagonal solver weights** (missed: +0.97 pp, and no weight helped later) → the blockout tiler's 3x3 letter
  contexts and region solves.
- **Hand-written metatile names** (many of round 1's turned out wrong, e.g. `water_151` is walkable) → computed
  tile cards; the name audit lists the contradictions.
- **The polish loop** (three attempts, each judged worse by eye) → build correctly from vanilla shapes instead.
  `pk map polish` remains as an experimental tool.
- **Chunk score as a quality judge** (empty maps outscored vanilla) → kept as a report; quality is judged by
  lint, layout hints and the human eye.

## In progress 🚧
- **Whole objects only:**
  - a catalogue of complete vanilla objects (trees, forest edges, bushes, rocks, cliffs, fences, ledges);
  - `pk object place`, `pk object line` and `pk forest`;
  - low-level paint, copy and stamp **refuse** writes that would break an object or leave an open bank.

  Targets: 0 half trees from `pk forest` on any box size; a benchmark map with 262 broken objects gets fixed
  through these commands.
- **Two-level layout:**
  - a coarse macro sketch (one letter per 4x4 area, from a model, the owner or a vanilla template) fixes the
    composition;
  - chunks learned from vanilla fill in the detail, with coasts and forest edges free to wiggle off the grid.

  Target: a coastal brief gives 3 variants with no floating paths or sand, every block matching its sketch,
  and the owner judging the composition first.
- **Paths and shapes:**
  - a winding path tool (A* with vanilla road statistics);
  - an MST network linking every exit;
  - autotiling learned from vanilla 8-neighbour masks, so every junction, crossing and dead end gets its
    correct tile;
  - a shape library of real vanilla ponds, coves, islands and groves.

  Known bugs: the path tool fails on the Sevii tileset and at diagonal joins.

## Next
- **Blind scoring** of the v3 maps and the dialogue entries, then a **V4 map round** with the new commands on
  the same briefs, as a before/after test.
- **Dialogue polish:**
  - automatic checks for caps rules (MAGIKARP, TRAINERS) and wrong game facts;
  - a style guide drawn from the best entries;
  - per-town context, so NPCs stay consistent across the region.
- **Production split:** a strong model plans the layout and critiques it; a cheap model carries out tickets and
  writes; the human art director has the final say in aiceus.

## Later
### Highland kit ⛰️
- **Plateau brush:** paint a shape at a height level, and the tools build cliff faces, rim edges, corners and
  stairs, with correct collision and elevation, learned from Routes 3, 4, 9, 10, 22 and 23. (The blockout
  elevation layer and the elevation-aware reach check are the first pieces.)
- **Ledges and stairs** as first-class objects.
- **Richer rock art** through the asset pipeline.
- Target: rebuild vanilla Route 3/4's cliffs with the brush; a new highland map passes lint, objects and reach
  from every entrance.

### Animation pipeline ✨
- **Animated `.px`** with 2-8 frames per tile, compiled into `src/tileset_anims.c` within VRAM and CPU budgets.
- **Animation critic:** a seamless loop, a small moving area, FRLG-like timing.
- **First set:** waving tall grass, a waterfall, a town fountain, lamps at night.

### Rebuild Kanto
- Region by region: macro layout, blockout, whole objects, checks, a human pass in aiceus, then build.
- A dialogue pass for every town.
- Playtest every changed map in mGBA.

---

## Help wanted
Self-contained tasks with clear targets:
- **The macro layout level, or the path autotile table:** pure Python, measured against vanilla.
- **The plateau brush,** if you like algorithms.
- **The animation compiler,** if you know `tileset_anims.c`.
- **Benchmark:** add a model. Copy a file from `tools/pk/bench/runs_v3/` (maps) or `tools/pk/bench/dialogue/runs/`
  (dialogue), run it, and open a PR with the outputs.
