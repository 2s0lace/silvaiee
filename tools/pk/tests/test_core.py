import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

HOME = Path(__file__).resolve().parents[1]
ROOT = HOME.parents[1]
sys.path.insert(0, str(HOME))
from pk.core.errors import PkError
from pk.core.repo import Repo, JsonDocument, resolve
from pk.core.out import Result, page
from pk.core.journal import Transaction, undo, entries, atomic_write, locked
from pk.core.build import verify_result, errors
from pk.maps.grid import decode, encode
from pk.index.builder import ensure
from pk.index.query import find, refs
from pk.batch import plan

spec = importlib.util.spec_from_file_location('pk_cli', HOME/'pk.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class Transactions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Repo(self.tmp.name)
        self.a = self.repo.root/'a'
        self.a.write_bytes(b'original\r\n')

    def tx(self, text=b'changed'):
        tx = Transaction(self.repo, 'test')
        tx.stage('a', text)
        return tx

    def test_create_update_delete_undo_redo(self):
        tx = self.tx()
        tx.stage('new/sub', b'new')
        n = tx.commit()
        self.assertEqual(self.a.read_bytes(), b'changed')
        self.assertEqual((self.repo.root/f'.pk/journal/{n}/0.before').read_bytes(), b'original\r\n')
        undo(self.repo, n)
        self.assertEqual(self.a.read_bytes(), b'original\r\n')
        self.assertFalse((self.repo.root/'new/sub').exists())
        undo(self.repo)
        self.assertEqual(self.a.read_bytes(), b'changed')
        self.assertEqual((self.repo.root/'new/sub').read_bytes(), b'new')
        tx = Transaction(self.repo, 'delete')
        tx.stage('a', None)
        n = tx.commit()
        self.assertFalse(self.a.exists())
        undo(self.repo, n)
        self.assertEqual(self.a.read_bytes(), b'changed')

    def test_noop_and_dry_run_do_not_create_state(self):
        self.assertIsNone(self.tx(b'original\r\n').commit())
        self.assertIsNone(self.tx().commit(True))
        self.assertFalse((self.repo.root/'.pk').exists())

    def test_hash_conflict_even_same_size_mtime(self):
        tx = self.tx()
        stat = self.a.stat()
        self.a.write_bytes(b'external\r\n')
        os.utime(self.a, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        with self.assertRaisesRegex(PkError, 'changed since read'):
            tx.commit()
        self.assertEqual(self.a.read_bytes(), b'external\r\n')

    def test_mtime_only_conflict(self):
        tx = self.tx()
        stat = self.a.stat()
        os.utime(self.a, ns=(stat.st_atime_ns, stat.st_mtime_ns+1_000_000))
        with self.assertRaises(PkError):
            tx.commit()

    def test_new_file_conflict(self):
        tx = Transaction(self.repo, 'new')
        tx.stage('b', b'pk')
        (self.repo.root/'b').write_bytes(b'editor')
        with self.assertRaises(PkError):
            tx.commit()

    def test_undo_refuses_external_edit(self):
        n = self.tx().commit()
        self.a.write_bytes(b'editor')
        with self.assertRaises(PkError):
            undo(self.repo, n)
        self.assertEqual(self.a.read_bytes(), b'editor')

    def test_rollback_on_second_file_failure(self):
        tx = self.tx()
        tx.stage('b', b'new')
        real = atomic_write
        def fail(path, data):
            if path == self.repo.root/'b':
                raise OSError('simulated full disk')
            real(path, data)
        with patch('pk.core.journal.atomic_write', side_effect=fail):
            with self.assertRaises(OSError):
                tx.commit()
        self.assertEqual(self.a.read_bytes(), b'original\r\n')
        self.assertFalse((self.repo.root/'b').exists())
        self.assertEqual(entries(self.repo.root)[-1][1]['state'], 'rolled_back')

    def test_pending_recovery(self):
        n = self.tx().commit()
        manifest_path = self.repo.root/f'.pk/journal/{n}/manifest.json'
        data = json.loads(manifest_path.read_bytes())
        data['state'] = 'prepared'
        manifest_path.write_text(json.dumps(data))
        with self.assertRaisesRegex(PkError, 'incomplete journal'):
            self.tx(b'next').commit()
        undo(self.repo, n)
        self.assertEqual(self.a.read_bytes(), b'original\r\n')

    def test_lock_blocks_another_writer(self):
        with locked(self.repo.root):
            with self.assertRaises(PkError):
                self.tx().commit()

    def test_path_escape_rejected(self):
        for path in ('../outside', self.repo.root.parent/'outside', '.pk/journal/1/manifest.json'):
            with self.subTest(path=path), self.assertRaises(PkError):
                Transaction(self.repo, 'bad').stage(path, b'x')

    def test_batch_overlay_and_atomic_commit(self):
        tx = Transaction(self.repo, 'batch')
        state = Repo(self.repo.root, tx)
        def dispatch(repo, transaction, args):
            if args[0] == 'fail':
                raise PkError('PARSE', 'simulated invalid op')
            transaction.stage('a', repo.read('a') + args[0].encode())
            return Result(args[0]), dict(transaction.writes)
        result = plan(state, tx, 'one\ntwo\n', dispatch)
        self.assertEqual(self.a.read_bytes(), b'original\r\n')
        tx.commit()
        self.assertEqual(self.a.read_bytes(), b'original\r\nonetwo')
        self.assertEqual(len(entries(self.repo.root)), 1)
        tx = Transaction(self.repo, 'failed batch')
        with self.assertRaises(PkError):
            plan(Repo(self.repo.root, tx), tx, 'three\nfail', dispatch)
        self.assertEqual(self.a.read_bytes(), b'original\r\nonetwo')

    def test_batch_rejects_external_side_effects(self):
        for command in ('build', 'undo', 'batch', 'index --json'):
            with self.subTest(command=command), self.assertRaises(PkError):
                plan(self.repo, self.tx(), command, None)


class Protocol(unittest.TestCase):
    def test_json_and_plain(self):
        r = Result('test', ['a  b\tc'])
        self.assertEqual(r.render(), 'OK test\na b c')
        self.assertEqual(json.loads(r.render(True))['data'], ['a b c'])
        self.assertEqual(Result('bad', ok=False, code='PARSE').render(), 'ERR PARSE bad')

    def test_pagination(self):
        self.assertEqual(page('x', ['a','b','c'], 1, 1).render(), 'OK x\nb\nMORE 2')
        for off, lim in [(-1, 1), (0, 0), (0, 21)]:
            with self.assertRaises(PkError):
                page('x', [], off, lim)

    def test_resolution(self):
        aliases = {'PalletTown_Frlg':'PalletTown_Frlg', 'MAP_PALLET_TOWN':'PalletTown_Frlg',
                   'PalletTown_Lab':'PalletTown_Lab'}
        self.assertEqual(resolve('map_pallet_town', aliases), 'PalletTown_Frlg')
        self.assertEqual(resolve('PalletTwon_Frlg', aliases), 'PalletTown_Frlg')
        with self.assertRaises(PkError) as cm:
            resolve('pallet', aliases)
        self.assertEqual(cm.exception.code, 'AMBIGUOUS')
        with self.assertRaises(PkError):
            resolve('xxxxx', aliases)

    def test_invalid_cli_is_bounded_json(self):
        for command in (['bogus'], ['map','ls','--limit','bad'], ['map','info'], ['undo','-1']):
            output, code = cli.main(command+['--json'], root=ROOT)
            self.assertEqual(code, 1)
            self.assertFalse(json.loads(output)['ok'])
            self.assertLess(len(output), 400)

    def test_help_matches_agent(self):
        output, code = cli.main(['help'], root=ROOT)
        self.assertEqual(code, 0)
        self.assertEqual(output.splitlines()[1:], (HOME/'AGENT.md').read_text().splitlines())
        self.assertLessEqual(len(output.splitlines()), 40)

    def test_map_info_golden(self):
        output, code = cli.main(['map','info','PalletTown_Frlg'], root=ROOT)
        self.assertEqual(code, 0)
        self.assertEqual(output+'\n', (HOME/'tests/golden/pallet_info.txt').read_text())
        self.assertLessEqual(len(output.splitlines()), 15)

    def test_unknown_identifier_and_ambiguous_map(self):
        for query, expected in [('pallet', 'AMBIGUOUS'), ('absentxxxx', 'NOTFOUND')]:
            output, code = cli.main(['map','info',query], root=ROOT)
            self.assertEqual(code, 1)
            self.assertTrue(output.startswith(f'ERR {expected} '))


class RealFormats(unittest.TestCase):
    def test_all_map_json_roundtrip(self):
        files = list((ROOT/'data/maps').glob('*/map.json'))
        self.assertGreaterEqual(len(files), 939)
        for file in files:
            with self.subTest(file=file.parent.name):
                raw = file.read_bytes()
                self.assertEqual(JsonDocument(raw).emit(), raw)
                doc = JsonDocument(raw)
                old_music = doc.value['music']
                doc.value['music'] = 'MUS_PK_TEST_ONLY'
                expected = raw.replace(json.dumps(old_music).encode(), b'"MUS_PK_TEST_ONLY"', 1)
                self.assertEqual(doc.emit(), expected)
                doc.value['music'] = old_music
                self.assertEqual(doc.emit(), raw)

    def test_all_grid_roundtrips(self):
        repo = Repo(ROOT)
        for layout in repo.layouts().values():
            for key in ('blockdata_filepath', 'border_filepath'):
                with self.subTest(file=layout[key]):
                    raw = repo.read(layout[key])
                    self.assertEqual(encode(decode(raw)), raw)

    def test_grid_bitfields_and_invalid_values(self):
        self.assertEqual(decode(b'\xff\xff\x00\x04'), [(1023,3,15),(0,1,0)])
        for cells in [[(1024,0,0)], [(0,4,0)], [(0,0,16)], [(-1,0,0)]]:
            with self.assertRaises(PkError):
                encode(cells)
        with self.assertRaises(PkError):
            decode(b'\x00')

    def test_json_edits_preserve_newline_and_order(self):
        doc = JsonDocument(b'{\r\n  "b": 1,\r\n  "a": 2\r\n}\r\n')
        doc.value['a'] = 3
        self.assertEqual(doc.emit(), b'{\r\n  "b": 1,\r\n  "a": 3\r\n}\r\n')

    def test_structural_check(self):
        # Counts every map.json and every layouts.json entry; do not hard-code the total,
        # which grows whenever a benchmark or test map is added.
        repo = Repo(ROOT)
        maps = len(list((ROOT/'data/maps').glob('*/map.json')))
        layouts = len(repo.json('data/layouts/layouts.json')['layouts'])
        self.assertEqual(repo.structural_check(), (maps, layouts))


class Index(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Repo(self.tmp.name)
        self.write('data/layouts/layouts.json', '{"layouts":[]}')
        self.write('data/maps/map_groups.json', '{"group_order":[]}')
        self.write('charmap.txt', "'A' = BB")
        self.write('data/maps/Town/scripts.inc', 'Hello::\n\t.string "Hi\\n"\n\t.string "there!$"\nScript::\n setflag FLAG_TEST\n')
        self.write('include/constants/flags.h', '#define FLAG_TEST 1\n#define FLAG_NEXT 2\n')
        self.write('include/constants/event_objects.h', 'enum {\n OBJ_EVENT_GFX_TEST,\n};\n')
        self.write('include/constants/metatile_behaviors.h', 'enum {\n MB_NORMAL,\n};\n')

    def write(self, key, value):
        path = self.repo.root/key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding='utf-8')

    def index(self):
        tx = Transaction(self.repo, 'index')
        db = ensure(self.repo, tx)
        tx.commit()
        self.addCleanup(db.close)
        return db

    def test_tables_texts_enums_refs_and_cache(self):
        db = self.index()
        self.assertEqual(db.execute('SELECT name,snippet FROM texts').fetchall(), [('Hello','Hi there!')])
        self.assertEqual(db.execute('SELECT name FROM scripts').fetchall(), [('Script',)])
        self.assertEqual(db.execute('SELECT name FROM object_gfx').fetchall(), [('OBJ_EVENT_GFX_TEST',)])
        self.assertEqual(db.execute('SELECT name FROM behaviors').fetchall(), [('MB_NORMAL',)])
        self.assertIn('text Hello', find(db, 'there', 'text').render())
        self.assertIn('scripts.inc:5', refs(self.repo, db, 'FLAG_TEST').render())
        tx = Transaction(self.repo, 'cached')
        cached = ensure(self.repo, tx)
        cached.close()
        self.assertFalse(tx.writes)

    def test_add_remove_modify_invalidation(self):
        self.index()
        self.write('data/text/new.inc', 'New::\n .string "New$"\n')
        db = self.index()
        self.assertEqual(db.execute('SELECT count(*) FROM texts').fetchone()[0], 2)
        (self.repo.root/'data/text/new.inc').unlink()
        self.write('data/maps/Town/scripts.inc', 'Changed::\n .string "Edited$"\n')
        db = self.index()
        self.assertEqual(db.execute('SELECT name FROM texts').fetchall(), [('Changed',)])

    def test_corrupt_cache_recovers_transactionally(self):
        self.index()
        (self.repo.root/'.pk/index.sqlite').write_bytes(b'bad cache')
        self.assertEqual(self.index().execute('SELECT count(*) FROM texts').fetchone()[0], 1)

    def test_find_is_deterministic_and_paginates(self):
        db = self.index()
        self.assertEqual(find(db, 'FLAG', limit=1).render(), find(db, 'FLAG', limit=1).render())
        self.assertEqual(find(db, 'FLAG', limit=1).more, 1)


class Build(unittest.TestCase):
    def test_success_requires_exit_and_firered(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = Path(tmp)/'pokefirered.gba'
            rom.write_bytes(b'\0'*172+b'BPRE'+b'\0'*16)
            verify_result(0, 'EXIT 0\n', rom)
            for rc, log in [(0,'EXIT 2'), (0,'no exit'), (1,'EXIT 0')]:
                with self.assertRaises(PkError):
                    verify_result(rc, log, rom)
            rom.write_bytes(b'\0'*192)
            with self.assertRaises(PkError):
                verify_result(0, 'EXIT 0', rom)

    def test_errors_deduplicated_bounded(self):
        log = '\n'.join([f'x.c:{i}: error: fail' for i in range(30)]*2)
        self.assertEqual(len(errors(log)), 15)

    def test_build_dry_run_no_writes(self):
        output, code = cli.main(['build','--dry-run'], root=ROOT)
        self.assertEqual((output, code), ('OK build dry-run firered', 0))


if __name__ == '__main__':
    unittest.main()
