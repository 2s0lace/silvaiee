"""Blockout format, safety and explicit stop-on-miss acceptance runner.

python tools/pk/tests/test_blockout.py --benchmark
Never commits a transaction; all grid edits are in memory. Only render outputs
are written, through Transaction.stage/commit, separately from map proposals.
"""
from copy import copy
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
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
        rows = ['T.pg~', 'srvfb', '^<>..']
        self.assertEqual(blockout.parse(blockout.serialize(rows), 5, 3), rows)

    def test_ambiguous_old_l_is_rejected(self):
        with self.assertRaisesRegex(PkError, "unknown blockout letter 'l'"):
            blockout.parse(blockout.serialize(['l']), 1, 1)

    def test_directional_adapter(self):
        from pk.maps.lint import physical_class
        pair = blockout.LetterPair()
        for cls, symbol in blockout.LEDGE_SYMBOL.items():
            with self.subTest(cls=cls):
                self.assertEqual(physical_class(pair.behavior_name(ord(symbol)), 1), cls)

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

    def test_kindle_west_ledge_is_not_south(self):
        grid, pair = load(self.repo, 'OneIsland_KindleRoad_Frlg')
        classify = blockout.classifier(self.repo, pair)
        self.assertEqual(classify.letter((0x085, 1, 3)), '<')
        rows = blockout.export(self.repo, grid, pair)
        self.assertIn('<', ''.join(rows))
        self.assertNotIn('l', ''.join(rows))

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

    def test_leave_one_out_view_hides_entire_layout(self):
        held_out, _ = load(self.repo, 'Route1_Frlg')
        ref = blockout.learning_reference(self.repo, ('Route1_Frlg',))
        self.assertNotIn('Route1_Frlg', ref.maps())
        self.assertNotIn(held_out.layout['id'], ref.layouts())
        self.assertFalse(any(d['layout'] == held_out.layout['id'] for d in ref.maps().values()))
        self.assertFalse(any(name.startswith('Pk') for name in ref.maps()))


class Context(unittest.TestCase):
    def rules(self):
        return SimpleNamespace(context3={}, context4={}, letters={'.': Counter({(8, 0, 3): 1}),
                                                                'p': Counter({(99, 0, 3): 100})})

    def test_exact_context_excludes_letter_only_beach_candidate(self):
        rows = ['...', '.p.', '...']
        rules = self.rules()
        rules.context3[blockout.letter_context(rows, 1, 1)] = Counter({(10, 0, 3): 3})
        rules.context4[blockout.letter_context(rows, 1, 1, True)] = Counter({(11, 0, 3): 3})
        choices, _, notes = blockout.context_candidates(rows, rules)
        self.assertEqual(choices[1, 1], [10])
        self.assertTrue(any('3x3:1 4-neighbour:0 letter:8 empty:0' in n for n in notes))

    def test_four_neighbour_then_letter_fallback_are_counted(self):
        rows = ['...', '.p.', '...']
        rules = self.rules()
        rules.context4[blockout.letter_context(rows, 1, 1, True)] = Counter({(11, 0, 3): 3})
        choices, _, notes = blockout.context_candidates(rows, rules)
        self.assertEqual(choices[1, 1], [11])
        self.assertTrue(any('3x3:0 4-neighbour:1 letter:8 empty:0' in n for n in notes))
        rules.context4.clear()
        choices, _, notes = blockout.context_candidates(rows, rules)
        self.assertEqual(choices[1, 1], [99])
        self.assertTrue(any('letter:9 empty:0' in n for n in notes))

    def test_empty_domains_report_counts_per_full_pattern(self):
        rules = self.rules()
        rules.letters.clear()
        choices, _, notes = blockout.context_candidates(['ppppp'] * 5, rules)
        self.assertFalse(any(choices.values()))
        self.assertIn('INFO BLOCKOUT context EMPTY pattern:ppp/ppp/ppp cells:9', notes)
        self.assertTrue(any('empty:25' in n for n in notes))

    def test_border_sentinel_and_cardinal_order(self):
        rows = ['T.', 'p>']
        self.assertEqual(blockout.letter_context(rows, 0, 0), '____T._p>')
        self.assertEqual(blockout.letter_context(rows, 0, 0, True), 'T_.p_')

    def test_failed_proposal_stages_no_map_data(self):
        grid = SimpleNamespace(name='PkBlockTest_Frlg', w=1, h=1)
        repo = SimpleNamespace(read=lambda path: blockout.serialize(['.']).encode())
        tx = SimpleNamespace(writes={})
        with patch.object(blockout, 'load', return_value=(grid, object())), \
             patch.object(blockout, 'check', return_value=[]), \
             patch.object(blockout, 'tile', return_value={'safe': False, 'rows': ['WARN OBJECT broken']}), \
             patch.object(blockout, 'save') as save:
            result = blockout.command(repo, tx, ['blockout', grid.name, '.pk/test.txt'], {})
        self.assertFalse(result.ok)
        save.assert_not_called()
        self.assertEqual(tx.writes, {})


