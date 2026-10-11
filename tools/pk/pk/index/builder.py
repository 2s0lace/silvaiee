import json
import re
import sqlite3

from ..core.errors import PkError

SCHEMA_VERSION = 4
TABLES = dict(map='maps', layout='layouts', tileset='tilesets', text='texts', script='scripts',
              flag='flags', var='vars', trainer='trainers', gfx='object_gfx', behavior='behaviors', music='music')
LABEL = re.compile(r'^([A-Za-z_][\w]*):{1,2}(?:\s|$)')
STRING = re.compile(r'^\s*\.string\s+"((?:[^"\\]|\\.)*)"')
DEFINE = re.compile(r'^\s*#define\s+([A-Za-z_]\w*)\b(.*)')


def source_paths(repo):
    root = repo.root
    paths = set()
    for pattern in ('data/maps/*/map.json', 'data/maps/*/scripts.inc', 'data/text/**/*.inc',
                    'data/scripts/**/*.inc', 'include/constants/**/*.h', 'src/**/*.c', 'src/**/*.h'):
        paths.update(root.glob(pattern))
    paths.update(root/p for p in ('data/layouts/layouts.json', 'data/maps/map_groups.json', 'charmap.txt',
                                  'data/event_scripts.s'))
    generated = {'include/constants/map_event_ids.h', 'include/constants/layouts.h',
                 'include/constants/map_groups.h', 'src/data/map_group_count.h'}
    return sorted(p.relative_to(root).as_posix() for p in paths if p.is_file() and p.relative_to(root).as_posix() not in generated)


def inventory(repo):
    return {key: [repo.path(key).stat().st_mtime_ns, repo.path(key).stat().st_size] for key in source_paths(repo)}


def plain(text):
    # Placeholders stay visible ({PLAYER}); dropping them produced "Hi, . I bet".
    text = re.sub(r'\\[nlp]', ' ', text)
    return ' '.join(text.replace('$', '').split())


def build(repo, tx):
    before = inventory(repo)
    db = sqlite3.connect(':memory:')
    db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
    db.execute('CREATE TABLE sources (path TEXT PRIMARY KEY, mtime INTEGER, size INTEGER)')
    db.execute('CREATE TABLE warps (source TEXT, warp INTEGER, destination TEXT, dest_warp TEXT)')
    for table in TABLES.values():
        db.execute(f'CREATE TABLE {table} (name TEXT, file TEXT, line INTEGER, snippet TEXT, payload TEXT)')
        db.execute(f'CREATE INDEX {table}_name ON {table}(name)')
    def add(kind, name, file, line=1, snippet='', payload=None):
        db.execute(f'INSERT INTO {TABLES[kind]} VALUES (?,?,?,?,?)',
                   (name, file, line, plain(snippet), json.dumps(payload, ensure_ascii=False, separators=(',', ':'))))
    for key in before:
        raw = tx.read(key)
        try:
            content = raw.decode('utf-8-sig')
        except UnicodeError:
            raise PkError('PARSE', f'{key} not UTF-8; repair source encoding') from None
        if key.endswith('/map.json'):
            data = json.loads(content)
            add('map', data['name'], key, snippet=f"{data['id']} {data['layout']}", payload=data)
            for n, warp in enumerate(data.get('warp_events', [])):
                db.execute('INSERT INTO warps VALUES (?,?,?,?)', (data['name'], n, warp['dest_map'], str(warp['dest_warp_id'])))
        elif key == 'data/layouts/layouts.json':
            for data in json.loads(content)['layouts']:
                add('layout', data['id'], key, snippet=data['name'], payload=data)
        elif key == 'src/data/tilesets/headers.h':
            for match in re.finditer(r'const struct Tileset\s+(\w+)\s*=\s*\{(.*?)\};', content, re.S):
                add('tileset', match[1], key, content[:match.start()].count('\n')+1, match[2])
        if key.endswith('.inc') or key == 'data/event_scripts.s':   # Common_EventScript_* live in the .s
            labels, chunks = [], []
            def flush():
                for name, line in labels:
                    add('text' if chunks else 'script', name, key, line, ''.join(chunks))
            for n, line in enumerate(content.splitlines(), 1):
                match = LABEL.match(line)
                if match:
                    flush()
                    labels, chunks = [(match[1], n)], []
                else:
                    match = STRING.match(line)
                    if match:
                        chunks.append(match[1])
            flush()
        if key.startswith('include/constants/'):
            for n, line in enumerate(content.splitlines(), 1):
                match = DEFINE.match(line) or re.match(r'^\s*((?:MB_|OBJ_EVENT_GFX_|FLAG_|VAR_|TRAINER_|MUS_)\w+)\s*([,=].*)$', line)
                if not match:
                    continue
                name = match[1]
                for prefix, kind in [('FLAG_', 'flag'), ('VAR_', 'var'), ('TRAINER_', 'trainer'),
                                     ('OBJ_EVENT_GFX_', 'gfx'), ('MB_', 'behavior'), ('MUS_', 'music')]:
                    if name.startswith(prefix):
                        add(kind, name, key, n, match[2])
                        break
    if inventory(repo) != before:
        raise PkError('CONFLICT', 'source inventory changed while indexing; retry')
    db.executemany('INSERT INTO sources VALUES (?,?,?)', [(k, *v) for k, v in before.items()])
    db.commit()
    tx.stage('.pk/index.sqlite', db.serialize())
    return db


def ensure(repo, tx, force=False):
    if not force:
        try:
            raw = tx.read('.pk/index.sqlite')
            db = sqlite3.connect(':memory:')
            db.deserialize(raw)
            cached = {k: [mtime, size] for k, mtime, size in db.execute('SELECT * FROM sources')}
            if db.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION and cached == inventory(repo):
                return db
            db.close()
        except (PkError, sqlite3.Error) as exc:
            if 'db' in locals():
                db.close()
            if isinstance(exc, PkError) and exc.code != 'NOTFOUND':
                raise
    return build(repo, tx)
