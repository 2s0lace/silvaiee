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
Names/tags never define a class. _ is an out-of-map CONTEXT sentinel only, not
a file letter. 3x3 patterns are row-major; four-neighbour keys are centre,N,E,S,W.
Export uses physical classes, tile cards and the existing provisional rendered-
palette heuristic for visual categories. That classifier is unchanged and still
needs visual review; semantic letter fidelity is not proof of good graphics.

Candidate learning: only exported vanilla outdoor blockouts. Exclude a benchmark
map's entire layout, including aliases, before learning context/pair statistics.
Same-pair tiles are allowed; other pairs contribute only valid primary IDs with
the same primary/version, and only when their exported target/source letters agree.
For each cell, candidates come from equal 3x3 LETTER patterns. If empty, try equal
four-neighbour LETTER patterns, then the same centre letter. Every fallback is
counted globally and per letter. Empty domains print every full pattern and its
cell count, and refuse tiling; there is no silent generic-grass substitute.
Collision/elevation come from observed tuples at the selected context level.
Object socket support can shrink a domain but never expand it or secretly fall
back to a less-specific context. Unsupported patterns print their cell counts
and refuse the proposal. Glue enforces the actual map border as before.

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
Benchmarks edit grids only in memory; only comparison PNGs are journaled to disk.
Previous accepted round trips: 10/10, 13812/13812, exact tuples unchanged; see DECISIONS.md.
Latest context acceptance: python tools/pk/tests/test_blockout.py --benchmark --rebuilds-only
Route1 leave-one-out FAIL/refused BEFORE solving. All 960 cells had context
candidates: 785 exact 3x3, 125 four-neighbour, 50 letter-only, 0 initially empty.
Object support pruning emptied one domain: pattern ___/TTp/TTp at 9,0.
No context was widened silently: forest and rest were explicitly skipped.
The retained wipe's 223/960 = 23.23% is NOT a reconstruction result; zero new
OBJECT and zero original-map reach loss reflect refusal, not a passing rebuild.
The comparison PNG .pk/render/blockout_Route1_Frlg.png shows original vs retained
wipe after refusal. Remaining rebuilds, coastal map, Parts 2-3 and v3 table skipped.
Unit command above: 23 pass in the game workspace. Public-copy unit command:
python release/silvaiee/tools/pk/tests/test_blockout.py -> 23 run, OK (skipped=2).
API evidence: lint.py physical_class:32/vanilla_outdoor:815; wfc.py learn:113,
fill:544/Fill:145; terrain.py solve:83/Rules.pair_score:79; objects.py Glue:121,
broken_side:151/constrained:110/SIDES:22; edit.py load:14/save:88; meta.py draw:87,
render_compare:297; cards.py aggregate:106/finalize:144; core/repo.py maps:109,
layouts:106. No invented solver attributes or material names are used.
The public checkout lacks the other agent's optional wfc.py. Missing WFC prints
INFO and falls back; WFC-specific tests explicitly print INFO and skip there.
