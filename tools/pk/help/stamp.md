Stamps are rectangles of cells (metatile + collision + elevation) for one tileset pair.
pk stamp extract MAP x,y,WxH snake_name [--on land|water|any] [--overwrite] (tools/pk/data/stamps/)
 --on: where blueprints may place it (default land; piers and bridges: water).
pk stamp from-tileset art/SET/tileset.pkt OBJECT.px name [--walk "x,y x,y"] [--on land|water|any] [--overwrite]
 An object drawn as one .px and listed cell by cell (OBJECT.px@x,y) in a BUILT .pkt becomes a stamp.
 Cells are solid (coll 1, elev 0, like vanilla houses and doors); --walk cells (object coords) are
 walkable (coll 0, elev 3); cells missing from the .pkt are holes (the map cell stays).
pk stamp mine PAIR [--min-size 2x2] [--min-count 3] [--cap 32] [--apply]
 Finds repeated all-solid rectangles (metatile+coll+elev) in the vanilla layouts of a tileset pair:
 trees, house walls, fences, signs. PAIR is a map or PRIMARY,SECONDARY. Without --apply it only
 lists proposals; --apply writes the greedy subset that covers the most structure cells, as
 tools/pk/data/stamps/mined_<tileset>_<kind>_N.json (never overwrites existing stamps).
pk stamp place MAP name x,y | pk stamp ls [gTileset_X]
pk map show MAP --stamps: replaces each recognised stamp instance with one symbol and a "stamps" legend.
In blueprints: section "structures", line "<id> <stamp> @x,y" (pk help bp).
Typical: extract a 2x2 tree once, then pk terrain forest MAP rect ... --stamp tree.
