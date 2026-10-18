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
        rows = ['T.pg~', 'srvfb', '^<>.n']
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

    def test_route1_connected_north_matches_lint(self):
        grid, pair = load(self.repo, 'Route1_Frlg')
        linked = blockout.objects.connected_sides(grid.data)
        self.assertIn('N', linked)
        glue = blockout.objects.Glue(self.repo, grid, pair)
        self.assertIsNone(glue.neighbour(9, 0, 'N'))
        rows = blockout.export(self.repo, grid, pair)
        self.assertEqual(blockout.letter_context(rows, 9, 0, connected=linked)[:3], '***')
        border = blockout.objects.border_reader(self.repo, grid.layout)
        self.assertEqual(glue.neighbour(0, 0, 'W'), (border(-1, 0), True))


class Context(unittest.TestCase):
    def rules(self):
        return SimpleNamespace(context3={}, context4={}, letters={'.': Counter({(8, 0, 3): 1}),
                               'p': Counter({(99, 0, 3): 100})},
                               pair=SimpleNamespace(behavior_name=lambda mt: 'DOOR' if mt == 100 else 'NORMAL'))

    def test_exact_context_excludes_letter_only_beach_candidate(self):
        rows = ['...', '.p.', '...']
        rules = self.rules()
        rules.context3[blockout.letter_context(rows, 1, 1)] = Counter({(10, 0, 3): 3})
        rules.context4[blockout.letter_context(rows, 1, 1, True)] = Counter({(11, 0, 3): 3})
        choices, _, notes = blockout.context_candidates(rows, rules)
        self.assertEqual(choices[1, 1], [10])
        self.assertTrue(any('3x3:1 4-neighbour:0 letter:8 any-non-door:0 empty:0' in n for n in notes))

    def test_four_neighbour_then_letter_fallback_are_counted(self):
        rows = ['...', '.p.', '...']
        rules = self.rules()
        rules.context4[blockout.letter_context(rows, 1, 1, True)] = Counter({(11, 0, 3): 3})
        choices, _, notes = blockout.context_candidates(rows, rules)
        self.assertEqual(choices[1, 1], [11])
        self.assertTrue(any('3x3:0 4-neighbour:1 letter:8 any-non-door:0 empty:0' in n for n in notes))
        rules.context4.clear()
        choices, _, notes = blockout.context_candidates(rows, rules)
        self.assertEqual(choices[1, 1], [99])
        self.assertTrue(any('letter:9 any-non-door:0 empty:0' in n for n in notes))

    def test_connected_target_edge_is_a_wildcard_not_a_closed_border(self):
        rows = ['.p.', '...', '...']
        rules = self.rules()
        rules.context3['ggg.p....'] = Counter({(10, 0, 3): 3})
        closed, _, _ = blockout.context_candidates(rows, rules)
        opened, _, _ = blockout.context_candidates(rows, rules, {'N'})
        self.assertEqual(closed[1, 0], [99])
        self.assertEqual(opened[1, 0], [10])
        self.assertEqual(opened.levels[1, 0], 0)

    def test_connected_source_edge_matches_known_target_letters(self):
        rules = self.rules()
        rules.context3['***.p....'] = Counter({(10, 0, 3): 3})
        choices, _, _ = blockout.context_candidates(['ggg', '.p.', '...'], rules)
        self.assertEqual(choices[1, 1], [10])

    def test_any_non_door_excludes_doors_and_reports_each_coordinate_step(self):
        rules = self.rules()
        del rules.letters['p']
        rules.letters['b'] = Counter({(100, 0, 3): 50, (101, 1, 3): 1})
        choices, _, notes = blockout.context_candidates(['p'], rules)
        # 101 has collision 1: a walkable letter never takes a blocking tile, even at any-non-door
        # (one lawn cell turned to rock cut Route 22's gate area off).
        self.assertEqual(set(choices[0, 0]), {8})
        self.assertEqual(choices.levels[0, 0], 3)
        steps = [n for n in notes if n.startswith('INFO BLOCKOUT widen 0,0 ')]
        self.assertEqual(len(steps), 3)
        self.assertIn('3x3->4-neighbour', steps[0])
        self.assertIn('4-neighbour->letter', steps[1])
        self.assertIn('letter->any-non-door', steps[2])

    def test_one_empty_domain_widens_only_that_cell_one_step(self):
        rows = ['...', '.p.', '...']
        rules = self.rules()
        rules.context3[blockout.letter_context(rows, 1, 1)] = Counter({(10, 0, 3): 3})
        rules.context4[blockout.letter_context(rows, 1, 1, True)] = Counter({(11, 0, 3): 3})
        choices, bits, _ = blockout.context_candidates(rows, rules)
        original_levels = dict(choices.levels)
        def support(repo, grid, pair, rows, values, bits, exhausted):
            if values.levels[1, 1] == 0:
                values[1, 1] = []
            return []
        with patch.object(blockout, 'supported_contexts', side_effect=support):
            notes = blockout.resolve_contexts(None, None, None, rows, choices, bits)
        self.assertEqual(choices[1, 1], [11])
        for p, level in original_levels.items():
            self.assertEqual(choices.levels[p], level + int(p == (1, 1)))
        self.assertEqual(sum(n.startswith('INFO BLOCKOUT widen 1,1 ') for n in notes), 1)

    def test_support_exhaustion_reaches_any_non_door_without_aborting_rest(self):
        rows = ['...', '.p.', '...']
        rules = self.rules()
        rules.context3[blockout.letter_context(rows, 1, 1)] = Counter({(10, 0, 3): 3})
        rules.context4[blockout.letter_context(rows, 1, 1, True)] = Counter({(11, 0, 3): 3})
        choices, bits, _ = blockout.context_candidates(rows, rules)
        def support(repo, grid, pair, rows, values, bits, exhausted):
            if values.levels[1, 1] < 3:
                values[1, 1] = []
            return []
        with patch.object(blockout, 'supported_contexts', side_effect=support):
            notes = blockout.resolve_contexts(None, None, None, rows, choices, bits)
        self.assertEqual(choices.levels[1, 1], 3)
        self.assertTrue(all(choices.values()))
        self.assertEqual(sum(n.startswith('INFO BLOCKOUT widen 1,1 ') for n in notes), 3)

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
        self.rules = SimpleNamespace(material=lambda cell: 'T' if cell[0] == 1 else '.', freq={})
        self.vocab = wfc.Vocabulary({(8, 1, 9, 9, 1, 9, 9, 1, 9): 1}, 'test')
        return grid, rows, choices, bits, fill

    def test_wfc_full_forest_plus_mutable_lawn_b_frozen(self):
        grid, rows, choices, bits, fill = self.setup_fill()
        with patch.object(blockout, 'learning_reference', return_value=object()), \
             patch('pk.maps.wfc.learn', return_value=self.vocab) as learn, \
             patch('pk.maps.wfc.fill', return_value=fill) as solve, \
             patch.object(blockout, 'hard_solve', return_value=dict(ok=True, assignment=dict(fill), steps=1)), \
             patch.object(blockout, 'HardObjects', return_value=SimpleNamespace(validates=lambda a: True)):
            notes = blockout.forest(object(), grid, object(), rows, self.rules, choices, bits,
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
             patch('pk.maps.wfc.learn', return_value=self.vocab), \
             patch('pk.maps.wfc.fill', return_value=fill), \
             patch.object(blockout.terrain, 'solve', return_value=dict(fill)) as solve, \
             patch.object(blockout, 'hard_solve', return_value=dict(ok=True, assignment=dict(fill), steps=1)), \
             patch.object(blockout, 'HardObjects', return_value=SimpleNamespace(validates=lambda a: True)):
            notes = blockout.forest(object(), grid, object(), rows, self.rules, choices, bits)
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
                               neighbour=lambda *a: (8, True),
                               broken_side=lambda *a: None)
        with patch.object(blockout.objects, 'Glue', return_value=glue):
            notes = blockout.supported_contexts(object(), grid, object(), rows, choices, bits)
        self.assertEqual(choices[0, 1], [2])
        self.assertTrue(any('empty-cells:0' in n for n in notes))

    def test_connected_side_skips_pruning_but_closed_side_uses_glue(self):
        rows = ['...', '...', '...']
        grid = self.Grid()
        for connected in (False, True):
            choices = {(x, y): [8] for y in range(3) for x in range(3)}
            bits = {p: {8: (8, 0, 3)} for p in choices}
            glue = SimpleNamespace(raw={}, objectish=lambda cell: False,
                neighbour=lambda x, y, side: None if connected and side == 'N' else (14, True),
                broken_side=lambda x, y, side, *a: True if side == 'N' else None)
            with patch.object(blockout.objects, 'Glue', return_value=glue):
                blockout.supported_contexts(None, grid, None, rows, choices, bits)
            self.assertEqual(bool(choices[1, 0]), connected)


