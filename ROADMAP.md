# Silvaiee roadmap

**Goal:** an AI agent rebuilds a whole region so it looks like Game Freak made it, only a little richer:
mountains, highlands, living animated towns and new art, all through `pk`.

**Lesson from the [benchmark](tools/pk/bench/results.md):** models are good at ideas and dialogue, and bad at
spatial detail and at judging their own maps. So the plan:
- **the LLM decides what goes where;**
- **algorithms perfect how it looks;**
- **cheap, measurable critics replace expensive vision judges.**

Every round has measurable targets, and a round is done only when its numbers pass.

**Art direction:** tastefully richer than vanilla, never far beyond it.
- Shade count, detail and colour may go a little above vanilla, up to the richest vanilla examples.
- Style rules stay strict: 1 px tinted outlines, light from the top-left, no noise or dithering, GBA limits.
- Ground stays calm; extra richness goes into objects.

---

## Round 1: reliable foundations (in progress)
- [ ] **Clean baseline:** the test suite is green, and the known-failing tests are explained.
- [ ] **Full-map export/import** as text. Target: byte-identical round trip on 100% of vanilla FRLG outdoor
      layouts.
- [ ] **Diagonal-aware terrain solver:** learn NE/NW/SE/SW neighbour tables. Target: +4 percentage points exact
      over baseline on the erase-and-resolve test, and no more seams.
- [ ] **Stamp mining:** detect houses, trees and fences in vanilla, and show them as objects in `map show`.
      Target: at least 80% of building and tree cells covered on Pallet, Viridian and Route 1.
- [ ] **Metatile catalog:** names and tags for the tiles that make up 95% of outdoor usage.
- [ ] **Working `map lint`:** calibrated on vanilla maps vs deliberately ugly ones. Target: every ugly map scores
      worse than every vanilla map, and the benchmark maps rank like the human scores.

## Round 2: self-perfecting maps and assets
- [ ] **Seam repair:** after every `map copy` or stamp, re-solve a 1–2 tile ring around the pasted area.
- [ ] **Patch critic:** score a map by how many of its 3x3 tile patterns appear in vanilla, plus shape
      statistics (straight runs, curvature, flat areas) pooled from all of Kanto.
- [ ] **Polish loop:** the LLM's sketch stays frozen. Shape polish (±1 tile boundary moves) and tile polish run
      on the CPU, and a change is kept only if the critic improves. Zero tokens.
      Target: benchmark maps scored below 3 reach at least 4, and vanilla maps stay unchanged.
- [ ] **Sockets:** each new object declares the ground it stands on, so it inherits vanilla statistics.
- [ ] **Asset style critic plus auto-cleanup:** palette snap, stray-pixel removal, outline gaps, light direction,
      shade count. Target: it passes the owner-approved assets (pier, bench_v2) and vanilla, and flags the
      rejected ones (planter, stone wall, footbridge).
- [ ] **Pixel seam check** for genuinely new terrain tiles, calibrated on vanilla seams.
- [ ] **Bootstrapping:** owner-approved maps become training data, so new assets gain real statistics.
- [ ] **Cheap judge test:** check whether small vision models (Gemini Flash, Haiku, Qwen-VL), given a yes/no
      checklist, agree with the human scores on the benchmark maps.

## Round 3: highland kit ⛰️
- [ ] **Plateau brush:** paint a shape at a height level, and the solver builds cliff faces, rim edges, corners
      and stairs, with correct collision and elevation. It learns from Routes 3, 4, 9, 10, 22 and 23.
- [ ] **Ledges and stairs** as first-class terrain: one-way drops and walkable ramps.
- [ ] **Height-aware reachability:** `reach.py` understands stairs, ledges and elevation changes, so no plateau
      becomes unreachable.
- [ ] **Richer rock art** through the asset pipeline: cliff faces a little more detailed than vanilla.
- Target: re-solve the cliffs of vanilla Route 3/4 with the brush and reproduce them closely; a new highland
  map passes lint, the patch critic and reachability from every entrance.

## Round 4: animation pipeline ✨
- [ ] **Animated `.px`:** 2–8 frames per tile in one file.
- [ ] **Compiler:** frame graphics plus registration in `src/tileset_anims.c`, within VRAM and per-frame CPU
      budgets.
- [ ] **Animation critic:** a seamless loop, a small moving area (vanilla flowers move only a few pixels),
      FRLG-like timing, same palette.
- [ ] **First set:** waving tall grass, a waterfall, a town fountain, flickering lamps at night.
- Target: the animations build, run in mGBA at full speed, and pass the critic; vanilla animations still
  work.

## Round 5: rebuild Kanto
- [ ] Apply the blueprints region by region with the full toolchain: sketch, polish loop, critics, a quick human
      check, then build.
- [ ] Dialogue pass for every town, using the dialogue benchmark's gate and voice rules.
- [ ] Playtest every changed map through the mGBA bridge.

---

## Help wanted
Each round splits into self-contained tasks with clear targets. Good first contributions:
- **Round 1 or 2:** calibrate `map lint` and build the 3x3 patch critic. Pure Python, and easy to measure.
- **Round 3:** the plateau brush, if you like algorithms.
- **Round 4:** the animation compiler, if you know `tileset_anims.c`.
- **Benchmark:** add a model. Copy a file from `tools/pk/bench/runs/`, run it, and open a PR.
