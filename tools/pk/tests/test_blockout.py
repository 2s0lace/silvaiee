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


_SHARED = []


def shared_repo():
    """One Repo for the tests that build the learned rules (about two minutes each time)."""
    if not _SHARED:
        _SHARED.append(Repo(ROOT))
    return _SHARED[0]


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


class EdgeOrientation(unittest.TestCase):
    """Edge, corner and mixed tiles (tile card family not open) are half-and-half: letters cannot say which side
    carries the rim, so they only stand where a vanilla 3x3 / 4-neighbour letter pattern matched."""
    def rules(self, cards_, letters):
        return SimpleNamespace(
            context3={}, context4={}, letters=letters, classify=SimpleNamespace(cards=cards_),
            pair=SimpleNamespace(behavior_name=lambda mt: 'NORMAL'))

    def domains(self, cards_):
        rows = ['...', '.p.', '...']
        letters = {'.': Counter({(8, 0, 3): 1}),
                   'p': Counter({(10, 0, 3): 3, (20, 0, 3): 2, (30, 0, 3): 9})}
        rules = self.rules(cards_, letters)
        rules.context3[blockout.letter_context(rows, 1, 1)] = Counter({(10, 0, 3): 3})
        return blockout.ContextDomains(rows, rules, frozenset()), rows

    def test_letter_and_any_levels_offer_open_tiles_only_plus_the_cells_own_matches(self):
        cards_ = {8: {'family': 'open'}, 10: {'family': 'edge'}, 20: {'family': 'open'}, 30: {'family': 'corner'}}
        choices, _ = self.domains(cards_)
        self.assertEqual(choices[1, 1], [10])
        choices.activate((1, 1), 2)
        self.assertEqual(sorted(choices[1, 1]), [10, 20])              # 30 is an edge tile with no pattern match
        choices.activate((1, 1), 3)
        self.assertNotIn(30, choices[1, 1])                            # nor does any-non-door bring it back

    def test_without_cards_every_tile_stays_available(self):
        choices, _ = self.domains(None)
        choices.activate((1, 1), 2)
        self.assertEqual(sorted(choices[1, 1]), [10, 20, 30])

    def test_a_letter_with_no_open_tile_keeps_edges_and_is_reported(self):
        cards_ = {8: {'family': 'open'}, 10: {'family': 'edge'}, 20: {'family': 'edge'}, 30: {'family': 'edge'}}
        choices, _ = self.domains(cards_)
        choices.activate((1, 1), 2)
        self.assertEqual(sorted(choices[1, 1]), [10, 20, 30])
        rows = [r for r in choices.reshape() if ' 1,1 ' in r]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].startswith('WARN BLOCKOUT reshape 1,1 (no vanilla edge for this letter pattern)'))
        self.assertIn('no open tile of its letter either', rows[0])

    def test_reshape_lists_only_cells_widened_to_letter_level_or_beyond(self):
        cards_ = {8: {'family': 'open'}, 10: {'family': 'edge'}, 20: {'family': 'open'}, 30: {'family': 'corner'}}
        choices, _ = self.domains(cards_)
        choices.levels = {p: 0 for p in choices.levels}
        self.assertEqual(choices.reshape(), [])
        choices.activate((1, 1), 1)
        self.assertEqual(choices.reshape(), [])
        choices.activate((1, 1), 2)
        self.assertEqual(len([r for r in choices.reshape() if ' 1,1 ' in r]), 1)

    def test_wrong_context_is_counted_for_non_open_tiles_only(self):
        rows = ['...', '.p.', '...']
        grid = HardConstraints.Grid(w=3, h=3)
        grid.cells = [(8, 0, 3)] * 9
        grid.set(1, 1, (10, 0, 3))
        good = blockout.letter_context(rows, 1, 1, True)
        rules = SimpleNamespace(classify=SimpleNamespace(cards={8: {'family': 'open'}, 10: {'family': 'edge'}}),
                                tile_context4={10: {good}})
        self.assertEqual(blockout.edge_orientation(grid, rows, rules, frozenset()), (1, []))
        rules.tile_context4 = {10: {'.pppp'}}                          # vanilla only puts it on lawn
        checked, bad = blockout.edge_orientation(grid, rows, rules, frozenset())
        self.assertEqual(checked, 1)
        self.assertTrue(bad[0].startswith('WARN BLOCKOUT edge context 1,1 0x00A'))
        rules.tile_context4 = {10: {'*' * 5}}                          # a connected side is a wildcard
        self.assertEqual(blockout.edge_orientation(grid, rows, rules, frozenset())[1], [])

    def test_remnant_cells_get_open_tiles_not_edge_tiles_chosen_one_by_one(self):
        grid = HardConstraints.Grid(w=1, h=1)
        grid.cells = [(8, 0, 3)]
        rules = SimpleNamespace(classify=SimpleNamespace(cards={10: {'family': 'edge'}, 20: {'family': 'open'}}))
        domains = {(0, 0): [10, 20]}
        bits = {(0, 0): {10: (10, 0, 3), 20: (20, 0, 3)}}
        seen = []

        def solve(grid, rules, letters, **kwargs):
            seen.append(kwargs['choices'])
            return {(0, 0): 20}
        fail = dict(ok=False, assignment={}, steps=1, reason='hard contradiction', conflict=[])
        glue = SimpleNamespace(broken_side=lambda *a, **k: None)
        with patch.object(blockout, 'hard_solve', return_value=fail), \
             patch.object(blockout.terrain, 'solve', side_effect=solve), \
             patch.object(blockout.objects, 'Glue', return_value=glue), \
             patch.object(blockout.objects, 'count_in', return_value=0):
            blockout.sectioned_solve(None, grid, None, ['.'], rules, domains, domains, bits, {(0, 0)}, {},
                                     True, 'rest')
        self.assertEqual(seen, [{(0, 0): [20]}])

    def test_a_tile_vanilla_requires_beside_an_object_is_allowed_at_letter_level(self):
        rows = ['T', '.']
        cards_ = {8: {'family': 'open'}, 7: {'family': 'mixed'}, 50: {'family': 'mixed'}}
        letters = {'T': Counter({(7, 1, 0): 5}), '.': Counter({(8, 0, 3): 1, (50, 0, 3): 4})}
        rules = self.rules(cards_, letters)
        rules.classify.raw = {7: {'nmt': {'N': Counter(), 'E': Counter(), 'W': Counter(), 'S': Counter({50: 20})}}}
        rules.context3[blockout.letter_context(rows, 0, 1)] = Counter({(8, 0, 3): 1})
        choices = blockout.ContextDomains(rows, rules, frozenset())
        self.assertEqual(choices.socket_tiles((0, 1)), {50})
        choices.activate((0, 1), 2)
        self.assertEqual(sorted(choices[0, 1]), [8, 50])          # 50 is not open, but the tree above wants it
        self.assertEqual(choices.socket_tiles((0, 0)), set())

    def test_no_open_tile_takes_the_edge_whose_vanilla_contexts_are_nearest(self):
        rows = ['...', '.p.', '...']
        cards_ = {8: {'family': 'open'}, 10: {'family': 'edge'}, 30: {'family': 'edge'}}
        letters = {'.': Counter({(8, 0, 3): 1}), 'p': Counter({(10, 0, 3): 3, (30, 0, 3): 9})}
        rules = self.rules(cards_, letters)
        rules.material = lambda cell: 'p' if cell[0] in (10, 30) else '.'
        # 10 is used with lawn all round except one side; 30 with beach all round
        rules.tile_context4 = {10: {'p...p'}, 30: {'ppppp'}}
        choices = blockout.ContextDomains(rows, rules, frozenset())
        choices.activate((1, 1), 2)
        self.assertEqual(choices[1, 1], [10])
        self.assertTrue(choices.no_open[1, 1])

    def test_an_unlocked_object_cell_is_offered_every_tile_of_its_letter(self):
        cards_ = {8: {'family': 'open'}, 10: {'family': 'edge'}, 20: {'family': 'open'}, 30: {'family': 'corner'}}
        choices, _ = self.domains(cards_)
        choices.rules.material = None
        choices.material = lambda cell: 'p' if cell[0] in (10, 20, 30) else '.'
        choices.activate((1, 1), 2)
        self.assertNotIn(30, choices[1, 1])
        choices.unlocked.add((1, 1))
        choices.activate((1, 1), 2)
        self.assertEqual(sorted(choices[1, 1]), [10, 20, 30])
        self.assertIn(30, choices.extra[1, 1])                     # offered, but tried last

    def test_edge_check_says_why_a_wrong_context_could_not_be_avoided(self):
        rows = ['...', '.p.', '...']
        grid = HardConstraints.Grid(w=3, h=3)
        grid.cells = [(8, 0, 3)] * 9
        grid.set(1, 1, (10, 0, 3))
        rules = SimpleNamespace(classify=SimpleNamespace(cards={8: {'family': 'open'}, 10: {'family': 'edge'}}),
                                tile_context4={10: {'.pppp'}})
        for state, label in (('no_open', '[no open tile of its letter]'), ('unlocked', '[object edge no pattern fits]'),
                             ('pinned', '[whole vanilla object]'), (None, '[misplaced]')):
            choices = SimpleNamespace(no_open=Counter(), unlocked=set(), pinned=set(),
                                      socket_tiles=lambda p: set())
            if state == 'no_open':
                choices.no_open[1, 1] += 1
            elif state:
                getattr(choices, state).add((1, 1))
            _, bad = blockout.edge_orientation(grid, rows, rules, frozenset(), choices)
            self.assertTrue(bad[0].endswith(label), (state, bad))

    def test_thin_objects_are_not_trees(self):
        def card(**shares):
            return dict(cls='wall', uses=40, nbr={d: {'wall': round(100 * shares[d]), 'walk': 100 - round(100 * shares[d])}
                                                   for d in 'NESW'})
        self.assertTrue(blockout.fence_card(card(N=0, E=0, S=0, W=0)))        # a post or a one-cell bush
        self.assertTrue(blockout.fence_card(card(N=.1, E=.9, S=.1, W=.9)))    # a hedge line
        self.assertFalse(blockout.fence_card(card(N=.6, E=.9, S=1., W=.7)))   # a tree body
        self.assertFalse(blockout.fence_card(dict(card(N=0, E=0, S=0, W=0), uses=1)))
        self.assertTrue(blockout.fence_card(dict(card(N=0, E=0, S=0, W=0), uses=1), min_uses=1))

    def test_a_green_rock_top_is_rock_a_tree_beside_trees_stays_a_tree(self):
        classifier = blockout.Classifier.__new__(blockout.Classifier)
        classifier.memo = {}
        classifier.cards = {2: dict(cls='wall'), 3: dict(cls='wall')}
        classifier.raw = {1: {'nmt': {'N': Counter(), 'E': Counter({2: 8}), 'S': Counter({2: 9}), 'W': Counter()}},
                          4: {'nmt': {'N': Counter(), 'E': Counter({3: 8}), 'S': Counter({3: 9}), 'W': Counter()}}}
        classifier.base = {(1, 1): 'T', (4, 1): 'T', (2, 1): 'r', (3, 1): 'T'}
        self.assertEqual(classifier.letter((1, 1, 0)), 'r')
        self.assertEqual(classifier.letter((4, 1, 0)), 'T')


