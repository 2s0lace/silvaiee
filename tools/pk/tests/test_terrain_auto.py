"""pk terrain auto: fill a box from its surroundings, no material given (in-memory, never committed)."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_maps import MapTest  # noqa: E402

from pk.maps import terrain  # noqa: E402
from pk.maps.edit import load  # noqa: E402
from pk.maps.layers import class_of  # noqa: E402


class TerrainAuto(MapTest):
    def test_shore_comes_back_from_surroundings(self):
        # Wipe Pallet's shore corner (ground | sea) with plain grass, then let auto refill it.
        self.map('paint', 'PalletTown_Frlg', '5,16,4x3', '0x010')
        result = terrain.auto(self.repo, self.tx, 'PalletTown_Frlg', '5,16,4x3')
        after = int(re.search(r'seams:\d+->(\d+)', result.summary)[1])
        self.assertEqual(after, 0, result.render())
        grid, pair = load(self.repo, 'PalletTown_Frlg')
        classes = {class_of(pair, *grid.at(x, y)[:2])[1] for y in range(16, 19) for x in range(5, 9)}
        # A transition between the town ground and the sea, not a flood of one material.
        self.assertTrue({'ground', 'water'} <= classes, (classes, result.render()))

    def test_no_doors_and_box_only(self):
        grid, pair = load(self.repo, 'PalletTown_Frlg')
        outside = {(x, y): grid.at(x, y) for y in range(grid.h) for x in range(grid.w)
                   if not (5 <= x < 9 and 16 <= y < 19)}
        terrain.auto(self.repo, self.tx, 'PalletTown_Frlg', '5,16,4x3')
        grid, pair = load(self.repo, 'PalletTown_Frlg')
        for p, cell in outside.items():
            self.assertEqual(grid.at(*p), cell, f'{p} outside the box changed')
        for y in range(16, 19):
            for x in range(5, 9):
                self.assertNotIn('DOOR', pair.behavior_name(grid.at(x, y)[0]))


class RepairKeepsPiece(MapTest):
    def test_copy_repair_never_changes_the_pasted_piece(self):
        # Benchmark v2: --repair rewrote small pasted pieces (sea became lawn). Now the interior of the
        # piece never changes, and its 1-tile rim only to a tile of the same physical class.
        source, _ = load(self.repo, 'Route21_North_Frlg')
        sx, sy, w, h = 4, 28, 6, 4
        piece = [source.at(sx + i, sy + j) for j in range(h) for i in range(w)]
        result = self.map('copy', 'PalletTown_Frlg', f'{sx},{sy},{w}x{h}', '6,15',
                          **{'from': 'Route21_North_Frlg', 'repair': True})
        grid, pair = load(self.repo, 'PalletTown_Frlg')
        from pk.maps.lint import physical_class
        phys = lambda c: (physical_class(pair.behavior_name(c[0]), c[1]), c[2])
        for j in range(h):
            for i in range(w):
                old, new = piece[j * w + i], grid.at(6 + i, 15 + j)
                if 0 < i < w - 1 and 0 < j < h - 1:
                    self.assertEqual(new, old, f'interior {i},{j} changed')          # interior: untouched
                else:
                    self.assertEqual(phys(new), phys(old), f'rim {i},{j} changed class')  # rim: same class
        self.assertIn('repair:', result.summary)


class ReachWriteTest(MapTest):
    def test_wall_across_pallet_south_exit_warns_with_right_count(self):
        # Rows 17..19 have 15 + 16 + 16 = 47 walkable cells south of y=16.
        result = self.map('paint', 'PalletTown_Frlg', '0,16,24x1', '0x01D')
        self.assertEqual(result.data[-1], 'WARN reach: 47 cells cut off, e.g. 2,17 3,17 4,17 5,17 6,17')
        # A subsequent harmless write compares before vs after this write only.
        second = self.map('paint', 'PalletTown_Frlg', '3,3', '0x004')
        self.assertEqual(second.data[-1], 'reach: ok')

    def test_harmless_writes_and_no_entrance_info(self):
        from pk.maps import create
        ok_paint = self.map('paint', 'PalletTown_Frlg', '3,3', '0x004')
        self.assertEqual(ok_paint.data[-1], 'reach: ok')
        ok_grid = self.map('grid', 'PalletTown_Frlg', '3,3', 'def A 0x004;A')
        self.assertEqual(ok_grid.data[-1], 'reach: ok')
        ok_copy = self.map('copy', 'PalletTown_Frlg', '5,12,2x1', '3,3')
        self.assertEqual(ok_copy.data[-1], 'reach: ok')
        ok_auto = terrain.auto(self.repo, self.tx, 'PalletTown_Frlg', '3,3,2x2')
        self.assertEqual(ok_auto.data[-1], 'reach: ok')
        ok_brush = terrain.command(self.repo, self.tx, ['brush', 'PalletTown_Frlg', 'rect', '3,3,2x2', 'ground'],
                                   {}, lambda o, d=20: (0, d))
        self.assertEqual(ok_brush.data[-1], 'reach: ok')
        ok_stamp = create.stamp(self.repo, self.tx, ['place', 'PalletTown_Frlg', 'tree', '2,2'],
                                {}, lambda o, d=20: (0, d))
        self.assertEqual(ok_stamp.data[-1], 'reach: ok')
        no_ent = self.map('paint', 'PkPalletSolV2_Frlg', '3,3', '0x004')
        self.assertEqual(no_ent.data[-1], 'INFO reach: not checked (no entrance)')


if __name__ == '__main__':
    unittest.main()
