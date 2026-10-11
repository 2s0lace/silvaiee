"""pk map paintover: a painted map image becomes metatiles again (milestone 4c/5.6 view)."""
import io
from pathlib import Path
import random
import sys
import tempfile
import unittest

HOME = Path(__file__).resolve().parents[1]
ROOT = HOME.parents[1]
sys.path.insert(0, str(HOME))
from pk.core.repo import Repo
from pk.core.journal import Transaction
from pk.maps.grid import decode, encode
from pk.maps.edit import load
from pk.maps import paintover
from pk.maps.meta import render_png

MAP = 'PkPalletDraft_Frlg'
BIN = f'data/layouts/{MAP}/map.bin'
COPY = 'PkPaintoverTest_Frlg'
COPY_BIN = f'data/layouts/{COPY}/map.bin'


class Paintover(unittest.TestCase):
    def setUp(self):
        self.tx = Transaction(Repo(ROOT), 'test')
        self.repo = Repo(ROOT, self.tx)

    def render_png_bytes(self, scale=2):
        grid, pair = load(self.repo, MAP)
        return render_png(self.repo, grid, pair, (0, 0, grid.w, grid.h), scale)

    def write_png(self, data, scale=2):
        folder = Path(tempfile.mkdtemp())
        path = folder / 'painted.png'
        path.write_bytes(data)
        return str(path)

    def test_clean_render_freezes_every_cell(self):
        png = self.write_png(self.render_png_bytes(2))
        result = paintover.command(self.repo, self.tx, ['paintover', MAP, png], {})
        self.assertIn('896 frozen', result.summary)
        self.assertIn('0 changed', result.summary)
        self.assertNotIn(BIN, self.tx.writes)      # nothing to patch: byte-exact survival

    def test_scrambled_cells_are_reproduced(self):
        original = decode(self.repo.read(BIN))
        png = self.write_png(self.render_png_bytes(2))
        random.seed(3)
        junk = list(original)
        for i in range(0, len(junk), 7):
            junk[i] = (0, 0, 0)                    # visually far from the rendered tiles
        self.tx.stage(BIN, encode(junk))
        result = paintover.command(self.repo, self.tx, ['paintover', MAP, png], {})
        after = decode(self.tx.read(BIN))
        same = sum(1 for a, b in zip(after, original) if a == b)
        self.assertGreater(same / len(original), 0.95, f'{same}/{len(original)}; {result.summary}')

    def test_bad_image_size_is_resized_with_a_warning(self):
        png = self.write_png(self.render_png_bytes(3))
        result = paintover.command(self.repo, self.tx, ['paintover', MAP, png], {})
        self.assertTrue(any('not the map size' in r and 'resized' in r for r in result.data), result.data)

    def test_into_creates_a_copy_map(self):
        if (ROOT / 'data/maps' / COPY).exists():
            self.skipTest(f'{COPY} already exists in the repo')
        png = self.write_png(self.render_png_bytes(1))
        result = paintover.command(self.repo, self.tx, ['paintover', MAP, png], {'into': COPY})
        self.assertIn(COPY, result.summary)
        self.assertEqual(decode(self.tx.read(COPY_BIN)), decode(self.repo.read(BIN)))
        include = self.tx.read('data/event_scripts.s')
        self.assertIn(COPY.encode(), include)
        groups = self.tx.read('data/maps/map_groups.json').decode('utf-8')
        self.assertIn(f'"{COPY}"', groups)


if __name__ == '__main__':
    unittest.main()