class WaterFamilies(unittest.TestCase):
    """One water family per connected water region; the sea shore against sand is a vanilla shore."""
    class Choices(dict):
        """The bits of ContextDomains that restrict_water and small_tree_region use."""
        def __init__(self, domains, ladder):
            super().__init__(domains)
            self.ladder, self.levels = ladder, {p: 0 for p in domains}

        def widen(self, p, reason):
            self.levels[p] += 1
            self[p] = self.ladder[self.levels[p]]
            return f'INFO BLOCKOUT widen {p[0]},{p[1]} test->test candidates:{len(self[p])} reason:{reason}'

    def test_shallow_water_shore_is_sand_not_path(self):
        repo = Repo(ROOT)
        _, pair = load(repo, 'PalletTown_Frlg')
        classify = blockout.classifier(repo, pair)
        for mt in (0x10C, 0x11D, 0x114, 0x116, 0x12E):   # sand edges drawn with shallow water behaviour
            with self.subTest(mt=hex(mt)):
                self.assertEqual(classify.letter((mt, 0, 3)), 's')
        self.assertEqual(classify.letter((0x12B, 0, 1)), '~')

    def test_kind_is_sea_when_touching_the_edge_or_large_else_pond(self):
        edge = ['~~...', '.....', '.....']
        self.assertEqual([k for _, k in blockout.water_bodies(edge, 5, 3)], ['sea'])
        pond = ['.....', '.~~..', '.....']
        self.assertEqual([k for _, k in blockout.water_bodies(pond, 5, 3)], ['pond'])
        lake = ['.' * 12] + ['.' + '~' * 10 + '.'] * 5 + ['.' * 12]      # 50 cells, enclosed
        self.assertEqual([k for _, k in blockout.water_bodies(lake, 12, 7)], ['sea'])

    def test_each_region_is_cut_to_its_family_and_its_shore_ring(self):
        rows = ['.....', '.~~~.', '.....', '.....', '..~..']
        grid = SimpleNamespace(w=5, h=5)
        rules = SimpleNamespace(water_family=lambda kind: (1, frozenset({1, 2}), frozenset({8})) if kind == 'pond'
                                else (3, frozenset({3}), frozenset({9})))
        active = {(x, y): [1, 2, 3, 8, 9] for y in range(5) for x in range(5)}
        notes, families = blockout.restrict_water(rules, rows, grid, active)
        self.assertEqual(active[2, 1], [1, 2])              # pond water: its own family only
        self.assertEqual(active[2, 0], [8])                 # shore ring: what vanilla puts next to the pond
        self.assertEqual(active[2, 4], [3])                 # touches the edge: the sea family
        self.assertEqual(active[1, 4], [9])
        self.assertEqual(active[0, 2], [1, 2, 3, 8, 9])     # not next to water: untouched
        self.assertEqual({k: v[1] for k, v in families.items()}, {1: 'pond', 2: 'sea'})
        self.assertTrue(any('region:1 size:3 kind:pond family:0x001' in n for n in notes))

    def test_a_cell_with_no_family_tile_widens_its_context_before_anything_else(self):
        rows = ['~.', '..']
        grid = SimpleNamespace(w=2, h=2)
        rules = SimpleNamespace(water_family=lambda kind: (1, frozenset({1}), frozenset({8})))
        domains = self.Choices({(0, 0): [5, 6]}, {1: [1, 5]})
        active = {(0, 0): [5, 6]}
        notes, _ = blockout.restrict_water(rules, rows, grid, active, domains)
        self.assertEqual(active[0, 0], [1])
        self.assertTrue(any('INFO BLOCKOUT widen 0,0' in n and 'water region:1' in n for n in notes))
        self.assertTrue(any('widened-cells:1 unrestricted-cells:0' in n for n in notes))

    def test_without_widening_left_a_cell_unrestricted_is_reported_not_silent(self):
        rows = ['~.', '..']
        rules = SimpleNamespace(water_family=lambda kind: (1, frozenset({1}), frozenset({8})))
        active = {(0, 0): [5, 6]}
        notes, _ = blockout.restrict_water(rules, rows, SimpleNamespace(w=2, h=2), active)
        self.assertEqual(active[0, 0], [5, 6])
        self.assertTrue(any('unrestricted-cells:1' in n for n in notes))
        self.assertTrue(any('kept an unrestricted domain' in n for n in notes))

    def test_family_is_one_open_tile_plus_the_banks_that_belong_to_it(self):
        rules = SimpleNamespace(
            water_open={1, 4}, water_kind={'sea': Counter({1: 90, 4: 30, 2: 5}), 'pond': Counter()},
            # 2 touches the main open tile most; 3 touches the deep tile most; 5 touches no open tile at all
            # (a rim corner between rim pieces) and follows its neighbour 2.
            water_adj={'sea': {1: Counter({2: 9, 3: 1}), 2: Counter({1: 5, 4: 1, 5: 4}), 3: Counter({4: 6, 1: 1}),
                               4: Counter({3: 6}), 5: Counter({2: 4})}, 'pond': {}},
            ring_adj={'sea': {1: {10}, 2: {11}, 3: {12}, 4: {13}, 5: {14}}, 'pond': {}})
        main, allowed, shore = blockout.family_of(rules, 'sea')
        self.assertEqual(main, 1)
        self.assertEqual(allowed, {1, 2, 5})
        self.assertEqual(shore, {10, 11, 14})
        self.assertIsNone(blockout.family_of(rules, 'pond'))

    def test_a_hard_contradiction_widens_the_conflicting_cell_before_the_window_splits(self):
        grid = HardConstraints.Grid(w=1, h=1)
        grid.cells = [(8, 0, 3)]
        domains = self.Choices({(0, 0): [8]}, {1: [8, 9]})
        active = {(0, 0): [8]}
        bits = {(0, 0): {8: (8, 0, 3), 9: (9, 0, 3)}}
        replies = [dict(ok=False, assignment={}, steps=1, reason='hard contradiction', conflict=[(0, 0)]),
                   dict(ok=True, assignment={(0, 0): 9}, steps=1, reason='', conflict=[])]
        with patch.object(blockout, 'hard_solve', side_effect=replies) as solve:
            solved, notes = blockout.sectioned_solve(None, grid, None, ['.'], SimpleNamespace(), active, active,
                                                     bits, {(0, 0)}, {}, True, 'rest', domains, {})
        self.assertEqual(solve.call_count, 2)
        self.assertEqual(solved, {(0, 0)})
        self.assertEqual(grid.at(0, 0), (9, 0, 3))
        self.assertTrue(any(n.startswith('INFO BLOCKOUT widen 0,0') and 'rest window' in n for n in notes))

    def test_widen_cell_keeps_the_family_restriction(self):
        domains = self.Choices({(0, 0): [5]}, {1: [1, 2, 5], 2: [1, 2, 5], 3: [1, 2, 5]})
        active = {(0, 0): [5]}
        row = blockout.widen_cell(domains, active, {(0, 0): frozenset({1, 2})}, (0, 0), 'test')
        self.assertTrue(row.startswith('INFO BLOCKOUT widen 0,0'))
        self.assertEqual(active[0, 0], [1, 2])
        blockout.widen_cell(domains, active, {}, (0, 0), 'test')
        blockout.widen_cell(domains, active, {}, (0, 0), 'test')
        self.assertIsNone(blockout.widen_cell(domains, active, {}, (0, 0), 'test'))   # ladder exhausted

    def test_report_counts_open_water_families_per_region(self):
        rules = SimpleNamespace(water_open={1, 4})
        grid = HardConstraints.Grid(w=2, h=1)
        body = {(0, 0), (1, 0)}
        grid.cells = [(1, 0, 1), (1, 0, 1)]
        notes, bad = blockout.water_report(rules, grid, {1: (body, 'sea', 1, frozenset({1, 2}))})
        self.assertEqual(bad, 0)
        self.assertIn('families-used:1 outside-family:none', notes[0])
        grid.cells = [(1, 0, 1), (4, 0, 1)]            # a second open tile (deep sea) in the same region
        notes, bad = blockout.water_report(rules, grid, {1: (body, 'sea', 1, frozenset({1, 2}))})
        self.assertEqual(bad, 1)
        self.assertIn('families-used:2 outside-family:0x004', notes[0])

    def solve_shore(self, water_tiles, open_water):
        class Glue:
            def __init__(self, *args):
                pass

            def objectish(self, cell):
                return False

            def broken_side(self, *args, **kwargs):
                return None
        grid = HardConstraints.Grid(w=1, h=2)
        rows = ['~', 's']
        choices = {(0, 0): water_tiles, (0, 1): [10]}
        bits = {(0, 0): {mt: (mt, 0, 1) for mt in water_tiles}, (0, 1): {10: (10, 0, 3)}}
        rules = SimpleNamespace(material=lambda cell: '~' if cell[0] < 10 else 's', freq={}, h={},
                                v={(1, 10): 5, (2, 10): 5}, water_open=open_water)
        with patch.object(blockout.objects, 'Glue', Glue):
            return blockout.hard_solve(None, grid, None, rows, rules, choices, bits)

    def test_a_beach_cell_no_vanilla_shore_fits_is_relaxed_but_never_to_a_rim(self):
        try:
            blockout.SHORE_RELAX['on'] = True
            plain = self.solve_shore([3], {3})        # open water, but vanilla never puts it beside this sand tile
            rim = self.solve_shore([2], {3})          # a rim: still refused
        finally:
            blockout.SHORE_RELAX['on'], blockout.SHORE_RELAX['cells'] = False, set()
        self.assertTrue(plain['ok'])
        self.assertFalse(rim['ok'])
        self.assertFalse(self.solve_shore([3], {3})['ok'])        # off again outside a tiling run

    def test_sand_next_to_water_needs_open_water_a_rim_never_sits_on_a_beach(self):
        self.assertEqual(self.solve_shore([1, 2], {1})['assignment'][0, 0], 1)
        refused = self.solve_shore([2], {1})
        self.assertFalse(refused['ok'])
        # Without water data (older rules, other fixtures) the old pair rule alone applies.
        self.assertTrue(self.solve_shore([2], None)['ok'])


