"""Composition stats (pk/maps/layout.py) and the vanilla border on map new; in memory, never committed."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_maps import MapTest  # noqa: E402

from pk.maps import create, layout, lint  # noqa: E402
from pk.maps.edit import load  # noqa: E402


class Layout(MapTest):
    def test_vanilla_pallet_has_no_layout_rows(self):
        grid, pair = load(self.repo, 'PalletTown_Frlg')
        self.assertEqual(layout.rows(self.repo, grid, pair, 'town'), [])

    def test_empty_lawn_fires(self):
        # Wipe Pallet's interior to one plain lawn: its screens become empty, far above vanilla.
        self.map('paint', 'PalletTown_Frlg', '2,2,20x16', '0x010')
        text = '\n'.join(lint.run(self.repo, 'PalletTown_Frlg').data)
        self.assertRegex(text, r'INFO LAYOUT open \d+,\d+-\d+,\d+ \d+% plain lawn')

    def test_new_map_gets_a_vanilla_border(self):
        result = create.new_map(self.repo, self.tx, 'PkBorderTest_Frlg',
                                {'size': '10x8', 'tilesets': 'gTileset_General_Frlg,gTileset_PalletTown',
                                 'fill': '0x010', 'type': 'route'})
        self.assertTrue(any(r.startswith('border: most common vanilla wall border') for r in result.data), result.data)
        border = self.tx.writes['data/layouts/PkBorderTest_Frlg/border.bin']
        self.assertEqual(border, (Path(__file__).resolve().parents[3] / 'data/layouts/PalletTown_Frlg/border.bin').read_bytes())


if __name__ == '__main__':
    unittest.main()
