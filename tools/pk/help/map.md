pk map ls [filter] | pk map info MAP
pk map show MAP [--rect x,y,WxH] [--layer class|exact|coll|elev|all] [--exact] [--no-events]
Class view symbols: . ground # solid " grass ~ water : sand v ^ < > ledges D door | waterfall = counter * ice
Named metatiles with --sym override their class symbol. Maps wider than 64 are shown in bands.
Events overlay: W warp o npc i item s sign h hidden t trigger. Rows "yy|cells", rulers "x|tens" "x|units".
--exact: one symbol per metatile plus legend "a=0x2A3(name)"; use --rect on busy maps.
--layer coll/elev alone: full grids, 0-3/0-F per cell. --layer all: class grid plus dominant values per class.
Example: "layer coll by class: #=1 .=0; exceptions N", followed by "yy| x=v ..." (absolute coordinates).
Exceptions override defaults, including every event-overlay cell; --no-events needs only differing values.
More than 10% exceptions: full grid for that layer. Ties use the lower value; elev is hexadecimal.
Parse symbol=value from the right (counter symbol: ==0); split the header at "; exceptions ".
pk map lint MAP (read-only; thresholds in tools/pk/data/lint_map.json)
 BROKEN: thresholds are far too loose; it scores 0 on visibly ugly maps. Judge renders instead.
 WARN SEAM h|v 0x123-0x2A3 @x,y: adjacent metatiles never seen in vanilla layouts of this tileset pair (terrain.Rules).
 WARN STAIR pair span run:n: a straight natural edge longer than straight_max (towns/cities only).
 WARN STAIR pair span steps:n: stair_min_steps equal runs joining with a constant one-tile shift.
 WARN STAIR pair span dash:n: equal natural runs gapped on one line (towns/cities only; catches broken shore bands).
 WARN RECT material x,y-WxH fill%: a natural component (>=rect_min x rect_min) filling rect_fill of its bbox; border-pinned ones are skipped.
 WARN EMPTY x,y-WxH: a 15x10 clean-ground interior window with no decor tags, no events (towns/cities only).
 Summary: lint MAP seams:N stairs:N rects:N empty:N score:N. Materials = class view or first tag of a named metatile.
pk map render MAP [--rect x,y,WxH] [--scale n] [--out path.png] (default .pk/render/MAP.png)
pk map at MAP x,y -> metatile, name, collision, elevation, behavior, layer, tileset
pk map paint MAP x,y[,WxH] METATILE [--coll n] [--elev n]
pk map fill MAP x,y METATILE (flood fill of the same metatile, max 2000 cells)
pk map line MAP x1,y1 x2,y2 METATILE
pk map grid MAP x,y "def X 0x2A3;XX?X;X?X" (rows by ; or stdin; ? or space keeps; symbols = defs or exact legend of the rect)
pk map copy MAP x,y,WxH tx,ty [--from OTHER_MAP] (same tileset pair)
pk map border MAP show | pk map border MAP METATILE [METATILE x3]
Without --coll/--elev a painted cell gets the metatile's dominant vanilla collision/elevation (>=80%), else keeps its own.
pk map paintover MAP IMAGE [--into NEW_MAP] ([--dry-run], writes map.bin + nothing else)
 The image must be the map's pixels at scale 1 or 2 (W*16 x H*16; anything else is BOX-resized with a WARN).
 Every metatile of the tileset pair is rendered once to 16x16 RGB; per cell the 12 best colour matches
 become the candidate set for the terrain solver (unary -0.05*mean |RGB| distance + learned vanilla
 adjacency rules.h/rules.v + small frequency prior; no hard-coded materials).
 Cells within mean distance 6/255 of their current metatile are frozen: untouched vanilla areas survive
 byte-exactly. --into first creates NEW_MAP as a copy of MAP (events kept, connections cleared).
 Output: cells/frozen/changed, mean distance, worst 5 cells (x,y dist), unseen-seam count.
pk map resize MAP --add top=n,left=n,right=n,bottom=n (negative crops) | --to WxH [--anchor tl|t|tr|l|c|r|bl|b|br]
 --fill extend (default, repeats edge) | border (border.bin pattern) | terrain (extend + solver) | METATILE
 --fork-layout when the layout is shared; --drop-out-of-bounds when cropping NPCs away (warps never).
 Fixes in the same transaction: events, setmetatile/setobjectxy(perm) in the map's scripts,
 warp*/set*warp coordinates in every script that targets the map, connection offsets on both sides,
 src/data/heal_locations.json. getplayerxy comparisons are reported as WARN manual.
 Limit: (w+15)*(h+14) <= 10240. Growing right/bottom changes no coordinates at all.
pk map extend-to MAP NEIGHBOUR (grow along the shared edge until the connection covers the neighbour)
pk map new NAME --size WxH --tilesets PRIMARY,SECONDARY --fill METATILE [--type town|city|route|indoor|cave]
 [--like MAP (copy header)] [--music MUS_X] [--group gMapGroup_X] (default group gMapGroup_PkNew, appended last)
