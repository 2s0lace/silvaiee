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
If broad non-door support is exhausted, widen the blocking neighbour's CONTEXT
one step or report a hard contradiction. Never exempt a cell from object rules.
No pruning is done across a side where Glue.neighbour returns None. Unconnected
sides use the exact border through Glue.broken_side, identical to lint's rules.

Only b cells are initially frozen. ALL other cells, including lawn and cells under
events, get context-restricted candidates. Event metadata is not modified.
First establish a globally coherent hard-object seed. Then solve each connected
T region in one atomic pass with its mutable non-building rim, never in sections.
Each connected ledge row is also one atomic pass (horizontal for v/^, vertical
for </>). Finish the rest/lawn without overwriting forest or ledge phases.
WFC and pair proposals must pass exact Glue.broken_side constraints, with arc
consistency and bounded backtracking; soft scores cannot override object sockets.
Incomplete WFC prints INFO and falls back. Its learned vocabulary is logically
conditioned on current domains/frozen neighbours, never invented or learned from
the held-out map. wfc.py and paths.py/shapes.py are not edited by this tool.
For remaining broken sides, re-solve a 3x3 window, then grow to 5x5. Letters and
b cells stay fixed, object rules remain hard, and the 5x5 trial may widen context
to observed same-letter tiles. A repair is accepted only if it strictly reduces
warning identities and introduces none. Report window, fixed count, search steps,
and any unresolved side; otherwise keep the previous coherent assignment.
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
Route1 leave-one-out PASS: 960/960 = 100.00% letters; new OBJECT:0;
original reach_lost:0; open_bank:0. Initial exact/four/letter:856/75/29.
116 cells widened in 161 reported steps; final exact/four/letter/any:844/75/37/4.
Hard coherent seed solved all 960 cells. Four connected forests were atomic:
one WFC proposal, three seen-pair fallbacks, all hard-validated. Ten ledge rows
solved atomically. Final local repair skipped explicitly: no broken sides.
The render still has fence-like right-wall substitutions classified as T;
letter/object acceptance is not a claim of exact graphical reconstruction.
Route22 leave-one-out FAIL: 210/1152 = 18.23% letters; new OBJECT:13;
original reach_lost:0; open_bank:3. A hard contradiction remains at 4,0 beside
a frozen building. The seed/rest were refused; 3x3/5x5 repairs fixed 0,
leaving 13 broken sides. Object rules were not relaxed. STOP, targets unchanged.
Comparison PNGs and full coordinate audits:
.pk/render/blockout_Route1_Frlg.png and .txt;
.pk/render/blockout_Route22_Frlg.png and .txt.
Original is left, proposal right. Audit headers/benchmark reach compare with
original vanilla; generic WARN reach compares with the wiped lawn instead.
No game-map data changed. Remaining three rebuilds, coastal map and Parts 2-3
were explicitly skipped at the first miss. Previous 10-map round trips were
explicitly skipped on this resumed run; their earlier 100% pass remains recorded.
Unit command above: 37 tests pass in the game workspace. Public-copy command:
python release/silvaiee/tools/pk/tests/test_blockout.py (result in DECISIONS.md).
API evidence: lint.py physical_class:32/vanilla_outdoor:815; wfc.py learn:113,
fill:544/Fill:145; terrain.py solve:83/Rules.pair_score:79; objects.py Glue:121,
broken_side:151/neighbour:141/connected_sides:63/constrained:110/SIDES:22;
edit.py load:14/save:88; meta.py draw:87,
render_compare:297; cards.py aggregate:106/finalize:144; core/repo.py maps:109,
layouts:106. Hard-object validation reuses Glue.objectish:134/broken_side:151.
WFC Vocabulary at wfc.py:87 defines counts/key/pos_index used for conditioning.
No invented solver attributes or material names are used.
The public checkout lacks the other agent's optional wfc.py. Missing WFC prints
INFO and falls back; WFC-specific tests explicitly print INFO and skip there.
