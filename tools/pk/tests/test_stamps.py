"""Maintainer round 1, step 3: stamp mining and instance detection.

Mining finds repeated all-solid rectangles in the vanilla layouts of a tileset pair. The
acceptance target: on PalletTown_Frlg, ViridianCity_Frlg and Route1_Frlg, at least 80% of
building/tree cells (class-view solid '#' cells) are covered by a detected stamp. Everything
runs in uncommitted transactions.
"""
from pathlib import Path
import sys
import unittest

HOME = Path(__file__).resolve().parents[1]
ROOT = HOME.parents[1]
sys.path.insert(0, str(HOME))
from pk.core.repo import Repo
from pk.core.journal import Transaction
from pk.maps import stamps as S
from pk.maps import edit
from pk.maps.edit import load
from pk.maps.layers import class_of

TARGETS = ('PalletTown_Frlg', 'ViridianCity_Frlg', 'Route1_Frlg')


def structure_cells(grid, pair):
    return {(x, y) for y in range(grid.h) for x in range(grid.w)
            if class_of(pair, *grid.at(x, y)[:2])[0] == '#'}


def union_covered(grid, library):
    covered = set()
    for stamp in library:
        covered |= S._match_cells(grid.w, grid.h, grid.cells, stamp)
    return covered


class Mining(unittest.TestCase):
    def setUp(self):
        self.tx = Transaction(Repo(ROOT), 'test')
        self.repo = Repo(ROOT, self.tx)

    def test_mine_proposes_and_selects(self):
        from pk.maps import create
        grid, pair = load(self.repo, 'PalletTown_Frlg')
        proposals = S.mine(self.repo, pair, (2, 2), 3)
        self.assertTrue(proposals)
        stamps = S.to_stamps(self.repo, pair, proposals, S.tree_metatiles(self.repo, pair))
        self.assertTrue(all(s['name'].startswith('mined_pallettown_') for s in stamps))
        chosen = S.select(self.repo, pair, stamps, 8)
        self.assertTrue(chosen)
        self.assertLessEqual(len(chosen), 8)
        result = create.stamp_mine(self.repo, self.tx, 'PalletTown_Frlg', {})
        self.assertIn('selected', result.summary)
        self.assertTrue(result.data)

    def test_apply_never_overwrites_shipped_stamps(self):
        from pk.maps import create
        result = create.stamp_mine(self.repo, self.tx, 'PalletTown_Frlg', {'apply': True})
        self.assertIn('applied', result.summary)
        self.assertFalse([k for k in self.tx.writes if k.startswith('tools/pk/data/stamps/mined_')],
                         'shipped mined stamps must not be overwritten')


class Acceptance(unittest.TestCase):
    def setUp(self):
        self.repo = Repo(ROOT)

    def test_eighty_percent_structure_covered(self):
        for name in TARGETS:
            grid, pair = load(self.repo, name)
            library = S.load_stamps(self.repo, pair)
            total = structure_cells(grid, pair)
            covered = len(total & union_covered(grid, library))
            self.assertGreaterEqual(covered / len(total), 0.80,
                                    f'{name}: {covered}/{len(total)} covered by {len(library)} stamps')

    def test_show_stamps_overlay(self):
        tx = Transaction(Repo(ROOT), 'test')
        repo = Repo(ROOT, tx)
        text = edit.command(repo, tx, ['show', 'PalletTown_Frlg'], {'stamps': True}).render()
        self.assertIn('stamps ', text)
        self.assertRegex(text, r'\nT[ =]|T=mined_')     # tree stamp symbol present
        plain = edit.command(repo, tx, ['show', 'PalletTown_Frlg'], {}).render()
        self.assertNotIn('stamps ', plain)              # opt-in only


if __name__ == '__main__':
    unittest.main()
