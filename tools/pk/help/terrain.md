Terrain = paint MATERIALS, pk picks metatiles. Material of a cell: first tag of its metatile name,
else its class: ground solid water grass sand ledge_s ledge_n ledge_e ledge_w door counter ice waterfall.
The solver uses how often two metatiles touch in vanilla maps of the same tileset pair. It works
outside-in, then refines. Changed cells plus a 1-cell margin are re-tiled; margin cells keep their material.
pk terrain learn MAP (materials and their metatile counts for the map's tileset pair)
pk terrain show MAP [--rect] (material view)
pk terrain brush MAP rect x,y,WxH MATERIAL | circle x,y,r MATERIAL | path x,y x,y ... [width=n] MATERIAL
pk terrain river MAP x,y x,y ... [width=n] (water path)
pk terrain ledge MAP x1,y1 x2,y2 [--dir s|n|e|w]
pk terrain forest MAP rect x,y,WxH --stamp tree (tile a stamp, e.g. a 2x2 tree from pk stamp extract)
pk terrain bake MAP [--rect] (re-pick metatiles in place from current materials; smooths seams)
WARN "seams never seen in vanilla" lists x,y + h/v pairs to inspect with pk map show --exact / render.
Finer materials (path vs grass, tree vs cliff): pk meta name MAP METATILE name --tags path (first tag = material).
Measured on vanilla: 81% of erased natural terrain is re-derived exactly, 1.7% unseen seams.
pk terrain auto MAP x,y,WxH [--diag 0.5] [--options N] [--pick n] (no material: the box is filled from its surroundings).
 Every cell may become any metatile vanilla used >= 3 times in the pair (never doors/warps; Pk* drafts are
 not reference data). The solver keeps pairs that occur in vanilla and avoids unseen ones, so
 water | box | grass becomes a shoreline where vanilla has one. Prints seams before->after around the box;
 seams that remain have no vanilla tile for that edge (e.g. tall grass touching water).
 --options N computes up to N distinct fills without modifying the map, renders each (box + 2-tile margin,
 scale 3) to .pk/render/auto_<MAP>_<n>.png, and prints the --pick n command; --pick n applies option n.
