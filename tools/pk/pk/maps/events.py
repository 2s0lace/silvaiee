"""pk ev: list / add / set / rm map events in map.json (one line per event)."""
import re

from ..core.errors import PkError
from ..core.out import Result, page
from ..core.repo import JsonDocument, resolve
from ..index.builder import ensure

KINDS = {'obj': 'object_events', 'warp': 'warp_events', 'coord': 'coord_events', 'bg': 'bg_events'}
SHORT_MOVE = {'WANDER': 'MOVEMENT_TYPE_WANDER_AROUND'}


def strip(value, prefix):
    value = str(value)
    return value[len(prefix):] if value.startswith(prefix) else value


def describe(kind, n, e):
    xy = f"@{e.get('x')},{e.get('y')} e{e.get('elevation', 0)}"
    if kind == 'obj':
        if e.get('type') == 'clone':
            return f"obj#{n} clone {strip(e.get('graphics_id'), 'OBJ_EVENT_GFX_')} {xy} of {e.get('target_map')}#{e.get('target_local_id')}"
        move = strip(e.get('movement_type', ''), 'MOVEMENT_TYPE_')
        rng = f" {e.get('movement_range_x', 0)}x{e.get('movement_range_y', 0)}" if 'WANDER' in move else ''
        trainer = '' if e.get('trainer_type', 'TRAINER_TYPE_NONE') == 'TRAINER_TYPE_NONE' else f" {strip(e['trainer_type'], 'TRAINER_TYPE_')} sight{e.get('trainer_sight_or_berry_tree_id')}"
        flag = '' if str(e.get('flag', '0')) == '0' else f" flag={e['flag']}"
        lid = f"{e['local_id']} " if e.get('local_id') else ''
        return f"obj#{n} {lid}{xy} {strip(e.get('graphics_id'), 'OBJ_EVENT_GFX_')} {move}{rng}{trainer}{flag} -> {e.get('script')}"
    if kind == 'warp':
        return f"warp#{n} {xy} -> {e.get('dest_map')}#{e.get('dest_warp_id')}"
    if kind == 'coord':
        return f"coord#{n} {xy} {e.get('var')}={e.get('var_value')} -> {e.get('script')}"
    if e.get('type') == 'hidden_item':
        return f"bg#{n} hidden {xy} {e.get('item')} flag={e.get('flag')}"
    return f"bg#{n} {e.get('type')} {xy} {strip(e.get('player_facing_dir', ''), 'BG_EVENT_PLAYER_FACING_')} -> {e.get('script')}"


def parse_ref(text):
    m = re.fullmatch(r'(obj|warp|coord|bg)#(\d+)', text)
    if not m:
        raise PkError('PARSE', f'event ref {text!r} must be obj#N, warp#N, coord#N or bg#N')
    return m[1], int(m[2])


def kv(words):
    out = {}
    for w in words:
        if w.startswith('@'):
            m = re.fullmatch(r'@(\d+),(\d+)', w)
            if not m:
                raise PkError('PARSE', f'position {w} must be @x,y')
            out['x'], out['y'] = int(m[1]), int(m[2])
        elif '=' in w:
            k, v = w.split('=', 1)
            out[k] = v
        else:
            raise PkError('PARSE', f'expected key=value or @x,y, got {w!r}')
    return out


class Symbols:
    """Constant lookup with short aliases (WOMAN_1 -> OBJ_EVENT_GFX_WOMAN_1_FRLG)."""
    def __init__(self, repo, tx):
        db = ensure(repo, tx)
        try:
            self.gfx = {n for (n,) in db.execute('SELECT name FROM object_gfx')}
            self.flags = {n for (n,) in db.execute('SELECT name FROM flags')}
            self.vars = {n for (n,) in db.execute('SELECT name FROM vars')}
            self.labels = {n for (n,) in db.execute('SELECT name FROM scripts UNION SELECT name FROM texts')}
            self.trainers = {n for (n,) in db.execute('SELECT name FROM trainers')}
        finally:
            db.close()
        text = repo.read('include/constants/event_object_movement.h').decode('utf-8')
        self.moves = set(re.findall(r'#define\s+(MOVEMENT_TYPE_\w+)\s', text))

    def graphics(self, word, frlg):
        if word in self.gfx:
            return word
        base = 'OBJ_EVENT_GFX_' + word
        for candidate in ([base + '_FRLG', base] if frlg else [base, base + '_FRLG']):
            if candidate in self.gfx:
                return candidate
        return resolve(word, {g: g for g in self.gfx})

    def movement(self, word):
        word = SHORT_MOVE.get(word, word)
        if word in self.moves:
            return word
        if 'MOVEMENT_TYPE_' + word in self.moves:
            return 'MOVEMENT_TYPE_' + word
        return resolve(word, {m: m for m in self.moves})

    def check(self, kind, value):
        pool = {'flag': self.flags, 'var': self.vars, 'label': self.labels}[kind]
        if value in ('0', '0x0', '') or value in pool:
            return value
        raise PkError('NOTFOUND', f'{kind} {value}; use pk find')


