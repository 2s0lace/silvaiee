"""Maintainer round 1, step 1: pk map export / import round trip.

Export is lossless: importing an export into the same layout must reproduce map.bin and
border.bin byte for byte. The test walks every vanilla FRLG outdoor layout (benchmark Pk*
maps are not vanilla). It runs in uncommitted transactions, so the project stays untouched.
"""
from pathlib import Path
import sys
import unittest

HOME = Path(__file__).resolve().parents[1]
ROOT = HOME.parents[1]
sys.path.insert(0, str(HOME))
from pk.core.repo import Repo
from pk.core.journal import Transaction
from pk.maps import export
from pk.maps.layers import Grid
from pk.maps.tiles import Pair

OUTDOOR = {'MAP_TYPE_TOWN', 'MAP_TYPE_CITY', 'MAP_TYPE_ROUTE', 'MAP_TYPE_OCEAN_ROUTE'}


class ExportImport(unittest.TestCase):
    def setUp(self):
        self.repo = Repo(ROOT)

    def roundtrip(self, layout, data):
        grid = Grid(self.repo, layout['id'], data, layout)
        pair = Pair(self.repo, layout)
        text = export.export_text(self.repo, grid, pair)
        tx = Transaction(self.repo, 'test')
        export.import_text(self.repo, tx, layout, text)
        b, br = layout['blockdata_filepath'], layout['border_filepath']
        return text, tx.writes[b], tx.writes[br], self.repo.read(b), self.repo.read(br)

    def test_all_vanilla_frlg_outdoor_layouts(self):
        layouts, maps = self.repo.layouts(), self.repo.maps()
        ref = {}
        for name, data in maps.items():
            if name.startswith('Pk'):                 # benchmark/scratch maps are not vanilla
                continue
            ref.setdefault(data['layout'], (name, data.get('map_type')))
        tested = 0
        for lid, (name, kind) in sorted(ref.items()):
            if layouts[lid].get('layout_version') != 'frlg' or kind not in OUTDOOR:
                continue
            with self.subTest(layout=lid):
                _, got_b, got_br, want_b, want_br = self.roundtrip(layouts[lid], maps[name])
                self.assertEqual(got_b, want_b, f'{lid} map.bin')
                self.assertEqual(got_br, want_br, f'{lid} border.bin')
            tested += 1
        self.assertGreaterEqual(tested, 70, 'outdoor FRLG layout count dropped')

    def test_text_has_header_legend_grid_and_border(self):
        name, data, layout = self.repo.map('PalletTown_Frlg')
        grid = Grid(self.repo, name, data, layout)
        pair = Pair(self.repo, layout)
        lines = export.export_text(self.repo, grid, pair).splitlines()
        self.assertEqual(lines[0], 'pkmap 1')
        self.assertIn(f'layout {layout["id"]}', lines)
        self.assertIn(f'size {layout["width"]}x{layout["height"]}', lines)
        for prefix in ('legend ', 'coll ', 'elev ', 'border '):
            self.assertTrue(any(line.startswith(prefix) for line in lines), prefix)
        self.assertIn('grid', lines)


if __name__ == '__main__':
    unittest.main()
