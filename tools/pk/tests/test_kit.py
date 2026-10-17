"""Tests for pk kit: KitIndexTest (indexer) and KitFindTest (search)."""
import unittest

# --- KitIndexTest (indexer) ---
import sys
import tempfile
from pathlib import Path

HOME = Path(__file__).resolve().parents[1]
ROOT = HOME.parents[1]
sys.path.insert(0, str(HOME))
from pk.core.errors import PkError
from pk.core.repo import Repo
from pk.maps import kit

CHARS = {'.': 'walk', '~': 'surf', '#': 'wall', 'g': 'grass_enc', 'L': 'ledge_s', 'D': 'door'}


def rows_to_cells(rows):
    """Row strings -> (row-major physical classes, w, h). Characters are listed in CHARS."""
    return [CHARS[c] for row in rows for c in row], len(rows[0]), len(rows)


class KitIndexTest(unittest.TestCase):
    """Indexer on small synthetic class grids; only the last test reads the real repo."""

    def test_contract_constants(self):
        # small sizes added for aiceus: a 3x2 box must be able to get a 3x2 piece
        self.assertEqual(kit.WINDOW_SIZES, ((2, 2), (3, 2), (2, 3), (3, 3), (4, 3), (3, 4), (4, 4), (5, 4),
                                            (4, 5), (6, 4), (8, 6), (10, 6), (12, 8), (15, 10)))
        self.assertEqual(kit.STRIDE, 2)
        self.assertEqual(kit.FEATURES, ('shore', 'pond', 'ledge', 'tallgrass', 'cliff'))

    def test_straight_shore_fields(self):
        cells, w, h = rows_to_cells(['......', '......', '~~~~~~', '~~~~~~'])
        fields, reason = kit.describe(cells, w, h)
        self.assertIsNone(reason)
        self.assertEqual(fields['features'], ['shore'])
        self.assertEqual(fields['classes'], {'surf': 0.5, 'walk': 0.5})
        self.assertEqual(fields['edges'], {'top': 'walk', 'bottom': 'surf', 'left': 'mixed', 'right': 'mixed'})
        self.assertEqual(fields['curvy'], 0)

    def test_curvy_counts_direction_changes(self):
        def shore(heights, h):
            return [''.join('~' if y >= b else '.' for b in heights) for y in range(h)]
        wavy, w, h = rows_to_cells(shore([2, 2, 3, 3, 2, 2, 3, 3], 6))     # up, down, up: 2 changes
        stair, w2, h2 = rows_to_cells(shore([1, 1, 2, 2, 3, 3, 4, 4], 6))  # one slope: 0 changes
        self.assertEqual(kit.describe(wavy, w, h)[0]['curvy'], 2)
        self.assertEqual(kit.describe(stair, w2, h2)[0]['curvy'], 0)

    def test_pond_is_surf_away_from_the_edge(self):
        cells, w, h = rows_to_cells(['......', '.~~...', '.~~...', '......'])
        self.assertEqual(kit.describe(cells, w, h)[0]['features'], ['shore', 'pond'])
        cells, w, h = rows_to_cells(['......', '......', '~~~~~~', '~~~~~~'])
        self.assertEqual(kit.describe(cells, w, h)[0]['features'], ['shore'])  # surf on the edge: no pond

    def test_ledge_feature(self):
        cells, w, h = rows_to_cells(['.....g', '..L...', '......', '~~~~~~'])
        self.assertEqual(kit.describe(cells, w, h)[0]['features'], ['shore', 'ledge'])

    def test_tallgrass_needs_twenty_percent(self):
        at_20 = ['grass_enc'] * 12 + ['walk'] * 48      # 12 of 60 cells
        below = ['grass_enc'] * 11 + ['walk'] * 49      # 11 of 60 cells
        self.assertIn('tallgrass', kit.describe(at_20, 10, 6)[0]['features'])
        self.assertNotIn('tallgrass', kit.describe(below, 10, 6)[0]['features'])

    def test_cliff_is_a_wall_run_of_four_next_to_walk(self):
        cliff, w, h = rows_to_cells(['####..', '......', '......', '......'])
        short, w2, h2 = rows_to_cells(['###...', '......', '~~~~~~', '~~~~~~'])   # run of 3
        no_walk, w3, h3 = rows_to_cells(['######', '~~~~~~', '~~~~~~', '~~~~~~'])  # run of 6, surf beside it
        self.assertEqual(kit.describe(cliff, w, h)[0]['features'], ['cliff'])
        self.assertNotIn('cliff', kit.describe(short, w2, h2)[0]['features'])
        self.assertNotIn('cliff', kit.describe(no_walk, w3, h3)[0]['features'])

    def test_skip_reasons_run_in_order_event_door_uniform(self):
        mixed, w, h = rows_to_cells(['......', '.~~~..', '......', '......'])
        self.assertEqual(kit.describe(mixed, w, h, has_event=True), (None, 'event'))
        with_door = list(mixed)
        with_door[7] = 'door'
        self.assertEqual(kit.describe(with_door, w, h), (None, 'door'))
        self.assertEqual(kit.describe(['walk'] * 24, 6, 4), (None, 'uniform'))

    def test_uniform_limit_is_inclusive_at_85_percent(self):
        exact = ['walk'] * 51 + ['surf'] * 9      # 85% of 60: kept
        over = ['walk'] * 52 + ['surf'] * 8       # 86.7%: skipped
        self.assertIsNone(kit.describe(exact, 10, 6)[1])
        self.assertEqual(kit.describe(over, 10, 6)[1], 'uniform')

    def test_edge_needs_seventy_percent(self):
        self.assertEqual(kit.edge_class(['walk'] * 7 + ['surf'] * 3), 'walk')
        self.assertEqual(kit.edge_class(['walk'] * 6 + ['surf'] * 4), 'mixed')

    def test_scan_counts_skips_and_dedupes(self):
        # 12x8 map: rows 0-3 walk, rows 4-7 surf. Windows at the same y are identical.
        classes = ['walk' if y < 4 else 'surf' for y in range(8) for x in range(12)]
        ids = [1 if c == 'walk' else 2 for c in classes]
        stats = dict(scanned=0, kept=0, skipped=dict.fromkeys(kit.SKIP_REASONS, 0))
        out, seen = [], set()
        kit.scan_map('Synthetic', 'A+B', 12, 8, classes, ids, [(1, 3)], seen, stats, out, sizes=((6, 4),))
        self.assertEqual(stats['scanned'], 12)
        self.assertEqual(stats['kept'], 1)
        self.assertEqual(stats['skipped'], dict(event=2, door=0, uniform=7, duplicate=2))
        self.assertEqual(sum(stats['skipped'].values()) + stats['kept'], stats['scanned'])
        self.assertEqual((out[0]['map'], out[0]['x'], out[0]['y'], out[0]['pair']), ('Synthetic', 2, 2, 'A+B'))
        self.assertEqual(out[0]['edges']['top'], 'walk')

    def test_portable_windows_dedupe_across_secondaries(self):
        # A portable window uses only primary metatiles, so the same content from two pairs sharing the
        # primary is one piece, not one per secondary tileset (window_digest hashes the primary then).
        classes = ['walk', 'surf', 'walk', 'surf', 'walk', 'surf', 'walk', 'surf', 'walk']
        ids = [0x100] * 9                                   # below primary_limit: portable
        stats = dict(scanned=0, kept=0, skipped=dict.fromkeys(kit.SKIP_REASONS, 0))
        out, seen = [], set()
        for pair_key in ('A+B', 'A+C'):
            kit.scan_map(pair_key, pair_key, 3, 3, classes, ids, [], seen, stats, out, sizes=((3, 3),))
        self.assertEqual(len(out), 1, out)
        self.assertEqual(out[0]['pair'], 'A+B')
        self.assertTrue(out[0]['portable'])

    def test_command_rejects_unknown_verbs_and_extra_arguments(self):
        repo = Repo(ROOT)   # not read: both calls fail before touching the repo
        with self.assertRaises(PkError) as ctx:
            kit.command(repo, ['nope'], {})
        self.assertEqual(ctx.exception.code, 'PARSE')
        with self.assertRaises(PkError) as ctx:
            kit.command(repo, ['index', 'extra'], {})
        self.assertEqual(ctx.exception.code, 'PARSE')

    def test_write_and_load_index_round_trip(self):
        entry = dict(map='Synthetic', x=2, y=2, w=6, h=4, pair='A+B', classes={'surf': 0.5, 'walk': 0.5},
                     edges={'top': 'walk', 'bottom': 'surf', 'left': 'mixed', 'right': 'mixed'},
                     features=['shore'], curvy=0, portable=False)
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repo(tmp)
            path = kit.write_index(repo, [entry])
            self.assertEqual(path, Path(tmp).resolve() / '.pk' / 'kit' / 'index.json')
            self.assertEqual(kit.load_index(repo), [entry])

    def test_corrupt_index_is_a_parse_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repo(tmp)
            repo.path(kit.INDEX_REL).parent.mkdir(parents=True)
            repo.path(kit.INDEX_REL).write_text('[{', encoding='utf-8')
            with self.assertRaises(PkError) as ctx:
                kit.load_index(repo)
            self.assertEqual(ctx.exception.code, 'PARSE')

    @unittest.skipUnless((ROOT / 'data/layouts/layouts.json').is_file(), 'needs the game repo')
    def test_real_repo_counts_add_up(self):
        entries, stats, notes = kit.build_index(Repo(ROOT))
        self.assertEqual(len(entries), stats['kept'])
        self.assertEqual(stats['scanned'], stats['kept'] + sum(stats['skipped'].values()))
        self.assertGreater(stats['kept'], 0)
        for entry in entries:
            self.assertFalse(entry['map'].startswith('Pk'))
            self.assertTrue(set(entry['features']) <= set(kit.FEATURES))


