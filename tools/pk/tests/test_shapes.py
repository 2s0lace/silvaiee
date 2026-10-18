"""Connected vanilla material masks and the `pk shape` search surface."""
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

HOME = Path(__file__).resolve().parents[1]
ROOT = HOME.parents[1]
if not (ROOT / 'data/maps/PalletTown_Frlg').is_dir():
    ROOT = next((p for p in ROOT.parents if (p / 'data/maps/PalletTown_Frlg').is_dir()), ROOT)
sys.path.insert(0, str(HOME))

from pk.core.errors import PkError
from pk.core.repo import Repo
from pk.core.journal import Transaction
from pk.maps.edit import load, save
from pk.maps import shapes


def entry(kind, ident, w, h, curvy):
    return dict(version=shapes.INDEX_VERSION, id=ident, kind=kind, w=w, h=h, source='Route1_Frlg', box=[1, 2, w, h], curviness=curvy,
                cells=[[0, 0, 'water']], pair='A+B', portable=True)


class ShapeUnits(unittest.TestCase):
    @unittest.skipUnless((ROOT / 'data/maps/PalletTown_Frlg').is_dir(), 'needs the game repo')
    def test_water_placement_copies_patch_without_material_paint(self):
        tx = Transaction(Repo(ROOT), 'test source patch placement')
        repo = Repo(ROOT, tx)
        _, pair = load(repo, 'PalletTown_Frlg')
        grid = shapes.Grid.__new__(shapes.Grid)
        grid.repo, grid.name = repo, 'PkShapeBankMemory_Frlg'
        grid.w = grid.h = 12
        grid.data = {'map_type': 'MAP_TYPE_TOWN', 'connections': [{'direction': 'up'}, {'direction': 'down'}]}
        grid.layout = {'blockdata_filepath': '.pk/test_source_bank_memory.bin'}
        grid.cells = [(0x296, 0, 3)] * 144
        e = entry('pond', 'pond-unit', 1, 1, 4)
        water = {(0, 0): (0x12B, 0, 1)}
        banks = {(dx, dy): (0x070, 1, 0) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx, dy) != (0, 0)}
        with mock.patch.object(shapes, 'load', return_value=(grid, pair)), \
             mock.patch.object(shapes, 'load_index', return_value=[e]), \
             mock.patch.object(shapes, 'portable_patch', return_value=(water, banks)), \
             mock.patch.object(shapes, 'object_warnings', return_value=[]), \
             mock.patch('pk.maps.lint.run', return_value=mock.Mock(summary='unit lint')), \
             mock.patch.object(shapes, 'terrain_apply') as paint:
            result = shapes.command_place(repo, tx, ['place', grid.name, 'pond-unit', '5,5'], {})
        paint.assert_not_called()
        for (x, y), cell in {**water, **banks}.items():
            self.assertEqual(grid.at(5 + x, 5 + y), cell)
        frozen = {(5 + x, 5 + y) for x, y in water.keys() | banks.keys()}
        self.assertTrue(all(grid.at(*p) == (0x296, 0, 3) for p in shapes.mask_ring(frozen, grid)))
        self.assertTrue(any('fidelity:1.000' in row for row in result.data))
        self.assertEqual(set(tx.writes), {'.pk/test_source_bank_memory.bin'})

    def test_bank_check_catches_an_open_side_even_with_other_banks(self):
        pair = mock.Mock()
        pair.behavior_name.side_effect = lambda mt: 'MB_FRLG_OCEAN_WATER' if mt == 2 else 'MB_FRLG_NORMAL'
        pair.info.side_effect = lambda mt: {'tags': ['lawn' if mt == 1 else 'rock']}
        grid = shapes.Grid.__new__(shapes.Grid)
        grid.w = grid.h = 5
        grid.cells = [(1, 0, 3)] * 25
        grid.set(2, 2, (2, 0, 1))
        for x, y in shapes.neighbours(2, 2, 5, 5):
            grid.set(x, y, (3, 1, 0))
        self.assertEqual(shapes.bank_errors(grid, pair, {(2, 2)}, [1]), [])
        grid.set(1, 2, (1, 0, 3))
        self.assertTrue(any('open water/bank side' in row for row in shapes.bank_errors(grid, pair, {(2, 2)}, [1])))
        for x, y in shapes.neighbours(2, 2, 5, 5):
            grid.set(x, y, (1, 0, 3))
        self.assertTrue(any('no non-water, non-lawn bank' in row for row in shapes.bank_errors(grid, pair, {(2, 2)}, [1])))

    def test_nonportable_bank_refuses_even_when_water_is_primary(self):
        pair = mock.Mock(version='frlg', primary={'symbol': 'PRIMARY'}, limits={'metatiles': 640})
        pair.valid.return_value = True
        with mock.patch.object(shapes, 'source_patch', return_value=(
                {(0, 0): (0x12B, 0, 1)}, {(-1, 0): (0x305, 1, 0)}, pair)):
            with self.assertRaisesRegex(PkError, r'not placeable: non-portable source tiles \(water:0 bank:1\)'):
                shapes.portable_patch(None, pair, {'id': 'pond-test'})

    def test_behavior_overrules_incorrect_water_tags(self):
        class Pair:
            def behavior_name(self, mt):
                return {1: 'MB_FRLG_NORMAL', 2: 'MB_FRLG_OCEAN_WATER', 3: 'MB_FRLG_SHALLOW_WATER'}[mt]

            def info(self, mt):
                return {'tags': ['water']}

        rules = shapes.ShapeRules.__new__(shapes.ShapeRules)
        rules.pair = Pair()
        self.assertEqual(rules.material((1, 0, 3)), 'ground')
        self.assertEqual(rules.material((1, 1, 3)), 'solid')
        self.assertEqual(rules.material((2, 0, 3)), 'water')
        self.assertFalse(shapes.is_water(rules.pair, (3, 0, 3)))

    def test_components_are_cardinal_and_deterministic(self):
        got = shapes.components({(1, 0), (0, 0), (2, 2), (3, 3)}, 4, 4)
        self.assertEqual(got, [{(0, 0), (1, 0)}, {(2, 2)}, {(3, 3)}])

    def test_curviness_distinguishes_rectangle_from_notch(self):
        rectangle = {(x, y) for y in range(3) for x in range(4)}
        notch = rectangle - {(1, 0)}
        self.assertEqual(shapes.outline_curviness(rectangle), 4)
        self.assertGreater(shapes.outline_curviness(notch), 4)

    def test_find_exact_then_near_and_curvy_first(self):
        entries = [entry('pond', 'a', 8, 6, 4), entry('pond', 'b', 8, 6, 10),
                   entry('pond', 'c', 7, 5, 20), entry('cove', 'd', 8, 6, 99)]
        hits, count = shapes.find(entries, 'pond', (8, 6), 10)
        self.assertEqual([e['id'] for _, e in hits], ['b', 'a', 'c'])
        self.assertEqual(count, 3)

    def test_find_rejects_unknown_kind(self):
        with self.assertRaises(PkError) as ctx:
            shapes.find([], 'square')
        self.assertEqual(ctx.exception.code, 'PARSE')

    def test_flip_changes_coordinates_not_materials(self):
        e = entry('pond', 'a', 4, 3, 4)
        e['cells'] = [[0, 1, 'water'], [3, 2, 'sand']]
        self.assertEqual(list(shapes._flipped_cells(e, 'h')), [(3, 1, 'water'), (0, 2, 'sand')])
        self.assertEqual(list(shapes._flipped_cells(e, 'v')), [(0, 1, 'water'), (3, 0, 'sand')])

    def test_index_round_trip(self):
        values = [entry('pond', 'a', 4, 3, 6)]
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repo(tmp)
            shapes.write_index(repo, values)
            self.assertEqual(shapes.load_index(repo), values)

    def test_dry_run_does_not_create_index(self):
        values = [entry('pond', 'a', 4, 3, 6)]
        with tempfile.TemporaryDirectory() as tmp:
            tx = Transaction(Repo(tmp), 'dry-run shape')
            tx.dry_run = True
            repo = Repo(tmp, tx)
            with mock.patch.object(shapes, 'build_index', return_value=(values, 1, [])):
                self.assertEqual(shapes.load_index(repo), values)
            self.assertFalse(repo.path(shapes.INDEX_REL).exists())

    def test_diagonal_fallback_keeps_every_cardinal(self):
        from collections import Counter
        pair = mock.Mock()
        pair.behavior_name.return_value = 'MB_FRLG_OCEAN_WATER'
        rules = mock.Mock(pair=pair)
        wanted = (False, True, True, False, True, True, False, True, True)
        diagonal = (True, True, True, False, True, True, False, True, True)
        wrong_bank = (False, False, True, False, True, True, False, True, True)
        rules.patterns = {diagonal: Counter({(1, 0, 3): 2}), wrong_bank: Counter({(2, 0, 3): 100})}
        self.assertEqual(set(shapes.shore_candidates(rules, wanted, 'surf')), {(1, 0, 3)})

    def test_every_non_rectangular_example_really_misses_fifteen_percent(self):
        e = entry('pond', 'a', 4, 4, 8)
        e['cells'] = [[x, y, 'water'] for y in range(4) for x in range(4) if (x, y) not in {(0, 0), (0, 1), (1, 0)}]
        self.assertLessEqual(len(e['cells']), 0.85 * e['w'] * e['h'])

    def test_lawn_is_measured_from_box_ring_not_global_or_inside(self):
        pair = mock.Mock()
        pair.behavior_name.side_effect = lambda mt: 'MB_FRLG_OCEAN_WATER' if mt == 2 else 'MB_FRLG_NORMAL'
        grid = shapes.Grid.__new__(shapes.Grid)
        grid.w, grid.h = 9, 9
        grid.data = {'object_events': [{'x': 2, 'y': 2}]}
        grid.cells = [(7, 0, 3)] * 81
        for x, y in shapes.placement_box(grid, (3, 3, 3, 3), 1):
            grid.set(x, y, (42, 0, 4))
        for x in range(3, 6):
            for y in range(3, 6):
                grid.set(x, y, (99, 0, 3))
        grid.set(2, 2, (5, 0, 3))
        grid.set(3, 2, (2, 0, 1))
        grid.set(4, 2, (9, 1, 3))
        self.assertEqual(shapes.lawn_around(grid, pair, (3, 3, 3, 3)), (42, 0, 4))


