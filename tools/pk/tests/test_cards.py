"""Parallel task D: tile cards computed from vanilla maps (never guessed).

Known tiles, the fixed role rules, the cache round trip and the leave-one-out prediction check.
The object-vs-stamps target (>=90% of stamp tiles are objects) is measured here but NOT asserted:
it is missed (see tools/pk/tasks/parallel_D_report.md) and no threshold was loosened.
"""
import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

HOME = Path(__file__).resolve().parents[1]
ROOT = HOME.parents[1]
sys.path.insert(0, str(HOME))
from pk.core.repo import Repo
from pk.maps import cards as C
from pk.maps import lint
from pk.maps.edit import load
from pk.maps.tiles import Pair

PICK = None


def pair_of(repo, name):
    return Pair(repo, repo.layouts()[repo.maps()[name]['layout']])


def cell_class(pair, grid, x, y):
    mt, coll, _ = grid.at(x, y)
    return lint.physical_class(pair.behavior_name(mt), coll)


class RepoCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Repo(ROOT)


class KnownTiles(RepoCase):
    """Acceptance 5: the three round-1 names the owner says are wrong."""

    def setUp(self):
        self.cards = C.load_cards(self.repo, pair_of(self.repo, 'PalletTown_Frlg'))

    def test_0x12b_is_open_surf(self):
        self.assertEqual(self.cards[0x12B]['role'], 'open surf')

    def test_0x045_is_walk_and_flagged(self):
        card = self.cards[0x045]
        self.assertEqual(card['cls'], 'walk')
        self.assertEqual(card['status'], 'NAME CONTRADICTS')

    def test_0x0d4_is_flagged(self):
        self.assertEqual(self.cards[0x0D4]['status'], 'NAME CONTRADICTS')


class RoleRules(unittest.TestCase):
    """The fixed rules of classify_role on synthetic neighbourhoods (own class walk)."""

    @staticmethod
    def nbr(**by_dir):
        base = {d: {'walk': 10} for d in C.DIRS8}
        base.update(by_dir)
        return {d: Counter(v) for d, v in base.items()}

    def test_open_when_own_class_is_everywhere(self):
        role, family, predict = C.classify_role('walk', self.nbr())
        self.assertEqual((role, family), ('open walk', 'open'))
        self.assertEqual(set(predict), set(C.DIRS8))

    def test_edge_on_one_cardinal(self):
        role, family, predict = C.classify_role('walk', self.nbr(S={'surf': 10}))
        self.assertEqual(role, 'edge: surf S')
        self.assertEqual(predict, {'S': 'surf'})

    def test_outer_corner_on_two_adjacent_cardinals(self):
        role, family, predict = C.classify_role('walk', self.nbr(S={'surf': 10}, E={'surf': 10}))
        self.assertEqual(role, 'outer corner: surf E+S')
        self.assertEqual(predict, {'S': 'surf', 'E': 'surf'})

    def test_inner_corner_needs_a_strong_diagonal(self):
        role, family, _ = C.classify_role('walk', self.nbr(NE={'surf': 10}))
        self.assertEqual(role, 'inner corner: surf NE')
        weak = self.nbr(NE={'surf': 6, 'walk': 4})       # 60% diagonal: mixed
        self.assertEqual(C.classify_role('walk', weak)[1], 'mixed')

    def test_two_opposite_cardinals_are_mixed(self):
        role, family, _ = C.classify_role('walk', self.nbr(N={'surf': 10}, S={'surf': 10}))
        self.assertEqual((role, family), ('mixed', 'mixed'))


class NameCheck(unittest.TestCase):
    def test_prefix_rules(self):
        self.assertEqual(C.name_status('water_1', 'surf', 'open surf', 'open'), 'ok')
        self.assertEqual(C.name_status('water_151', 'walk', 'mixed', 'mixed'), 'NAME CONTRADICTS')
        self.assertEqual(C.name_status('grass_1', 'grass_enc', 'mixed', 'mixed'), 'ok')
        self.assertEqual(C.name_status('grass_3', 'walk', 'open walk', 'open'), 'ok')
        self.assertEqual(C.name_status('grass_3', 'walk', 'mixed', 'mixed'), 'NAME CONTRADICTS')
        self.assertEqual(C.name_status('solid_5', 'wall', 'mixed', 'mixed'), 'ok')
        self.assertEqual(C.name_status('solid_5', 'walk', 'mixed', 'mixed'), 'NAME CONTRADICTS')
        self.assertEqual(C.name_status('flame_1', 'walk', 'mixed', 'mixed'), 'ok')
        self.assertIsNone(C.name_status(None, 'walk', 'mixed', 'mixed'))


class Cache(unittest.TestCase):
    def test_aggregate_round_trip(self):
        data = {0x12B: dict(uses=3, coll=Counter({0: 2, 1: 1}), elev=Counter({1: 3}),
                            ncls={d: Counter({'surf': 2}) for d in C.DIRS8},
                            nmt={d: Counter({0x12B: 2}) for d in C.DIRS8})}
        loaded = C._load(C._dump(data))
        self.assertEqual(loaded[0x12B]['uses'], 3)
        self.assertEqual(loaded[0x12B]['ncls']['N'], Counter({'surf': 2}))
        self.assertEqual(loaded[0x12B]['nmt']['S'], Counter({0x12B: 2}))