# --- KitFindTest (search) ---
import io
from unittest import mock

from PIL import Image

PAIR = 'gTileset_Fixture_A+gTileset_Fixture_B'
OTHER_PAIR = 'gTileset_Fixture_C+gTileset_Fixture_D'
WALK, SURF = (1, 0, 0), (2, 0, 0)     # (metatile, collision, elevation) cells for FakeGrid


class FakeGrid:
    """Just enough of layers.Grid for ring_edges: row-major (metatile, collision, elevation) cells."""
    def __init__(self, w, h, fill):
        self.w, self.h, self.cells = w, h, [fill] * (w * h)

    def inside(self, x, y):
        return 0 <= x < self.w and 0 <= y < self.h

    def at(self, x, y):
        return self.cells[y * self.w + x]

    def paint(self, x, y, cell):
        self.cells[y * self.w + x] = cell


class FakePair:
    NAMES = {1: 'MB_NORMAL', 2: 'MB_POND_WATER'}   # walk and surf behaviours

    def behavior_name(self, metatile):
        return self.NAMES[metatile]


def window(map_name, x=0, y=0, w=10, h=6, edges=None, features=(), curvy=0, pair=PAIR):
    """A fixture index entry with the field names of the contract (kit.py docstring)."""
    stored = dict(top='walk', bottom='walk', left='walk', right='walk')
    stored.update(edges or {})
    return dict(map=map_name, x=x, y=y, w=w, h=h, pair=pair, classes={}, edges=stored,
                features=list(features), curvy=curvy, portable=False)


