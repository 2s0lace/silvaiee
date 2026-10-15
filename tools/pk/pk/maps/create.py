"""pk map new (SPEC §5.4) and stamps (SPEC §5.3)."""
import json
import re

from ..core.errors import PkError
from ..core.build import add_map_includes
from ..core.out import Result, page
from ..core.repo import JsonDocument
from .grid import encode
from .layers import Grid, render
from .tiles import Pair, tileset
from .edit import default_bits, load, area, point, save, changed_view
from .resize import MAX_MAP_DATA

STAMPS = 'tools/pk/data/stamps'
TYPES = {'town': 'MAP_TYPE_TOWN', 'city': 'MAP_TYPE_CITY', 'route': 'MAP_TYPE_ROUTE',
         'indoor': 'MAP_TYPE_INDOOR', 'cave': 'MAP_TYPE_UNDERGROUND'}
NEW_GROUP = 'gMapGroup_PkNew'   # appended last: existing group/map numbers (save data) never shift


def snake(name):
    base = name[:-5] if name.endswith('_Frlg') else name
    return re.sub(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', '_', base).replace('__', '_').upper()


def new_map(repo, tx, name, opts):
    if not re.fullmatch(r'[A-Z][A-Za-z0-9_]*', name):
        raise PkError('PARSE', 'map name is CamelCase like PalletTown_Pond_Frlg')
    if repo.root.joinpath('data/maps', name).exists():
        raise PkError('CONFLICT', f'map {name} exists')
    m = re.fullmatch(r'(\d+)x(\d+)', opts.get('size', ''))
    if not m or 'tilesets' not in opts or 'fill' not in opts:
        raise PkError('PARSE', 'pk map new NAME --size WxH --tilesets PRIMARY,SECONDARY --fill METATILE '
                               '[--type town|city|route|indoor|cave] [--like MAP] [--music MUS_X] [--group G]')
    w, h = int(m[1]), int(m[2])
    if (w + 15) * (h + 14) > MAX_MAP_DATA:
        raise PkError('LIMIT', f'{w}x{h} exceeds MAX_MAP_DATA_SIZE')
    primary, secondary = opts['tilesets'].split(',', 1)
    tileset(repo, primary), tileset(repo, secondary)
    version = 'frlg' if primary.endswith('_Frlg') else 'emerald'
    map_id = 'MAP_' + snake(name)
    layouts = JsonDocument(repo.read('data/layouts/layouts.json'))
    layout_id = 'LAYOUT_' + snake(name) + ('_FRLG' if version == 'frlg' else '')
    if any(x.get('id') == layout_id for x in layouts.value['layouts']):
        raise PkError('CONFLICT', f'{layout_id} exists')
    if any(d['id'] == map_id for d in repo.maps().values()):
        raise PkError('CONFLICT', f'{map_id} exists')
    folder = f'data/layouts/{name}'
    layout = dict(id=layout_id, name=f'{name}_Layout', width=w, height=h, border_width=2, border_height=2,
                  primary_tileset=primary, secondary_tileset=secondary,
                  border_filepath=f'{folder}/border.bin', blockdata_filepath=f'{folder}/map.bin',
                  layout_version=version)
    pair = Pair(repo, layout)
    mt = pair.lookup(opts['fill'])
    cell = default_bits(repo, pair, mt, (mt, 0, 3))
    tx.stage(layout['blockdata_filepath'], encode([cell] * (w * h)))
    tx.stage(layout['border_filepath'], encode([cell] * 4))
    layouts.value['layouts'].append(layout)
    tx.stage('data/layouts/layouts.json', layouts.emit())
    if opts.get('like'):
        _, like, _ = repo.map(opts['like'])
        header = {k: like[k] for k in like if k not in ('id', 'name', 'layout', 'connections', 'object_events',
                                                         'warp_events', 'coord_events', 'bg_events', 'shared_events_map', 'shared_scripts_map')}
    else:
        kind = TYPES.get(opts.get('type', 'route'))
        if not kind:
            raise PkError('PARSE', '--type town|city|route|indoor|cave')
        outdoor = kind in ('MAP_TYPE_TOWN', 'MAP_TYPE_CITY', 'MAP_TYPE_ROUTE')
        header = {'music': 'MUS_RG_ROUTE1', 'region': 'REGION_KANTO', 'region_map_section': 'MAPSEC_PALLET_TOWN',
                  'requires_flash': False, 'weather': 'WEATHER_SUNNY' if outdoor else 'WEATHER_NONE',
                  'map_type': kind, 'allow_cycling': outdoor, 'allow_escaping': kind == 'MAP_TYPE_UNDERGROUND',
                  'allow_running': True, 'show_map_name': outdoor, 'floor_number': 0,
                  'battle_scene': 'MAP_BATTLE_SCENE_NORMAL'}
    if opts.get('music'):
        header['music'] = opts['music']
    data = {'id': map_id, 'name': name, 'layout': layout_id, **header, 'connections': None,
            'object_events': [], 'warp_events': [], 'coord_events': [], 'bg_events': []}
    tx.stage(f'data/maps/{name}/map.json', (json.dumps(data, indent=2, ensure_ascii=False) + '\n').encode('utf-8'))
    tx.stage(f'data/maps/{name}/scripts.inc', f'{name}_MapScripts::\n\t.byte 0\n'.encode('utf-8'))
    groups = JsonDocument(repo.read('data/maps/map_groups.json'))
    group = opts.get('group', NEW_GROUP)
    if group not in groups.value:
        groups.value['group_order'].append(group)
        groups.value[group] = []
    groups.value[group].append(name)
    tx.stage('data/maps/map_groups.json', groups.emit())
    stage_map_include(repo, tx, name)
    return Result(f'map new {name} {map_id} {layout_id} {w}x{h} group {group}',
                  ['next: pk ev add ... warp to reach it; pk map show ' + name])


def stage_map_include(repo, tx, name):
    """A newly registered map needs its `.include` line in data/event_scripts.s, else
    pk build dies with an undefined `_MapScripts` reference."""
    path = 'data/event_scripts.s'
    text = repo.read(path).decode('utf-8')
    synced = add_map_includes(text, [name])
    if synced != text:
        tx.stage(path, synced.encode('utf-8'))


def stamp_fits(stamp, pair):
    """Same pair, or only primary metatiles and the same primary (trees fit every General_Frlg map)."""
    if (stamp['primary'], stamp['secondary']) == (pair.primary['symbol'], pair.secondary['symbol']):
        return True
    return stamp['primary'] == pair.primary['symbol'] and all(
        c[0] < pair.limits['metatiles'] for row in stamp['cells'] for c in row)


def stamp_on(opts):
    """Where a stamp may stand in blueprints: land (default), water (piers, bridges) or any."""
    on = opts.get('on') or 'land'
    if on not in ('land', 'water', 'any'):
        raise PkError('PARSE', '--on land|water|any')
    return on


def stamp_path(name):
    if not re.fullmatch(r'[a-z][a-z0-9_]*', name):
        raise PkError('PARSE', 'stamp names are lowercase snake_case')
    return f'{STAMPS}/{name}.json'


def stamp(repo, tx, args, opts, pagination):
    verb, rest = args[0], args[1:]
    if verb == 'extract' and len(rest) == 3:
        grid, pair = load(repo, rest[0])
        x, y, w, h = area(rest[1], grid)
        path = stamp_path(rest[2])
        if repo.root.joinpath(path).exists() and not opts.get('overwrite'):
            raise PkError('CONFLICT', f'stamp {rest[2]} exists; add --overwrite')
        cells = [[list(grid.at(x + i, y + j)) for i in range(w)] for j in range(h)]
        data = dict(name=rest[2], size=[w, h], primary=pair.primary['symbol'], secondary=pair.secondary['symbol'],
                    source=f'{grid.name} {x},{y}', cells=cells, on=stamp_on(opts))
        tx.stage(path, (json.dumps(data, separators=(',', ':')) + '\n').encode('utf-8'))
        return Result(f'stamp {rest[2]} {w}x{h} from {grid.name} {x},{y}', render(grid, pair, (x, y, w, h), 'class', False))
    if verb == 'ls':
        rows = []
        for p in sorted(repo.root.joinpath(STAMPS).glob('*.json')) if repo.root.joinpath(STAMPS).exists() else []:
            d = json.loads(p.read_text(encoding='utf-8'))
            if rest and rest[0] not in (d['primary'], d['secondary']):
                continue
            rows.append(f"{d['name']} {d['size'][0]}x{d['size'][1]} {d['primary']}+{d['secondary']} from {d['source']}")
        return page(f'stamps {len(rows)}', rows, *pagination(opts))
    if verb == 'place' and len(rest) == 3:
        grid, pair = load(repo, rest[0])
        d = json.loads(repo.read(stamp_path(rest[1])))
        if not stamp_fits(d, pair):
            raise PkError('CONFLICT', f"stamp {rest[1]} is for {d['primary']}+{d['secondary']}")
        x, y = point(rest[2], grid)
        w, h = d['size']
        if not grid.inside(x + w - 1, y + h - 1):
            raise PkError('BOUNDS', f'stamp {w}x{h} at {x},{y} exceeds {grid.w}x{grid.h}')
        before_cells = list(grid.cells)
        cells = []
        for j, row in enumerate(d['cells']):
            for i, c in enumerate(row):
                if c is None:              # hole in a drawn object: keep the map cell
                    continue
                grid.set(x + i, y + j, tuple(c))
                cells.append((x + i, y + j))
        repaired = []
        notes = []
        if opts.get('repair'):
            from . import terrain
            rules = terrain.Rules(repo, pair, include_pk=False)
            repaired = terrain.repair(repo, pair, rules, grid, (x, y, w, h), int(opts.get('ring', 2)), outside_only=True)
            warn = terrain.repair_warning((x, y, w, h), repaired, cells)
            if warn:
                notes.append(warn)
        save(tx, grid)
        from . import reach
        tail = f' repair:{len(repaired)}' if opts.get('repair') else ''
        return Result(f'stamp {rest[1]} placed in {grid.name} at {x},{y}{tail}',
                      notes + changed_view(grid, pair, cells, 'class') + [reach.check_write(repo, pair, before_cells, grid)])
    if verb == 'from-tileset' and len(rest) == 3:
        return stamp_from_tileset(repo, tx, *rest, opts)
    if verb == 'mine' and len(rest) == 1:
        return stamp_mine(repo, tx, rest[0], opts)
    raise PkError('PARSE', 'usage: pk stamp extract MAP x,y,WxH name | pk stamp from-tileset SET.pkt OBJECT.px name'
                           ' | pk stamp mine PAIR [--min-size 2x2] [--min-count 3] [--apply]'
                           ' | pk stamp ls [tileset] | pk stamp place MAP name x,y')


def stamp_mine(repo, tx, word, opts):
    """Repeated all-solid rectangles in the vanilla layouts of a tileset pair -> stamp proposals.
    Only --apply writes them; it keeps the greedy subset that covers the most structure cells."""
    from . import stamps as S
    from .meta import pair_for
    pair = pair_for(repo, word)
    m = re.fullmatch(r'(\d+)x(\d+)', opts.get('min-size', '2x2'))
    if not m:
        raise PkError('PARSE', '--min-size is WxH, e.g. 2x2')
    min_size = (int(m[1]), int(m[2]))
    min_count = int(opts.get('min-count', 3))
    cap = int(opts.get('cap', 32))
    proposals = S.mine(repo, pair, min_size, min_count)
    mined = S.to_stamps(repo, pair, proposals, S.tree_metatiles(repo, pair))
    chosen = S.select(repo, pair, mined, cap)
    rows = [f'{s["name"]} {s["size"][0]}x{s["size"][1]} from {s["source"]}' for s in chosen]
    if opts.get('apply'):
        written = 0
        for s in chosen:
            path = stamp_path(s['name'])
            if not repo.root.joinpath(path).exists():
                tx.stage(path, (json.dumps(s, separators=(',', ':')) + '\n').encode('utf-8'))
                written += 1
        return Result(f'stamp mine {pair.primary["symbol"]}+{pair.secondary["symbol"]} '
                      f'{len(chosen)} selected, {written} applied', rows)
    return Result(f'stamp mine {pair.primary["symbol"]}+{pair.secondary["symbol"]} '
                  f'{len(mined)} repeated, {len(chosen)} selected (--apply writes)', rows)


def stamp_from_tileset(repo, tx, spec_path, drawing, name, opts):
    """A stamp from an object drawn as one .px and listed cell by cell (FILE.px@x,y) in a built .pkt.
    Cells are solid (coll 1, elev 0) like vanilla buildings and doors; --walk "x,y x,y" (cell coords
    inside the object) makes cells walkable (coll 0, elev 3). Cells not listed in the .pkt are holes."""
    from ..art.compiler import Spec, IDS_DIR
    from .tiles import LIMITS
    spec = Spec(repo, spec_path)
    path = stamp_path(name)
    if not opts.get('overwrite'):
        try:
            repo.read(path)                # sees staged writes too
            raise PkError('CONFLICT', f'stamp {name} exists; add --overwrite')
        except PkError as e:
            if e.code == 'CONFLICT':
                raise
    try:
        ids = json.loads(repo.read(f'{IDS_DIR}/{spec.name}.json'))
    except PkError:
        raise PkError('NOTFOUND', f'tileset {spec.name} is not built yet; run pk tileset build {spec_path}')
    drawing = drawing.rsplit('/', 1)[-1]
    parts = [e for e in spec.entries if e['file'] == drawing]
    if not parts:
        raise PkError('NOTFOUND', f'no metatile line in {spec_path} uses {drawing}')
    base = LIMITS[spec.meta['version']]['metatiles'] if spec.meta['kind'] == 'secondary' else 0
    placed = {}
    for e in parts:
        if e['x'] % 16 or e['y'] % 16:
            raise PkError('PARSE', f"{spec_path}:{e['line']}: @x,y must be multiples of 16")
        if e['name'] not in ids:
            raise PkError('NOTFOUND', f"metatile {e['name']} not in the built tileset; rebuild {spec_path}")
        placed[(e['x'] // 16, e['y'] // 16)] = base + ids[e['name']]
    walk = set()
    for word in (opts.get('walk') or '').replace(';', ' ').split():
        m = re.fullmatch(r'(\d+),(\d+)', word)
        if not m or (int(m[1]), int(m[2])) not in placed:
            raise PkError('PARSE', f'--walk {word}: cell x,y inside the object')
        walk.add((int(m[1]), int(m[2])))
    w, h = max(x for x, _ in placed) + 1, max(y for _, y in placed) + 1
    cells = [[None if (i, j) not in placed else [placed[(i, j)], 0, 3] if (i, j) in walk else [placed[(i, j)], 1, 0]
              for i in range(w)] for j in range(h)]
    secondary = spec.meta['kind'] == 'secondary'
    if secondary and 'pair_with' not in spec.meta:
        raise PkError('PARSE', f'{spec_path}: a secondary tileset needs "pair_with gTileset_X" for stamps')
    data = dict(name=name, size=[w, h], primary=spec.meta['pair_with'] if secondary else spec.symbol,
                secondary=spec.symbol if secondary else '-', source=f'{spec_path} {drawing}', cells=cells,
                on=stamp_on(opts))
    tx.stage(path, (json.dumps(data, separators=(',', ':')) + '\n').encode('utf-8'))
    rows = [''.join('.' if c is None else '_' if c[1] == 0 else '#' for c in row) for row in cells]
    return Result(f'stamp {name} {w}x{h} from {drawing} ({len(placed)} cells, {len(walk)} walkable)',
                  ['legend #=solid _=walkable .=hole'] + rows)
