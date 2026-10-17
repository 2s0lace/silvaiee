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
  GET  /api/render/NAME.png?scale=2&rect=x,y,WxH   one box of the map (piece thumbnails)
  GET  /api/fit/NAME?box=x,y,WxH&limit=8  vanilla pieces that fit the box (`pk kit fit`)
  GET  /api/auto/NAME?box=x,y,WxH         3 preview fills for the box (`pk terrain auto --options 3`)
  GET  /api/tickets                       the lines of .pk/aiceus/tickets.md
  POST /api/ticket                        {"map","x","y","w","h","note"} -> appended line
  POST /api/ticket/delete                 {"index", "line"} -> removes that line (must match exactly)
"""
import argparse
import base64
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
RECT_RE = re.compile(r'^(\d+),(\d+),(\d+)x(\d+)$')
HIT_RE = re.compile(r'^(\S+) (\d+),(\d+),(\d+)x(\d+) (\S+) (\S+) curvy:(\d+)(?: portable:(?:true|false))?$')
AUTO_RE = re.compile(r'^option (\d+): seams (\d+), changed (\d+), png (\S+)$')
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
        more = doc.get('more')
        if more is None or more == offset:      # guard a command that would ignore --offset
            return doc, rows
        offset = more
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
    INFO REPEAT x,y ... 4x4 is a 4x4 REPEAT box, and a line with no position (INFO) is x=y=-1.
    Every line becomes an item; nothing is dropped."""
    parts = text.split()
    if parts[:2] == ['INFO', 'REPEAT'] and len(parts) >= 6 and parts[-1] == '4x4':
        m = re.match(r'^(\d+),(\d+)$', parts[2])
        if m:
            return {'check': 'REPEAT', 'x': int(m.group(1)), 'y': int(m.group(2)), 'w': 4, 'h': 4, 'text': text}
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


def parse_rect(text, info, name):
    """'x,y,WxH' -> (x, y, w, h), checked against the map size."""
    hit = RECT_RE.match(text or '')
    if not hit:
        raise ApiError('PARSE', 'box must look like x,y,WxH')
    x, y, w, h = (int(v) for v in hit.groups())
    if w < 1 or h < 1 or x + w > info['w'] or y + h > info['h']:
        raise ApiError('BOUNDS', f'box {text} is outside {name} {info["w"]}x{info["h"]}')
    return x, y, w, h


_render_lock = threading.Lock()


def api_render(cfg, name, scale, rect=None):
    table = catalog(cfg)
    if name not in table:
        raise ApiError('NOTFOUND', f'no map {name}')
    if scale not in SCALES:
        raise ApiError('PARSE', f'scale must be one of {SCALES}')
    extra, suffix = [], ''
    if rect is not None:
        x, y, w, h = parse_rect(rect, table[name], name)
        extra, suffix = ['--rect', f'{x},{y},{w}x{h}'], f'.r{x}_{y}_{w}x{h}'
    png = cfg.root/'.pk'/'render'/f'{name}.s{scale}{suffix}.png'
    fresh = (png.is_file()
             and png.stat().st_mtime >= _newest_mtime(cfg.root/'data/maps'/name)
             and png.stat().st_mtime >= _newest_mtime(cfg.root/table[name]['layout_dir']))
    if not fresh:
        with _render_lock:          # parallel thumbnail requests made concurrent pk renders fail
            run_pk(cfg, ['map', 'render', name, *extra, '--scale', str(scale),
                         '--out', f'.pk/render/{name}.s{scale}{suffix}.png'])
    if not png.is_file():
        raise ApiError('INTERNAL', f'pk map render produced no PNG for {name}')
    return png.read_bytes()


def _parse_hits(rows):
    """pk kit fit/find rows -> (pieces, infos). Each hit line is followed by its copy command."""
    pieces, infos, unparsed = [], [], []
    for row in rows:
        hit = HIT_RE.match(row)
        if hit:
            src, px, py, pw, ph, pair, features, curvy = hit.groups()
            pieces.append({'map': src, 'x': int(px), 'y': int(py), 'w': int(pw), 'h': int(ph), 'pair': pair,
                           'features': [] if features == '-' else features.split(','),
                           'curvy': int(curvy), 'cmd': ''})
        elif row.startswith('pk map copy ') and pieces and not pieces[-1]['cmd']:
            pieces[-1]['cmd'] = row
        elif row.startswith('INFO'):
            infos.append(row)
        else:
            unparsed.append(row)               # never drop a line silently
    return pieces, infos + [f'INFO aiceus could not parse: {row}' for row in unparsed]


