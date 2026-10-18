"""Blockout format, safety and explicit stop-on-miss acceptance runner.

python tools/pk/tests/test_blockout.py --benchmark
Never commits a transaction; all grid edits are in memory. Only render outputs
are written, through Transaction.stage/commit, separately from map proposals.
"""
from copy import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pk.core.repo import Repo
from pk.core.journal import Transaction
from pk.core.errors import PkError
from pk.maps import blockout
from pk.maps.edit import load
from pk.maps.lint import vanilla_outdoor
from pk.maps.meta import render_compare

ROOT = Path(__file__).resolve().parents[3]
if not (ROOT / 'data/maps/Route1_Frlg').is_dir():
    ROOT = next((p for p in ROOT.parents if (p / 'data/maps/Route1_Frlg').is_dir()), ROOT)


class Format(unittest.TestCase):
    def test_roundtrip_and_fixed_alphabet(self):
        rows = ['T.pg~', 'srlfb']
        self.assertEqual(blockout.parse(blockout.serialize(rows), 5, 2), rows)

    def test_unknown_with_coordinate(self):
        with self.assertRaisesRegex(PkError, '1,0'):
            blockout.parse(blockout.serialize(['.?']), 2, 1)

    def test_ragged_extra_missing_and_trailing_space(self):
        for rows in (['..', '.'], ['..'], ['..', '..', '..'], ['.. ', '..']):
            with self.subTest(rows=rows), self.assertRaises(PkError):
                blockout.parse(blockout.serialize(rows), 2, 2)

    def test_fixed_header_and_legend(self):
        for text in ('blockout 2\n' + blockout.LEGEND + '\n.\n',
                     'blockout 1\nlegend .=water\n.\n'):
            with self.assertRaises(PkError):
                blockout.parse(text, 1, 1)

    def test_crlf(self):
        self.assertEqual(blockout.parse(blockout.serialize(['..']).replace('\n', '\r\n'), 2, 1), ['..'])


class Maps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Repo(ROOT)

    def test_unused_045_not_water_from_bad_name(self):
        _, pair = load(self.repo, 'PalletTown_Frlg')
        classify = blockout.classifier(self.repo, pair)
        self.assertNotEqual(classify.letter((0x045, 0, 3)), '~')

    def test_export_ignores_named_metadata(self):
        grid, pair = load(self.repo, 'Route1_Frlg')
        expected = blockout.export(self.repo, grid, pair)
        original = pair.info
        try:
            pair.info = lambda mt: {'name': 'water_bogus', 'tags': ['water'], 'sym': '~'}
            # Classifier only looks at physical/card fields and actual rendered RGB.
            actual = blockout.Classifier(self.repo, pair).export(grid)
            self.assertEqual(expected, actual)
        finally:
            pair.info = original

    def test_building_must_already_exist(self):
        grid, pair = load(self.repo, 'Route1_Frlg')
        rows = blockout.export(self.repo, grid, pair)
        p = next((x, y) for y, row in enumerate(rows) for x, c in enumerate(row) if c != 'b')
        x, y = p
        rows[y] = rows[y][:x] + 'b' + rows[y][x + 1:]
        with self.assertRaisesRegex(PkError, 'not an existing building'):
            blockout.check(self.repo, grid, pair, rows)

    def test_unchanged_import_preserves_exact_cells(self):
        grid, pair = load(self.repo, 'Route1_Frlg')
        before = list(grid.cells)
        report = blockout.tile(self.repo, grid, pair, blockout.export(self.repo, grid, pair))
        self.assertTrue(report['safe'])
        self.assertEqual(before, grid.cells)

    def test_vanilla_write_refused(self):
        tx = Transaction(self.repo, 'test blockout')
        repo = Repo(ROOT, tx)
        grid, pair = load(repo, 'Route1_Frlg')
        tx.stage('.pk/blockout_test.txt', blockout.serialize(blockout.export(repo, grid, pair)).encode())
        before = dict(tx.writes)
        # Stub preflight only; this test is about the write guard, not the vocabulary.
        from unittest.mock import patch
        with patch.object(blockout, 'check', return_value=[]):
            with self.assertRaisesRegex(PkError, 'vanilla is read-only'):
                blockout.command(repo, tx, ['blockout', grid.name, '.pk/blockout_test.txt'], {})
        self.assertEqual(before, tx.writes)


def benchmark():
    repo = Repo(ROOT)
    choices = ['Route1_Frlg', 'Route22_Frlg', 'PalletTown_Frlg', 'Route3_Frlg', 'OneIsland_KindleRoad_Frlg']
    choices += [name for name, _ in vanilla_outdoor(repo) if name not in choices][:5]
    matched = total = 0
    for name in choices:
        grid, pair = load(repo, name)
        original = list(grid.cells)
        rows = blockout.parse(blockout.serialize(blockout.export(repo, grid, pair)), grid.w, grid.h)
        report = blockout.tile(repo, grid, pair, rows)
        actual = blockout.export(repo, grid, pair)
        count = sum(a == b for arow, brow in zip(rows, actual) for a, b in zip(arow, brow))
        matched += count
        total += grid.w * grid.h
        ok = report['safe'] and count == grid.w * grid.h and grid.cells == original
        print(f'roundtrip {name} {count}/{grid.w * grid.h} exact_tiles:{grid.cells == original} pass:{ok}', flush=True)
        if not ok:
            print('STOP roundtrip target missed; INFO retiles/coastal/Parts 2-3 skipped', flush=True)
            return 1
    print(f'roundtrip maps:{len(choices)} cells:{matched}/{total} fidelity:{matched / total:.2%} PASS', flush=True)
    # Real wipe: every non-building cell becomes the target map's own modal lawn.
    for name in choices[:5]:
        original, pair = load(repo, name)
        rows = blockout.export(repo, original, pair)
        lawn = __import__('collections').Counter(c for i, c in enumerate(original.cells)
                    if rows[i // original.w][i % original.w] == '.').most_common(1)[0][0]
        grid = copy(original)
        grid.name = 'PkBlockBenchmark_Frlg'
        grid.cells = [cell if rows[i // grid.w][i % grid.w] == 'b' else lawn
                      for i, cell in enumerate(original.cells)]
        report = blockout.tile(repo, grid, pair, rows)
        # Acceptance is compared with original vanilla, not the weakened wipe.
        added = len(set(blockout.objects.check(repo, grid, pair)) - set(blockout.objects.check(repo, original, pair)))
        losses = blockout.reach_losses(original, grid, pair)
        render = f'.pk/render/blockout_{name}.png'
        output = Transaction(repo, 'blockout benchmark render only')
        output.stage(render, render_compare(repo, (original, pair), (grid, pair)))
        output.commit(False)
        ok = added == 0 and not losses and report['fidelity'] >= .95 and not report['open_bank']
        print(f'retile {name} new_OBJECT:{added} reach_lost:{sum(losses.values())} '
              f'fidelity:{report["fidelity"]:.2%} open_bank:{report["open_bank"]} pass:{ok} render:{render}', flush=True)
        print('\n'.join(report['rows']), flush=True)
        if not ok:
            print('STOP retile target missed; INFO remaining retiles/coastal/Parts 2-3 skipped; thresholds unchanged', flush=True)
            return 1
    print('INFO coastal benchmark not implemented; Part 1 incomplete; STOP', flush=True)
    return 1


if __name__ == '__main__':
    if '--benchmark' in sys.argv:
        raise SystemExit(benchmark())
    unittest.main()
