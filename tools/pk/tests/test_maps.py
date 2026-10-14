"""Milestone 3: map views, edits, metatiles, events, check.

Mutating commands run inside an uncommitted Transaction against the real project:
results are inspected in tx.writes and nothing is ever written to disk.
"""
import json
from pathlib import Path
import re
import sys
import unittest

HOME = Path(__file__).resolve().parents[1]
ROOT = HOME.parents[1]
sys.path.insert(0, str(HOME))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pk.core.errors import PkError
from pk.core.repo import Repo
from pk.core.journal import Transaction
from pk.maps.grid import decode
from pk.maps import edit, meta, events, check

MAP = 'PalletTown_Frlg'
BIN = 'data/layouts/PalletTown_Frlg/map.bin'
JSON = f'data/maps/{MAP}/map.json'
SCRIPTS = f'data/maps/{MAP}/scripts.inc'


def pagination(opts, default=20):
    return int(opts.get('offset', 0)), int(opts.get('limit', default))


class MapTest(unittest.TestCase):
    def setUp(self):
        self.tx = Transaction(Repo(ROOT), 'test')
        self.repo = Repo(ROOT, self.tx)
        self.before = decode((ROOT/BIN).read_bytes())

    def tearDown(self):
        # Never commit: the project on disk must stay untouched.
        self.assertEqual(decode((ROOT/BIN).read_bytes()), self.before)

    def map(self, *args, **opts):
        return edit.command(self.repo, self.tx, list(args), opts)

    def cells(self):
        return decode(self.tx.writes[BIN])


class Views(MapTest):
    def test_show_golden_and_budget(self):
        result = self.map('show', MAP)
        text = result.render()
        self.assertLessEqual(len(text), 900)          # SPEC §12: 24x20 town view <= 900 chars
        golden = HOME/'tests/golden/pallet_show.txt'
        if not golden.exists():
            golden.write_text(text + '\n', encoding='utf-8')
        self.assertEqual(text + '\n', golden.read_text(encoding='utf-8'))
        self.assertIn('01|############tt##########', text)
        self.assertIn('17|##.....~~~~..o........##', text)

    def test_exact_coll_elev_and_rect(self):
        text = self.map('show', MAP, exact=True, rect='4,3,10x6').render()
        self.assertIn('legend ', text)
        self.assertIn('x|4567890123', text)
        coll = self.map('show', MAP, layer='coll', rect='0,0,4x2').render()
        self.assertIn('00|1111', coll)
        with self.assertRaises(PkError):
            self.map('show', MAP, rect='20,0,10x2')

    def test_layer_all_compact_is_lossless(self):
        # Decode only the printed class grid/defaults/exceptions, not hidden source classes.
        from pk.maps.layers import parse_rect
        from test_compact_layers import decode_view
        cases = [
            (MAP, {}), ('Route1_Frlg', {}), ('MtMoon_1F_Frlg', {}),
            ('Route1_Frlg', {'no-events': True}),
            (MAP, {'rect': '4,3,10x6'}),
            ('Route23_Frlg', {'rect': '3,97,15x12'}),
            ('Route20_Frlg', {}),
        ]
        for name, opts in cases:
            with self.subTest(name=name, opts=opts):
                grid, _ = edit.load(self.repo, name)
                rect = parse_rect(opts.get('rect'), grid.w, grid.h)
                if not opts.get('rect'):
                    rect = (0, 0, min(grid.w, edit.SHOW_MAX_W), grid.h)
                text = self.map('show', name, layer='all', **opts).render()
                decoded = decode_view(text, rect)
                x0, y0, w, h = rect
                for layer, idx in (('coll', 1), ('elev', 2)):
                    expected = {(x, y): grid.at(x, y)[idx]
                                for y in range(y0, y0+h) for x in range(x0, x0+w)}
                    self.assertEqual(decoded[layer], expected, f'{name} {layer}')
        text = self.map('show', 'Route1_Frlg', layer='all').render()
        self.assertLess(len(text), 2000)

    def test_wide_map_is_banded(self):
        text = self.map('show', 'Route20_Frlg').render()
        self.assertIn('band 0-63 of 120', text)

    def test_at(self):
        text = self.map('at', MAP, '6,7').render()
        self.assertIn('metatile 0x2A3', text)
        self.assertIn('MB_FRLG_WARP_DOOR', text)