class HardConstraints(unittest.TestCase):
    class Grid:
        def __init__(self, w=2, h=6):
            self.w, self.h, self.data = w, h, {}
            self.cells = [(1 if y % 2 == 0 else 2, 1, 0) for y in range(h) for x in range(w)]

        def at(self, x, y):
            return self.cells[y * self.w + x]

        def set(self, x, y, cell):
            self.cells[y * self.w + x] = cell

        def inside(self, x, y):
            return 0 <= x < self.w and 0 <= y < self.h

    class Glue:
        """Tiny learned-object contract: crown/trunk alternate; rows agree."""
        def __init__(self, repo, grid, pair):
            self.grid = grid

        def objectish(self, cell):
            return cell[0] in (1, 2)

        def broken_side(self, x, y, side, cell=None):
            cell = cell or self.grid.at(x, y)
            dx, dy = blockout.objects.SIDES[side]
            q = x + dx, y + dy
            if not self.grid.inside(*q):
                return None
            a, b = cell[0], self.grid.at(*q)[0]
            if a in (1, 2) and b in (1, 2):
                return True if (a != b if dx else a == b) else None
            if (a == 1 and side == 'S') or (a == 2 and side == 'N'):
                return True
            return None

    def fixture(self, h=6):
        grid = self.Grid(h=h)
        rows = ['TT'] * h
        choices = {(x, y): [1, 2] for y in range(h) for x in range(2)}
        bits = {p: {1: (1, 1, 0), 2: (2, 1, 0)} for p in choices}
        rules = SimpleNamespace(material=lambda cell: 'T' if cell[0] in (1, 2) else '.',
                                freq={1: 10, 2: 10}, letters={'T': Counter({(1, 1, 0): 10, (2, 1, 0): 10})})
        return grid, rows, choices, bits, rules

    def warnings(self, repo, grid, pair):
        glue = self.Glue(repo, grid, pair)
        return [f'WARN OBJECT @{x},{y} {side}' for y in range(grid.h) for x in range(grid.w)
                for side in blockout.objects.SIDES if glue.broken_side(x, y, side) is not None]

    def test_whole_connected_region_keeps_two_row_rhythm(self):
        grid, rows, choices, bits, rules = self.fixture()
        for x in range(2):
            grid.set(x, 3, (1, 1, 0))
        original = list(grid.cells)
        with patch.object(blockout.objects, 'Glue', self.Glue):
            result = blockout.hard_solve(None, grid, None, rows, rules, choices, bits)
        self.assertTrue(result['ok'], result)
        self.assertEqual(grid.cells, original)  # search itself never mutates
        blockout.apply_candidates(grid, result['assignment'], choices, bits)
        self.assertEqual(self.warnings(None, grid, None), [])

    def test_no_object_rule_escape_for_broad_but_incompatible_candidates(self):
        grid, rows, choices, bits, rules = self.fixture(h=2)
        choices = {p: [1] for p in choices}
        original = list(grid.cells)
        with patch.object(blockout.objects, 'Glue', self.Glue):
            result = blockout.hard_solve(None, grid, None, rows, rules, choices, bits, letters=False)
        self.assertFalse(result['ok'])
        self.assertEqual(grid.cells, original)

    def test_solved_neighbour_is_hard_boundary_in_both_directions(self):
        grid, rows, choices, bits, rules = self.fixture()
        with patch.object(blockout.objects, 'Glue', self.Glue):
            result = blockout.hard_solve(None, grid, None, rows, rules, choices, bits, {(0, 1)})
        self.assertTrue(result['ok'])
        self.assertEqual(result['assignment'], {(0, 1): 2})

    def test_local_repair_fixes_3x3_without_changing_letters(self):
        grid, rows, choices, bits, rules = self.fixture()
        for x in range(2):
            grid.set(x, 2, (2, 1, 0))
        with patch.object(blockout.objects, 'Glue', self.Glue), \
             patch.object(blockout.objects, 'check', side_effect=self.warnings):
            notes = blockout.local_repair(None, grid, None, rows, rules, choices, bits)
        self.assertEqual(self.warnings(None, grid, None), [])
        self.assertTrue(any('3x3 fixed:' in row for row in notes), notes)
        self.assertTrue(all(rules.material(cell) == 'T' for cell in grid.cells))

    def test_local_repair_grows_to_5x5_when_phase_conflict_spans_two_rows(self):
        grid, rows, choices, bits, rules = self.fixture(h=8)
        for x in range(2):
            grid.set(x, 3, (1, 1, 0))
            grid.set(x, 4, (2, 1, 0))
        with patch.object(blockout.objects, 'Glue', self.Glue), \
             patch.object(blockout.objects, 'check', side_effect=self.warnings):
            notes = blockout.local_repair(None, grid, None, rows, rules, choices, bits)
        self.assertEqual(self.warnings(None, grid, None), [])
        self.assertTrue(any('3x3 no-fit' in row for row in notes), notes)
        self.assertTrue(any('5x5 fixed:' in row for row in notes), notes)
        self.assertTrue(all(rules.material(cell) == 'T' for cell in grid.cells))

    def test_components_do_not_slice_a_connected_column(self):
        cells = {(0, y) for y in range(20)} | {(3, y) for y in range(4)}
        regions = blockout.connected_regions(cells)
        self.assertEqual(sorted(map(len, regions)), [4, 20])
        self.assertEqual(set().union(*regions), cells)

    def test_each_ledge_row_is_one_joint_solve(self):
        grid = self.Grid(w=5, h=3)
        rows = ['vv.vv', '.....', 'vv...']
        choices = {(x, y): [1] for y in range(3) for x in range(5)}
        bits = {p: {1: (1, 1, 0)} for p in choices}
        calls = []
        def solved(repo, grid, pair, rows, rules, choices, bits, region, **kwargs):
            calls.append({p for p in region if rows[p[1]][p[0]] == 'v'})
            return dict(ok=True, assignment={p: 1 for p in region}, steps=1)
        with patch.object(blockout, 'hard_solve', side_effect=solved):
            blockout.ledge_rows(None, grid, None, rows, None, choices, bits)
        self.assertEqual(sorted(map(len, calls)), [2, 2, 2])
        self.assertEqual(set().union(*calls), {(0, 0), (1, 0), (3, 0), (4, 0), (0, 2), (1, 2)})


