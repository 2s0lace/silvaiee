# Benchmark run: Gemini

You are the "Gemini" entry in a level-design benchmark. Your tag is **Gemini**: your map is PkPalletGemini_Frlg. This file is your whole task, so do it now, alone, start to finish.

FireRed ROM hack, level design test. Max 5 renders, each at --scale 1; LOOK at each PNG if you can see images.
Run everything from the repo root: python tools/pk/pk.py <command>. Skim tools/pk/AGENT.md and pk help map only.

RULES (breaking any of them disqualifies the run):
- Change maps ONLY through pk commands. Never write .bin, .json or .inc files yourself.
- Only touch PkPalletGemini_Frlg. Never edit PalletTown_Frlg or any other map. Never run pk build or pk bp apply.

Goal: extend Pallet Town south with a small park and a beach. Build it mostly by COPYING large chunks of vanilla
maps; paint single tiles only to fix seams.

1. python tools/pk/pk.py map new PkPalletGemini_Frlg --size 24x28 --tilesets gTileset_General_Frlg,gTileset_PalletTown --fill 0x296 --type town
2. python tools/pk/pk.py map copy PkPalletGemini_Frlg 0,0,24x20 0,0 --from PalletTown_Frlg
3. Find good chunks in Route1_Frlg and Route21_North_Frlg (shoreline, sea, tree corners, flowers), using
   render or map show.
4. Fill rows y=20..27 with chunks:
   pk map copy PkPalletGemini_Frlg x,y,WxH tx,ty --from Route21_North_Frlg   (or Route1_Frlg)
   - a park near the town, a sandy beach below it, and the sea along the whole bottom edge;
   - an irregular shoreline: no staircases, no straight natural edge longer than 6 tiles;
   - the vanilla pond (x7-10, y17-19) must connect to the sea or end naturally;
   - the town must stay walkable down to the beach.
5. Fix visible seams with small copies or pk map paint. pk undo is free.
6. Final step: python tools/pk/pk.py map render PkPalletGemini_Frlg --scale 2
   Write your report to tools/pk/bench/reports/Gemini.md (this is the only non-map file you may create):
   the chunks you used, the number of renders you looked at, whether you could see the images,
   problems you hit, and an honest 0-10 rating against vanilla. Then reply "done" plus that rating.