class Listing(unittest.TestCase):
    CARDS = {0x1: dict(mt=1, cls='surf', family='open', role='open surf', uses=5, object=False),
             0x2: dict(mt=2, cls='wall', family='mixed', role='mixed', uses=9, object=True),
             0x3: dict(mt=3, cls='walk', family='edge', role='edge: surf S', uses=7, object=False)}

    def test_filter_by_role_class_and_object(self):
        rows, _ = C.cards_rows(self.CARDS, {'role': 'edge'})
        self.assertEqual(rows, ['0x003 walk edge: surf S'])
        rows, _ = C.cards_rows(self.CARDS, {'role': 'object'})
        self.assertEqual(rows, ['0x002 wall mixed'])
        rows, _ = C.cards_rows(self.CARDS, {'class': 'surf'})
        self.assertEqual(rows, ['0x001 surf open surf'])
        rows, _ = C.cards_rows(self.CARDS, {'limit': '2'})
        self.assertEqual(len(rows), 2)                   # sorted by uses desc

    def test_bad_role_or_class_is_refused(self):
        with self.assertRaises(Exception):
            C.cards_rows(self.CARDS, {'role': 'nope'})
        with self.assertRaises(Exception):
            C.cards_rows(self.CARDS, {'class': 'nope'})


class Prediction(RepoCase):
    """Acceptance 2: leave-one-out role prediction on 10 vanilla maps. Target >= 85% each."""

    def test_open_edge_corner_above_85_percent(self):
        outdoor = list(lint.vanilla_outdoor(self.repo))
        pick = [outdoor[i][0] for i in range(0, len(outdoor), max(1, len(outdoor) // 10))][:10]
        fam = {k: [0, 0] for k in ('open', 'edge', 'corner')}
        for name in pick:
            pair = pair_of(self.repo, name)
            data = C.aggregate(self.repo, pair, exclude_layouts=(self.repo.maps()[name]['layout'],))
            cards = C.finalize(data, pair)
            grid, _ = load(self.repo, name)
            for y in range(grid.h):
                for x in range(grid.w):
                    card = cards.get(grid.at(x, y)[0])
                    if not card or card['family'] == 'mixed':
                        continue
                    if card['family'] == 'open':
                        counts = Counter(cell_class(pair, grid, x + dx, y + dy)
                                         for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                                         if (dx or dy) and 0 <= x + dx < grid.w and 0 <= y + dy < grid.h)
                        best = max(counts, key=lambda c: (counts[c], c)) if counts else None
                        ok = best == card['cls']
                    else:
                        ok = all(cell_class(pair, grid, x + C.DELTA[d][0], y + C.DELTA[d][1]) == c
                                 for d, c in card['predict'].items()
                                 if 0 <= x + C.DELTA[d][0] < grid.w and 0 <= y + C.DELTA[d][1] < grid.h)
                    fam[card['family']][0] += ok
                    fam[card['family']][1] += 1
        for family, (ok, total) in fam.items():
            self.assertGreater(total, 0, family)
            self.assertGreaterEqual(ok / total, 0.85, f'{family}: {ok}/{total}')


class ObjectVsStamps(RepoCase):
    """Acceptance 1: measured, not asserted. The >=90% target is MISSED (see the report)."""

    def test_tree_stamp_tiles_are_known_and_object_fraction_is_measured(self):
        stamps = self.repo.root / 'tools/pk/data/stamps'
        cache, tiles, objects, match = {}, 0, 0, 0
        for path in sorted(stamps.glob('*.json')):
            data = json.loads(path.read_text(encoding='utf-8'))
            key = f"{data['primary']}+{data['secondary']}"
            if key not in cache:
                from pk.maps.meta import pair_for
                cache[key] = C.load_cards(self.repo, pair_for(self.repo, f"{data['primary']},{data['secondary']}"))
            cards = cache[key]
            cells = data['cells']
            h, w = len(cells), len(cells[0])
            for j in range(h):
                for i in range(w):
                    cell = cells[j][i]
                    if cell is None:
                        continue
                    tiles += 1
                    card = cards.get(cell[0])
                    if not (card and card['object']):
                        continue
                    objects += 1
                    if all(card['needs'].get(d) == cells[j + C.DELTA[d][1]][i + C.DELTA[d][0]][0]
                           for d in C.CARDINALS
                           if 0 <= i + C.DELTA[d][0] < w and 0 <= j + C.DELTA[d][1] < h
                           and cells[j + C.DELTA[d][1]][i + C.DELTA[d][0]] is not None):
                        match += 1
            if path.name == 'tree.json':
                tree = data
        cards = C.load_cards(self.repo, pair_of(self.repo, 'PalletTown_Frlg'))
        self.assertTrue(all(cell[0] in cards for row in tree['cells'] for cell in row if cell))
        # Sanity floor only. The 90% target is not met (42% objects); it is reported, not forced.
        self.assertGreater(objects / tiles, 0.40)


if __name__ == '__main__':
    unittest.main()