class WaterLearned(unittest.TestCase):
    """Real vanilla data (builds the learned rules once, about two minutes)."""
    @classmethod
    def setUpClass(cls):
        cls.repo = shared_repo()
        _, cls.pair = load(cls.repo, 'PalletTown_Frlg')
        cls.rules = blockout.learned(cls.repo, cls.pair)

    def test_sea_family_is_one_open_tile_with_its_banks(self):
        main, allowed, shore = self.rules.water_family('sea')
        self.assertIn(main, self.rules.water_open)
        self.assertEqual(allowed & self.rules.water_open, {main})   # a deep open tile is another family
        self.assertTrue(allowed - {main}, 'banks vanilla puts next to the open tile')
        self.assertTrue(shore)

    def test_open_water_comes_from_cards_not_names(self):
        cards_ = blockout.classifier(self.repo, self.pair).cards
        self.assertTrue(self.rules.water_open)
        for mt in self.rules.water_open:
            self.assertEqual((cards_[mt]['cls'], cards_[mt]['family']), ('surf', 'open'))

    def test_vanilla_small_trees_are_wall_rectangles(self):
        self.assertTrue(self.rules.tree_templates)
        cards_ = blockout.classifier(self.repo, self.pair).cards
        for tpl in self.rules.tree_templates:
            self.assertLessEqual(len(tpl), blockout.SMALL_TREE)
            self.assertTrue(all(cards_[cell[0]]['cls'] == 'wall' for _, _, cell in tpl))
        # a 2x2 tree: the first cell, the one to its right, and the two below
        self.assertTrue(any({(dx, dy) for dx, dy, _ in tpl} == {(0, 0), (1, 0), (0, 1), (1, 1)}
                            for tpl in self.rules.tree_templates))


