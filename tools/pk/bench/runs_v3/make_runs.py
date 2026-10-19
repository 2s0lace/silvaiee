"""Generate the v3 run files: 5 briefs x 3 models. python tools/pk/bench/runs_v3/make_runs.py"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODELS = ('Sol', 'DeepSeek', 'Argon', 'Sol56', 'Gemini', 'Opus', 'DS1017')

BRIEFS = {
    1: dict(title='Coastal path', size='24x20', tilesets='gTileset_General_Frlg,gTileset_PalletTown',
            fill='0x010', type='route', refs='Route21_North_Frlg, PalletTown_Frlg, OneIsland_KindleRoad_Frlg',
            text="""A quiet coastal path. Trees and grass in the north, a sandy shore with one cove in the
south, sea along at least the bottom 4 rows. A walkable exit on the top edge at x=10..13. Shoreline must
curve like vanilla; no straight natural edge longer than 6 tiles."""),
    2: dict(title='Forest route with ledges', size='20x30', tilesets='gTileset_General_Frlg,gTileset_PalletTown',
            fill='0x010', type='route', refs='Route1_Frlg, Route2_Frlg, Route22_Frlg',
            text="""A north-south route. Exits on the top and bottom edges at x=8..11, connected by a walkable
path. Tree borders on both sides. Two one-way ledges (jump south) that make the walk interesting, 2-3 tall
grass patches, and one side nook behind trees that rewards exploring."""),
    3: dict(title='Town park with a pond', size='24x20', tilesets='gTileset_General_Frlg,gTileset_CeladonCity',
            fill='0x010', type='town', refs='CeladonCity_Frlg, PalletTown_Frlg, FuchsiaCity_Frlg',
            text="""A park inside a town. A pond with natural edges (not a rectangle), paths connecting an
exit in the middle of each of the 4 edges, flower beds and trees in groups (not single tiles scattered),
open lawn where people would sit. Feels planned, not random."""),
    4: dict(title='Small Sevii island', size='28x24', tilesets='gTileset_General_Frlg,gTileset_SeviiIslands123',
            fill='0x12B', type='route', refs='OneIsland_KindleRoad_Frlg, ThreeIsland_BondBridge_Frlg, FourIsland_Frlg',
            text="""A small island in open sea. A beach rim, a grassy interior, rocks or a low cliff on one side,
one grove of trees, and a sheltered sandy cove. At least 3 tiles of sea on every map edge. The island must
be one walkable landmass (surfing around it is fine)."""),
    5: dict(title='Rocky hill path', size='30x20', tilesets='gTileset_General_Frlg,gTileset_PewterCity',
            fill='0x010', type='route', refs='Route3_Frlg, Route4_Frlg, PewterCity_Frlg',
            text="""An east-west path past rocky hills. Walkable exits on the left and right edges in rows
y=8..11, connected. Rock/cliff areas and ledges that shape the walk, some tall grass, a few trees. Keep
elevation simple: no stairs or raised plateaus you can walk onto."""),
}

TEMPLATE = """# Benchmark v3 run: {tag}, brief {n} ({title})

You are "{tag}" in a level-design study (5 briefs x 6 models). Your map is **{map}**.
This file is your whole task; do it alone, start to finish. Work from the game repo root:
python tools/pk/pk.py <command>.

**Follow `tools/pk/agents/mapper-playbook.md` (phases 0-3; phase 4 only if your budget allows).**
Read it and `tools/pk/AGENT.md` first.

RULES (breaking one disqualifies the run):
- Change maps ONLY through pk commands; never write .bin/.json/.inc files yourself.
- Only touch {map} (and, for phase 4, your own copies named {map_stem}A/B/C_Frlg, deleted at the end
  with pk map delete). Never edit vanilla maps or other Pk* maps.
- Never run pk build, pk bp apply or pk map polish. ONE pk write at a time.
- ORIGINALITY: no pasted piece may be larger than 10x8, and at most 30% of your map's cells may come from
  any single vanilla map. This is a test of composing NEW terrain from vanilla parts, not of copying a
  vanilla route. Copying a whole route body disqualifies the run.
- Max 6 renders that you look at (normal, --annotate or --roles views of the map don't count; PNGs do).

## Start
python tools/pk/pk.py map new {map} --size {size} --tilesets {tilesets} --fill {fill} --type {type}

## Brief
{text}
Vanilla references to study first: {refs}.
No NPCs, signs or warps; terrain only.

## Report: tools/pk/bench/reports_v3/{tag}_{n}.md (the only non-map file you may write)
Use exactly these headings, so 15 reports can be compared:
1. **Plan:** your 5-line plan from phase 0.
2. **Pieces:** source map + box for each vanilla piece you pasted.
3. **Tool use:** counts of kit find/fit, copy --repair, terrain auto (and --options), undo.
4. **Warnings hit:** every WARN reach / WARN repair line you got and what you did.
5. **Critic rounds:** the defect lists from your fresh critic and what you fixed (or "no subagent available").
6. **Final lint:** the summary line of pk map lint {map}.
7. **Hardest part:** 2-3 sentences, concrete (which tiles, which coordinates).
8. **Tool wishes:** what command or information would have helped most.
Do NOT rate your own map. End with: python tools/pk/pk.py map render {map} --scale 2
"""


def main():
    (HERE.parent/'reports_v3').mkdir(exist_ok=True)
    for tag in MODELS:
        for n, brief in BRIEFS.items():
            stem = f'PkV3{tag}{n}'
            text = TEMPLATE.format(tag=tag, n=n, map=f'{stem}_Frlg', map_stem=stem, **brief)
            (HERE/f'{tag}_{n}.md').write_text(text, encoding='utf-8')
    print(f'wrote {len(MODELS) * len(BRIEFS)} run files to {HERE}')


if __name__ == '__main__':
    main()
