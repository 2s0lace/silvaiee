#!/usr/bin/env python3
"""aiceus backend: a read-only HTTP view over pk for a human Art Director.

Every answer is produced by running `pk ... --json`; this server never reads map.bin,
map.json or scripts.inc itself. Run `python aiceus/server.py` from the game repo root;
it listens on 127.0.0.1 only (default port 8765). The only file it writes itself is
.pk/aiceus/tickets.md; PNGs are written by `pk map render` into .pk/render/.

Routes (all JSON unless noted; errors are {"ok": false, "error": "CODE message"}):
  GET  /                                  aiceus/index.html, or a one-line placeholder
  GET  /api/maps                          outdoor FRLG maps and Pk* maps
  GET  /api/map/NAME                      size, stratum, lint summary + parsed WARN/INFO
  GET  /api/render/NAME.png?scale=1..4    the PNG rendered by pk (cached until the map changes)
  GET  /api/tickets                       the lines of .pk/aiceus/tickets.md
  POST /api/ticket                        {"map","x","y","w","h","note"} -> appended line
"""
import argparse
import json
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

PK = 'tools/pk/pk.py'
OUTDOOR = {'MAP_TYPE_TOWN', 'MAP_TYPE_CITY', 'MAP_TYPE_ROUTE', 'MAP_TYPE_OCEAN_ROUTE'}
SCALES = (1, 2, 3, 4)
POS_RE = re.compile(r'@(\d+),(\d+)')
BOX_RE = re.compile(r'(\d+),(\d+)-(\d+),(\d+)')
PLACEHOLDER = ('<!doctype html><meta charset="utf-8"><title>aiceus</title>'
               '<p>aiceus backend is running. aiceus/index.html is not installed yet.</p>\n')


class ApiError(Exception):
    """A failure with an HTTP-worthy code; the handler turns it into JSON."""

    def __init__(self, code, message):
        super().__init__(f'{code} {message}')
        self.code = code
        self.message = message


@dataclass
class Config:
    root: Path
    tickets: Path
    index: Path


# --- pk: every fact comes from here ---------------------------------------------

def run_pk(cfg, args, timeout=300):
    """Run one read-only pk command with --json and return its parsed object."""
    proc = subprocess.run([sys.executable, PK, *args, '--json'], cwd=str(cfg.root),
                          capture_output=True, text=True, encoding='utf-8', errors='replace',
                          timeout=timeout)
    try:
        doc = json.loads(proc.stdout)
    except ValueError:
        raise ApiError('INTERNAL', f'pk {" ".join(args)} produced no JSON') from None
    if not doc.get('ok'):
        raise ApiError(doc.get('code') or 'ERROR', doc.get('summary') or 'pk failed')
    return doc


def run_pk_pages(cfg, args):
    """Follow MORE n / --offset so no row is dropped silently."""
    doc, rows, offset = run_pk(cfg, args), [], None
    while True:
        rows += doc.get('data') or []
        offset = doc.get('more')
        if offset is None:
            return doc, rows
        doc = run_pk(cfg, [*args, '--offset', str(offset)])


_catalog_lock = threading.Lock()
_catalog = None


def catalog(cfg):
    """name -> {type, w, h, layout_dir} for every map, from one `pk map ls --all`."""
    global _catalog
    with _catalog_lock:
        if _catalog is None:
            _, rows = run_pk_pages(cfg, ['map', 'ls', '--all'])
            table = {}
            for row in rows:
                name, size, _tilesets, map_type, layout_dir = row.split(' ')
                w, h = size.split('x')
                table[name] = {'type': map_type, 'w': int(w), 'h': int(h), 'layout_dir': layout_dir}
            _catalog = table
        return _catalog


# --- endpoints ------------------------------------------------------------------

