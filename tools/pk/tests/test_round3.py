import io
import sys
import unittest
from pathlib import Path

HOME = Path(__file__).resolve().parents[1]
ROOT = HOME.parents[1]
sys.path.insert(0, str(HOME))
from PIL import Image
from pk.core.repo import Repo
from pk.core.journal import Transaction
from pk.maps import kit, edit, meta
from pk.maps.paintover import copy_map
from pk.core.errors import PkError
from pk.maps import lint
from unittest import mock


class MemoryRepo(Repo):
    def maps(self):
        maps = super().maps()
        for path, data in self.transaction.writes.items():
            if path.startswith('data/maps/') and path.endswith('/map.json') and data is not None:
                maps[path.split('/')[2]] = self.json(path)
        return maps


class PortableTest(unittest.TestCase):
    def setUp(self):
        self.tx = Transaction(Repo(ROOT), 'round3 validation')
        self.repo = MemoryRepo(ROOT, self.tx)
        self.target = copy_map(self.repo, self.tx, 'PalletTown_Frlg', 'PkR3Portable_Frlg')[0]

    def test_render_portable(self):
        entries = kit.load_index(self.repo)
        grid, pair = edit.load(self.repo, self.target)
        seen = set()
        for entry in entries:
            if not entry['portable'] or entry['map'] in seen:
                continue
            if entry['pair'].split('+')[0] != kit.pair_name(pair).split('+')[0]:
                continue
            # Test different secondary tilesets, where portability matters.
            if entry['pair'] == kit.pair_name(pair):
                continue
            source, sp = edit.load(self.repo, entry['map'])
            rect = entry['x'], entry['y'], entry['w'], entry['h']
            before = Image.open(io.BytesIO(meta.render_png(self.repo, source, sp, rect))).convert('RGB')
            edit.command(self.repo, self.tx, ['copy', self.target,
                         f"{entry['x']},{entry['y']},{entry['w']}x{entry['h']}", '0,0'], {'from': entry['map']})
            dest, dp = edit.load(self.repo, self.target)
            after = Image.open(io.BytesIO(meta.render_png(self.repo, dest, dp,
                               (0, 0, entry['w'], entry['h'])))).convert('RGB')
            different = sum(a != b for a, b in zip(before.get_flattened_data(), after.get_flattened_data()))
            print(f"RENDER {entry['map']} {rect} different_pixels={different}", flush=True)
            self.assertEqual(different, 0, 'STOP: render target missed')
            seen.add(entry['map'])
            if len(seen) == 5:
                break
        self.assertEqual(len(seen), 5)

    def test_fit_sources(self):
        entries = kit.load_index(self.repo)
        # Fixed small box on Pallet's northwest boundary.
        name, edges, notes, hits, counts = kit.fit(self.repo, self.target, (0, 0, 2, 2), entries, 200)
        sources = {e['map'] for _, e in hits}
        print(f'FIT {name} 0,0,2x2 sources={len(sources)} matches={counts["matched"]}', flush=True)
        self.assertGreaterEqual(len(sources), 10, 'STOP: fit target missed')
        self.assertTrue(all((e['w'], e['h']) == (2, 2) for _, e in hits))

    def test_secondary_ids_are_refused(self):
        source, pair = edit.load(self.repo, 'CeruleanCity_Frlg')
        x, y = next((x, y) for y in range(source.h) for x in range(source.w)
                    if source.at(x, y)[0] >= pair.limits['metatiles'])
        with self.assertRaises(PkError) as caught:
            edit.command(self.repo, self.tx, ['copy', self.target, f'{x},{y},1x1', '0,0'],
                         {'from': source.name})
        self.assertEqual(caught.exception.code, 'CONFLICT')
        self.assertIn(f'0x{source.at(x, y)[0]:03X}', str(caught.exception))

    def test_fit_filters_near_before_limiting(self):
        grid, pair = edit.load(self.repo, self.target)
        edges, _ = kit.ring_edges(grid, pair, (0, 0, 2, 2))
        base = dict(x=0, y=0, pair=kit.pair_name(pair), edges=edges, features=[], curvy=0)
        entries = [dict(base, map='Near', w=3, h=2), dict(base, map='Exact', w=2, h=2)]
        hits = kit.fit(self.repo, self.target, (0, 0, 2, 2), entries, 1)[3]
        self.assertEqual([e['map'] for _, e in hits], ['Exact'])

    def test_find_requires_portability_and_shared_primary(self):
        base = dict(map='Source', x=0, y=0, w=2, h=2, edges=dict.fromkeys(kit.SIDES, 'walk'),
                    features=[], curvy=0)
        entries = [dict(base, pair='A+B', portable=True), dict(base, pair='A+C', portable=False),
                   dict(base, pair='D+B', portable=True)]
        hits, _ = kit.find(entries, pair='A+Target')
        self.assertEqual([e['pair'] for _, e in hits], ['A+B'])


class AnnotationTest(unittest.TestCase):
    def test_every_warn_draws_one_box(self):
        repo = Repo(ROOT)
        grid, pair = edit.load(repo, 'PkPalletMuse_Frlg')
        warns = [row for row in lint.run(repo, grid.name, show_all=True).data if row.startswith('WARN ')]
        original = __import__('PIL.ImageDraw', fromlist=['ImageDraw']).ImageDraw.rectangle
        calls = []

        def record(pen, *args, **kwargs):
            calls.append(args[0])
            return original(pen, *args, **kwargs)

        with mock.patch('PIL.ImageDraw.ImageDraw.rectangle', record):
            png, count, notes = meta.render_annotated(repo, grid, pair, (0, 0, grid.w, grid.h))
        print(f'ANNOTATE warnings={len(warns)} boxes={len(calls)}', flush=True)
        self.assertGreater(len(warns), 0)
        self.assertEqual(len(calls), len(warns))
        self.assertEqual(count, len(warns))
        self.assertEqual(notes, [])
        self.assertEqual(Image.open(io.BytesIO(png)).size, (grid.w * 16 + 56, grid.h * 16 + 56))

    def test_all_colors_and_crop_skip_info(self):
        image = io.BytesIO()
        Image.new('RGB', (160, 160)).save(image, 'PNG')
        rows = ['WARN SEAM h 0x001-0x002 @1,1', 'WARN RARE v 0x001-0x002 @2,2',
                'WARN STAIR water|ground 3,3-4,4 run:2', 'WARN RECT water 5,5-6,6 100%',
                'WARN EMPTY 7,7-8,8']
        png, count, notes = meta.annotate_png(image.getvalue(), (0, 0, 10, 10), 1, rows)
        self.assertEqual(count, len(rows))
        _, count, notes = meta.annotate_png(image.getvalue(), (20, 20, 10, 10), 1, rows)
        self.assertEqual(count, 0)
        self.assertTrue(all(line.startswith('INFO') for line in notes))
        self.assertEqual(len(notes), len(rows))


if __name__ == '__main__':
    unittest.main()

