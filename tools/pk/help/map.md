pk map ls [filter] [--all] | pk map info MAP
 Rows are "name WxH primary/secondary MAP_TYPE layout_dir" (20 per page; --all lists every match).
pk map show MAP [--rect x,y,WxH] [--layer class|exact|coll|elev|all] [--exact] [--no-events] [--stamps]
Class view symbols: . ground # solid " grass ~ water : sand v ^ < > ledges D door | waterfall = counter * ice
Named metatiles with --sym override their class symbol. Maps wider than 64 are shown in bands.
Events overlay: W warp o npc i item s sign h hidden t trigger. Rows "yy|cells", rulers "x|tens" "x|units".
--exact: one symbol per metatile plus legend "a=0x2A3(name)"; use --rect on busy maps.
--stamps: each recognised stamp instance (data/stamps, mined or hand-made) becomes one symbol,
 with a "stamps X=namexN" legend line; class cells not in a stamp are unchanged. Opt-in.
--layer coll/elev alone: full grids, 0-3/0-F per cell. --layer all: class grid plus dominant values per class.
Example: "layer coll by class: #=1 .=0; exceptions N", followed by "yy| x=v ..." (absolute coordinates).
Exceptions override defaults, including every event-overlay cell; --no-events needs only differing values.
More than 10% exceptions: full grid for that layer. Ties use the lower value; elev is hexadecimal.
Parse symbol=value from the right (counter symbol: ==0); split the header at "; exceptions ".
pk map lint MAP (read-only; thresholds in tools/pk/data/lint_map.json; 0 is clean)
 map lint is experimental and not yet calibrated on all vanilla maps.
 pk map lint --snapshot: the only writer of tools/pk/data/vanilla_adjacency.json; it freezes the
  vanilla h/v/diagonal pair counts, the 3x3 metatile-window counts and known metatiles per tileset
  pair and re-measures the per-stratum thresholds. SEAM/RARE/patch read that snapshot, never live
  files, and skip with INFO when it is missing.
 RARE: a shore (water/sand) boundary whose metatile pair occurs <= rare_rate * layouts times in the snapshot.
 WARN SEAM h|v 0x123-0x2A3 @x,y: adjacent metatiles never seen in the vanilla snapshot of this pair.
  WARN SEAM for a custom metatile prints INFO UNCHECKED 0xNNN xK (no socket) unless it declares a
  socket (socket=0xNNN in the .pkt, or auto-inferred at build); SEAM/RARE then test the socket.
 WARN STAIR pair span run:n: a straight natural edge longer than straight_max (towns/cities only).
 WARN STAIR pair span steps:n: stair_min_steps equal runs joining with a constant one-tile shift.
 WARN STAIR pair span dash:n: equal natural runs gapped on one line (towns/cities only; catches broken shore bands).
 WARN RECT material x,y-WxH fill%: a natural component (>=rect_min x rect_min) filling rect_fill of its bbox; tendrils are pruned and a border-pinned one is skipped only if >=rect_min cells sit on the border.
 WARN EMPTY x,y-WxH: a 15x10 interior window with <2 decor/event cells and material entropy below empty_entropy (towns/cities only).
 Summary: lint MAP stratum:S seams:N stairs:N rare:N rects:N empty:N score:S (vanilla:B, +D)
  patch:P. A check fires (and counts +1) only above the p95 of its stratum, or the max when the
  stratum has <20 maps. patch is the share of the map's 3x3 windows seen in vanilla (its own windows
  removed for a vanilla map); info only, it never changes score or severity.
  Each check prints at most max_lines WARN rows; when cut it prints "INFO SEAM +N more (use --all)".
 pk map lint MAP --all prints every WARN row of every fired check (the aiceus backend always uses it).
pk map seam MAP x,y,WxH [--ring 2] (read-only re-solve of the band around the box against the seen)
 vanilla adjacencies; the box interior and everything beyond the ring stay frozen. --repair on copy/
 stamp place runs it on the pasted box. Fixes SEAM/RARE with few-cell edits.
pk map polish MAP --into NEW_MAP [--max-steps 200] [--dry-run] (copy MAP, then improve it with the
 critic, zero tokens). Physical classes come from game data only (walk, surf, ledge_*, wall, grass_enc,
 door, plus elevation; names/tags never decide anything). A changed cell may only take a tile that
 vanilla used in the sketch's 3x3 physical-class pattern at that cell, must keep the original cell's
 physical class and elevation (physical class lock), and must score at or below the vanilla-p95 odd-tile
 RGB distance threshold. Only cells within 2 of a WARN, not under/next to an event and not a wall/door/
 stamp, may change. A move is kept only if severity falls, `patch` does not fall, and reachable cell sets
 stay identical, within 10% of the map. Prints `INFO NOFIT x,y pattern` for cells with no fitting tile.
pk map render MAP [--rect x,y,WxH] [--scale n] [--out path.png] (default .pk/render/MAP.png)
 --viewport x,y: render the 15x10 screen at x,y (scale 3 default). --grid: draw the 15x10 screen grid.
 --compare B: A and B side by side at the same scale, 1-tile gap (before/after review).
pk map at MAP x,y -> metatile, name, collision, elevation, behavior, layer, tileset
pk map paint MAP x,y[,WxH] METATILE [--coll n] [--elev n]
pk map fill MAP x,y METATILE (flood fill of the same metatile, max 2000 cells)
pk map line MAP x1,y1 x2,y2 METATILE
pk map grid MAP x,y "def X 0x2A3;XX?X;X?X" (rows by ; or stdin; ? or space keeps; symbols = defs or exact legend of the rect)
pk map copy MAP x,y,WxH tx,ty [--from OTHER_MAP] (same pair, or shared primary with every copied id below its metatile limit)
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
pk map delete NAME (Pk* drafts only; one journaled transaction, pk undo restores it): unregisters the map
 (map_groups.json, layouts.json if unshared, event_scripts.s include) and deletes its files. Refuses vanilla
 maps and maps another map still references. Copies (polish/paintover --into) rename the source's own labels.
pk map export MAP [--out file] (default .pk/map/MAP.map): the whole map as text.
 pk map import MAP file [--dry-run]: parse it back and write map.bin + border.bin in one transaction.
 Format: "pkmap 1", layout id, tileset pair, size; "grid" then "legend SYM 0xID" lines and "yy| SYM SYM ..."
 rows; "coll"/"elev" defaults per metatile symbol plus "coll! x,y=v"/"elev! x,y=v" exceptions; "border WxH"
 with "0xID,coll,elev" cells. Export then import is byte-identical on every vanilla FRLG outdoor layout.

 --annotate: absolute x/y rulers every 5 tiles; all lint WARN boxes and @x,y labels.
 SEAM red, RARE orange, STAIR purple, RECT teal, EMPTY blue. Cropped-out boxes print INFO.

 INFO SHAPE x,y pattern: physical 3x3 class pattern absent from the frozen v4 vocabulary.
 Vanilla keeps its vocabulary (no leave-one-out), so untouched regions remain quiet. Pk drafts never contribute reference counts.
 SHAPE is informational and changes neither score nor severity; unavailable vocabulary prints INFO.
 INFO REPEAT x,y x,y x,y 4x4: non-overlapping 4x4 metatile block appearing >=3 times with >=2 physical classes (info only).
