pk: token-efficient interface to this FireRed hack (milestones 1-5b). Details: pk help <noun>.
Run from repo root in ANY shell: python tools/pk/pk.py <command> (also: cmd "pk", PowerShell ".\pk").
Never open .bin, map.json, scripts.inc or graphics.h directly; use pk.
Content policy: see blueprints/VISION.md (source of truth). Roles: tools/pk/agents/.
Do not apply any blueprint to real maps without the owner's explicit approval.
Output: first line OK or ERR CODE; MORE n = next --offset. --dry-run writes nothing. --json = one object.
Every write is one journaled transaction: pk log | pk undo [n] (undo again = redo). pk help errors.
MAP = exact directory (PalletTown_Frlg), MAP_* id or unique fuzzy name. Ambiguity lists candidates.
METATILE = 0x2A3 | 675 | name given with pk meta name.
pk find "query" [--kind map|text|script|flag|var|trainer|gfx|behavior|layout|tileset|music] | pk refs EXACT_LABEL_OR_CONSTANT
pk map ls [filter] | pk map info MAP
pk map show MAP [--rect x,y,WxH] [--exact] [--layer coll|elev|all] [--stamps] [--roles] (help map: symbols)
pk map lint MAP (ugliness score: seams stairs rare rects empty; 0 is clean)
pk map lint --snapshot (freeze vanilla adjacency + re-measure strata; only writer of the snapshot)
pk map render MAP [--rect] [--scale 1] [--out file.png] (real graphics, for vision)
pk map at MAP x,y | pk map paint MAP x,y[,WxH] METATILE | fill | line | grid | copy | border
pk map paintover MAP IMAGE [--into NEW_MAP] (real metatiles back from a painted image; matching cells frozen)
pk map resize MAP --add right=8,bottom=4 [--fill extend|border|terrain|METATILE]
(resize moves events, script coordinates, connections and heal spots; grow right/bottom when possible)
pk map export MAP [--out file] | import MAP file (lossless) | blockout MAP --export|FILE [--check] (experimental; help blockout)
pk map extend-to MAP NEIGHBOUR | pk map new NAME --size WxH --tilesets P,S --fill METATILE
pk terrain brush MAP rect|circle|path ... MATERIAL (water, grass, sand, ground, solid, ledge_s, tags)
pk terrain river MAP x,y x,y [width=2] | ledge | forest --stamp tree | bake | learn | show | auto MAP x,y,WxH [--options N|--pick N]
pk stamp extract MAP x,y,WxH name | from-tileset SET.pkt OBJ.px name | mine PAIR [--min-count 3] [--apply] | ls
pk stamp place MAP name x,y
pk kit index | find [--feature F --top C --bottom C --left C --right C --size WxH --pair P --sheet] | fit MAP x,y,WxH (help kit); pk layout learn|score MAP FILE|calibrate (help layout)
pk shape find --kind pond|cove|sandbar|beach|island|grove|tallgrass [--size WxH --sheet] | place MAP KIND_OR_ID x,y (help shape)
pk bp check|plan|render|apply MAP | pk bp world | pk bp export MAP (help bp: blueprint format)
pk tileset build art/X/tileset.pkt | pk tileset export MAP name | pk px show|check|render FILE.px
pk sprite show|export GFX | pk sprite build FILE.px name --like GFX (NPC overworld sprites)
pk meta ls|show|name|autoname|carts MAP [--sym X --tags t --role R --class C --sheet] (help meta)
pk ev ls MAP | pk ev add MAP obj @x,y gfx=LASS move=WANDER text="..." | ev set | ev rm
pk text get LABEL | pk text set LABEL "plain text" | text add | ls | grep | replace | check
Plain text is wrapped to 208px, 2 lines per box: || = new box, | = new line. {PLAYER} etc. allowed.
pk check [MAP|all] [--warnings]: only NEW errors fail (vanilla issues are baselined).
pk build [--release] (staged, journaled, ~15 min) | pk batch < ops.txt (atomic, one command per line)
pk index (auto) | Tests: C:\Python314\python.exe -B -m unittest discover tools/pk/tests
Sidecars: tools/pk/data (names, stamps, limits, baseline) and .pk (index, journal, renders).
Workflow: find -> show/get -> small edit -> check -> (render) -> build. Prefer --dry-run first.
