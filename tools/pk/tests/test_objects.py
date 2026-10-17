"""Broken multi-tile objects (pk/maps/objects.py), in memory, never committed."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_maps import MapTest  # noqa: E402

from pk.maps import lint, objects  # noqa: E402
from pk.maps.edit import load  # noqa: E402


class BrokenObjects(MapTest):
    def test_vanilla_pallet_has_none(self):
        grid, pair = load(self.repo, 'PalletTown_Frlg')
        self.assertEqual(objects.check(self.repo, grid, pair), [])
        self.assertIn('objects:0', lint.run(self.repo, 'PalletTown_Frlg').summary)

    def test_tree_without_upper_half(self):
        # Pallet's west tree wall: 0x01C at 0,2 always has 0x014 above it in vanilla. Replace that upper
        # half with plain grass and the crown below must be reported as missing its upper half.
        grid, pair = load(self.repo, 'PalletTown_Frlg')
        self.assertEqual((grid.at(0, 1)[0], grid.at(0, 2)[0]), (0x014, 0x01C))
        self.map('paint', 'PalletTown_Frlg', '0,1', '0x010')
        grid, pair = load(self.repo, 'PalletTown_Frlg')
        rows = objects.check(self.repo, grid, pair)
        self.assertTrue(any(r.startswith('WARN OBJECT @0,2 0x01C needs above: 0x014') for r in rows), rows)
        result = lint.run(self.repo, 'PalletTown_Frlg', show_all=True)
        self.assertIn('WARN OBJECT @0,2', '\n'.join(result.data))
        self.assertNotIn('objects:0', result.summary)

    def test_objects_do_not_change_score(self):
        self.map('paint', 'PalletTown_Frlg', '0,1', '0x010')
        before = lint.severity_scores
        summary = lint.run(self.repo, 'PalletTown_Frlg').summary
        self.assertIs(lint.severity_scores, before)
        # score/severity come only from the five calibrated checks; objects are listed, not scored
        self.assertRegex(summary, r'objects:\d+ score:\d+ severity:')


class SolversKeepObjects(MapTest):
    def test_auto_never_leaves_a_broken_tree(self):
        # A box over half of Pallet's tree wall: whatever auto does, no tree may end up broken.
        from pk.maps import terrain
        for box in ('0,1,1x2', '1,3,1x1', '0,0,3x3'):
            terrain.auto(self.repo, self.tx, 'PalletTown_Frlg', box)
            grid, pair = load(self.repo, 'PalletTown_Frlg')
            self.assertEqual(objects.check(self.repo, grid, pair), [], box)

    def test_options_flag_fills_that_break_objects(self):
        from pk.maps import terrain
        self.map('paint', 'PalletTown_Frlg', '0,1', '0x010')          # a headless tree to start from
        result = terrain.auto(self.repo, self.tx, 'PalletTown_Frlg', '3,1,2x2', options_count=3)
        self.assertTrue(all(r.startswith(('option', 'pk terrain auto', 'INFO')) for r in result.data), result.data)


if __name__ == '__main__':
    unittest.main()