class SmallTrees(unittest.TestCase):
    """A small T group is filled with whole vanilla trees or left as lawn; never fence posts or stumps."""
    @staticmethod
    def shape(rows):
        return blockout.shape_template({(x, y): cell for y, row in enumerate(rows) for x, cell in enumerate(row)
                                        if cell})

    TREE = shape.__func__((((30, 1, 0), (31, 1, 0)), ((38, 1, 0), (39, 1, 0))))
    PAIR = shape.__func__((((68, 1, 0), (70, 1, 0)),))
    ELL = shape.__func__((((5, 1, 0), None), ((5, 1, 0), (5, 1, 0))))

    def test_covers_match_the_shape_of_a_vanilla_object_exactly(self):
        templates = Counter({self.PAIR: 99, self.TREE: 5, self.ELL: 2})
        tree = blockout.tree_covers({(1, 1), (2, 1), (1, 2), (2, 2)}, Counter({self.TREE: 5, self.ELL: 2}))
        self.assertEqual(len(tree), 1)
        self.assertEqual(sorted(c[0] for c in tree[0].values()), [30, 31, 38, 39])
        pair = blockout.tree_covers({(4, 0), (5, 0)}, templates)
        self.assertEqual([sorted(c[0] for c in cover.values()) for cover in pair], [[68, 70]])
        ell = blockout.tree_covers({(0, 0), (0, 1), (1, 1)}, templates)
        self.assertEqual(len(ell), 1)
        self.assertEqual(blockout.tree_covers({(1, 1)}, templates), [])          # no one-cell object was seen
        self.assertEqual(blockout.tree_covers({(0, 0), (0, 1)}, templates), [])  # a vertical pair: not seen either

    def test_two_trees_side_by_side_are_covered_exactly(self):
        templates = Counter({self.TREE: 5})
        region = {(x, y) for x in range(4) for y in range(2)}
        covers = blockout.tree_covers(region, templates)
        self.assertTrue(covers)
        self.assertTrue(all(set(c) == region for c in covers))

    def fixture(self):
        grid = Forest.Grid()
        grid.w = grid.h = 4
        grid.cells = [(8, 0, 3)] * 16
        rows = ['....', '.TT.', '.TT.', '....']
        component = {(1, 1), (2, 1), (1, 2), (2, 2)}
        region = {(x, y) for y in range(4) for x in range(4)}
        forced = {p: [8] for p in region}
        bits = {p: {8: (8, 0, 3)} for p in region}
        rules = SimpleNamespace(material=lambda cell: 'T' if cell[1] else '.', freq={8: 1},
                                letters={'.': Counter({(8, 0, 3): 1})}, tree_templates=Counter())
        glue = SimpleNamespace(broken_side=lambda *a, **k: None)
        return grid, rows, component, region, forced, bits, rules, glue

    def test_no_whole_tree_leaves_lawn_and_says_so(self):
        grid, rows, component, region, forced, bits, rules, glue = self.fixture()
        lawn = set()
        with patch.object(blockout.objects, 'Glue', return_value=glue), \
             patch.object(blockout, 'hard_solve', return_value=dict(
                 ok=True, assignment={p: 8 for p in region}, steps=1, reason='', conflict=[])):
            notes = blockout.small_tree_region(None, grid, None, rows, rules, forced, bits, component,
                                               region, 1, 1, lawn)
        self.assertIn('INFO BLOCKOUT tree group 1,1 left as lawn (no whole tree fits)', notes)
        self.assertEqual(lawn, component)
        self.assertEqual({grid.at(*p) for p in component}, {(8, 0, 3)})

    def test_a_whole_tree_is_pinned_and_the_ring_solved_around_it(self):
        grid, rows, component, region, forced, bits, rules, glue = self.fixture()
        rules.tree_templates = Counter({self.TREE: 3})
        ring = region - component
        lawn = set()
        with patch.object(blockout.objects, 'Glue', return_value=glue), \
             patch.object(blockout, 'hard_solve', return_value=dict(
                 ok=True, assignment={p: 8 for p in ring}, steps=7, reason='', conflict=[])) as solve:
            notes = blockout.small_tree_region(None, grid, None, rows, rules, forced, bits, component,
                                               region, 1, 1, lawn)
        self.assertEqual(solve.call_args.args[7], ring)              # only the ring is solved
        self.assertEqual([grid.at(*p)[0] for p in sorted(component, key=lambda p: (p[1], p[0]))], [30, 31, 38, 39])
        self.assertEqual(lawn, set())
        self.assertTrue(any('whole-tree region:1/1 symbol:T size:4 template:0x01E 0x01F 0x026 0x027' in n
                            for n in notes))

    def test_broken_object_side_rejects_the_tree_and_restores_the_cells(self):
        grid, rows, component, region, forced, bits, rules, glue = self.fixture()
        rules.tree_templates = Counter({self.TREE: 3})
        glue.broken_side = lambda *a, **k: ('top', 1, 2, False) if grid.at(1, 1)[0] == 30 else None
        lawn = set()
        with patch.object(blockout.objects, 'Glue', return_value=glue), \
             patch.object(blockout, 'hard_solve', return_value=dict(
                 ok=True, assignment={p: 8 for p in region - component}, steps=1, reason='', conflict=[])):
            notes = blockout.small_tree_region(None, grid, None, rows, rules, forced, bits, component,
                                               region, 1, 1, lawn)
        self.assertNotIn(30, [c[0] for c in grid.cells])
        self.assertTrue(any('left as lawn' in n for n in notes))

    def test_ring_cell_without_a_tree_tile_widens_one_step(self):
        grid, rows, component, region, forced, bits, rules, glue = self.fixture()
        rules.tree_templates = Counter({self.TREE: 3})
        domains = WaterFamilies.Choices({p: [8] for p in region}, {1: [8, 9]})
        replies = [dict(ok=False, assignment={}, steps=0, reason='empty hard domain', conflict=[(1, 0)]),
                   dict(ok=True, assignment={p: 8 for p in region - component}, steps=2, reason='', conflict=[])]
        bits[1, 0] = {8: (8, 0, 3), 9: (9, 0, 3)}
        with patch.object(blockout.objects, 'Glue', return_value=glue), \
             patch.object(blockout, 'hard_solve', side_effect=replies):
            notes = blockout.small_tree_region(None, grid, None, rows, rules, forced, bits, component,
                                               region, 1, 1, set(), domains)
        self.assertTrue(any(n.startswith('INFO BLOCKOUT widen 1,0') for n in notes))
        self.assertEqual(forced[1, 0], [8, 9])
        self.assertTrue(any('whole-tree region:1/1' in n for n in notes))

    def test_small_regions_route_to_whole_trees(self):
        grid = HardConstraints.Grid(w=3, h=3)
        rows = ['...', '.T.', '...']
        choices = {(x, y): [8] for y in range(3) for x in range(3)}
        bits = {p: {8: (8, 0, 0)} for p in choices}
        rules = SimpleNamespace(material=lambda cell: '.', context3={}, tree_templates=Counter())
        with patch.object(blockout, 'small_tree_region', return_value=['INFO routed']) as small,              patch.object(blockout, 'learning_reference', side_effect=PkError('TEST', 'skip WFC')):
            notes = blockout.object_regions(None, grid, None, rows, rules, choices, bits)
        self.assertEqual(small.call_count, 1)
        self.assertIn('INFO routed', notes)