def api_fit(cfg, name, box, limit):
    """`pk kit fit` for the box. If nothing fits exactly, relax step by step with `pk kit find`
    (same tileset pair always, since map copy --from needs it) and say which step answered."""
    table = catalog(cfg)
    if name not in table:
        raise ApiError('NOTFOUND', f'no map {name}')
    x, y, w, h = parse_rect(box, table[name], name)
    if not limit.isdigit() or not 1 <= int(limit) <= 30:
        raise ApiError('PARSE', 'limit must be 1..30')
    doc, rows = run_pk_pages(cfg, ['kit', 'fit', name, f'{x},{y},{w}x{h}', '--limit', limit])
    summary = doc.get('summary', '')
    pieces, infos = _parse_hits(rows)
    exact = [p for p in pieces if (p['w'], p['h']) == (w, h)]
    if exact:          # kit also returns near sizes (+-2); a piece the exact size of the box is what the user drew
        return {'ok': True, 'summary': summary, 'pieces': exact, 'info': infos, 'relaxed': ''}

    edges = [f'--{side}={cls}' for side, cls in re.findall(r'\b(top|bottom|left|right)=(\S+)', summary)
             if cls not in ('free', 'mixed')]
    steps = [('same size, any edges', ['--size', f'{w}x{h}']),     # size first: a piece must fit the box
             ('same edges, any size', [*edges]),
             ('any piece with this tileset', [])]
    info = table[name]
    for label, extra in steps:
        if not extra and label != steps[-1][0]:
            continue
        find_args = ['kit', 'find', '--pair', name, *[a for e in extra for a in e.split('=', 1)],
                     '--limit', '30']
        _doc, found_rows = run_pk_pages(cfg, find_args)
        found, found_infos = _parse_hits(found_rows)
        # Keep pieces that fit inside the map when pasted at the box corner; fill in target and spot.
        fits = [p for p in found if x + p['w'] <= info['w'] and y + p['h'] <= info['h']]
        fits.sort(key=lambda p: abs(p['w'] - w) + abs(p['h'] - h))     # closest to the box size first
        if any((p['w'], p['h']) == (w, h) for p in fits):
            fits = [p for p in fits if (p['w'], p['h']) == (w, h)]
        elif pieces:       # no exact size anywhere: kit fit's near sizes at least match the edges
            return {'ok': True, 'summary': summary, 'pieces': pieces, 'relaxed': 'near sizes',
                    'info': infos + [f'INFO no piece is exactly {w}x{h}; showing near sizes that match the edges']}
        elif fits:
            infos.append(f'INFO no piece is exactly {w}x{h}; showing the closest sizes')
        dropped = len(found) - len(fits)
        if not fits:
            infos.append(f'INFO tried {label}: {len(found)} pieces, none fit inside the map at {x},{y}')
            continue
        for p in fits:
            p['cmd'] = (f'pk map copy {name} {p["x"]},{p["y"]},{p["w"]}x{p["h"]} {x},{y} '
                        f'--from {p["map"]} --repair')
        infos.append(f'INFO no exact fit; relaxed to {label}' +
                     (f' ({dropped} too big for this spot left out)' if dropped else ''))
        return {'ok': True, 'summary': summary, 'pieces': fits[:int(limit)], 'info': infos + found_infos,
                'relaxed': label}
    return {'ok': True, 'summary': summary, 'pieces': [], 'info': infos, 'relaxed': 'nothing found'}


def api_auto(cfg, name, box):
    """`pk terrain auto NAME x,y,WxH --options 3` (read-only preview fills, no map writes)."""
    table = catalog(cfg)
    if name not in table:
        raise ApiError('NOTFOUND', f'no map {name}')
    x, y, w, h = parse_rect(box, table[name], name)
    with _render_lock:
        doc, rows = run_pk_pages(cfg, ['terrain', 'auto', name, f'{x},{y},{w}x{h}', '--options', '3'])
    options, infos, unparsed = [], [], []
    for row in rows:
        hit = AUTO_RE.match(row)
        if hit:
            opt_n, seams, changed, png_rel = hit.groups()
            png_path = cfg.root / png_rel
            b64 = base64.b64encode(png_path.read_bytes()).decode('ascii') if png_path.is_file() else ''
            options.append({'option': int(opt_n), 'seams': int(seams), 'changed': int(changed),
                            'png': png_rel, 'src': f'data:image/png;base64,{b64}' if b64 else f'/{png_rel}',
                            'cmd': ''})
        elif row.startswith('pk terrain auto ') and options and not options[-1]['cmd']:
            options[-1]['cmd'] = row
        elif row.startswith('INFO'):
            infos.append(row)
        else:
            unparsed.append(row)
    infos += [f'INFO aiceus could not parse: {row}' for row in unparsed]
    return {'ok': True, 'summary': doc.get('summary', ''), 'options': options, 'info': infos}


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


def delete_ticket(cfg, payload):
    """Remove one line; the caller must send the exact text, so a stale page can't delete the wrong one."""
    if not isinstance(payload, dict) or not isinstance(payload.get('index'), int):
        raise ApiError('PARSE', 'body must be {"index": n, "line": "..."}')
    lines = api_tickets(cfg)['tickets']
    index = payload['index']
    if not 0 <= index < len(lines) or lines[index] != payload.get('line'):
        raise ApiError('NOTFOUND', 'that ticket is not there any more; reload')
    del lines[index]
    cfg.tickets.write_text(''.join(line + '\n' for line in lines), encoding='utf-8')
    return {'ok': True, 'tickets': lines}


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
                rect = (query.get('rect') or [None])[0]
                self._send(200, 'image/png', api_render(cfg, rest[:-4], int(scale), rect))
            elif method == 'GET' and path.startswith('/api/fit/'):
                self._json(api_fit(cfg, unquote(path[len('/api/fit/'):]), (query.get('box') or [''])[0],
                                   (query.get('limit') or ['8'])[0]))
            elif method == 'GET' and path.startswith('/api/auto/'):
                self._json(api_auto(cfg, unquote(path[len('/api/auto/'):]), (query.get('box') or [''])[0]))
            elif method == 'GET' and path.startswith('/.pk/render/auto_') and path.endswith('.png'):
                target = (cfg.root / path.lstrip('/')).resolve()
                if not target.is_file() or (cfg.root / '.pk/render').resolve() not in target.parents:
                    raise ApiError('NOTFOUND', f'no render {path}')
                self._send(200, 'image/png', target.read_bytes())
            elif method == 'GET' and path == '/api/tickets':
                self._json(api_tickets(cfg))
            elif method == 'POST' and path == '/api/ticket/delete':
                self._json(delete_ticket(cfg, self._body()))
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