@unittest.skipUnless((ROOT / 'data/layouts/layouts.json').is_file(), 'needs the game repo')
class ShapeRealRepo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries, cls.maps, cls.notes = shapes.build_index(Repo(ROOT))

    def test_sources_are_vanilla_and_masks_fit_boxes(self):
        self.assertGreater(self.maps, 0)
        for e in self.entries:
            self.assertFalse(e['source'].startswith('Pk'))
            self.assertTrue(e['cells'])
            self.assertTrue(all(0 <= x < e['w'] and 0 <= y < e['h'] for x, y, _ in e['cells']))

    def test_acceptance_counts(self):
        counts = {kind: sum(e['kind'] == kind for e in self.entries) for kind in shapes.KINDS}
        self.assertGreaterEqual(counts['pond'], 30, counts)
        self.assertGreaterEqual(counts['cove'], 15, counts)
        self.assertGreaterEqual(counts['grove'], 20, counts)
        self.assertGreaterEqual(counts['island'] + counts['sandbar'], 10, counts)

    def test_water_masks_have_real_water_behavior(self):
        repo = Repo(ROOT)
        for e in self.entries:
            if e['kind'] != 'pond':
                continue
            grid, pair = load(repo, e['source'])
            sx, sy, _, _ = e['box']
            for x, y, material in e['cells']:
                self.assertEqual(material, 'water')
                self.assertTrue(shapes.is_water(pair, grid.at(sx + x, sy + y)), e['id'])

    def test_portable_source_patch_retains_every_tile_and_all_bank_sides(self):
        repo = Repo(ROOT)
        e = next(e for e in self.entries if e['id'] == 'pond-f9c83a2777')
        source, source_pair = load(repo, e['source'])
        _, target_pair = load(repo, 'PalletTown_Frlg')
        water, banks = shapes.portable_patch(repo, target_pair, e)
        sx, sy, _, _ = e['box']
        mask = set(water)
        expected = {(x + dx, y + dy) for x, y in mask for dx in (-1, 0, 1) for dy in (-1, 0, 1)} - mask
        self.assertEqual(set(banks), expected)
        for (x, y), cell in {**water, **banks}.items():
            self.assertEqual(cell, source.at(sx + x, sy + y))
            self.assertLess(cell[0], source_pair.limits['metatiles'])
        with self.assertRaisesRegex(PkError, 'cannot be flipped'):
            shapes.portable_patch(repo, target_pair, e, flip='h')

    def test_outer_lawn_solve_freezes_water_and_complete_bank(self):
        tx = Transaction(Repo(ROOT), 'test copied bank freeze')
        repo = Repo(ROOT, tx)
        grid, pair = load(repo, 'PalletTown_Frlg')
        water = {(8, 9)}
        banks = shapes.mask_ring(water, grid)
        grid.set(8, 9, (0x12B, 0, 1))
        for p in banks:
            grid.set(*p, (0x070, 1, 0))
        frozen = {p: grid.at(*p) for p in water | banks}
        lawn = (0x296, 0, 3)
        shapes.auto_outline(repo, tx, grid, pair, shapes.ShapeRules(repo, pair), (8, 9, 1, 1), set(frozen), lawn=lawn)
        self.assertEqual({p: grid.at(*p) for p in frozen}, frozen)
        self.assertTrue(all(grid.at(*p) == lawn for p in shapes.mask_ring(frozen, grid)))

    def test_failed_placement_restores_pending_writes(self):
        tx = Transaction(Repo(ROOT), 'test shape rollback')
        repo = Repo(ROOT, tx)
        tx.stage('tools/pk/help/shape.md', b'prior pending content')
        prior = dict(tx.writes)

        def fail_after_staging(*args):
            tx.stage('tools/pk/help/shape.md', b'changed pending content')
            tx.stage('tools/pk/help/terrain.md', b'extra pending content')
            raise PkError('CONFLICT', 'test refusal after staging')

        with mock.patch.object(shapes, '_command_place', side_effect=fail_after_staging):
            with self.assertRaises(PkError):
                shapes.command_place(repo, tx, ['place', 'PkShapeTest_Frlg', 'pond-9ed7d101ba', '7,5'], {})
        self.assertEqual(tx.writes, prior)

    def test_event_overlap_is_refused_before_paint(self):
        tx = Transaction(Repo(ROOT), 'test shape event guard')
        repo = Repo(ROOT, tx)
        with mock.patch.object(shapes, 'terrain_apply') as paint:
            with self.assertRaises(PkError) as ctx:
                shapes.command_place(repo, tx, ['place', 'PalletTown_Frlg', 'pond-9ed7d101ba', '5,5'], {})
        self.assertEqual(ctx.exception.code, 'CONFLICT')
        paint.assert_not_called()

    def test_five_ponds_keep_game_water_and_directional_shores(self):
        repo, tx, rows = benchmark()
        for row in rows:
            self.assertEqual(row['status'], 'placed', row)
            self.assertEqual(row['objects_added'], 0, row)
            self.assertFalse(row['reach'].startswith('WARN'), row)
            self.assertEqual(row['fidelity'], 1.0, row)
            self.assertGreaterEqual(row['missing'], 0.15, row)
            self.assertEqual(row['pattern_misses'], 0, row)
            self.assertEqual(row['lawn_misses'], 0, row)
            self.assertEqual(row['unexpected_sand'], 0, row)
            self.assertEqual(row['bank_errors'], 0, row)
            self.assertEqual(row['source_tile_misses'], 0, row)
            self.assertLessEqual(row['seams'], row['baseline_seams'], row)