class Coast(unittest.TestCase):
    """The coast map (24x20: sea, beach, two tree groups) tiled from a wiped lawn with the vanilla rules."""
    BLOCKOUT = '.pk/blockout/coast.txt'

    @classmethod
    def setUpClass(cls):
        cls.repo = shared_repo()
        if not (ROOT / cls.BLOCKOUT).is_file() or not (ROOT / 'data/layouts/PkBlockCoast_Frlg').is_dir():
            raise unittest.SkipTest('coast map or .pk/blockout/coast.txt missing')
        cls.grid, cls.pair = load(cls.repo, 'PkBlockCoast_Frlg')
        cls.rows = blockout.parse((ROOT / cls.BLOCKOUT).read_text('utf-8'), cls.grid.w, cls.grid.h)
        cls.grid.cells = [(0x010, 0, 3)] * len(cls.grid.cells)
        cls.report = blockout.tile(cls.repo, cls.grid, cls.pair, cls.rows)

    def test_no_new_object_warnings_and_every_wrong_edge_context_is_reported_as_reshape(self):
        self.assertEqual(self.report['added'], 0)
        reshape = {r.split()[3] for r in self.report['rows'] if r.startswith('WARN BLOCKOUT reshape')}
        wrong = [r.split()[4] for r in self.report['rows'] if r.startswith('WARN BLOCKOUT edge context')]
        self.assertTrue(set(wrong) <= reshape, (wrong, sorted(reshape)))
        self.assertFalse(any(r.startswith('WARN OBJECT') for r in self.report['rows']))

    def test_every_water_region_uses_one_family(self):
        lines = [r for r in self.report['rows'] if r.startswith('INFO BLOCKOUT water region:') and 'families-used' in r]
        self.assertTrue(lines)
        self.assertTrue(all('families-used:1 outside-family:none' in r for r in lines), lines)

    def test_sandy_shore_not_pond_rim(self):
        rim = {0x120, 0x121, 0x122, 0x123, 0x124}
        for y in range(self.grid.h):
            for x in range(self.grid.w):
                if self.rows[y][x] == '~':
                    self.assertNotIn(self.grid.at(x, y)[0], rim, (x, y))

    def test_tree_letters_survive_as_whole_trees(self):
        actual = blockout.export(self.repo, self.grid, self.pair)
        cards_ = blockout.classifier(self.repo, self.pair).cards
        for y in range(self.grid.h):
            for x in range(self.grid.w):
                if self.rows[y][x] in 'Trn':
                    self.assertEqual(actual[y][x], self.rows[y][x], (x, y))
                    self.assertEqual(cards_[self.grid.at(x, y)[0]]['cls'], 'wall', (x, y))   # no stump, no post


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
        report = blockout.tile(repo, grid, pair, rows, exclude=(name,), baseline=original)
        # Acceptance is compared with original vanilla, not the weakened wipe: tile() measures new object
        # warnings and lost reach against `baseline`, and a rebuild it flags unsafe is a FAIL.
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
        ok = report['safe'] and added == 0 and not losses and report['fidelity'] >= .95 and not report['open_bank']
        print(f'retile {name} new_OBJECT:{added} reach_lost:{sum(losses.values())} '
              f'fidelity:{report["fidelity"]:.2%} open_bank:{report["open_bank"]} safe:{report["safe"]} pass:{ok} render:{render} audit:{audit}', flush=True)
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
