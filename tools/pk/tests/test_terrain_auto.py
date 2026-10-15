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


if __name__ == '__main__':
    unittest.main()
