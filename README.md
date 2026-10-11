# Silvaiee

**AI agents that ROM-hack Pokémon FireRed, plus a blind benchmark of how well they do it.**

The name comes from Silvally, the Pokémon that changes type depending on the memory you give it. Silvaiee
works the same way: plug in any model (Claude, GPT, Gemini, DeepSeek, Qwen...) and it becomes your mapper,
writer or pixel artist.

Silvaiee is built around `pk`, an LLM-friendly command line for pokeemerald-expansion (FRLG). `pk` lets AI agents (Claude, GLM, DeepSeek, Gemini...) edit a FireRed/LeafGreen ROM hack built on
[pokeemerald-expansion](https://github.com/rh-hideout/pokeemerald-expansion) **without reading raw game files**.
Agents never open `map.bin`, `map.json` or `scripts.inc`. They run short commands and get short, predictable
answers, so a map edit costs a few hundred tokens instead of tens of thousands.

> **Status: experimental, no support.** Built in one day as an experiment in AI-driven ROM hacking, and written
> almost entirely by AI agents (Claude, with GLM and DeepSeek). Tested on Windows with pokeemerald-expansion
> 1.17.1, FRLG build only. Issues and PRs are welcome, but there is no promise of answers.

## What it does
- **Maps:** compact text views of a map with coordinates and a legend, real PNG renders for vision models,
  painting, copying regions between maps, borders, and creating new maps.
- **Smart resize:** grows a map and moves everything that depends on coordinates in one step: events, warps,
  coordinates inside scripts (`setmetatile`, `setobjectxy`), connections on both sides, and heal locations.
- **Terrain:** material brushes (water, sand, grass, ledges, rivers, forests), solved with adjacency statistics
  learned from the vanilla maps, so new terrain uses real transition tiles.
- **Events and dialogue:** add, edit or remove NPCs, signs and warps. Text goes in as plain text and is wrapped
  to the real font width (208 px, 2 lines per box). `pk text check` finds overflows.
- **Tilesets and sprites:** a text pixel-art format (`.px`), a tileset compiler (palette packing, deduplication,
  registration), an importer for AI-generated images, and lint that catches copies of vanilla tiles and
  hand-drawn animated tiles.
- **Blueprints (`.bp`):** a declarative plan for a location (NPCs, trainers, signs, day/night variants) that
  can be checked, previewed as a render and applied idempotently.
- **Safety:** every write is one journaled transaction (`pk undo` / redo), there is `--dry-run` everywhere,
  `pk check` reports only new errors (vanilla issues are baselined), reachability is checked from every
  entrance, and `pk build` is staged.

## Install
1. Use a pokeemerald-expansion checkout with a working FRLG build.
2. Copy `tools/pk` into the repo root, so you have `<repo>/tools/pk/pk.py`. Optional: copy `pk.cmd` to the repo
   root to get a `pk` shortcut on Windows.
3. Requirements: Python 3.12+ and Pillow (`pip install pillow`). Nothing else.
4. Test that it works: `python tools/pk/pk.py help`. The first command builds an index in `.pk/`; add `.pk/`
   to your `.gitignore`.

## Quick tour
```
python tools/pk/pk.py map show PalletTown_Frlg            # text view of the map
python tools/pk/pk.py map render PalletTown_Frlg --scale 2 # PNG in .pk/render/
python tools/pk/pk.py map resize PalletTown_Frlg --add right=8,bottom=6 --fill extend
python tools/pk/pk.py ev add PalletTown_Frlg obj @8,9 gfx=LASS move=WANDER text="Hi!|Nice day.||Bye."
python tools/pk/pk.py text check PalletTown_Frlg
python tools/pk/pk.py undo
```
Output always starts with `OK` or `ERR CODE`. `MORE n` means more results (use `--offset`). `--json` gives one
JSON object.

## For agents
Point your agent at `tools/pk/AGENT.md`: a 35-line index, the same text `pk help` prints. `pk help <noun>`
gives details per area. `tools/pk/agents/` contains example role files for a multi-agent setup:
- `glm-tech.md`: technical work;
- `muse-mapper.md`: maps and dialogue;
- `deepseek-assets.md`: pixel art;
- `gemini-artist.md`: image generation.

`SPEC.md` is the full design and `DECISIONS.md` records why things are the way they are.

## What we learned (read this before you try it)
- **Technical edits work well with any model.** Events, dialogue, resize, checks and builds all work.
- **Map composition is the hard part.** Asking a model to paint terrain tile by tile gives a patchwork. The best
  results came from **kitbashing**: building new areas from large chunks of vanilla maps
  (`pk map copy ... --from OTHER_MAP`), then fixing seams.
- **Claude Opus and Claude Sonnet worked best.**
  - Opus designed, built and reviewed the toolchain.
  - Sonnet composed the best map: about 7/10 against vanilla with kitbashing.
  - Cheaper models (Haiku, DeepSeek, GLM) could follow instructions but couldn't judge their own renders, so
    their maps got worse with every fix.
- **AI image generators** (Gemini) make nice concepts, but the output is off-grid and off-palette. Treat it as
  reference, or run it through `pk px import` and clean it up by hand.

## Model benchmark
`tools/pk/bench/` holds two reproducible benchmarks, level design and dialogue. Each has one task file per
model, blind letters, a scoring page (`gui.py`) and three scores per entry: human, blind AI and self.

| Model | Map (Pallet extension) | Dialogue (Viridian NPCs) |
|---|---|---|
| Claude Opus | **6.0** | **8.3** |
| Gemini | 5.0 | 7.9 |
| Astra | 5.0 | 6.6 |
| Sol | 4.0 | 8.1 |
| DeepSeek v4.1 Flash | 2.5 | – |
| Claude Sonnet | 2.0 | 7.4 |
| Muse | 1.5 | – |

Scores are out of 10: the average of human and blind-AI scores. Vanilla = 10 for maps.

The short version:
- models write far better than they build;
- they can't judge their own maps (self-ratings up to +5.5 too high) but judge their own text fairly well;
- blind AI review matched human review almost exactly on maps.

The full tables, findings and caveats (one attempt per model, and the AI judge is Claude Opus) are in
[tools/pk/bench/results.md](tools/pk/bench/results.md). A pixel-art benchmark comes next.

Want to add a model? Copy a file from `runs/` and `dialogue/runs/`, change the tag, run it, and open a PR with
the outputs.

## Roadmap
Mountains and highlands, animated tiles, self-perfecting maps and art, and then a full Kanto rebuild.
See [ROADMAP.md](ROADMAP.md). Contributions are welcome; each round has clear, measurable targets.

## Known issues
- **`pk map lint` is broken.** It is meant to flag ugly maps (seams, staircase shorelines, rectangular patches,
  empty screens), but its thresholds were loosened until it reports `score:0` on everything, ugly maps
  included. Don't trust it. Recalibrating it against a set of deliberately ugly maps would be a great first PR.
- **`pk map paintover`** (snapping a painted image back onto metatiles) is experimental and was never validated
  on real AI paintings.
- **FRLG only.** Emerald/RSE maps are not supported.
- **The tests read your repo.** Some of them compare against vanilla maps, so they fail once you have edited
  those maps or renamed metatiles.
- **Hard-coded for 1.17.1.** Font width, metatile and tileset limits, and file layouts are set for this
  version. Other versions may need changes in `tools/pk/data/`.

## Not included
- **No Nintendo assets.** Exported vanilla tiles, sprites and renders are not in this repo. `pk` reads them from
  your own checkout.
- **No blueprints or region plans.** The project this was built for kept its blueprints private. `pk bp` works
  with any `blueprints/` folder you create (see `pk help bp`).

## License
MIT, see `LICENSE`. Pokémon is © Nintendo / Creatures / GAME FREAK. This project contains no Pokémon game
data and isn't affiliated with them.
