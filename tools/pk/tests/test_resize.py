"""Milestone 4: smart resize, extend-to, new maps, stamps (uncommitted transactions only)."""
import json
from pathlib import Path
import sys
import unittest

HOME = Path(__file__).resolve().parents[1]
ROOT = HOME.parents[1]
sys.path.insert(0, str(HOME))
from pk.core.errors import PkError
from pk.core.repo import Repo
from pk.core.journal import Transaction
from pk.maps.grid import decode
from pk.maps import resize, create


def pagination(opts, default=20):
    return int(opts.get('offset', 0)), int(opts.get('limit', default))


class Base(unittest.TestCase):
    def setUp(self):
        self.tx = Transaction(Repo(ROOT), 'test')
        self.repo = Repo(ROOT, self.tx)

    def staged_json(self, path):
        return json.loads(self.tx.writes[path])

    def resize(self, name, **opts):
        return resize.run(self.repo, self.tx, name, opts)


class Includes(unittest.TestCase):
    def text(self):
        return '\n'.join(f'\t.include "data/maps/{n}/scripts.inc"' for n in ('Alpha', 'Beta')) + \
               '\n\t.include "data/text/x.inc"'

    def test_appends_missing_after_last_map_include(self):
        from pk.core.build import add_map_includes
        out = add_map_includes(self.text(), ['Gamma'])
        self.assertIn('\t.include "data/maps/Gamma/scripts.inc"\n\t.include "data/text/x.inc"', out)

    def test_idempotent_and_keeps_existing(self):
        from pk.core.build import add_map_includes
        once = add_map_includes(self.text(), ['Gamma'])
        self.assertEqual(add_map_includes(once, ['Gamma']), once)

    def test_no_map_includes_prepends(self):
        from pk.core.build import add_map_includes
        out = add_map_includes('\t.include "data/text/x.inc"\n', ['New_Map'])
        self.assertTrue(out.startswith('\t.include "data/maps/New_Map/scripts.inc"\n'))


class Resize(Base):
    def test_grow_right_bottom_keeps_vanilla_coordinates(self):
        before = decode((ROOT/'data/layouts/PalletTown_Frlg/map.bin').read_bytes())
        result = self.resize('PalletTown_Frlg', add='right=8,bottom=8')
        self.assertIn('24x20 -> 32x28 events:0 scripts:0 conns:0', result.summary)
        cells = decode(self.tx.writes['data/layouts/PalletTown_Frlg/map.bin'])
        self.assertEqual(len(cells), 32 * 28)
        for y in range(20):
            self.assertEqual(cells[y*32:y*32+24], before[y*24:y*24+24])
        self.assertEqual(cells[27*32+31], before[19*24+23])          # extend: corner repeats corner
        layout = next(x for x in self.staged_json('data/layouts/layouts.json')['layouts'] if x['id'] == 'LAYOUT_PALLET_TOWN')
        self.assertEqual((layout['width'], layout['height']), (32, 28))
        self.assertNotIn('data/maps/PalletTown_Frlg/scripts.inc', self.tx.writes)

    def test_grow_left_top_shifts_everything(self):
        result = self.resize('PalletTown_Frlg', add='left=2,top=1')
        data = self.staged_json('data/maps/PalletTown_Frlg/map.json')
        self.assertEqual((data['object_events'][0]['x'], data['object_events'][0]['y']), (5, 11))
        self.assertEqual((data['warp_events'][0]['x'], data['warp_events'][0]['y']), (8, 8))
        # up/down connections move by the added columns, on both sides
        self.assertEqual([c['offset'] for c in data['connections']], [2, 2])
        route1 = self.staged_json('data/maps/Route1_Frlg/map.json')
        self.assertEqual(next(c for c in route1['connections'] if c['map'] == 'MAP_PALLET_TOWN')['offset'], -2)
        scripts = self.tx.writes['data/maps/PalletTown_Frlg/scripts.inc'].decode('utf-8')
        self.assertIn('setobjectxyperm LOCALID_PALLET_SIGN_LADY, 7, 16', scripts)    # was 5, 15
        heal = self.staged_json('src/data/heal_locations.json')
        pallet = next(h for h in heal['heal_locations'] if h['id'] == 'HEAL_LOCATION_PALLET_TOWN')
        self.assertEqual((pallet['x'], pallet['y']), (8, 9))                        # was 6, 8
        self.assertIn('scripts:', result.summary)

    def test_round_trip_restores_bytes(self):
        self.resize('PalletTown_Frlg', add='left=3,top=3')
        self.resize('PalletTown_Frlg', add='left=-3,top=-3')
        for path, data in self.tx.writes.items():
            if path.startswith('.pk/'):
                continue
            self.assertEqual(data, (ROOT/path).read_bytes(), path)

    def test_limits_and_crops(self):
        with self.assertRaises(PkError) as ctx:
            self.resize('ViridianCity_Frlg', to='200x200')
        self.assertEqual(ctx.exception.code, 'LIMIT')
        with self.assertRaises(PkError) as ctx:
            self.resize('PalletTown_Frlg', add='right=-10')        # would cut warps
        self.assertEqual(ctx.exception.code, 'BOUNDS')

    def test_shared_layout_needs_fork(self):
        maps = Repo(ROOT).maps()
        shared = {}
        for name, d in maps.items():
            shared.setdefault(d['layout'], []).append(name)
        name = next(v[0] for v in shared.values() if len(v) > 1)
        with self.assertRaises(PkError) as ctx:
            self.resize(name, add='bottom=1')
        self.assertEqual(ctx.exception.code, 'CONFLICT')

    def test_extend_to(self):
        result = resize.extend_to(self.repo, self.tx, 'Route1_Frlg', 'ViridianCity_Frlg', {})
        self.assertIn('24x40 -> 48x40', result.summary)   # Viridian (48 wide) spans x -12..35 of Route 1


