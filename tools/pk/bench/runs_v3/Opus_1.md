# Benchmark v3 run: Opus, brief 1 (Coastal path)

You are "Opus" in a level-design study (5 briefs x 6 models). Your map is **PkV3Opus1_Frlg**.
This file is your whole task; do it alone, start to finish. Work from the game repo root:
python tools/pk/pk.py <command>.

**Follow `tools/pk/agents/mapper-playbook.md` (phases 0-3; phase 4 only if your budget allows).**
Read it and `tools/pk/AGENT.md` first.

RULES (breaking one disqualifies the run):
- Change maps ONLY through pk commands; never write .bin/.json/.inc files yourself.
- Only touch PkV3Opus1_Frlg (and, for phase 4, your own copies named PkV3Opus1A/B/C_Frlg, deleted at the end
  with pk map delete). Never edit vanilla maps or other Pk* maps.
- Never run pk build, pk bp apply or pk map polish. ONE pk write at a time.
- ORIGINALITY: no pasted piece may be larger than 10x8, and at most 30% of your map's cells may come from
  any single vanilla map. This is a test of composing NEW terrain from vanilla parts, not of copying a
  vanilla route. Copying a whole route body disqualifies the run.
- Max 6 renders that you look at (normal, --annotate or --roles views of the map don't count; PNGs do).

## Start
python tools/pk/pk.py map new PkV3Opus1_Frlg --size 24x20 --tilesets gTileset_General_Frlg,gTileset_PalletTown --fill 0x010 --type route

## Brief
A quiet coastal path. Trees and grass in the north, a sandy shore with one cove in the
south, sea along at least the bottom 4 rows. A walkable exit on the top edge at x=10..13. Shoreline must
curve like vanilla; no straight natural edge longer than 6 tiles.
Vanilla references to study first: Route21_North_Frlg, PalletTown_Frlg, OneIsland_KindleRoad_Frlg.
No NPCs, signs or warps; terrain only.

## Report: tools/pk/bench/reports_v3/Opus_1.md (the only non-map file you may write)
Use exactly these headings, so 15 reports can be compared:
1. **Plan:** your 5-line plan from phase 0.
2. **Pieces:** source map + box for each vanilla piece you pasted.
3. **Tool use:** counts of kit find/fit, copy --repair, terrain auto (and --options), undo.
4. **Warnings hit:** every WARN reach / WARN repair line you got and what you did.
5. **Critic rounds:** the defect lists from your fresh critic and what you fixed (or "no subagent available").
6. **Final lint:** the summary line of pk map lint PkV3Opus1_Frlg.
7. **Hardest part:** 2-3 sentences, concrete (which tiles, which coordinates).
8. **Tool wishes:** what command or information would have helped most.
Do NOT rate your own map. End with: python tools/pk/pk.py map render PkV3Opus1_Frlg --scale 2
