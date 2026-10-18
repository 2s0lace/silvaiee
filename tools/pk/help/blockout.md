Experimental blockout tiling; not yet accepted. See tasks/gpt_layout_blockout.md.
pk map blockout MAP --export [--out .pk/NAME.blockout]
pk map blockout MAP FILE --check (read-only, no cache or render writes)
pk map blockout PkBlockNAME_Frlg FILE [--dry-run]
Format: blockout 1, then exactly this legend, then exactly the map's rows:
legend T=trees .=lawn p=path g=tallgrass ~=water s=sand r=rock v=ledge_s ^=ledge_n <=ledge_w >=ledge_e f=flowers b=building
v south, ^ north, < west, > east match pk map show's physical ledge directions.
Old l is invalid, not an alias. Unknown letters, changed legend and wrong sizes
are errors. Existing b cells only; buildings are never invented by the tiler.
Any future unrepresented physical/card class may receive one documented symbol.
Names/tags never define a class. Context-only _ means outside an unconnected
side; * is a wildcard beyond a connected side, never a file letter. A diagonal
crossing a connected side is also unknown. Both learned and target contexts use
objects.connected_sides. 3x3 is row-major; four-neighbour keys are centre,N,E,S,W.
Export uses physical classes, tile cards and the existing provisional rendered-
palette heuristic for visual categories. That classifier is unchanged and still
needs visual review; semantic letter fidelity is not proof of good graphics.

Candidate learning: only exported vanilla outdoor blockouts. Exclude a benchmark
map's entire layout, including aliases, before learning context/pair statistics.
Same-pair tiles are allowed; other pairs contribute only valid primary IDs with
the same primary/version, and only when their exported target/source letters agree.
For each cell, candidates come from equal 3x3 LETTER patterns. If empty, try equal
four-neighbour LETTER patterns, then the same centre letter, then any observed
portable non-door tile (physical_class, not names). Every transition prints its
cell coordinates, old/new level, candidate count and reason. Counts are also
reported globally and per letter. An empty cell never aborts all other cells.
Collision/elevation come from observed tuples at the selected context level.
If support empties a cell, widen that cell ONE step and retry support from the
unpruned current-level domains, allowing neighbours to recover. The ladder is
3x3 -> 4-neighbour -> letter -> any-non-door. Support is not the final guard.
If even broad non-door support is exhausted, report that coordinate and attempt
the rest; final OBJECT/reach/fidelity guards still reject any unsafe proposal.
No pruning is done across a side where Glue.neighbour returns None. Unconnected
sides use the exact border through Glue.broken_side, identical to lint's rules.

Only b cells are initially frozen. ALL other cells, including lawn and cells under
events, get context-restricted candidates. Event metadata is not modified.
Solver order: forest WFC first, including its mutable non-building edge ring;
then the rest (including lawn/joins) via context-restricted terrain.solve.
WFC incomplete/invalid fills print INFO and use the context-restricted seen-pair
fallback. wfc.py and paths.py/shapes.py are not edited by this tool.
After all phases, the unchanged OBJECT/reach/bank/fidelity guards decide whether
any map data may be staged. Rejected proposals can be rendered for diagnosis but
are never written. >=95% letter fidelity, zero new OBJECT, reach not worse.
Unrestricted terrain auto is skipped explicitly because joins already participate
in the context solve. Complete bank synthesis is still not independently accepted.
Unchanged imports retain exact tuples. Checks report exit/door connectivity,
unseen vanilla letter patterns, raw open/mirror; Part 3 measures print skipped INFO.
Parts 2-3 (library/calibrated measures/v3 table) remain unimplemented.

Tests: python tools/pk/tests/test_blockout.py
Acceptance: python tools/pk/tests/test_blockout.py --benchmark
Resume rebuilds: python tools/pk/tests/test_blockout.py --benchmark --rebuilds-only
Benchmarks edit grids only in memory; comparison PNGs and full coordinate audit
logs (.pk/render/blockout_MAP.txt) are journaled, never map data.
Previous accepted round trips: 10/10, 13812/13812, exact tuples unchanged; see DECISIONS.md.
Latest acceptance: python tools/pk/tests/test_blockout.py --benchmark --rebuilds-only
Route1 leave-one-out: 958/960 = 99.79% letters PASS; original reach_lost:0 PASS;
new OBJECT:23 FAIL (target 0). The write was rejected. STOP, targets unchanged.
Connected N/S no longer block 9,0. Initial contexts: exact 856, four 75, letter 29.
115 cells widened in 160 reported steps; final exact/four/letter/any:845/73/39/3.
Broad non-door cells: 9,5; 10,36; 8,4. WFC left 370 cells unresolved; pair fallback
solved its 498-cell forest/rim, then 660 rest cells. Only two T letters mismatch,
but repeated tree-half rows still trigger the unchanged final object guard.
PNG: .pk/render/blockout_Route1_Frlg.png (original left, rejected proposal right).
Full coordinate audit: .pk/render/blockout_Route1_Frlg.txt. Its header/benchmark
reach result compares against original vanilla; generic WARN reach rows compare
against the wiped lawn instead. No game-map data changed. Remaining rebuilds,
coastal map, Parts 2-3 and v3 table were explicitly skipped after the object miss.
Unit command above: 30 pass in the game workspace. Public-copy unit command:
python release/silvaiee/tools/pk/tests/test_blockout.py -> 30 run, OK (skipped=2).
API evidence: lint.py physical_class:32/vanilla_outdoor:815; wfc.py learn:113,
fill:544/Fill:145; terrain.py solve:83/Rules.pair_score:79; objects.py Glue:121,
broken_side:151/neighbour:141/connected_sides:63/constrained:110/SIDES:22;
edit.py load:14/save:88; meta.py draw:87,
render_compare:297; cards.py aggregate:106/finalize:144; core/repo.py maps:109,
layouts:106. No invented solver attributes or material names are used.
The public checkout lacks the other agent's optional wfc.py. Missing WFC prints
INFO and falls back; WFC-specific tests explicitly print INFO and skip there.
