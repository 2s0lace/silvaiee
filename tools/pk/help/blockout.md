Experimental Part 1 prototype; not accepted. See tasks/gpt_layout_blockout.md.
Acceptance STOP: OneIsland_KindleRoad_Frlg contains west-facing ledge 0x085,
which the required fixed l=ledge_s legend cannot faithfully represent. Four
unchanged vanilla round trips passed before this error; the ten-map target fails.
No wiped retiles, coastal render, library or new measures were run after the miss.
The global vanilla-pattern preflight is also blocked by this representability gap.
pk map blockout MAP --export [--out .pk/NAME.blockout]
pk map blockout MAP FILE --check (read-only, no cache or render writes)
pk map blockout PkBlockNAME_Frlg FILE [--dry-run]
Format: blockout 1, then exactly this legend, then exactly the map's rows:
legend T=trees .=lawn p=path g=tallgrass ~=water s=sand r=rock l=ledge_s f=flowers b=building
Unknown letters, changed legend and wrong dimensions are errors. Non-south ledges
are unrepresentable and refused, not silently re-labelled. Existing b cells only.
Export uses game physical classes, tile cards and a provisional rendered-palette
heuristic for visual categories. It never uses metatile names/tags. Inspect exports:
the colour heuristic is NOT a validated tree/building/road/flower recognizer.
Checks report original exit/door coordinates, absent vanilla 3x3 letter patterns,
raw letter open/mirror measures. Part 3 measures print an explicit skipped INFO.
Unchanged imports retain exact existing tuples. Changed imports propose a retile
from vanilla outdoor references only; target-pair lawn, class-restricted materials
and small whole vanilla kit objects. Unfit objects stay lawn with INFO.
Shape/path acceptance is currently failing, so the prototype prints INFO fallbacks.
Unrestricted terrain auto is explicitly skipped. Complete bank synthesis remains
unimplemented: open water/lawn proposals are refused, never claimed complete.
Writes are limited to PkBlock*_Frlg. Proposals adding object warnings, losing reach,
leaving open water against lawn or falling below 95% fidelity stage no map writes.
The library and calibrated new measures (Parts 2-3) are not implemented.
Tests: python tools/pk/tests/test_blockout.py
Acceptance (stops on first miss): python tools/pk/tests/test_blockout.py --benchmark
Only benchmark PNG outputs are committed by that runner; no map data is committed.
Verified API definitions: edit.py load:14/save:88; lint.py physical_class:32 and
vanilla_outdoor:815; cards.py aggregate:106/finalize:144; meta.py draw:87 and
render_compare:297; terrain.py Rules.pair_score:79/solve:83; objects.py Glue:121,
check:187; kit.py window_cuts:187/glued_sides:195; reach.py World:24,
entrances:78/reach:95; layout.py measure:51; core/journal.py stage:120/commit:131.