FIXTURES = [
    window('MapA_Frlg', edges=dict(bottom='surf'), features=['shore'], curvy=2),
    window('MapB_Frlg', w=8, h=6, edges=dict(bottom='surf', left='mixed'), features=['shore'], curvy=5),
    window('MapC_Frlg', edges=dict(bottom='surf', left='mixed', right='mixed'), features=['shore'], curvy=2),
    window('MapD_Frlg', w=6, h=4),
    window('MapE_Frlg', w=12, h=8, features=['pond']),
    window('MapF_Frlg', pair=OTHER_PAIR, features=['pond']),
]


class KitFindTest(unittest.TestCase):
    """Search over fixture index entries, plus read-only checks on the real vanilla maps."""

    def setUp(self):
        self.repo = Repo(ROOT)    # no transaction: find, fit and sheet only read

    def assertRefused(self, code, fn, *args):
        with self.assertRaises(PkError) as ctx:
            fn(*args)
        self.assertEqual(ctx.exception.code, code)

    def test_exact_size_tier_comes_before_near(self):
        hits, counts = kit.find(FIXTURES, size=(10, 6))
        self.assertEqual([e['map'] for _, e in hits],
                         ['MapA_Frlg', 'MapF_Frlg', 'MapC_Frlg', 'MapE_Frlg', 'MapB_Frlg'])
        self.assertEqual([t for t, _ in hits], ['exact', 'exact', 'exact', 'near', 'near'])
        self.assertEqual(counts, dict(matched=5, exact=3, near=2))   # MapD (6x4) is out of range

    def test_shore_query_ranks_curvy_then_fewer_mixed(self):
        hits, _ = kit.find(FIXTURES, top='walk', bottom='surf')
        self.assertEqual([e['map'] for _, e in hits], ['MapB_Frlg', 'MapA_Frlg', 'MapC_Frlg'])
        hits, _ = kit.find(FIXTURES, feature='shore')
        self.assertEqual([e['map'] for _, e in hits], ['MapB_Frlg', 'MapA_Frlg', 'MapC_Frlg'])

    def test_non_shore_query_ignores_curvy(self):
        hits, _ = kit.find(FIXTURES, top='walk')
        names = [e['map'] for _, e in hits]
        # MapB is the curviest but has a mixed edge, so MapA (no mixed edge) comes first here.
        self.assertLess(names.index('MapA_Frlg'), names.index('MapB_Frlg'))

    def test_edge_pair_feature_and_limit_filters(self):
        hits, _ = kit.find(FIXTURES, top='walk', bottom='surf')
        self.assertEqual({e['map'] for _, e in hits}, {'MapA_Frlg', 'MapB_Frlg', 'MapC_Frlg'})
        hits, _ = kit.find(FIXTURES, feature='pond')
        self.assertEqual({e['map'] for _, e in hits}, {'MapE_Frlg', 'MapF_Frlg'})
        hits, _ = kit.find(FIXTURES, pair=OTHER_PAIR)
        self.assertEqual([e['map'] for _, e in hits], ['MapF_Frlg'])
        hits, counts = kit.find(FIXTURES, limit=2)
        self.assertEqual((len(hits), counts['matched']), (2, 6))
        hits, _ = kit.find(FIXTURES, bottom='walk', size=(12, 8))
        self.assertEqual([(t, e['map']) for t, e in hits], [('exact', 'MapE_Frlg'), ('near', 'MapF_Frlg')])

    def test_bad_arguments_are_refused_before_any_read(self):
        self.assertRefused('PARSE', kit.parse_size, '10')
        self.assertRefused('PARSE', kit.parse_size, '0x6')
        self.assertRefused('PARSE', kit.parse_box, '4,8')
        self.assertRefused('BOUNDS', kit.parse_limit, {'limit': '0'})
        self.assertRefused('PARSE', kit.parse_limit, {'limit': 'ten'})
        self.assertRefused('PARSE', kit.parse_filters, self.repo, {'feature': 'lake'})
        self.assertRefused('PARSE', kit.parse_filters, self.repo, {'top': 'lake'})
        self.assertRefused('PARSE', kit.command_find, self.repo, ['find', 'extra'], {})
        self.assertRefused('PARSE', kit.command_fit, self.repo, ['fit', 'PalletTown_Frlg'], {})
        self.assertRefused('PARSE', kit.command_fit, self.repo, ['fit', 'PalletTown_Frlg', '4,8,10x6'],
                           {'top': 'walk'})
        self.assertRefused('BOUNDS', kit.command_fit, self.repo, ['fit', 'PalletTown_Frlg', '4,8,10x6'],
                           {'limit': '500'})

    def test_pair_option_accepts_plus_comma_or_map_name(self):
        _, pair = kit.load(self.repo, 'PalletTown_Frlg')
        key = kit.pair_name(pair)
        self.assertEqual(kit.parse_filters(self.repo, {'pair': key})['pair'], key)
        self.assertEqual(kit.parse_filters(self.repo, {'pair': key.replace('+', ',')})['pair'], key)
        self.assertEqual(kit.parse_filters(self.repo, {'pair': 'PalletTown_Frlg'})['pair'], key)

    def test_hit_lines_format_find_and_fit(self):
        hit = ('exact', window('PalletTown_Frlg', x=4, y=8, w=10, h=6, features=['shore', 'pond'], curvy=3,
                               pair='gTileset_General+gTileset_Pallet'))
        self.assertEqual(kit.hit_lines(hit), [
            'PalletTown_Frlg 4,8,10x6 gTileset_General+gTileset_Pallet shore,pond curvy:3 portable:false',
            'pk map copy TARGET 4,8,10x6 TX,TY --from PalletTown_Frlg --repair'])
        plain = ('any', window('PalletTown_Frlg', x=0, y=0))
        self.assertEqual(kit.hit_lines(plain, target='PkKitTest_Frlg', at=(2, 3))[1],
                         'pk map copy PkKitTest_Frlg 0,0,10x6 2,3 --from PalletTown_Frlg --repair')
        self.assertEqual(kit.hit_lines(plain)[0], f'PalletTown_Frlg 0,0,10x6 {PAIR} - curvy:0 portable:false')

    def test_ring_side_reads_as_the_index_does(self):
        grid, pair = FakeGrid(12, 12, WALK), FakePair()
        for x in range(1, 8):
            grid.paint(x, 0, SURF)        # top: 7 of 10 surf = 70%, exactly the bar: surf, no note
        for x in range(1, 7):
            grid.paint(x, 2, SURF)        # bottom: 6 of 10 surf = 60%: below the bar, so mixed, with a note
        edges, notes = kit.ring_edges(grid, pair, (1, 1, 10, 1))
        self.assertEqual(edges, dict(top='surf', bottom='mixed', left='walk', right='walk'))
        self.assertEqual(len(notes), 1)
        self.assertIn('bottom', notes[0])

    def test_ring_side_on_the_map_edge_is_unconstrained(self):
        edges, notes = kit.ring_edges(FakeGrid(12, 12, WALK), FakePair(), (0, 0, 3, 2))
        self.assertIsNone(edges['top'])
        self.assertIsNone(edges['left'])
        self.assertEqual((edges['bottom'], edges['right']), ('walk', 'walk'))
        self.assertEqual(sum('edge' in note for note in notes), 2)

    def test_fit_on_real_map_prints_target_and_position(self):
        grid, pair = kit.load(self.repo, 'PalletTown_Frlg')
        edges, _ = kit.ring_edges(grid, pair, (4, 8, 10, 6))
        entries = [window('MapX_Frlg', edges=edges, features=['shore'], pair=kit.pair_name(pair)),
                   window('MapY_Frlg', edges=edges, pair=OTHER_PAIR)]
        with mock.patch.object(kit, 'load_index', return_value=entries):
            result = kit.command_fit(self.repo, ['fit', 'PalletTown_Frlg', '4,8,10x6'], {})
        self.assertTrue(result.ok)
        self.assertIn('pk map copy PalletTown_Frlg 0,0,10x6 4,8 --from MapX_Frlg --repair', result.data)
        self.assertFalse(any('MapY' in line for line in result.data))

    def test_command_find_output_and_limit(self):
        with mock.patch.object(kit, 'load_index', return_value=FIXTURES):
            result = kit.command_find(self.repo, ['find'],
                                      dict(feature='shore', top='walk', bottom='surf', limit='2'))
        self.assertEqual(result.summary, 'find 2 of 3 matches')
        self.assertEqual(result.data, [
            'MapB_Frlg 0,0,8x6 gTileset_Fixture_A+gTileset_Fixture_B shore curvy:5 portable:false',
            'pk map copy TARGET 0,0,8x6 TX,TY --from MapB_Frlg --repair',
            'MapA_Frlg 0,0,10x6 gTileset_Fixture_A+gTileset_Fixture_B shore curvy:2 portable:false',
            'pk map copy TARGET 0,0,10x6 TX,TY --from MapA_Frlg --repair'])
        self.assertTrue(result.render().startswith('OK find 2 of 3 matches'))

    def test_empty_results_say_why(self):
        with mock.patch.object(kit, 'load_index', return_value=[]):
            result = kit.command_find(self.repo, ['find'], {})
        self.assertTrue(any(line.startswith('INFO') for line in result.data))
        with mock.patch.object(kit, 'load_index', return_value=FIXTURES):
            result = kit.command_find(self.repo, ['find'], dict(feature='cliff'))
        self.assertEqual(result.summary, 'find 0 of 0 matches')
        self.assertTrue(any(line.startswith('INFO no window matches') for line in result.data))

    def test_sheet_draws_real_pieces_and_skips_missing_maps_with_info(self):
        hits = [('any', window('PalletTown_Frlg', x=0, y=0, w=6, h=4)),
                ('any', window('PalletTown_Frlg', x=2, y=2, w=8, h=6)),
                ('any', window('NoSuchMap_Frlg'))]
        png, notes = kit.sheet(self.repo, hits)
        image = Image.open(io.BytesIO(png))
        self.assertEqual(image.format, 'PNG')
        self.assertGreaterEqual(image.width, 2 * 8 * 16 * 2 + 3 * kit.SHEET_GAP)   # two columns of 8x6 at scale 2
        self.assertEqual(len(notes), 1)
        self.assertIn('rank 3', notes[0])

    def test_sheet_with_nothing_to_draw_returns_none(self):
        png, notes = kit.sheet(self.repo, [('any', window('NoSuchMap_Frlg'))])
        self.assertIsNone(png)
        self.assertEqual(len(notes), 1)


if __name__ == '__main__':
    unittest.main()