def auto_script(repo, tx, map_name, kind, text, ops_text):
    """Generate Map_EventScript_Auto_N + Map_Text_Auto_N using vanilla msgbox patterns."""
    from ..text.ops import Text
    from ..text.strings import emit
    path = f'data/maps/{map_name}/scripts.inc'
    content = repo.read(path).decode('utf-8')
    prefix = map_name[:-5] if map_name.endswith('_Frlg') else map_name
    n = 1
    while re.search(rf'^{prefix}_(EventScript|Text)_Auto_{n}::', content, re.M):
        n += 1
    script, label = f'{prefix}_EventScript_Auto_{n}', f'{prefix}_Text_Auto_{n}'
    source, _ = Text(repo).compose(text)
    box = 'MSGBOX_SIGN' if kind == 'sign' else 'MSGBOX_NPC'
    newline = '\r\n' if '\r\n' in content else '\n'
    if not content.endswith(newline):
        content += newline
    content += newline + newline.join([f'{script}::', f'\tmsgbox {label}, {box}', '\tend']) + newline
    content += newline + emit(label, source, newline=newline)
    tx.stage(path, content.encode('utf-8'))
    return script


def command(repo, tx, args, opts, pagination):
    verb, rest = args[0], args[1:]
    if not rest:
        raise PkError('PARSE', 'usage: pk ev ls|add|set|rm <map> ...')
    name, _, layout = repo.map(rest[0])
    path = f'data/maps/{name}/map.json'
    doc = JsonDocument(repo.read(path))
    data = doc.value
    w, h = layout['width'], layout['height']
    frlg = layout.get('layout_version') == 'frlg'
    if verb == 'ls' and len(rest) == 1:
        rows = [describe(k, n, e) for k, key in KINDS.items() for n, e in enumerate(data.get(key) or [])]
        return page(f'ev {name} {len(rows)}', rows, *pagination(opts, 30), maximum=60)
    sym = Symbols(repo, tx)

    def place(e, fields):
        if 'x' in fields:
            if not (0 <= fields['x'] < w and 0 <= fields['y'] < h):
                raise PkError('BOUNDS', f"@{fields['x']},{fields['y']} outside {w}x{h}")
            e['x'], e['y'] = fields['x'], fields['y']
        if 'elev' in fields:
            e['elevation'] = int(fields['elev'])

    if verb == 'add' and len(rest) >= 3:
        kind, fields = rest[1], kv(rest[2:])
        if 'x' not in fields:
            raise PkError('PARSE', 'ev add needs @x,y')
        if kind in ('obj', 'sign') and 'text' in fields and 'script' in fields:
            raise PkError('PARSE', 'use text= or script=, not both')
        if kind == 'obj':
            e = {'type': 'object', 'graphics_id': sym.graphics(fields.get('gfx', 'BOY'), frlg), 'x': 0, 'y': 0,
                 'elevation': 3, 'movement_type': sym.movement(fields.get('move', 'FACE_DOWN')),
                 'movement_range_x': 0, 'movement_range_y': 0, 'trainer_type': 'TRAINER_TYPE_NONE',
                 'trainer_sight_or_berry_tree_id': '0', 'script': '0x0', 'flag': '0'}
            if fields.get('id'):
                if not re.fullmatch(r'LOCALID_\w+', fields['id']):
                    raise PkError('PARSE', 'id= must be LOCALID_*')
                e = {'local_id': fields['id'], **e}
            if 'range' in fields:
                rx, ry = fields['range'].split('x')
                e['movement_range_x'], e['movement_range_y'] = int(rx), int(ry)
            if 'flag' in fields:
                e['flag'] = sym.check('flag', fields['flag'])
            key = 'object_events'
        elif kind == 'warp':
            m = re.fullmatch(r'(\w+)#(\d+)', fields.get('to', ''))
            if not m:
                raise PkError('PARSE', 'warp needs to=<map>#<warp index>')
            dest, _, _ = repo.map(m[1])
            dest_data = repo.json(f'data/maps/{dest}/map.json')
            if int(m[2]) >= len(dest_data.get('warp_events') or []):
                raise PkError('BOUNDS', f'{dest} has no warp #{m[2]}')
            e = {'x': 0, 'y': 0, 'elevation': 0, 'dest_map': dest_data['id'], 'dest_warp_id': m[2]}
            key = 'warp_events'
        elif kind == 'sign':
            e = {'type': 'sign', 'x': 0, 'y': 0, 'elevation': 0, 'player_facing_dir': 'BG_EVENT_PLAYER_FACING_ANY', 'script': '0x0'}
            key = 'bg_events'
        elif kind == 'trigger':
            m = re.fullmatch(r'(\w+)=(\w+)', fields.get('var', ''))
            if not m or 'script' not in fields:
                raise PkError('PARSE', 'trigger needs var=VAR_X=value and script=Label')
            e = {'type': 'trigger', 'x': 0, 'y': 0, 'elevation': 3, 'var': sym.check('var', m[1]), 'var_value': m[2], 'script': '0x0'}
            key = 'coord_events'
        else:
            raise PkError('PARSE', 'ev add kind is obj|warp|sign|trigger')
        place(e, fields)
        if kind in ('obj', 'sign', 'trigger'):
            if 'text' in fields:
                e['script'] = auto_script(repo, tx, name, kind, fields['text'], None)
            elif 'script' in fields:
                e['script'] = sym.check('label', fields['script'])
        data.setdefault(key, []).append(e)
        tx.stage(path, doc.emit())
        short = {'object_events': 'obj', 'warp_events': 'warp', 'bg_events': 'bg', 'coord_events': 'coord'}[key]
        return Result(f'ev add {name}', [describe(short, len(data[key]) - 1, e)])
    if verb == 'set' and len(rest) >= 3:
        kind, n = parse_ref(rest[1])
        items = data.get(KINDS[kind]) or []
        if n >= len(items):
            raise PkError('NOTFOUND', f'{rest[1]} in {name}; use pk ev ls')
        e, fields = items[n], kv(rest[2:])
        place(e, {k: v for k, v in fields.items() if k in ('x', 'y', 'elev')})
        for k, v in fields.items():
            if k in ('x', 'y', 'elev'):
                continue
            if k == 'gfx':
                e['graphics_id'] = sym.graphics(v, frlg)
            elif k == 'move':
                e['movement_type'] = sym.movement(v)
            elif k == 'range':
                rx, ry = v.split('x')
                e['movement_range_x'], e['movement_range_y'] = int(rx), int(ry)
            elif k == 'script':
                e['script'] = sym.check('label', v)
            elif k == 'flag':
                e['flag'] = sym.check('flag', v)
            elif k == 'text' and kind in ('obj', 'bg'):
                e['script'] = auto_script(repo, tx, name, 'sign' if kind == 'bg' else 'obj', v, None)
            else:
                raise PkError('PARSE', f'cannot set {k} on {kind}; keys: @x,y elev gfx move range script flag text')
        tx.stage(path, doc.emit())
        return Result(f'ev set {name}', [describe(kind, n, e)])
    if verb == 'rm' and len(rest) == 2:
        kind, n = parse_ref(rest[1])
        items = data.get(KINDS[kind]) or []
        if n >= len(items):
            raise PkError('NOTFOUND', f'{rest[1]} in {name}; use pk ev ls')
        if kind == 'warp':
            # Later warps shift down by one: fix every warp in other maps that targets this map.
            fixes = []
            for other, odata in repo.maps().items():
                for wn, warp in enumerate(odata.get('warp_events') or []):
                    if warp.get('dest_map') == data['id'] and str(warp.get('dest_warp_id')).isdigit():
                        t = int(warp['dest_warp_id'])
                        if t == n:
                            raise PkError('CONFLICT', f'{other} warp#{wn} leads to {name} warp#{n}; remove or retarget it first')
                        if t > n:
                            fixes.append((other, wn))
            for other, wn in fixes:
                opath = f'data/maps/{other}/map.json'
                odoc = JsonDocument(repo.read(opath))
                warp = odoc.value['warp_events'][wn]
                warp['dest_warp_id'] = str(int(warp['dest_warp_id']) - 1) if isinstance(warp['dest_warp_id'], str) else warp['dest_warp_id'] - 1
                tx.stage(opath, odoc.emit())
        removed = items.pop(n)
        tx.stage(path, doc.emit())
        return Result(f'ev rm {name} {rest[1]}', [describe(kind, n, removed)])
    raise PkError('PARSE', 'usage: pk ev ls|add|set|rm; see pk help ev')