def api_maps(cfg):
    maps = [{'name': name, 'type': info['type'], 'w': info['w'], 'h': info['h']}
            for name, info in catalog(cfg).items()
            if name.startswith('Pk') or (name.endswith('_Frlg') and info['type'] in OUTDOOR)]
    return {'ok': True, 'maps': sorted(maps, key=lambda m: m['name'])}


def parse_lint_line(text):
    """One lint row -> {check,x,y,w,h,text}. @x,y is 1x1, x,y-x2,y2 is a box (RECT/EMPTY/STAIR),
    a line with no position (INFO) is x=y=-1. Every line becomes an item; nothing is dropped."""
    parts = text.split()
    check = parts[1] if parts[:1] == ['WARN'] and len(parts) > 1 else 'INFO'
    x = y = -1
    w = h = 1
    hit = POS_RE.search(text)
    if hit:
        x, y = int(hit.group(1)), int(hit.group(2))
    else:
        hit = BOX_RE.search(text)
        if hit:
            x0, y0, x1, y1 = (int(v) for v in hit.groups())
            x, y, w, h = x0, y0, x1 - x0 + 1, y1 - y0 + 1
    return {'check': check, 'x': x, 'y': y, 'w': w, 'h': h, 'text': text}


def api_map(cfg, name):
    table = catalog(cfg)
    if name not in table:
        raise ApiError('NOTFOUND', f'no map {name}')
    info = table[name]
    doc, rows = run_pk_pages(cfg, ['map', 'lint', name, '--all'])
    match = re.search(r'stratum:(\w+)', doc.get('summary', ''))
    return {'ok': True, 'name': name, 'w': info['w'], 'h': info['h'],
            'stratum': match.group(1) if match else '',
            'summary': doc.get('summary', ''),
            'lint': [parse_lint_line(line) for line in rows]}


def _newest_mtime(path):
    newest = 0.0
    if path.is_dir():
        for item in path.rglob('*'):
            if item.is_file():
                newest = max(newest, item.stat().st_mtime)
    return newest


def api_render(cfg, name, scale):
    table = catalog(cfg)
    if name not in table:
        raise ApiError('NOTFOUND', f'no map {name}')
    if scale not in SCALES:
        raise ApiError('PARSE', f'scale must be one of {SCALES}')
    png = cfg.root/'.pk'/'render'/f'{name}.s{scale}.png'
    fresh = (png.is_file()
             and png.stat().st_mtime >= _newest_mtime(cfg.root/'data/maps'/name)
             and png.stat().st_mtime >= _newest_mtime(cfg.root/table[name]['layout_dir']))
    if not fresh:
        run_pk(cfg, ['map', 'render', name, '--scale', str(scale), '--out', f'.pk/render/{name}.s{scale}.png'])
    if not png.is_file():
        raise ApiError('INTERNAL', f'pk map render produced no PNG for {name}')
    return png.read_bytes()


def _ticket_line(name, x, y, w, h, note):
    if w == 1 and h == 1:
        return f'{name} @{x},{y}: {note}'
    return f'{name} {x},{y}-{w}x{h}: {note}'


def post_ticket(cfg, payload):
    if not isinstance(payload, dict):
        raise ApiError('PARSE', 'body must be a JSON object')
    name = payload.get('map')
    table = catalog(cfg)
    if not isinstance(name, str) or name not in table:
        raise ApiError('NOTFOUND', f'no map {payload.get("map")!r}')
    try:
        x, y, w, h = (int(payload[key]) for key in ('x', 'y', 'w', 'h'))
    except (KeyError, TypeError, ValueError):
        raise ApiError('PARSE', 'x, y, w and h must be integers') from None
    note = payload.get('note')
    if not isinstance(note, str) or not note.strip():
        raise ApiError('PARSE', 'note must not be empty')
    info = table[name]
    if w < 1 or h < 1 or x < 0 or y < 0 or x + w > info['w'] or y + h > info['h']:
        raise ApiError('BOUNDS', f'box {x},{y}-{w}x{h} is outside {name} {info["w"]}x{info["h"]}')
    line = _ticket_line(name, x, y, w, h, ' '.join(note.split()))
    cfg.tickets.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg.tickets, 'a', encoding='utf-8') as handle:
        handle.write(line + '\n')
    return {'ok': True, 'line': line}


