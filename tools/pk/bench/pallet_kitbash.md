# Benchmark: kitbash an enlarged Pallet Town

This is the same task for every model. Replace `{M}` with a short model tag (Sonnet, Astra, Qwen...) and paste
everything below the line as the prompt. Run each model once, in a fresh session, with no help in between.

Afterwards record in `tools/pk/bench/results.md`:
- the model and its settings;
- whether it can see images;
- the number of renders it looked at;
- tokens or cost if you can see them;
- the time it took.

---

FireRed ROM hack, level design test. Max 5 renders, each at --scale 1; LOOK at each PNG if you can see images.
Run everything from the repo root: python tools/pk/pk.py <command>. Skim tools/pk/AGENT.md and pk help map only.

RULES (breaking any of them disqualifies the run):
- Change maps ONLY through pk commands. Never write .bin, .json or .inc files yourself.
- Only touch PkPallet{M}_Frlg. Never edit PalletTown_Frlg or any other map. Never run pk build or pk bp apply.

Goal: extend Pallet Town south with a small park and a beach. Build it mostly by COPYING large chunks of vanilla
maps; paint single tiles only to fix seams.

1. python tools/pk/pk.py map new PkPallet{M}_Frlg --size 24x28 --tilesets gTileset_General_Frlg,gTileset_PalletTown --fill 0x296 --type town
2. python tools/pk/pk.py map copy PkPallet{M}_Frlg 0,0,24x20 0,0 --from PalletTown_Frlg
3. Find good chunks in Route1_Frlg and Route21_North_Frlg (shoreline, sea, tree corners, flowers), using
   render or map show.
4. Fill rows y=20..27 with chunks:
   pk map copy PkPallet{M}_Frlg x,y,WxH tx,ty --from Route21_North_Frlg   (or Route1_Frlg)
   - a park near the town, a sandy beach below it, and the sea along the whole bottom edge;
   - an irregular shoreline: no staircases, no straight natural edge longer than 6 tiles;
   - the vanilla pond (x7-10, y17-19) must connect to the sea or end naturally;
   - the town must stay walkable down to the beach.
5. Fix visible seams with small copies or pk map paint. pk undo is free.
6. Final step: python tools/pk/pk.py map render PkPallet{M}_Frlg --scale 2
   Report in 5 lines: the chunks you used, the render path, and an honest 0-10 rating against vanilla.
