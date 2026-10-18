Experimental blockout tiling; not yet accepted. See tasks/gpt_layout_blockout.md.
pk map blockout MAP --export [--out .pk/NAME.blockout]
pk map blockout MAP FILE --check (read-only, no cache or render writes)
pk map blockout PkBlockNAME_Frlg FILE [--dry-run]
Format: blockout 1, then exactly this legend, then exactly the map's rows:
legend T=trees n=fence .=lawn p=path g=tallgrass ~=water s=sand r=rock v=ledge_s ^=ledge_n <=ledge_w >=ledge_e f=flowers b=building
v south, ^ north, < west, > east match pk map show's physical ledge directions.
Old l is invalid, not an alias. Unknown letters, changed legend and wrong sizes
are errors. Existing b cells only; buildings are never invented by the tiler.
Any future unrepresented physical/card class may receive one documented symbol.
Names/tags never define a class. Context-only _ means outside an unconnected
side; * is a wildcard beyond a connected side, never a file letter. A diagonal
crossing a connected side is also unknown. Both learned and target contexts use
objects.connected_sides. 3x3 is row-major; four-neighbour keys are centre,N,E,S,W.
Export uses physical classes, tile cards and the existing provisional rendered-
palette heuristic for visual categories. n separates wall-class fence/hedge lines
using tile-card physical neighbour counts: both transverse sides <40% wall and
at least one longitudinal continuation >60% wall, with >=ROLE_MIN observations.
Two-row/2x2 tree objects do not meet that line criterion. Names/tags are ignored.
The remaining classifier is still heuristic and needs visual review; semantic
letter fidelity is not proof of good graphics. Export the new legend again for
old blockouts; the old legend is not silently interpreted as the extended one.

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
T, r, or n region in one atomic pass with its mutable non-building rim, never in
sections. Other object regions and ledge rows are not overwritten by that rim.
For regions >=128 cells, erode two cells from the outline (including holes).
Pin that interior to the dominant tile in the LOO homogeneous 3x3 letter context
only if every interior domain permits its observed tuple and exact object rules
permit all self-neighbours. Otherwise report why no dominant interior was pinned.
Solve the remaining two-cell boundary/rim jointly with WFC/context candidates and
hard objects; validate the whole interior+band before acceptance. Failure rolls
back the whole region. Report symbol, size, interior/band sizes and solver steps.
Water families: every connected ~ region is one body (4-neighbour). Like the vanilla bodies it is learned
from, it is sea when it touches the map edge or has more than 40 cells, otherwise pond. Its family is the open
tile (tile card family open, class surf) that vanilla bodies of that kind are mostly made of, plus the other water
tiles that touch it more than they touch any other open tile (its banks), plus, for the cells around the region,
the tiles vanilla puts next to those. Candidates are cut to the family before the solve: a cell with no family
tile widens its context one step at a time (INFO widen ... reason:water region:N), never lets a second family in.
Hard rule: water touching a sand cell must be open water, so a pond rim never sits on a beach. Walkable shallow-
water tiles are sand (s), because vanilla draws every sandy shore with them. Report per region:
INFO BLOCKOUT water region:N size kind families-used outside-family tiles; WARN if families-used is not 1.
Small trees: a T region of 8 cells or fewer is filled with whole vanilla trees only: rectangles at least 2x2 of
wall-class tiles that vanilla draws alone in a lawn (learned, most frequent first), pinned, with the ring around
them solved under the hard object rules (a ring cell with no tile widens one step). If no whole tree fits the
cells become lawn and the tiler prints INFO BLOCKOUT tree group x,y left as lawn (no whole tree fits); fence posts,
stumps and rock never stand in for a tree. After the solve every T, r and n cell must export as its letter
(INFO BLOCKOUT object letters ... survived; WARN ... lost makes the proposal unsafe); left-as-lawn cells are the
one reported exception.
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
Latest acceptance: python tools/pk/tests/test_blockout.py --benchmark
Round trips PASS: all 10 maps, 13812/13812 = 100%, exact tuples unchanged,
revalidated with n=fence in the legend.
Route1 leave-one-out PASS: 960/960 = 100.00% letters; new OBJECT:0;
original reach_lost:0; open_bank:0. Four T components, two n components and one
r component were solved with hard objects. Right-wall trees are restored in the
comparison render. The n components have sizes 9 and 7; hard search 86 and 66 steps.
Route22 leave-one-out FAIL: 600/1152 = 52.08% letters; new OBJECT:13;
original reach_lost:0; open_bank:6. Its largest r component has 331 active cells:
44 depth-two interior cells choose 0x071 from LOO context, boundary/rim 348.
WFC uses 15 steps, but the band fails a hard boundary domain at 4,2; the entire
region is rolled back. The 46-cell rock component/rim 67 solves in 1340 hard
steps (33 WFC steps before pair fallback). Full region sizes/steps are in the audit.
The hard contradiction at 4,0 beside frozen b remains; seed is refused. Final
3x3/5x5 repairs fix 0 and leave 13 broken sides around the gate. STOP, no relaxation.
Comparison PNGs and full coordinate audits:
.pk/render/blockout_Route1_Frlg.png and .txt;
.pk/render/blockout_Route22_Frlg.png and .txt.
Original is left, proposal right. Audit headers/benchmark OBJECT and reach compare
with original vanilla; generic INFO OBJECT/WARN reach compare with the wiped lawn.
No game-map data changed. Remaining three rebuilds, coastal map and Parts 2-3
were explicitly skipped at the first miss.
python tools/pk/tests/test_blockout.py -> 43 tests PASS.
python release/silvaiee/tools/pk/tests/test_blockout.py -> 43 run, OK (skipped=2);
the optional WFC module is absent only in the public checkout, explicitly reported.
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
Optional elevation layer: after the letter rows, a line `elevation` and the same number of rows with one hex
digit per cell (the game's walking height). Export always writes it; the tiler applies it to every non-b cell.
Without it each tile gets its usual vanilla height (fine for flat maps; plateaus need it).
Large maps are solved in overlapping 24x24 windows (stride 16) when the whole-map solve hits a contradiction;
remnants get a soft fill that never breaks an object.
Edge orientation: FRLG edge, corner and mixed tiles (tile card family not open) are half grass / half sand, and
letters cannot say which side carries the rim. Such a tile may only stand where this very cell matched a vanilla 3x3 or
4-neighbour letter pattern; widening to letter / any-non-door offers open tiles only, plus the tiles an object beside the
cell requires (vanilla's own pairing). A cell that needs the wider levels is named: WARN BLOCKOUT reshape x,y (no vanilla
edge for this letter pattern), with [no open tile of its letter either] for letters that have none (ledges, fence ends,
flowers: the edge tile nearest to the cell's vanilla contexts is taken). Remnants no hard window could solve get open tiles
only, never edge tiles chosen one by one. A water/sand pair no vanilla shore fits is relaxed for that cell only
(INFO shore pair rule relaxed, WARN reshape ... no vanilla shore for this water/sand pattern); object rules never are.
Check: INFO BLOCKOUT edge orientation lists every placed non-open tile whose 4-neighbour letter context (centre,N,E,S,W)
vanilla (all outdoor layouts, the rebuilt map included) never puts it in: WARN BLOCKOUT edge context x,y 0xMMM ...
[no open tile of its letter] / [required beside an object] / [misplaced]. Only misplaced ones make the proposal unsafe.
Thin objects: n is every wall tile with one cell thickness on an axis (fence, hedge, post, bush; a green rock top follows
its rock neighbours); small T groups left are trees and fill with the exact vanilla shapes (tree, pair, L).
tile(..., baseline=GRID) measures new object warnings and reach against GRID (a rebuild passes the original vanilla map);
the benchmark counts a rebuild that tile() flags unsafe as a failure.
Water/tree acceptance: python tools/pk/tests/test_blockout.py (67 tests; WaterLearned and Coast build the learned rules,
about two minutes). Coast map: .pk/render/coast_v2.png.