class Edits(MapTest):
    def test_paint_only_touches_area_and_uses_vanilla_bits(self):
        self.map('paint', MAP, '3,3,3x2', '0x2A3')
        after = self.cells()
        changed = {i for i, (a, b) in enumerate(zip(self.before, after)) if a != b}
        self.assertEqual(changed, {y * 24 + x for y in (3, 4) for x in (3, 4, 5)})
        self.assertEqual(after[3 * 24 + 3], (0x2A3, 1, 0))   # door: always solid, elevation 0 in vanilla

    def test_paint_explicit_bits_and_invalid_id(self):
        self.map('paint', MAP, '3,3', '0x2A3', coll='0', elev='3')
        self.assertEqual(self.cells()[3 * 24 + 3], (0x2A3, 0, 3))
        with self.assertRaises(PkError) as ctx:
            self.map('paint', MAP, '3,3', '0x3FE')
        self.assertEqual(ctx.exception.code, 'BOUNDS')

    def test_fill_line_copy(self):
        water = self.before[17 * 24 + 7][0]
        ground = self.before[3 * 24 + 3][0]
        region, todo = {(7, 17)}, [(7, 17)]          # expected: 4-connected cells with the same id
        while todo:
            x, y = todo.pop()
            for n in ((x+1, y), (x-1, y), (x, y+1), (x, y-1)):
                if 0 <= n[0] < 24 and 0 <= n[1] < 20 and n not in region and self.before[n[1]*24+n[0]][0] == water:
                    region.add(n)
                    todo.append(n)
        self.map('fill', MAP, '7,17', hex(ground))
        after = self.cells()
        changed = {(i % 24, i // 24) for i, (a, b) in enumerate(zip(self.before, after)) if a[0] != b[0]}
        self.assertEqual(changed, region)
        self.map('line', MAP, '2,2', '6,6', hex(water))
        self.assertTrue(all(self.cells()[i * 24 + i][0] == water for i in range(2, 7)))
        mid = self.cells()
        self.map('copy', MAP, '5,4,5x3', '14,8')
        after = self.cells()
        for j in range(3):
            for i in range(5):
                self.assertEqual(after[(8 + j) * 24 + 14 + i], mid[(4 + j) * 24 + 5 + i])

    def test_grid_patch_with_defs_and_keep(self):
        ground = self.before[3 * 24 + 3][0]
        self.map('grid', MAP, '2,2', f'def X 0x2A3;X?X; X ')
        after = self.cells()
        self.assertEqual(after[2 * 24 + 2][0], 0x2A3)
        self.assertEqual(after[2 * 24 + 3], self.before[2 * 24 + 3])      # '?' keeps
        self.assertEqual(after[3 * 24 + 2], self.before[3 * 24 + 2])      # ' ' keeps
        self.assertEqual(after[3 * 24 + 3][0], 0x2A3)
        with self.assertRaises(PkError):
            self.map('grid', MAP, '2,2', 'Q')

    def test_border(self):
        result = self.map('border', MAP, 'show')
        self.assertEqual(len(result.data), 2)
        self.map('border', MAP, '0x2A3')
        border = decode(self.tx.writes['data/layouts/PalletTown_Frlg/border.bin'])
        self.assertEqual([c[0] for c in border], [0x2A3] * 4)


class Meta(MapTest):
    def test_ls_show_name(self):
        result = meta.command(self.repo, self.tx, ['ls', MAP], {'limit': '3'}, pagination)
        self.assertEqual(len(result.data), 3)
        result = meta.command(self.repo, self.tx, ['show', MAP, '0x2A3'], {}, pagination)
        self.assertEqual(len(result.data), 17)
        self.assertTrue(all(len(r) == 16 for r in result.data[1:]))
        meta.command(self.repo, self.tx, ['name', MAP, '0x2A3', 'house_door'], {'sym': 'D', 'tags': 'door'}, pagination)
        names = json.loads(self.tx.writes['tools/pk/data/metatiles/gTileset_PalletTown.json'])
        self.assertEqual(names['2A3'], {'name': 'house_door', 'tags': ['door'], 'sym': 'D'})
        self.map('paint', MAP, '3,3', 'house_door')                         # names resolve in edits
        self.assertEqual(self.cells()[3 * 24 + 3][0], 0x2A3)

    def test_autoname_does_not_override_manual(self):
        # The PalletTown pair is fully manually named in the shipped sidecar, so trim it first
        # to give autoname unnamed metatiles: the point is that it names those and leaves
        # manual names (here 0x2A3 house_door) alone.
        path = 'tools/pk/data/metatiles/gTileset_PalletTown.json'
        trimmed = {k: v for k, v in json.loads(self.repo.read(path)).items() if k in ('2A3', '2A4')}
        self.tx.stage(path, (json.dumps(trimmed, indent=1, sort_keys=True) + '\n').encode('utf-8'))
        meta.command(self.repo, self.tx, ['name', MAP, '0x2A3', 'house_door'], {}, pagination)
        meta.command(self.repo, self.tx, ['autoname', MAP], {}, pagination)
        names = json.loads(self.tx.writes[path])
        self.assertEqual(names['2A3']['name'], 'house_door')
        self.assertTrue(any(v.get('auto') for v in names.values()))


class Events(MapTest):
    def ev(self, *args):
        return events.command(self.repo, self.tx, list(args), {}, pagination)

    def test_ls(self):
        result = self.ev('ls', MAP)
        self.assertEqual(len(result.data), 14)
        self.assertTrue(result.data[0].startswith('obj#0 LOCALID_PALLET_SIGN_LADY @3,10'))

    def test_add_with_text_generates_script(self):
        result = self.ev('add', MAP, 'obj', '@8,9', 'gfx=LASS', 'move=WANDER', 'range=2x2',
                         'text=The sea breeze is lovely today. || Have you met PROF. OAK?')
        self.assertIn('LASS_FRLG', result.data[0])
        data = json.loads(self.tx.writes[JSON])
        new = data['object_events'][-1]
        self.assertEqual((new['x'], new['y'], new['movement_type']), (8, 9, 'MOVEMENT_TYPE_WANDER_AROUND'))
        scripts = self.tx.writes[SCRIPTS].decode('utf-8')
        self.assertIn('PalletTown_EventScript_Auto_1::\n\tmsgbox PalletTown_Text_Auto_1, MSGBOX_NPC\n\tend\n', scripts)
        self.assertIn('PalletTown_Text_Auto_1::\n\t.string "The sea breeze is lovely today.\\p"\n', scripts)
        # Everything before the appended block is unchanged.
        self.assertTrue(scripts.startswith((ROOT/SCRIPTS).read_text(encoding='utf-8')))

    def test_json_edit_preserves_other_bytes(self):
        self.ev('set', MAP, 'obj#0', '@4,10')
        old = (ROOT/JSON).read_text(encoding='utf-8')
        new = self.tx.writes[JSON].decode('utf-8')
        diff = [(a, b) for a, b in zip(old.splitlines(), new.splitlines()) if a != b]
        self.assertEqual(diff, [('      "x": 3,', '      "x": 4,')])

    def test_rm_guards_and_errors(self):
        with self.assertRaises(PkError) as ctx:
            self.ev('rm', MAP, 'warp#1')
        self.assertEqual(ctx.exception.code, 'CONFLICT')
        self.ev('rm', MAP, 'obj#1')
        self.assertEqual(len(json.loads(self.tx.writes[JSON])['object_events']), 2)
        for bad in (['add', MAP, 'obj', '@99,1'], ['add', MAP, 'obj', '@1,1', 'script=NoSuchLabel'],
                    ['set', MAP, 'obj#9', '@1,1'], ['add', MAP, 'warp', '@1,1', 'to=PalletTown_Frlg#9']):
            with self.assertRaises(PkError):
                self.ev(*bad)


class Check(MapTest):
    def test_map_check_clean_and_detects_bad_warp(self):
        result = check.run(self.repo, self.tx, MAP, {}, pagination)
        self.assertTrue(result.ok, result.render())
        data = json.loads((ROOT/JSON).read_text(encoding='utf-8'))
        data['warp_events'][0]['dest_warp_id'] = '9'
        self.tx.stage(JSON, json.dumps(data, indent=2).encode('utf-8'))
        result = check.run(self.repo, self.tx, MAP, {}, pagination)
        self.assertFalse(result.ok)
        self.assertIn('does not exist', result.render())


class FakeGrid:
    def __init__(self, rows):
        self.w = len(rows[0])
        self.h = len(rows)
        self.mats = {(x, y): cell for y, row in enumerate(rows) for x, cell in enumerate(row)}
        self.ids = {(x, y): ord(cell) for (x, y), cell in self.mats.items()}   # for hex views

    def at(self, x, y):
        return (self.ids[(x, y)], 0, 0)           # numeric metatile id; material stays a letter

    def inside(self, x, y):
        return 0 <= x < self.w and 0 <= y < self.h


class FakePair:
    def info(self, mt):
        return {'tags': []}


class Lint(unittest.TestCase):
    def setUp(self):
        self.repo = Repo(ROOT)

    def cfg(self, **tweaks):
        cfg = json.loads((ROOT/'tools/pk/data/lint_map.json').read_text(encoding='utf-8'))
        cfg.update(dict(natural=['s', 'w', '.'], empty_types=['X'], straight_types=['X'], dash_types=['X'],
                        rect_border=False, map_type='X', marks={}, empty_water_frac=0.3), **tweaks)
        return cfg

    def test_seams_use_vanilla_adjacency(self):
        from pk.maps import lint

        class Snap:
            class Stats:
                h = {(ord('a'), ord('b'))}
                v = {(ord('a'), ord('b'))}
                metatiles = {ord('a'), ord('b')}

            def pair(self, pair):
                return self.Stats()

        grid = FakeGrid(['ab', 'ba'])
        rows, unchecked = lint.seams(grid, FakePair(), Snap())
        self.assertEqual(unchecked, {})
        self.assertEqual(rows, [f'WARN SEAM h 0x{ord("b"):03X}-0x{ord("a"):03X} @0,1',
                                f'WARN SEAM v 0x{ord("b"):03X}-0x{ord("a"):03X} @1,0'])

    def test_dash_and_chain_boundary_shapes(self):
        from pk.maps import lint
        cfg = self.cfg()
        grid = FakeGrid(['sssss......sssss', 'wwwwwwwwwwwwwwww'])
        rows, n = lint.stairs(dict(cfg, mat=grid.mats), grid, cfg)
        self.assertEqual((rows, n), (['WARN STAIR s|w 0,0-15,0 dash:2'], 1))
        cfg = self.cfg(stair_min_steps=3)
        grid = FakeGrid(['sss......', 'wwwsss...', 'wwwwwwsss', 'wwwwwwwww'])
        rows, n = lint.stairs(dict(cfg, mat=grid.mats), grid, cfg)
        self.assertEqual((rows, n), (['WARN STAIR s|w 0,0-8,2 steps:3'], 1))

    def test_stairs_taken_marks_whole_chain(self):
        # A 4-step run must be counted once: without the (line, run) `taken` key it was re-found
        # one line down as a fresh 3-step chain and reported again.
        from pk.maps import lint
        cfg = self.cfg(stair_min_steps=3)
        grid = FakeGrid(['sswwwwww',
                         'wwsswwww',
                         'wwwwssww',
                         'wwwwwwss',
                         'wwwwwwww'])
        rows, n = lint.stairs(dict(cfg, mat=grid.mats), grid, cfg)
        self.assertEqual(n, 2, rows)      # one 4-step chain and its opposite shore; not re-counted

    def test_stairs_length_one_shifts_are_deduped(self):
        # length == 1 made the shift list (1, -1, 1, -1): the duplicated shifts appended the same
        # chain twice. The dedup keeps exactly one horizontal and one vertical warning.
        from pk.maps import lint
        cfg = self.cfg(stair_min_run=1, stair_min_steps=3)
        grid = FakeGrid(['ssss', 'wsss', 'wwss', 'wwws'])
        rows, n = lint.stairs(dict(cfg, mat=grid.mats), grid, cfg)
        self.assertEqual(n, 2)
        self.assertTrue(all('steps:3' in r for r in rows), rows)

    def test_rect_fill_and_border(self):
        from pk.maps import lint
        cfg = self.cfg()
        grid = FakeGrid(['.' * 10] + ['.wwwwwwww.'] * 8 + ['.' * 10])
        rows, n = lint.rects(dict(cfg, mat=grid.mats), grid, dict(cfg, rect_border=False))
        self.assertEqual((rows, n), (['WARN RECT w 1,1-8,8 100%'], 1))
        for value in (True, False):
            touch = FakeGrid(['.wwwwwwww.'] * 10)
            meta = dict(cfg, mat=touch.mats, rect_border=value)
            rows, n = lint.rects(meta, touch, meta)
            self.assertEqual(n, 0 if value else 1, f'rect_border={value} on a border-touching rect')
        grid = FakeGrid(['.' * 10, 'wwwwwwwww.', 'wwwwwwwww.', '.' * 10])
        meta = dict(cfg, mat=grid.mats, rect_border=True)
        rows, n = lint.rects(meta, grid, meta)
        self.assertEqual(n, 0, 'border-touching natural rects are vanilla-like')

    def test_rect_tendril_to_border_is_not_exempt(self):
        # A square pond joined to the border by a 1-tile stream used to inherit the stream's
        # bbox, touch the border and get skipped. The tendril is pruned before the border test.
        from pk.maps import lint
        cfg = self.cfg()
        rows_grid = ['.' * 14 for _ in range(14)]
        for y in range(2, 10):
            for x in range(2, 10):
                rows_grid[y] = rows_grid[y][:x] + 'w' + rows_grid[y][x + 1:]
        for y in range(10, 13):            # 1-tile stream from the pond down to the bottom edge
            rows_grid[y] = rows_grid[y][:5] + 'w' + rows_grid[y][6:]
        grid = FakeGrid(rows_grid)
        meta = dict(cfg, mat=grid.mats, rect_border=True)
        rows, n = lint.rects(meta, grid, meta)
        self.assertEqual(n, 1)
        self.assertTrue(rows[0].startswith('WARN RECT w 2,2-9,9'), rows)

    def test_empty_window_needs_clean_ground(self):
        from pk.maps import lint
        cfg = self.cfg()
        grid = FakeGrid(['.' * 19] * 14)
        meta = dict(cfg, mat=grid.mats)
        rows, n = lint.empty(meta, grid, FakePair(), cfg)
        self.assertEqual((rows, n), (['WARN EMPTY 2,2-16,11'], 1))
        blocks = grid.mats | {(5, 5): 'solid'}
        self.assertEqual(lint.empty(dict(meta, mat=blocks), grid, FakePair(), cfg), ([], 0),
                         'solid tiles anywhere in a 15x10 window cancel it')
        self.assertEqual(lint.empty(dict(meta, marks={(8, 5): 'o'}), grid, FakePair(), cfg),
                         (['WARN EMPTY 2,2-16,11'], 1),
                         'one event cell no longer rescues a whole screen')
        self.assertEqual(lint.empty(dict(meta, marks={(8, 5): 'o', (9, 5): 'o'}), grid, FakePair(), cfg),
                         ([], 0), 'two event cells rescue the window')
        varied = {c: 's' if (c[0] + c[1]) % 2 else 'g' for c in grid.mats}
        self.assertEqual(lint.empty(dict(meta, mat=varied), grid, FakePair(), cfg), ([], 0),
                         'a material-entropy floor rescues a varied window')

    def test_socketed_custom_metatile_checks_ground(self):
        # A3: bench_v2_l/r carry socket=0x296 (meadow). On a meadow field every adjacency is
        # vanilla-seen (0 SEAM); on deep water the bench<->water pair was never seen (>=1 SEAM).
        from pk.maps import lint
        bench = (0x2E2, 0x2E3)
        results = {}
        for label, ground in (('grass', 0x296), ('water', 0x12B)):
            tx = Transaction(Repo(ROOT), 'test')
            repo = Repo(ROOT, tx)
            grid, _ = edit.load(repo, 'PkSandbox_Frlg')
            for y in range(grid.h):
                for x in range(grid.w):
                    grid.set(x, y, (ground, 0, 0))
            grid.set(grid.w // 2, grid.h // 2, (bench[0], 1, 0))
            grid.set(grid.w // 2 + 1, grid.h // 2, (bench[1], 1, 0))
            edit.save(tx, grid)
            results[label] = int(re.search(r'seams:(\d+)', lint.run(repo, 'PkSandbox_Frlg').summary)[1])
        self.assertEqual(results['grass'], 0, results)
        self.assertGreaterEqual(results['water'], 1, results)

    def test_lint_part_a_calibration(self):
        """Part A: 0 SEAM on every vanilla outdoor map, all vanilla score 0, and every fixture /
        benchmark with a human score <=4 scores strictly above the vanilla max (0)."""
        from pk.maps import lint
        repo = self.repo
        summary = lambda name: lint.run(repo, name).summary
        score = lambda name: int(re.search(r'score:(\d+)', summary(name))[1])
        vanilla = [name for name, _ in lint.vanilla_outdoor(repo)]
        self.assertEqual(len(vanilla), 72)
        for name in vanilla:
            self.assertIn('seams:0', summary(name), name)
            self.assertEqual(score(name), 0, name)
        best = ('PkLintRect_Frlg', 'PkLintStraight_Frlg', 'PkLintStairs_Frlg', 'PkLintSeam_Frlg',
                'PkLintEmpty_Frlg', 'PkPalletMuse_Frlg', 'PkPalletSonnet_Frlg',
                'PkPalletDeepSeek_Frlg', 'PkPalletSol_Frlg')
        for name in best:
            self.assertGreater(score(name), 0, name)


if __name__ == '__main__':
    unittest.main()
