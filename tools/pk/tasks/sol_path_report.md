# Owner-review revision: Route 1

The brush now penalizes encounter grass, requires two straight steps between bends, learns destination
lawn variants (rather than injecting a grass id), uses vanilla edge/corner masks and road-facing pixel
sides, and caps approaches to ledges/protected grass. Beach tiles sharing the road colour are excluded.

Inherited measurements remain: 72 outdoor maps; width median 2/p95 6, axis-run p95 8,
cross-section width changes 35.4%. These are not between-bend skeleton statistics.

| Wiped Route 1 target | Result |
|---|---|
| New OBJECT | 0 |
| Reach | unchanged, six ability states |
| Longest straight | 8 (limit 8) |
| Minimum segment before a bend | 2 |
| New road/encounter-grass contacts | 0 |
| Ledge cells | unchanged; routing uses existing gaps |
| Path seams | **18 (limit 5): MISS**, previously 73 |
| Distinct options | **2 (target 3): MISS** |

Including changed lawn-verge cells gives 70 seams. The unchanged fixture wipes 192 road cells and
already has 12 broken ledge sides; the path adds none. No target was relaxed. Two existing unit
assertions also fail: zero seams on original Route 1, and three routes. Generator work stopped at the miss.

Commands:
`C:/Python314/python.exe -B tools/pk/tests/test_paths.py --benchmark` (exit 1);
`C:/Python314/python.exe -B -m unittest tools.pk.tests.test_paths tools.pk.tests.test_terrain_auto tools.pk.tests.test_objects -v`.

Comparison: `.pk/render/path_route1.png` (vanilla left, revision right).
Remaining weaknesses: unseen corner/verge joins and insufficient route diversity.