def benchmark(name='PkShapeMemory_Frlg', render=False, commit=False):
    """Five different ponds on a widened Pallet copy; ordinary tests never commit any map writes."""
    from pk.maps.paintover import copy_map
    from pk.maps import resize
    from pk.maps.meta import render_png
    from pk.maps.objects import check
    from pk.maps import reach
    class PendingRepo(Repo):
        def maps(self):
            maps = super().maps()
            for path, value in self.transaction.writes.items():
                if path.startswith('data/maps/') and path.endswith('/map.json') and value is not None:
                    maps[path.split('/')[2]] = self.json(path)
            return maps

    tx = Transaction(Repo(ROOT), 'shape improved benchmark')
    repo = PendingRepo(ROOT, tx)
    copy_map(repo, tx, 'PalletTown_Frlg', name)
    resize.run(repo, tx, name, {'add': 'right=64,bottom=4', 'fill': '0x296'})
    specs = [('pond-792c2959c3', 28, 8), ('pond-9ed7d101ba', 35, 6),
             ('pond-9c8a08d481', 45, 7), ('pond-29b1fa8baf', 59, 6), ('pond-d5d3f3dadd', 72, 6)]
    entries = {e['id']: e for e in shapes.load_index(repo)}
    rows = []
    for ident, x, y in specs:
        grid, pair = load(repo, name)
        before, objects = list(grid.cells), set(check(repo, grid, pair))
        entry = entries[ident]
        mask = {(x + i, y + j) for i, j, _ in entry['cells']}
        box = (x, y, entry['w'], entry['h'])
        lawn = shapes.lawn_around(grid, pair, box)
        rules = shapes.placement_rules(repo, pair, entry)
        from pk.maps import terrain
        baseline_tx = Transaction(Repo(ROOT), 'shape auto baseline')
        baseline_tx.writes.update(tx.writes)
        baseline_repo = PendingRepo(ROOT, baseline_tx)
        baseline_grid, baseline_pair = load(baseline_repo, name)
        terrain.apply(baseline_repo, baseline_tx, baseline_grid, baseline_pair, rules,
                      {p: 'water' for p in mask}, 'baseline')
        terrain.auto(baseline_repo, baseline_tx, name,
                     f'{x-2},{y-2},{entry["w"]+4}x{entry["h"]+4}', frozen=mask)
        baseline_grid, _ = load(baseline_repo, name)
        footprint = mask | shapes.mask_ring(mask, grid)
        baseline_seams = len(shapes.unseen(baseline_grid, rules, footprint))
        pending = dict(tx.writes)
        try:
            result = shapes.command_place(repo, tx, ['place', name, ident, f'{x},{y}'], {})
        except PkError as exc:
            if tx.writes != pending or load(repo, name)[0].cells != before:
                raise AssertionError('refused pond changed pending writes or map cells') from exc
            rows.append(dict(id=ident, size=f'{entry["w"]}x{entry["h"]}', status='not_placeable',
                             reason=str(exc), details=exc.data, nonportable_bank=len(entry['nonportable_bank']),
                             seams=None, baseline_seams=baseline_seams, objects_added=0,
                             reach=reach.check_write(repo, pair, before, grid), fidelity=None,
                             bank_errors=None, unchanged=True))
            continue
        grid, pair = load(repo, name)
        water = {(i, j) for j in range(grid.h) for i in range(grid.w) if shapes.is_water(pair, grid.at(i, j))}
        rules = shapes.placement_rules(repo, pair, entry)
        footprint = mask | shapes.mask_ring(mask, grid)
        misses = sum(grid.at(*p) not in shapes.shore_candidates(rules, shapes.pattern(water, *p), entry['water_kind'])
                     for p in mask)
        copied_bank = shapes.mask_ring(mask, grid)
        background = shapes.mask_ring(mask | copied_bank, grid)
        lawn_misses = sum(grid.at(*p) != lawn for p in background)
        unexpected_sand = sum('SAND' in pair.behavior_name(grid.at(*p)[0]) for p in background)
        source_water, source_bank = shapes.portable_patch(repo, pair, entry)
        tile_misses = sum(grid.at(x + i, y + j) != cell for (i, j), cell in {**source_water, **source_bank}.items())
        banks_missing = shapes.bank_errors(grid, pair, mask, [lawn[0]])
        rows.append(dict(id=ident, size=f'{entry["w"]}x{entry["h"]}', status='placed',
                         seams=len(shapes.unseen(grid, rules, footprint)), baseline_seams=baseline_seams,
                         objects_added=len(set(check(repo, grid, pair)) - objects),
                         reach=reach.check_write(repo, pair, before, grid),
                         fidelity=len(mask & water) / len(mask),
                         missing=1 - len(mask) / (entry['w'] * entry['h']), pattern_misses=misses,
                         lawn=f'0x{lawn[0]:03X}', lawn_misses=lawn_misses, unexpected_sand=unexpected_sand,
                         bank_errors=len(banks_missing), source_tile_misses=tile_misses,
                         output=result.data))
    if render:
        from PIL import Image, ImageDraw
        import io
        panels = []
        for row in rows:
            e = entries[row['id']]
            if row['status'] == 'not_placeable':
                g, p = load(repo, e['source'])
                sx, sy, w, h = e['box']
                image = Image.open(io.BytesIO(render_png(repo, g, p, (sx-1, sy-1, w+2, h+2), 2))).convert('RGB')
                draw = ImageDraw.Draw(image)
                for bx, by, _ in e['nonportable_bank']:
                    draw.rectangle(((bx+1)*32, (by+1)*32, (bx+2)*32-1, (by+2)*32-1), outline='magenta', width=3)
            else:
                g, p = load(repo, name)
                _, x, y = next(s for s in specs if s[0] == row['id'])
                image = Image.open(io.BytesIO(render_png(repo, g, p, (x-2, y-2, e['w']+4, e['h']+4), 2))).convert('RGB')
            panel = Image.new('RGB', (max(image.width, 330), image.height + 76), '#202020')
            panel.paste(image, (0, 76))
            d = ImageDraw.Draw(panel)
            d.text((5, 5), row['id'], fill='white', font=shapes._font())
            d.text((5, 25), 'SOURCE: NOT PLACEABLE' if row['status'] == 'not_placeable' else 'PLACED',
                   fill='salmon' if row['status'] == 'not_placeable' else 'white', font=shapes._font())
            d.text((5, 45), 'Magenta: secondary bank tiles' if row['status'] == 'not_placeable' else 'Copied water + bank',
                   fill='white', font=shapes._font())
            panels.append(panel)
        image = Image.new('RGB', (sum(p.width for p in panels) + 8 * (len(panels)-1), max(p.height for p in panels)), '#202020')
        x = 0
        for panel in panels:
            image.paste(panel, (x, 0))
            x += panel.width + 8
        path = repo.path('.pk/render/shape_ponds.png')
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, 'PNG')
    if commit:
        tx.commit()
    return repo, tx, rows
