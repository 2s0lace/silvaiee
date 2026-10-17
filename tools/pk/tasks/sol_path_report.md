# Path improvement report

Road edges tagged `grass` now join the road family through the vanilla interior's dominant colour.
The constrained join pass freezes objects/events, preserves collision/elevation, and checks every
entrance in all six ability states. Equal-count replacements of OBJECT warnings are refused too.

Inherited scan: 72 outdoor maps; 98 qualifying path/sand components, width median 2/p95 6,
axis-run p95 8, cross-section width changes 953/2,692 (35.4%). These are axis/cross-section statistics,
not a validated skeleton measurement between bends; that limitation remains.

| Case | New OBJECT | Reach | Straight | Path seams |
|---|---:|---|---:|---:|
| Original Route 1 | 0 | unchanged | 8 | 0 |
| Wiped PkPathTest copy | 0 | unchanged | 8 | **73; target <=5 MISS** |
| Pallet junctions | deferred after required miss | | | |

Benchmark wipes all 192 walkable road-family cells in memory. The wipe itself creates 12 existing
OBJECT sides; painting adds none. Three routes and PNGs are distinct. Of 140 painted footprint cells,
134 retain road pixels; six remain grass caps.

Commands:
`C:/Python314/python.exe -B tools/pk/tests/test_paths.py --benchmark` (exit 1: target miss);
`C:/Python314/python.exe -B -m unittest tools.pk.tests.test_paths tools.pk.tests.test_terrain_auto tools.pk.tests.test_objects -v`.

Comparison: `.pk/render/path_route1.png` (original left, attempted path right).
Weakness: the erased-road fixture still needs better boundary reconstruction. The previous report's
claim that the path material was inherently insufficient was too strong. No map changes were committed.
