"""aiceus backend: start the server on a free port and call every endpoint."""
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

HOME = Path(__file__).resolve().parents[1]
ROOT = HOME.parents[1]
sys.path.insert(0, str(HOME))


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Aiceus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.tickets = Path(cls.tmp.name)/'tickets.md'
        cls.port = free_port()
        cls.base = f'http://127.0.0.1:{cls.port}'
        cls.proc = subprocess.Popen(
            [sys.executable, 'aiceus/server.py', '--port', str(cls.port), '--tickets', str(cls.tickets)],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.time() + 60
        while time.time() < deadline:
            if cls.proc.poll() is not None:
                raise RuntimeError('server exited: ' + cls.proc.stderr.read())
            try:
                with urllib.request.urlopen(cls.base + '/api/tickets', timeout=2) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                time.sleep(0.2)
        raise RuntimeError('server did not start')

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls.proc.stdout.close()
        cls.proc.stderr.close()
        cls.tmp.cleanup()

    def call(self, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode('utf-8')
        request = urllib.request.Request(
            self.base + path, data=data, method='POST' if payload is not None else 'GET',
            headers={'Content-Type': 'application/json'} if payload is not None else {})
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return response.status, response.headers.get_content_type(), response.read()
        except urllib.error.HTTPError as error:
            try:
                return error.code, error.headers.get_content_type(), error.read()
            finally:
                error.close()

    def json_call(self, path, payload=None):
        status, _ctype, body = self.call(path, payload)
        return status, json.loads(body)

    def test_index_is_served(self):
        status, ctype, body = self.call('/')
        self.assertEqual(status, 200)
        self.assertEqual(ctype, 'text/html')
        self.assertIn(b'aiceus', body.lower())  # real page says "Aiceus", placeholder "aiceus"

    def test_maps_catalog(self):
        status, doc = self.json_call('/api/maps')
        self.assertEqual(status, 200)
        self.assertTrue(doc['ok'])
        by_name = {m['name']: m for m in doc['maps']}
        self.assertEqual(by_name['PalletTown_Frlg'],
                         {'name': 'PalletTown_Frlg', 'type': 'MAP_TYPE_TOWN', 'w': 24, 'h': 20})
        self.assertIn('PkPalletOpus_Frlg', by_name)
        self.assertNotIn('PalletTown_PlayersHouse_1F_Frlg', by_name)   # indoor, not outdoor

    def test_map_detail_counts_match_summary(self):
        status, doc = self.json_call('/api/map/PkPalletOpus_Frlg')
        self.assertEqual(status, 200)
        self.assertEqual((doc['w'], doc['h'], doc['stratum']), (24, 28, 'town'))
        seams = int(re.search(r'seams:(\d+)', doc['summary']).group(1))
        rare = int(re.search(r'rare:(\d+)', doc['summary']).group(1))
        self.assertEqual(len([i for i in doc['lint'] if i['check'] == 'SEAM']), seams)
        self.assertEqual(len([i for i in doc['lint'] if i['check'] == 'RARE']), rare)
        self.assertGreater(seams, 12)                                  # --all beats the max_lines cap
        self.assertEqual(set(doc['lint'][0]), {'check', 'x', 'y', 'w', 'h', 'text'})

    def test_bad_map_name(self):
        status, doc = self.json_call('/api/map/NoSuchMap_Frlg')
        self.assertEqual(status, 404)
        self.assertFalse(doc['ok'])
        self.assertTrue(doc['error'].startswith('NOTFOUND'), doc)

    def test_render_png(self):
        status, ctype, body = self.call('/api/render/PalletTown_Frlg.png?scale=1')
        self.assertEqual(status, 200)
        self.assertEqual(ctype, 'image/png')
        self.assertTrue(body.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(self.call('/api/render/PalletTown_Frlg.png?scale=9')[0], 400)

    def test_ticket_round_trip(self):
        status, doc = self.json_call('/api/ticket', {'map': 'PalletTown_Frlg', 'x': 3, 'y': 4,
                                                     'w': 5, 'h': 2, 'note': 'shore looks bare'})
        self.assertEqual(status, 200)
        self.assertEqual(doc['line'], 'PalletTown_Frlg 3,4-5x2: shore looks bare')
        _status, doc = self.json_call('/api/ticket', {'map': 'PalletTown_Frlg', 'x': 1, 'y': 1,
                                                      'w': 1, 'h': 1, 'note': 'odd tile'})
        self.assertEqual(doc['line'], 'PalletTown_Frlg @1,1: odd tile')
        status, doc = self.json_call('/api/tickets')
        self.assertEqual(status, 200)
        self.assertIn('PalletTown_Frlg 3,4-5x2: shore looks bare', doc['tickets'])
        self.assertIn('PalletTown_Frlg @1,1: odd tile', doc['tickets'])

    def test_ticket_outside_map_rejected(self):
        status, doc = self.json_call('/api/ticket', {'map': 'PalletTown_Frlg', 'x': 20, 'y': 18,
                                                     'w': 8, 'h': 8, 'note': 'off the map'})
        self.assertEqual(status, 400)
        self.assertTrue(doc['error'].startswith('BOUNDS'), doc)
        status, doc = self.json_call('/api/ticket', {'map': 'PalletTown_Frlg', 'x': 1, 'y': 1,
                                                     'w': 1, 'h': 1, 'note': '   '})
        self.assertEqual(status, 400)
        self.assertTrue(doc['error'].startswith('PARSE'), doc)


if __name__ == '__main__':
    unittest.main()
