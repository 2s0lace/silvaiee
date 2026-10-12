"""Milestone 4b: material brushes + learned-adjacency solver (uncommitted transactions only)."""
from pathlib import Path
import random
import sys
import unittest

HOME = Path(__file__).resolve().parents[1]
ROOT = HOME.parents[1]
sys.path.insert(0, str(HOME))
from pk.core.repo import Repo
from pk.core.journal import Transaction
from pk.maps.grid import decode
from pk.maps import terrain, resize
from pk.maps.edit import load
from pk.maps.terrain import Rules, solve

BIN = 'data/layouts/PalletTown_Frlg/map.bin'


def pagination(opts, default=20):
    return int(opts.get('offset', 0)), int(opts.get('limit', default))


class Terrain(unittest.TestCase):
    def setUp(self):
        self.tx = Transaction(Repo(ROOT), 'test')
        self.repo = Repo(ROOT, self.tx)

    def run_terrain(self, *args, **opts):
        return terrain.command(self.repo, self.tx, list(args), opts, pagination)

    def test_learn(self):
        result = self.run_terrain('learn', 'PalletTown_Frlg')
        self.assertIn('layouts:', result.summary)
        self.assertTrue(any(r.startswith('water ') for r in result.data))

    def test_brush_sets_material_and_respects_margin(self):
        grid, pair = load(self.repo, 'PalletTown_Frlg')
        rules = Rules(self.repo, pair)
        before = list(grid.cells)
        self.run_terrain('brush', 'PalletTown_Frlg', 'rect', '15,14,4x3', 'water')
        after = decode(self.tx.writes[BIN])
        for i, (a, b) in enumerate(zip(before, after)):
            x, y = i % 24, i // 24
            inside = 15 <= x < 19 and 14 <= y < 17
            ring = 14 <= x < 20 and 13 <= y < 18
            if inside:
                self.assertEqual(rules.material(b), 'water', (x, y))
            elif ring:
                self.assertEqual(rules.material(b), rules.material(a), (x, y))   # margin keeps material
            else:
                self.assertEqual(a, b, (x, y))

    def test_solver_reproduces_natural_vanilla_terrain(self):
        """Erase 5x5 windows of vanilla terrain (flat ground variants, water, ledges only), keep
        only their materials, re-solve. Measured when written: 81% exact, 1.7% unseen seams.
        The ground variants (meadow, lawn, path) are material tags added after that measurement;
        including them is what the tags were for (see DECISIONS, milestone 4b)."""
        from pk.maps.terrain import unseen
        random.seed(7)
        total = same = pairs = bad = 0
        natural = {'ground', 'water', 'grass', 'sand', 'ledge_s', 'ledge_n', 'ledge_e', 'ledge_w',
                   'meadow', 'lawn', 'path'}
        for name in ('PalletTown_Frlg', 'Route1_Frlg', 'Route22_Frlg', 'ViridianForest_Frlg', 'Route2_Frlg',
                     'CeruleanCity_Frlg', 'Route24_Frlg'):
            grid, pair = load(self.repo, name)
            rules = Rules(self.repo, pair)
            for _ in range(40):
                x0, y0 = random.randrange(1, grid.w - 6), random.randrange(1, grid.h - 6)
                window = [(x, y) for y in range(y0, y0 + 5) for x in range(x0, x0 + 5)]
                mats = {p: rules.material(grid.at(*p)) for p in window}
                if not set(mats.values()) <= natural:
                    continue
                original = {p: grid.at(*p) for p in window}
                for p in window:                       # erase: most common metatile of the material
                    top = max(rules.by_mat[mats[p]], key=lambda m: rules.freq.get(m, 0))
                    grid.set(*p, (top, *original[p][1:]))
                result = solve(grid, rules, mats, margin=0)
                for p in window:
                    total += 1
                    same += result[p] == original[p][0]
                    grid.set(*p, (result[p], *original[p][1:]))
                bad += len(unseen(grid, rules, window))
                pairs += 60
                for p in window:
                    grid.set(*p, original[p])
        self.assertGreater(total, 1000)
        self.assertGreaterEqual(same / total, 0.75, f'{same}/{total}')
        self.assertLessEqual(bad / pairs, 0.05, f'{bad}/{pairs} unseen seams')

    def test_river_and_resize_fill_terrain(self):
        self.run_terrain('river', 'Route1_Frlg', '3,22', '8,22', 'width=2')
        grid, pair = load(self.repo, 'Route1_Frlg')
        rules = Rules(self.repo, pair)
        self.assertEqual(rules.material(grid.at(5, 22)), 'water')
        tx = Transaction(Repo(ROOT), 'test2')
        result = resize.run(Repo(ROOT, tx), tx, 'PalletTown_Frlg', {'add': 'right=6', 'fill': 'terrain'})
        self.assertIn('-> 30x20', result.summary)


if __name__ == '__main__':
    unittest.main()