class Forest(unittest.TestCase):
    class Grid:
        w, h = 3, 3

        def __init__(self):
            self.cells = [(8, 0, 3)] * 9

        def at(self, x, y):
            return self.cells[y * self.w + x]

        def set(self, x, y, cell):
            self.cells[y * self.w + x] = cell

        def inside(self, x, y):
            return 0 <= x < self.w and 0 <= y < self.h

    def setup_fill(self, complete=True):
        try:
            from pk.maps import wfc
        except ImportError:
            print('INFO forest WFC contract tests skipped: optional pk.maps.wfc is not installed', flush=True)
            self.skipTest('optional pk.maps.wfc is not installed')
        grid = self.Grid()
        rows = ['bT.', '.T.', '.T.']
        choices = {(x, y): [1] if rows[y][x] == 'T' else [8, 9]
                   for y in range(3) for x in range(3) if rows[y][x] != 'b'}
        bits = {p: {mt: (mt, int(mt == 1), 3) for mt in values} for p, values in choices.items()}
        fill = wfc.Fill({p: 1 if rows[p[1]][p[0]] == 'T' else 9 for p in choices})
        fill.complete = complete
        if not complete:
            fill.unsatisfied = [(1, 1)]
        return grid, rows, choices, bits, fill

    def test_wfc_full_forest_plus_mutable_lawn_b_frozen(self):
        grid, rows, choices, bits, fill = self.setup_fill()
        with patch.object(blockout, 'learning_reference', return_value=object()), \
             patch('pk.maps.wfc.learn', return_value=[1]) as learn, \
             patch('pk.maps.wfc.fill', return_value=fill) as solve:
            notes = blockout.forest(object(), grid, object(), rows, object(), choices, bits,
                                    exclude=('Route1_Frlg',))
        self.assertEqual(learn.call_args.kwargs['exclude'], ('Route1_Frlg',))
        self.assertFalse(learn.call_args.kwargs['use_cache'])
        self.assertEqual(set(solve.call_args.args[3]), set(choices))
        self.assertEqual(solve.call_args.kwargs['forced'], choices)
        self.assertEqual(grid.at(0, 0), (8, 0, 3))  # b untouched
        self.assertEqual(grid.at(0, 1), (9, 0, 3))  # lawn is allowed to change
        self.assertTrue(any('forest WFC solved:8' in n for n in notes))

    def test_incomplete_wfc_uses_context_restricted_pair_fallback(self):
        grid, rows, choices, bits, fill = self.setup_fill(False)
        with patch.object(blockout, 'learning_reference', return_value=object()), \
             patch('pk.maps.wfc.learn', return_value=[1]), \
             patch('pk.maps.wfc.fill', return_value=fill), \
             patch.object(blockout.terrain, 'solve', return_value=dict(fill)) as solve:
            notes = blockout.forest(object(), grid, object(), rows, object(), choices, bits)
        self.assertEqual(solve.call_args.kwargs['margin'], 0)
        self.assertEqual(solve.call_args.kwargs['choices'], choices)
        self.assertTrue(any('seen-pair fallback solved:8' in n for n in notes))

    def test_out_of_domain_assignment_is_refused_before_any_mutation(self):
        grid = self.Grid()
        before = list(grid.cells)
        with self.assertRaisesRegex(PkError, 'out-of-domain'):
            blockout.apply_candidates(grid, {(1, 1): 99}, {(1, 1): [1]}, {(1, 1): {1: (1, 1, 3)}})
        self.assertEqual(grid.cells, before)

    def test_object_support_lets_lawn_change_instead_of_rejecting_tree(self):
        grid = self.Grid()
        rows = ['...', '.T.', '...']
        choices = {(x, y): [1] if (x, y) == (1, 1) else [8, 2] for y in range(3) for x in range(3)}
        bits = {p: {mt: (mt, int(mt == 1), 3) for mt in values} for p, values in choices.items()}
        glue = SimpleNamespace(raw={1: {'nmt': {s: Counter({2: 10}) if s == 'W' else Counter()
                                              for s in blockout.objects.SIDES}}},
                               objectish=lambda cell: cell[0] == 1,
                               broken_side=lambda *a: None)
        with patch.object(blockout.objects, 'Glue', return_value=glue):
            notes = blockout.supported_contexts(object(), grid, object(), rows, choices, bits)
        self.assertEqual(choices[0, 1], [2])
        self.assertTrue(any('empty-cells:0' in n for n in notes))


def benchmark(rebuilds_only=False):
    repo = Repo(ROOT)
    choices = ['Route1_Frlg', 'Route22_Frlg', 'PalletTown_Frlg', 'Route3_Frlg', 'OneIsland_KindleRoad_Frlg']
    choices += [name for name, _ in vanilla_outdoor(repo) if name not in choices][:5]
    matched = total = 0
    # Resume the previously blocked Kindle Road first, then finish this same
    # ten-map cohort; earlier four are revalidated against the extended legend.
    if rebuilds_only:
        print('INFO roundtrips skipped: resume wiped-map acceptance after previous 10/10, 100% pass', flush=True)
    for name in ([] if rebuilds_only else choices[4:] + choices[:4]):
        grid, pair = load(repo, name)
        original = list(grid.cells)
        try:
            rows = blockout.parse(blockout.serialize(blockout.export(repo, grid, pair)), grid.w, grid.h)
        except PkError as exc:
            print(f'roundtrip {name} FAIL {exc.code}: {exc}', flush=True)
            print('STOP roundtrip target missed; INFO retiles/coastal/Parts 2-3 skipped', flush=True)
            return 1
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
    if not rebuilds_only:
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
        print(f'INFO retile {name} learning excludes its entire layout', flush=True)
        report = blockout.tile(repo, grid, pair, rows, exclude=(name,))
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
        actual = blockout.export(repo, grid, pair)
        mismatches = Counter(a for expected, observed in zip(rows, actual)
                             for a, b in zip(expected, observed) if a != b)
        print('INFO mismatches ' + ' '.join(f'{char}:{n}' for char, n in sorted(mismatches.items())), flush=True)
        if not ok:
            print('STOP retile target missed; INFO remaining retiles/coastal/Parts 2-3 skipped; thresholds unchanged', flush=True)
            return 1
    print('INFO coastal benchmark not implemented; Part 1 incomplete; STOP', flush=True)
    return 1


if __name__ == '__main__':
    if '--benchmark' in sys.argv:
        raise SystemExit(benchmark('--rebuilds-only' in sys.argv))
    unittest.main()
