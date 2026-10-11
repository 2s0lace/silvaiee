Stamps are rectangles of cells (metatile + collision + elevation) for one tileset pair.
pk stamp extract MAP x,y,WxH snake_name [--on land|water|any] [--overwrite] (tools/pk/data/stamps/)
 --on: where blueprints may place it (default land; piers and bridges: water).
pk stamp from-tileset art/SET/tileset.pkt OBJECT.px name [--walk "x,y x,y"] [--on land|water|any] [--overwrite]
 An object drawn as one .px and listed cell by cell (OBJECT.px@x,y) in a BUILT .pkt becomes a stamp.
 Cells are solid (coll 1, elev 0, like vanilla houses and doors); --walk cells (object coords) are
 walkable (coll 0, elev 3); cells missing from the .pkt are holes (the map cell stays).
pk stamp place MAP name x,y | pk stamp ls [gTileset_X]
In blueprints: section "structures", line "<id> <stamp> @x,y" (pk help bp).
Typical: extract a 2x2 tree once, then pk terrain forest MAP rect ... --stamp tree.
