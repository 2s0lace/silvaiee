Kit: real vanilla outdoor windows, indexed by what they contain, to copy onto a map with --repair.
pk kit index: scans every vanilla outdoor map into .pk/kit/index.json (git-ignored). It rebuilds only when the
 file is missing, so rerun it after map edits. It writes its cache even with --dry-run (the one exception).
pk kit find [--feature F] [--top E] [--bottom E] [--left E] [--right E] [--size WxH] [--pair P] [--limit 10] [--sheet]
 F: shore pond ledge tallgrass cliff. E: walk surf wall grass_enc door mixed ledge_n|e|s|w.
 A side counts as a class only when >= 70% of its cells are that class; otherwise it is mixed.
 --size: exact size first, then within 2 cells per dimension. --pair: A+B, A,B or a map name (also includes portable pieces sharing the primary).
 Each hit prints `MAP x,y,WxH pair features curvy:N portable:true|false`, then `pk map copy TARGET x,y,WxH TX,TY --from MAP --repair`.
 --sheet writes .pk/kit/sheet.png: the top hits in rows of 5, each labelled with its rank.
pk kit fit MAP x,y,WxH [--limit 10] [--sheet]
 Reads the class just outside each side of the box, then finds pieces with those edges, the same size and the
 target's tileset pair or portable pieces sharing its primary. Prints the same lines with TARGET and TX,TY filled in. Only --limit and --sheet apply.
Features: shore (surf next to walk), pond (surf that does not touch the window edge), ledge, tallgrass
 (grass_enc >= 20%), cliff (wall run of >= 4 next to walk). curvy: direction changes along the shore line.
Skipped windows: any event cell (no NPCs, signs or warps are copied), any door cell, any window with one class > 85%.

Portable: every metatile id is below the primary metatile limit. Old indexes rebuild automatically.
