"""The compact protocol must reconstruct every printed collision/elevation cell."""
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pk.core.errors import PkError
from pk.core.out import Result
from pk.maps.layers import render, render_by_class


def decode_view(text, rect):
    """Independent consumer: no access to the source grid or class_of()."""
    x0, y0, w, h = rect
    classes, full, defaults, exceptions, counts = {}, {}, {}, {}, {}
    layer = None
    for row in text.splitlines():
        if row.startswith('layer '):
            layer = row.split()[1]
            if ' by class: ' in row:
                pairs, count = row.split(': ', 1)[1].rsplit('; exceptions ', 1)
                defaults[layer] = {k: int(v, 16) for k, v in
                                   (pair.rsplit('=', 1) for pair in pairs.split())}
                exceptions[layer] = {}
                counts[layer] = int(count)
            else:
                full[layer] = {}
        elif layer and re.match(r'^\d+\|', row):
            yy, values = row.split('|', 1)
            y = int(yy)
            if layer in defaults:
                for pair in values.split():
                    x, value = pair.split('=')
                    exceptions[layer][int(x), y] = int(value, 16)
            elif layer == 'class':
                classes.update({(x0+i, y): sym for i, sym in enumerate(values)})
            else:
                full[layer].update({(x0+i, y): int(v, 16) for i, v in enumerate(values)})
    decoded = {}
    for name in ('coll', 'elev'):
        if name not in defaults:
            decoded[name] = full[name]
            continue
        assert len(exceptions[name]) == counts[name]
        decoded[name] = {}
        for y in range(y0, y0+h):
            for x in range(x0, x0+w):
                cell = (x, y)
                decoded[name][cell] = (exceptions[name][cell] if cell in exceptions[name]
                                       else defaults[name][classes[cell]])
    return decoded


class CompactLayers(unittest.TestCase):
    def fixture(self, differing=0, overlay=False, symbol='='):
        rect = (7, 100, 10, 10)
        cells = {(x, y): (0, int((y-100)*10+x-7 < differing), 3)
                 for y in range(100, 110) for x in range(7, 17)}
        data = {'object_events': [{'x': 16, 'y': 109}]} if overlay else {}
        grid = SimpleNamespace(data=data, at=lambda x, y: cells[x, y])
        pair = SimpleNamespace(info=lambda mt: {'sym': symbol}, behavior_name=lambda mt: 'MB_NORMAL')
        return grid, pair, rect

    def output(self, grid, pair, rect, show_events=True):
        rows = ['layer class', *render(grid, pair, rect, 'class', show_events)]
        for layer in ('coll', 'elev'):
            compact = render_by_class(grid, pair, rect, layer, show_events)
            rows += compact if compact is not None else ['layer '+layer, *render(grid, pair, rect, layer)]
        return Result('test', rows).render()

    def test_exact_ten_percent_and_full_grid_fallback(self):
        for count, fallback in ((0, False), (10, False), (11, True)):
            with self.subTest(count=count):
                grid, pair, rect = self.fixture(count)
                rows = render_by_class(grid, pair, rect, 'coll')
                self.assertEqual(rows is None, fallback)
                decoded = decode_view(self.output(grid, pair, rect), rect)
                self.assertEqual(decoded['coll'], {(x, y): grid.at(x, y)[1]
                                 for y in range(100, 110) for x in range(7, 17)})

    def test_hidden_classes_are_explicit_exceptions(self):
        grid, pair, rect = self.fixture(overlay=True)
        for layer, value in (('coll', 0), ('elev', 3)):
            rows = render_by_class(grid, pair, rect, layer)
            self.assertTrue(rows[0].endswith('; exceptions 1'))
            self.assertEqual(rows[1], f'109| 16={value}')
            self.assertTrue(render_by_class(grid, pair, rect, layer, False)[0].endswith('; exceptions 0'))
        decoded = decode_view(self.output(grid, pair, rect), rect)
        self.assertEqual(decoded['coll'][16, 109], 0)
        self.assertEqual(decoded['elev'][16, 109], 3)

    def test_overlays_count_toward_fallback_threshold(self):
        grid, pair, rect = self.fixture(10, overlay=True)
        self.assertIsNone(render_by_class(grid, pair, rect, 'coll'))
        self.assertIsNotNone(render_by_class(grid, pair, rect, 'coll', False))

    def test_delimiter_symbols_and_hex_values(self):
        for symbol in ('=', ';', '|', ':'):
            with self.subTest(symbol=symbol):
                grid, pair, rect = self.fixture(symbol=symbol)
                grid.at = lambda x, y: (0, 0, 15 if (x, y) == (7, 100) else 3)
                decoded = decode_view(self.output(grid, pair, rect), rect)
                self.assertEqual(decoded['elev'][7, 100], 15)
                self.assertEqual(decoded['elev'][16, 109], 3)

    def test_ties_use_lower_value_and_output_is_deterministic(self):
        grid, pair, rect = self.fixture()
        grid.at = lambda x, y: (int(y == 100 and x < 9), int((x, y) == (8, 100)), 3)
        pair.info = lambda mt: {'sym': '=' if mt else '.'}
        rows = render_by_class(grid, pair, rect, 'coll')
        self.assertIn('==0', rows[0])
        self.assertEqual(rows[1:], ['100| 8=1'])
        self.assertEqual(rows, render_by_class(grid, pair, rect, 'coll'))

    def test_invalid_layer_rejected(self):
        grid, pair, rect = self.fixture()
        with self.assertRaises(PkError):
            render_by_class(grid, pair, rect, 'class')


if __name__ == '__main__':
    unittest.main()
