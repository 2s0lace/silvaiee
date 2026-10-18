Terrain = paint MATERIALS, pk picks metatiles. Material of a cell: first tag of its metatile name,
else its class: ground solid water grass sand ledge_s ledge_n ledge_e ledge_w door counter ice waterfall.
The solver uses how often two metatiles touch in vanilla maps of the same tileset pair. It works
outside-in, then refines. Changed cells plus a 1-cell margin are re-tiled; margin cells keep their material.
pk terrain learn MAP (materials and their metatile counts for the map's tileset pair)
pk terrain show MAP [--rect] (material view)
pk terrain brush MAP rect x,y,WxH MATERIAL | circle x,y,r MATERIAL | path x,y x,y ... [width=n] MATERIAL
pk terrain path MAP x1,y1 x2,y2 [x3,y3 ...] [--width 2] [--material path|sand] [--options 3] [--pick n]
 Routes use deterministic A* and bend at least every 8 cells (the vanilla outdoor-road p95). Walls,
 water, doors, warps/events, and cells holding together trees/rocks are blocked. A blocked door waypoint
 gets a safe adjacent edge cap. Width is limited to the measured vanilla range (1..6; median 2).
 --options N writes no map data and renders N deterministic alternatives at scale 3; --pick N applies one.
 Painting uses the terrain solver plus its 1-tile auto-join ring. A write is refused if it adds a broken
 object side or reduces reachability; successful output includes the lint and reach summaries.
 Road families include grass-tagged edge/corner tiles sharing the vanilla road interior's colour.
 Join cleanup freezes objects/events and preserves collision/elevation. Previews mark unsafe fills
 REFUSED; writing checks each entrance in all six ability states. Options/pick accept 1..20.
 Routes penalize encounter grass and require >=2 straight steps between bends. Cleanup uses the target
 map's own lawn variants for a verge and vanilla road-facing corners; ledges/objects remain immutable.
 Experimental: wiped Route 1 has 18 path seams (limit 5) and only 2 options (target 3); see sol_path_report.md.
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
terrain auto and copy/stamp --repair never break a tree, rock or cliff: repair skips a swap that would add
 a WARN OBJECT, and auto writes nothing ("refused: would break N object sides") when its fill would;
 --options marks such options with BREAKS.
