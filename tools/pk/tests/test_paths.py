"""Deterministic routing tests for pk terrain path (all writes remain in memory)."""
import sys
import unittest
from unittest import mock
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pk.core.journal import Transaction  # noqa: E402
from pk.core.repo import Repo  # noqa: E402
from pk.maps.edit import load  # noqa: E402
from pk.maps import paths, terrain, objects, meta  # noqa: E402
from pk.core.errors import PkError  # noqa: E402


ROOT = Path(__file__).resolve().parents[3]
# The release checkout intentionally omits FRLG map fixtures; use the shared game data above it.
if not (ROOT / 'data/maps/Route1_Frlg').is_dir():
    ROOT = next((p for p in ROOT.parents if (p / 'data/maps/Route1_Frlg').is_dir()), ROOT)


class PathRoutes(unittest.TestCase):
    def setUp(self):
        self.tx = Transaction(Repo(ROOT), 'path test')
        self.repo = Repo(ROOT, self.tx)
        self.grid, self.pair = load(self.repo, 'Route1_Frlg')

    def test_three_deterministic_distinct_routes_with_vanilla_run_limit(self):
        points = [(11, 0), (12, 39)]
        first = paths.route_options(self.repo, self.grid, self.pair, points, 2, 3)
        second = paths.route_options(self.repo, self.grid, self.pair, points, 2, 3)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 3)
        self.assertEqual(len({tuple(route) for route in first}), 3)
        self.assertTrue(all(paths.longest_straight(route) <= paths.VANILLA_STRAIGHT_P95
                            for route in first))

    def test_route_footprints_do_not_paint_blocked_cells(self):
        route = paths.route(self.repo, self.grid, self.pair, [(11, 0), (12, 39)], width=2)
        blocked = paths.blocked_cells(self.repo, self.grid, self.pair)
        self.assertFalse(paths.widened(route, 2) & blocked)

    def test_road_family_includes_grass_tagged_transitions(self):
        rules = terrain.Rules(self.repo, self.pair)
        family, _ = paths.road_family(self.repo, self.pair, rules)
        self.assertTrue({0x0DC, 0x0DB, 0x0DD, 0x0D4, 0x0E4} <= family)
        self.assertNotIn(0x010, family)

    def test_paint_preserves_blockers_bits_and_reach(self):
        route = paths.route(self.repo, self.grid, self.pair, [(11, 0), (12, 39)], 2)
        blocked = paths.blocked_cells(self.repo, self.grid, self.pair)
        before = list(self.grid.cells)
        cells, rules = paths._paint(self.repo, self.grid, self.pair, route, 2, 'path')
        for x, y in blocked:
            if self.grid.inside(x, y):
                self.assertEqual(self.grid.at(x, y), before[y * self.grid.w + x])
        self.assertEqual([c[1:] for c in before], [c[1:] for c in self.grid.cells])
        _, reach_line = paths._validate(self.repo, self.grid, self.pair, before, cells)
        self.assertEqual(reach_line, 'reach: ok')
        self.assertFalse(objects.check(self.repo, self.grid, self.pair))
        self.assertEqual(len(terrain.unseen(self.grid, rules, cells)), 0)
        self.assertFalse(self.tx.writes)

    def test_replacing_one_warning_with_another_is_refused(self):
        before = list(self.grid.cells)
        with mock.patch.object(objects, 'check', side_effect=[['WARN OBJECT old'], ['WARN OBJECT new']]):
            with self.assertRaisesRegex(PkError, 'add 1 WARN OBJECT'):
                paths._validate(self.repo, self.grid, self.pair, before, {(3, 3)})
        self.assertFalse(self.tx.writes)

    def test_pick_zero_is_rejected_without_staging(self):
        with self.assertRaisesRegex(PkError, '--pick must be 1..20'):
            paths.command(self.repo, self.tx, ['Route1_Frlg', '11,0', '12,39'], {'pick': '0'})
        self.assertFalse(self.tx.writes)


def benchmark():
    """Required wiped-copy acceptance case. No map data is committed; exit nonzero on a miss."""
    repo = Repo(ROOT)
    original, pair = load(repo, 'Route1_Frlg')
    rules = terrain.Rules(repo, pair)
    family, coverage = paths.road_family(repo, pair, rules)
    road = {(x, y) for y in range(original.h) for x in range(original.w)
            if original.at(x, y)[0] in family}
    vanilla_seams = len(terrain.unseen(original, rules, road))
    grid = paths._clone(original)
    grid.name = 'PkPathTest_Frlg'
    grid.data = dict(original.data, name=grid.name)
    for x, y in road:
        old = grid.at(x, y)
        grid.set(x, y, (0x010, old[1], old[2]))
    before = list(grid.cells)
    before_objects = set(objects.check(repo, grid, pair))
    routes = paths.route_options(repo, grid, pair, [(11, 0), (12, 39)], 2, 3)
    for i, route in enumerate(routes, 1):
        paths._preview(repo, grid, pair, route, 2, 'path', i)
    cells, rules = paths._paint(repo, grid, pair, routes[0], 2, 'path')
    _, reach_line = paths._validate(repo, grid, pair, before, cells)
    new_objects = set(objects.check(repo, grid, pair)) - before_objects
    seams = len(terrain.unseen(grid, rules, cells))
    render = repo.path('.pk/render/path_route1.png')
    render.parent.mkdir(parents=True, exist_ok=True)
    render.write_bytes(meta.render_compare(repo, (original, pair), (grid, pair), scale=2))
    result = dict(fixture=grid.name, wiped_cells=len(road), wipe_object_sides=len(before_objects),
                  new_objects=len(new_objects), reach=reach_line, straight=paths.longest_straight(routes[0]),
                  options=len({tuple(route) for route in routes}), seams=seams, seam_limit=vanilla_seams + 5,
                  road_cells=sum(coverage.get(grid.at(*p)[0], 0) >= .15 for p in cells),
                  painted_cells=len(cells), render=str(render))
    print(json.dumps(result, indent=2))
    return 0 if not new_objects and reach_line == 'reach: ok' and seams <= vanilla_seams + 5 else 1


if __name__ == '__main__':
    if '--benchmark' in sys.argv:
        sys.exit(benchmark())
    unittest.main()