def api_tickets(cfg):
    if not cfg.tickets.is_file():
        return {'ok': True, 'tickets': []}
    return {'ok': True, 'tickets': cfg.tickets.read_text(encoding='utf-8').splitlines()}


# --- HTTP -----------------------------------------------------------------------

STATUS = {'NOTFOUND': 404, 'AMBIGUOUS': 404, 'PARSE': 400, 'BOUNDS': 400}


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'aiceus/1'

    def log_message(self, fmt, *args):
        sys.stderr.write('aiceus %s\n' % (fmt % args))

    def do_GET(self):
        self._dispatch('GET')

    def do_POST(self):
        self._dispatch('POST')

    def _dispatch(self, method):
        cfg = self.server.config
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/') or '/'
        query = parse_qs(parsed.query)
        try:
            if method == 'GET' and path in ('/', '/index.html'):
                body = (cfg.index.read_bytes() if cfg.index.is_file()
                        else PLACEHOLDER.encode('utf-8'))
                self._send(200, 'text/html; charset=utf-8', body)
            elif method == 'GET' and path == '/api/maps':
                self._json(api_maps(cfg))
            elif method == 'GET' and path.startswith('/api/map/'):
                self._json(api_map(cfg, unquote(path[len('/api/map/'):])))
            elif method == 'GET' and path.startswith('/api/render/'):
                rest = unquote(path[len('/api/render/'):])
                if not rest.endswith('.png'):
                    raise ApiError('NOTFOUND', 'render path must end in .png')
                scale = (query.get('scale') or ['1'])[0]
                if not scale.isdigit():
                    raise ApiError('PARSE', 'scale must be an integer')
                self._send(200, 'image/png', api_render(cfg, rest[:-4], int(scale)))
            elif method == 'GET' and path == '/api/tickets':
                self._json(api_tickets(cfg))
            elif method == 'POST' and path == '/api/ticket':
                self._json(post_ticket(cfg, self._body()))
            else:
                raise ApiError('NOTFOUND', f'no route for {method} {path}')
        except ApiError as exc:
            self._send(STATUS.get(exc.code, 500), 'application/json; charset=utf-8',
                       json.dumps({'ok': False, 'error': f'{exc.code} {exc.message}'}).encode('utf-8'))
        except Exception as exc:                                  # never leak a traceback to the client
            self._send(500, 'application/json; charset=utf-8',
                       json.dumps({'ok': False, 'error': f'INTERNAL {exc}'}).encode('utf-8'))

    def _body(self):
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length) if length else b''
        try:
            return json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeError):
            raise ApiError('PARSE', 'body must be JSON') from None

    def _json(self, payload):
        self._send(200, 'application/json; charset=utf-8',
                   json.dumps(payload, ensure_ascii=False).encode('utf-8'))

    def _send(self, status, content_type, body):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)


def main(argv=None):
    parser = argparse.ArgumentParser(description='aiceus: read-only web view over pk (127.0.0.1 only)')
    parser.add_argument('--port', type=int, default=8765, help='TCP port (default 8765)')
    parser.add_argument('--tickets', default=None, help='override the tickets file (tests)')
    parser.add_argument('--index', default=None, help='override the frontend page (tests)')
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    cfg = Config(root=root,
                 tickets=Path(args.tickets) if args.tickets else root/'.pk/aiceus/tickets.md',
                 index=Path(args.index) if args.index else root/'aiceus/index.html')
    httpd = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    httpd.daemon_threads = True
    httpd.config = cfg
    print(f'aiceus serving {root} on http://127.0.0.1:{httpd.server_address[1]} '
          f'(Ctrl-C to stop)', flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == '__main__':
    main()