class ObjectRegions(unittest.TestCase):
    def test_fence_card_uses_one_cell_thickness_not_names(self):
        line = dict(cls='wall', uses=20, nbr={d: {'wall': n, 'walk': 20 - n}
                    for d, n in [('N', 2), ('S', 1), ('E', 19), ('W', 18)]}, name='tree')
        self.assertTrue(blockout.fence_card(line))
        line['nbr']['S'] = {'wall': 20}
        self.assertFalse(blockout.fence_card(line))  # two-row object, not a line
        line['uses'] = 1
        self.assertFalse(blockout.fence_card(line))

    def test_fence_is_exported_separately_from_tree(self):
        repo = Repo(ROOT)
        grid, pair = load(repo, 'Route1_Frlg')
        classify = blockout.classifier(repo, pair)
        self.assertEqual(classify.letter((0x0E7, 1, 0)), 'n')
        self.assertEqual(classify.letter((0x01C, 1, 0)), 'T')
        rows = classify.export(grid)
        self.assertIn('n', ''.join(rows))
        self.assertEqual(blockout.parse(blockout.serialize(rows), grid.w, grid.h), rows)

    def test_two_cell_erosion_keeps_outline_and_holes_in_band(self):
        component = {(x, y) for y in range(12) for x in range(12)}
        self.assertEqual(len(blockout.region_interior(component)), 64)
        component.remove((6, 6))
        interior = blockout.region_interior(component)
        self.assertNotIn((4, 4), interior)
        self.assertIn((2, 2), interior)

    def test_interior_dominant_learned_context_respects_every_domain(self):
        grid = HardConstraints.Grid(w=12, h=12)
        component = {(x, y) for y in range(12) for x in range(12)}
        rows = ['r' * 12] * 12
        choices = {p: [3, 4] for p in component}
        bits = {p: {3: (3, 1, 0), 4: (4, 1, 0)} for p in component}
        rules = SimpleNamespace(context3={'r' * 9: Counter({(3, 1, 0): 90, (4, 1, 0): 10})},
                                material=lambda cell: 'r')
        model = SimpleNamespace(pair_ok=lambda *a: True)
        with patch.object(blockout, 'HardObjects', return_value=model):
            plan, detail = blockout.interior_plan(None, grid, None, rows, rules, choices, bits, component, 'r')
            self.assertEqual(len(plan), 64)
            self.assertEqual(set(plan.values()), {3})
            choices[5, 5] = [4]
            plan, _ = blockout.interior_plan(None, grid, None, rows, rules, choices, bits, component, 'r')
            self.assertEqual(set(plan.values()), {4})
        self.assertIn('dominant:0x003', detail)

    def test_all_object_letters_are_whole_connected_components(self):
        grid = HardConstraints.Grid(w=5, h=3)
        rows = ['rr.nn', 'rr.TT', '...TT']
        choices = {(x, y): [8] for y in range(3) for x in range(5)}
        bits = {p: {8: (8, 0, 0)} for p in choices}
        calls = []
        rules = SimpleNamespace(material=lambda cell: '.', context3={})
        # Record before the deliberately empty letter domains are rejected.
        original = blockout.connected_regions
        def components(cells, *args):
            if cells:
                calls.append(set(cells))
            return original(cells, *args)
        with patch.object(blockout, 'connected_regions', side_effect=components), \
             patch.object(blockout, 'learning_reference', side_effect=PkError('TEST', 'skip WFC')):
            notes = blockout.object_regions(None, grid, None, rows, rules, choices, bits)
        self.assertEqual(sorted(map(len, calls)), [2, 4, 4])
        self.assertTrue(any('connected-regions:3' in row for row in notes))

    def test_large_rock_solves_band_only_and_rolls_back_on_failure(self):
        for ok in (True, False):
            grid = HardConstraints.Grid(w=12, h=12)
            before = list(grid.cells)
            rows = ['r' * 12] * 12
            choices = {(x, y): [3] for y in range(12) for x in range(12)}
            bits = {p: {3: (3, 1, 0)} for p in choices}
            rules = SimpleNamespace(context3={'r' * 9: Counter({(3, 1, 0): 20})},
                                    material=lambda cell: 'r')
            calls = []
            def solved(repo, grid, pair, rows, rules, choices, bits, region, **kwargs):
                calls.append(set(region))
                return dict(ok=ok, assignment={p: 3 for p in region}, steps=42, reason='test refusal')
            model = SimpleNamespace(pair_ok=lambda *a: True, validates=lambda *a: True)
            with patch.object(blockout, 'learning_reference', side_effect=PkError('TEST', 'skip WFC')), \
                 patch.object(blockout, 'HardObjects', return_value=model), \
                 patch.object(blockout.terrain, 'solve', return_value={p: 3 for p in choices}), \
                 patch.object(blockout, 'hard_solve', side_effect=solved):
                notes = blockout.object_regions(None, grid, None, rows, rules, choices, bits)
            self.assertEqual([len(c) for c in calls], [80])
            self.assertTrue(any('size:144 interior:64 boundary/rim:80' in n for n in notes))
            if ok:
                self.assertEqual(set(grid.cells), {(3, 1, 0)})
            else:
                self.assertEqual(grid.cells, before)


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
        audit = f'.pk/render/blockout_{name}.txt'
        output = Transaction(repo, 'blockout benchmark render only')
        output.stage(render, render_compare(repo, (original, pair), (grid, pair)))
        output.stage(audit, ('command: python tools/pk/tests/test_blockout.py --benchmark' +
                     (' --rebuilds-only' if rebuilds_only else '') + '\n' +
                     f'{name} fidelity:{report["fidelity"]:.2%} new_OBJECT:{added} '
                     f'original_reach_lost:{sum(losses.values())} open_bank:{report["open_bank"]}\n' +
                     '\n'.join(report['rows']) + '\n').encode('utf-8'))
        output.commit(False)
        ok = added == 0 and not losses and report['fidelity'] >= .95 and not report['open_bank']
        print(f'retile {name} new_OBJECT:{added} reach_lost:{sum(losses.values())} '
              f'fidelity:{report["fidelity"]:.2%} open_bank:{report["open_bank"]} pass:{ok} render:{render} audit:{audit}', flush=True)
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
