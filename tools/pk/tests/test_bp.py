"""Milestone 5b: blueprints (uncommitted transactions only; real blueprints/Kanto/*.bp)."""
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
from pk.bp import apply
from pk.bp.parse import parse
from pk.maps import reach

KANTO = sorted(p.stem for p in (ROOT/'blueprints/Kanto').glob('*.bp'))


def fresh():
    tx = Transaction(Repo(ROOT), 'test')
    return tx, Repo(ROOT, tx)


class Blueprints(unittest.TestCase):
    def test_parse_sections(self):
        bp = parse((ROOT/'blueprints/Kanto/PalletTown_Frlg.bp').read_text(encoding='utf-8'), 'pallet')
        self.assertEqual(bp['map'], 'PalletTown_Frlg')
        self.assertEqual(bp['size'], (32, 28))
        self.assertEqual(bp['grow'], {'right': 8, 'bottom': 8})
        self.assertEqual(len(bp['sections']['npcs']), 5)

    def test_every_kanto_blueprint_checks_clean_and_is_idempotent(self):
        self.assertGreaterEqual(len(KANTO), 7)
        for name in KANTO:
            tx, repo = fresh()
            first = apply.run(repo, tx, f'blueprints/Kanto/{name}.bp', 'check', {})
            self.assertIn('errors:0', first.summary, name)
            before = dict(tx.writes)
            apply.run(Repo(ROOT, tx), tx, f'blueprints/Kanto/{name}.bp', 'apply', {})
            changed = [k for k in tx.writes if tx.writes[k] != before.get(k) and not k.startswith('.pk/')]
            self.assertEqual(changed, [], f'{name} second apply changed {changed}')

    def test_pallet_details(self):
        tx, repo = fresh()
        apply.run(repo, tx, 'blueprints/Kanto/PalletTown_Frlg.bp', 'apply', {})
        data = json.loads(tx.writes['data/maps/PalletTown_Frlg/map.json'])
        ids = [o.get('local_id') for o in data['object_events']]
        self.assertIn('LOCALID_BP_PALLETTOWN_STARGAZER', ids)
        star = next(o for o in data['object_events'] if o.get('local_id') == 'LOCALID_BP_PALLETTOWN_STARGAZER')
        self.assertTrue(star['flag'].startswith('FLAG_TEMP_'))            # when=night -> hidden by day
        scripts = tx.writes['data/maps/PalletTown_Frlg/scripts.inc'].decode('utf-8')
        self.assertIn('\tcall PalletTown_EventScript_BP_TimeOfDay\n', scripts)   # hooked into OnTransition
        self.assertIn('goto_if_eq VAR_0x8000, 3, Common_EventScript_NopReturn', scripts)
        self.assertIn('PalletTown_Text_BP_beachboy_Night::', scripts)
        # vanilla text and events untouched
        original = (ROOT/'data/maps/PalletTown_Frlg/scripts.inc').read_text(encoding='utf-8')
        self.assertIn('PalletTown_Text_OakDontGoOut::\n\t.string "OAK: Hey! Wait!\\n"', scripts)
        self.assertEqual(len(data['warp_events']), 3)
        self.assertTrue(len(scripts) > len(original))

    def test_policy_rejects_items_and_quest_vars_and_unknown_trainers(self):
        tx, repo = fresh()
        base = (ROOT/'blueprints/Kanto/Route22_Frlg.bp').read_text(encoding='utf-8')
        for extra in ('items\n  potion ball ITEM_POTION @10,22\n',
                      'trainers\n  bob YOUNGSTER @10,22 FACE_UP sight=3 trainer=TRAINER_X\n',
                      'declare\n  var VAR_NEW_QUEST\n'):
            tx.stage('blueprints/Kanto/pk_test_policy.bp', (base + extra).encode('utf-8'))
            with self.assertRaises(PkError):
                apply.run(repo, tx, 'blueprints/Kanto/pk_test_policy.bp', 'plan', {})

    def test_structures_place_stamps(self):
        from pk.maps.grid import decode
        tx, repo = fresh()
        base = (ROOT/'blueprints/Kanto/Route22_Frlg.bp').read_text(encoding='utf-8')
        tx.stage('blueprints/Kanto/pk_test_struct.bp',
                 (base + 'structures\n  oak1 tree @8,21\n  ghost pk_no_such_stamp @12,21\n').encode('utf-8'))
        result = apply.run(repo, tx, 'blueprints/Kanto/pk_test_struct.bp', 'apply', {}).render()
        self.assertIn('structure oak1 tree @8,21', result)
        self.assertIn('stamp pk_no_such_stamp missing', result)
        tree = json.loads((ROOT/'tools/pk/data/stamps/tree.json').read_text(encoding='utf-8'))
        layout = next(l for l in json.loads(tx.writes['data/layouts/layouts.json'])['layouts']
                      if l.get('id') == 'LAYOUT_ROUTE22')
        cells = decode(tx.writes[layout['blockdata_filepath']])
        w = layout['width']
        for j in range(2):
            for i in range(2):
                self.assertEqual(list(cells[(21 + j) * w + 8 + i]), tree['cells'][j][i])

    def test_raw_patches_exact_cells_and_are_idempotent(self):
        from pk.maps.grid import decode
        tx, repo = fresh()
        base = (ROOT/'blueprints/Kanto/Route22_Frlg.bp').read_text(encoding='utf-8')
        bp = base + 'raw 2,3\n  def F 0x004\n  def W 0x001 coll=0 elev=3\n  FF?W\n  F  F\n'
        tx.stage('blueprints/Kanto/pk_test_raw.bp', bp.encode('utf-8'))
        result = apply.run(repo, tx, 'blueprints/Kanto/pk_test_raw.bp', 'apply', {}).render()
        self.assertIn('raw 5 cells', result)
        layout = next(l for l in json.loads(tx.writes['data/layouts/layouts.json'])['layouts']
                      if l.get('id') == 'LAYOUT_ROUTE22')
        path, w = layout['blockdata_filepath'], layout['width']
        cells = decode(tx.writes[path])
        at = lambda x, y: cells[y * w + x]
        self.assertEqual(at(2, 3)[0], 0x004)
        self.assertEqual(at(5, 3), (0x001, 0, 3))
        self.assertEqual(at(5, 4)[0], 0x004)
        before = dict(tx.writes)
        apply.run(Repo(ROOT, tx), tx, 'blueprints/Kanto/pk_test_raw.bp', 'apply', {})
        self.assertEqual(tx.writes[path], before[path])
        tx2, repo2 = fresh()
        tx2.stage('blueprints/Kanto/pk_test_raw.bp', (base + 'raw 2,3\n  QQ\n').encode('utf-8'))
        with self.assertRaises(PkError):
            apply.run(repo2, tx2, 'blueprints/Kanto/pk_test_raw.bp', 'plan', {})

    def test_safeguards_duplicates_water_and_strict(self):
        base = (ROOT/'blueprints/Kanto/Route22_Frlg.bp').read_text(encoding='utf-8')

        def errors(extra):
            tx, repo = fresh()
            tx.stage('blueprints/Kanto/pk_test_safe.bp', (base + extra).encode('utf-8'))
            try:
                apply.run(repo, tx, 'blueprints/Kanto/pk_test_safe.bp', 'check', {})
                return ''
            except PkError as e:
                return str(e) + ' ' + ' '.join(e.data)
        self.assertIn('duplicate structures id t1', errors('structures\n  t1 tree @8,21\n  t1 tree @12,21\n'))
        self.assertIn('overlaps structure t1', errors('structures\n  t1 tree @8,21\n  t2 tree @9,22\n'))
        self.assertIn('covers the keep area', errors('structures\n  t1 tree @4,4\n'))
        self.assertIn('covers', errors('structures\n  t1 tree @22,23\n'))        # the blueprint's new pond
        self.assertEqual(errors('structures\n  t1 tree @8,21\n'), '')
        # strict: unseen seams become errors (a beach cut into the sea on Pallet)
        beach = '\n'.join([
            'blueprint PalletTown_Frlg v1', 'base PalletTown_Frlg', 'size 32x28 grow=right:8,bottom:8 fill=extend',
            'tilesets gTileset_General_Frlg gTileset_PalletTown', 'terrain', '  keep 0,0 22x17',
            '  rect 2,24 28x2 sand', '  circle 16,26 r1 sand', '  rect 0,26 30x2 water', 'vanilla', '  keep all', ''])
        for opts, expect in (({}, 'errors:0'), ({'strict': True}, 'ERR strict: ')):
            tx, repo = fresh()
            tx.stage('blueprints/Kanto/pk_test_strict.bp', beach.encode('utf-8'))
            self.assertIn(expect, apply.run(repo, tx, 'blueprints/Kanto/pk_test_strict.bp', 'plan', opts).render())

    def test_one_way_basin_is_a_trap(self):
        """Route 22's basin with its exit gap walled again: a ledge in, no way out -> TRAP."""
        tx, repo = fresh()
        text = (ROOT/'blueprints/Kanto/Route22_Frlg.bp').read_text(encoding='utf-8')
        self.assertIn('rect 34,19 2x1 grass', text)
        tx.stage('blueprints/Kanto/pk_test_trap.bp', text.replace('rect 34,19 2x1 grass', 'rect 34,19 2x1 rock').encode('utf-8'))
        with self.assertRaises(PkError) as ctx:
            apply.run(repo, tx, 'blueprints/Kanto/pk_test_trap.bp', 'check', {})
        self.assertIn('TRAP state none', str(ctx.exception) + ' '.join(ctx.exception.data))

    def test_later_brush_carves_forest(self):
        from pk.maps.edit import load
        from pk.maps.layers import class_of
        tx, repo = fresh()
        apply.run(repo, tx, 'blueprints/Kanto/Route22_Frlg.bp', 'plan', {})
        grid, pair = load(repo, 'Route22_Frlg')
        self.assertEqual(class_of(pair, *grid.at(22, 23)[:2])[0], '~')      # pond inside the forest base
        self.assertEqual(class_of(pair, *grid.at(2, 28)[:2])[0], '#')       # untouched forest stays

    def test_gate_bypass_is_detected(self):
        """Open Viridian's sealed north-east forest into the new park: the south entrance must
        then reach the gym plateau / north road, and the reachability diff must say so."""
        tx, repo = fresh()
        text = (ROOT/'blueprints/Kanto/ViridianCity_Frlg.bp').read_text(encoding='utf-8')
        text = text.replace('rect 42,0 18x15 forest edge=hard', 'rect 42,4 16x30 grass')
        tx.stage('blueprints/Kanto/pk_test_gate.bp', text.replace('blueprint ViridianCity_Frlg v1', 'blueprint ViridianCity_Frlg v1').encode('utf-8'))
        with self.assertRaises(PkError) as ctx:
            apply.run(repo, tx, 'blueprints/Kanto/pk_test_gate.bp', 'check', {})
        self.assertIn('GATE', str(ctx.exception) + ' '.join(ctx.exception.data))

    def test_vanilla_maps_have_no_self_bypass(self):
        r = Repo(ROOT)
        for name in ('ViridianCity_Frlg', 'PalletTown_Frlg', 'Route2_Frlg', 'PewterCity_Frlg'):
            n, d, l = r.map(name)
            self.assertEqual(reach.diff(r, n, (d, l, r), (d, l, r)), [], name)


if __name__ == '__main__':
    unittest.main()
