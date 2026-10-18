Experimental Part 1 prototype; not accepted. See tasks/gpt_layout_blockout.md.
Owner-approved directional legend: v south, ^ north, < west, > east, matching
pk map show's ledge symbols. The old ambiguous l is invalid, not an alias.
Additional genuinely unrepresented physical/card classes may receive one symbol
per class, documented here and in the serialized legend; names never define them.
Previous stopped acceptance: python tools/pk/tests/test_blockout.py --benchmark.
Round trips PASS: all 10 maps, 13812/13812 letters, original tuples retained.
First wiped retile (Route1) FAIL: 712/960 = 74.17%, 20 new OBJECT rows,
no reach loss compared with original vanilla. Target remains >=95%, 0 new OBJECT.
STOP: remaining retiles, coastal map and Parts 2-3 (including v3 table) skipped.
Render: .pk/render/blockout_Route1_Frlg.png (original left, rejected proposal right).
13 unit tests pass: python tools/pk/tests/test_blockout.py.
Previous weaknesses: 246 object cells were unfit and stayed lawn; independent
material selection can choose visually wrong walkable tiles and incomplete ledges.
Proposal diagnostics compare reach against the walkable wipe; acceptance separately
compares against original vanilla. Only the latter is the retile reach target.
Latest forest acceptance: python tools/pk/tests/test_blockout.py --benchmark --rebuilds-only
Route1 leave-one-out FAIL: 597/960 = 62.19%, new OBJECT:0, original-map reach_lost:0.
WFC left 300 T cells unresolved. Its seen-pair fallback would add 28 OBJECT rows,
so it was refused; all 300 T cells remain unfilled, not a valid forest placement.
The stricter leave-one-out material learner also had no observed same-pair v
candidates: mismatches T:300, g:1, r:1, v:61. No thresholds were relaxed.
STOP: the other four rebuilds, coastal map, library/new measures and v3 table
were skipped. The Route1 comparison PNG now shows this latest rejected proposal.
Unit tests: python tools/pk/tests/test_blockout.py -> 18 pass in the game workspace.
pk map blockout MAP --export [--out .pk/NAME.blockout]
pk map blockout MAP FILE --check (read-only, no cache or render writes)
pk map blockout PkBlockNAME_Frlg FILE [--dry-run]
Format: blockout 1, then exactly this legend, then exactly the map's rows:
legend T=trees .=lawn p=path g=tallgrass ~=water s=sand r=rock v=ledge_s ^=ledge_n <=ledge_w >=ledge_e f=flowers b=building
Unknown letters, changed legend and wrong dimensions are errors. All ledge letters
preserve physical jump direction for export, candidates and connectivity checks.
Legend entries are split at the LAST =, so <=ledge_w and >=ledge_e each have a
one-character symbol. Existing b cells only. Fences already have physical wall
class (T/r visual split); stairs are physical door; puddles and currents already
have physical surf/water where game behavior and collision classify them so.
Export uses game physical classes, tile cards and a provisional rendered-palette
heuristic for visual categories. It never uses metatile names/tags. Inspect exports:
the colour heuristic is NOT a validated tree/building/road/flower recognizer.
Checks report original exit/door coordinates, absent vanilla 3x3 letter patterns,
raw letter open/mirror measures. Part 3 measures print an explicit skipped INFO.
Unchanged imports retain exact existing tuples. Changed imports propose a retile
from vanilla outdoor references only; target-pair lawn, class-restricted materials
and small whole vanilla kit rocks. Unfit rocks stay lawn with INFO.
T cells are now solved jointly as a FOREST, not placed as individual tree stamps.
Candidates must export as T. wfc.learn(exclude=...) learns vanilla 3x3 patterns;
wfc.fill receives the full T mask and per-cell forced domains. Non-T cells stay
frozen. Glue prunes candidates against the actual border and frozen object sides.
Incomplete, invalid-domain or object-breaking WFC fills fall back to terrain.solve
with seen-pair scoring and margin zero. Object-breaking fallback fills are refused
and rolled back; the object guard is never relaxed. Missing WFC prints INFO.
Leave-one-out rebuilds filter out the held-out layout (including aliases) from
pattern and pair learning, not just WFC's window vocabulary. Reference data is
vanilla outdoor only; no Pk map contributes. The classifier is unchanged.
Shape/path acceptance is currently failing, so the prototype prints INFO fallbacks.
Unrestricted terrain auto is explicitly skipped. Complete bank synthesis remains
unimplemented: open water/lawn proposals are refused, never claimed complete.
Writes are limited to PkBlock*_Frlg. Proposals adding object warnings, losing reach,
leaving open water against lawn or falling below 95% fidelity stage no map writes.
The library and calibrated new measures (Parts 2-3) are not implemented.
Tests: python tools/pk/tests/test_blockout.py
Acceptance (stops on first miss): python tools/pk/tests/test_blockout.py --benchmark
Resume wiped rebuilds only: python tools/pk/tests/test_blockout.py --benchmark --rebuilds-only
Only benchmark PNG outputs are committed by that runner; no map data is committed.
Verified API definitions: edit.py load:14/save:88; lint.py physical_class:32 and
vanilla_outdoor:815; cards.py aggregate:106/finalize:144; meta.py draw:87 and
render_compare:297; terrain.py Rules.pair_score:79/solve:83; objects.py Glue:121,
check:187; kit.py window_cuts:187/glued_sides:195; reach.py World:24,
entrances:78/reach:95; layout.py measure:51; core/journal.py stage:120/commit:131.
Forest API evidence: wfc.py learn:113/fill:544/Fill:145; objects.py
broken_side:151/SIDES:22; core/repo.py maps:109/layouts:106.
Public checkout dependency: pk/maps/wfc.py is not currently installed there and
belongs to another agent; this change does not copy or edit it. Public execution
reports INFO then uses the guarded seen-pair fallback. WFC-specific contract
tests explicitly skip with INFO if the optional module is absent.