class Create(Base):
    def test_new_map(self):
        result = create.new_map(self.repo, self.tx, 'PalletTown_Pond_Frlg',
                                dict(size='16x12', tilesets='gTileset_General_Frlg,gTileset_PalletTown', fill='0x001', type='route'))
        self.assertIn('MAP_PALLET_TOWN_POND', result.summary)
        groups = self.staged_json('data/maps/map_groups.json')
        self.assertEqual(groups['group_order'][-1], create.NEW_GROUP)
        self.assertEqual(groups[create.NEW_GROUP][-1], 'PalletTown_Pond_Frlg')
        self.assertEqual(len(self.tx.writes['data/layouts/PalletTown_Pond_Frlg/map.bin']), 16 * 12 * 2)
        data = self.staged_json('data/maps/PalletTown_Pond_Frlg/map.json')
        self.assertEqual(data['layout'], 'LAYOUT_PALLET_TOWN_POND_FRLG')
        scripts = self.tx.writes['data/event_scripts.s'].decode('utf-8')
        rows = scripts.splitlines()
        mine = [i for i, l in enumerate(rows) if 'data/maps/PalletTown_Pond_Frlg/scripts.inc' in l]
        self.assertEqual(len(mine), 1, 'map include missing or duplicated in data/event_scripts.s')
        rest = [i for i, l in enumerate(rows) if 'scripts.inc"' in l and 'data/maps/' in l]
        self.assertEqual(max(rest), mine[0], 'include not after the last pre-existing map include')
        with self.assertRaises(PkError):
            create.new_map(self.repo, self.tx, 'PalletTown_Frlg', dict(size='4x4', tilesets='a,b', fill='0'))

    def test_stamp_extract_and_place(self):
        create.stamp(self.repo, self.tx, ['extract', 'PalletTown_Frlg', '5,4,5x4', 'pk_test_house'], {}, pagination)
        stamp = json.loads(self.tx.writes['tools/pk/data/stamps/pk_test_house.json'])
        self.assertEqual(stamp['size'], [5, 4])
        create.stamp(self.repo, self.tx, ['place', 'PalletTown_Frlg', 'pk_test_house', '2,12'], {}, pagination)
        cells = decode(self.tx.writes['data/layouts/PalletTown_Frlg/map.bin'])
        before = decode((ROOT/'data/layouts/PalletTown_Frlg/map.bin').read_bytes())
        for j in range(4):
            for i in range(5):
                self.assertEqual(cells[(12+j)*24+2+i], before[(4+j)*24+5+i])


if __name__ == '__main__':
    unittest.main()
