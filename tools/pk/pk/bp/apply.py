"""Compile a blueprint into pk operations inside ONE transaction (SPEC §14.3-14.4).

plan  = run everything, report, never commit
check = plan + map check + reachability gates, fail on any error
apply = plan + commit (pk.py commits the transaction when no error was raised)
"""
import json
import re

from ..core.errors import PkError
from ..core.out import Result
from ..core.repo import Repo, JsonDocument
from ..maps import resize as map_resize, check as map_check, reach
from ..maps.edit import load, save
from ..maps.events import Symbols
from ..maps.terrain import Rules, apply as terrain_apply, shape_cells
from ..maps.create import stamp_path
from ..text.ops import Text
from ..text.strings import blocks, replace as replace_block, emit
from .parse import parse, entities

POLICY = 'tools/pk/data/policy.json'
MATERIAL_ALIAS = {'grass': 'ground', 'tall_grass': 'grass', 'rock': 'solid', 'cliff': 'solid'}
FALLBACK = {'flowers': 'ground', 'path': 'ground', 'stairs': 'ground'}
WHEN = {  # hide the NPC unless the time of day (gettimeofday: 0 morning 1 day 2 evening 3 night) matches
    'morning': [0], 'day': [0, 1], 'evening': [2], 'night': [3], 'dark': [2, 3]}
HEADER_FIELDS = {'music': ('music', 'MUS_'), 'weather': ('weather', 'WEATHER_'), 'type': ('map_type', 'MAP_TYPE_'),
                 'bike': ('allow_cycling', None), 'run': ('allow_running', None), 'flash': ('requires_flash', None),
                 'escape': ('allow_escaping', None), 'show_name': ('show_map_name', None)}


def snake(text):
    return re.sub(r'[^A-Za-z0-9]+', '_', text).strip('_').upper()


class Script:
    """Edits one map's scripts.inc in memory: upsert label blocks, keeping other bytes."""
    def __init__(self, repo, tx, name):
        self.repo, self.tx, self.path = repo, tx, f'data/maps/{name}/scripts.inc'
        self.text = repo.read(self.path).decode('utf-8')
        self.nl = '\r\n' if '\r\n' in self.text else '\n'

    def find(self, label):
        lines = self.text.split('\n')
        for i, line in enumerate(lines):
            if re.match(rf'^{re.escape(label)}::?\s*$', line.rstrip('\r')):
                j = i + 1
                while j < len(lines) and not re.match(r'^\w+::?\s*$', lines[j].rstrip('\r')):
                    j += 1
                while j > i + 1 and not lines[j - 1].strip():
                    j -= 1
                return lines, i, j
        return lines, None, None

    def upsert(self, label, body):
        lines, i, j = self.find(label)
        block = [f'{label}::'] + [f'\t{b}' for b in body]
        if i is None:
            if self.text and not self.text.endswith(self.nl):
                self.text += self.nl
            self.text += self.nl + self.nl.join(block) + self.nl
        else:
            cr = '\r' if lines[i].endswith('\r') else ''
            self.text = '\n'.join(lines[:i] + [b + cr for b in block] + lines[j:])

    def text_block(self, label, source):
        for b in blocks(self.text):
            if b.label == label:
                self.text = replace_block(self.text, b, source)
                return
        if self.text and not self.text.endswith(self.nl):
            self.text += self.nl
        self.text += self.nl + emit(label, source, newline=self.nl)

    def save(self):
        self.tx.stage(self.path, self.text.encode('utf-8'))


def material(rules, word, warns, where):
    # Blueprint words first: "grass" means plain grass ground, "tall_grass" the encounter grass
    # (whose class label in pk map show is "grass").
    alias = MATERIAL_ALIAS.get(word)
    if alias and alias in rules.by_mat:
        return alias
    if word in rules.by_mat:
        return word
    if word in FALLBACK:
        warns.append(f'{where}: material {word} has no tagged metatiles; used {FALLBACK[word]} '
                     f'(tag some: pk meta name ... --tags {word})')
        return FALLBACK[word]
    raise PkError('NOTFOUND', f'{where}: material {word}; known: {" ".join(sorted(rules.by_mat))}')


def raw_patches(repo, tx, name, lines, path):
    """Section raw: "at x,y" (or "raw x,y" header) starts a patch; "def X 0x2A3 [coll=n] [elev=n]";
    rows of symbols, ' ' or '?' keep the cell. Symbols are defs or named metatile syms (pk meta name
    --sym): never the frequency legend of map show --exact, so re-applying is deterministic."""
    from ..maps.edit import default_bits
    grid, pair = load(repo, name)
    named = {}
    for ts in (pair.primary, pair.secondary):
        for k, v in pair.names(ts).items():
            if v.get('sym') and len(v['sym']) == 1:
                named.setdefault(v['sym'], int(k, 16))
    defs, origin, row, count = {}, None, 0, 0
    for n, line in lines:
        where = f'{path}:{n}'
        body = line.rstrip()           # rows keep '#' (a valid symbol); no comments inside raw
        m = re.fullmatch(r'\s*at\s+(\d+),(\d+)\s*', body)
        if m:
            origin, row = (int(m[1]), int(m[2])), 0
            continue
        m = re.fullmatch(r'\s*def\s+(\S)\s+(\S+)((?:\s+(?:coll|elev)=\d+)*)\s*', body)
        if m:
            extra = dict(kv.split('=') for kv in m[3].split())
            defs[m[1]] = (pair.lookup(m[2]), int(extra['coll']) if 'coll' in extra else None,
                          int(extra['elev']) if 'elev' in extra else None)
            continue
        if not body.strip():
            continue
        if origin is None:
            raise PkError('PARSE', f'{where}: raw rows need "raw x,y" or "at x,y" first')
        y = origin[1] + row
        for i, sym in enumerate(body):
            if sym in ' ?':
                continue
            x = origin[0] + i
            if not grid.inside(x, y):
                raise PkError('BOUNDS', f'{where}: raw cell {x},{y} outside {grid.w}x{grid.h}')
            if sym in defs:
                mt, coll, elev = defs[sym]
            elif sym in named:
                mt, coll, elev = named[sym], None, None
            else:
                raise PkError('PARSE', f'{where}: raw symbol {sym!r} undefined; add "def {sym} <metatile>"')
            grid.set(x, y, default_bits(repo, pair, mt, grid.at(x, y), coll, elev))
            count += 1
        row += 1
    save(tx, grid)
    return count


ID_SECTIONS = ('structures', 'npcs', 'trainers', 'signs', 'triggers', 'warps')


def duplicate_ids(bp, path):
    """An id twice in a section is always a mistake (often a section pasted twice)."""
    errors = []
    for section in ID_SECTIONS:
        seen = {}
        for n, w in bp['sections'][section]:
            if w:
                seen.setdefault(w[0], []).append(n)
        errors += [f'{path}: duplicate {section} id {i} (lines {",".join(map(str, ns))})'
                   for i, ns in seen.items() if len(ns) > 1]
    return errors


def xy(text):
    m = re.fullmatch(r'@?(\d+),(\d+)', text or '')
    return (int(m[1]), int(m[2])) if m else None


def structure_cells(repo, bp):
    cells = set()
    for e in entities(bp, 'structures'):
        name = e['words'][0] if e['words'] else None
        origin = xy(e['pos'])
        if not name or origin is None or 'locked' in e['words']:
            continue
        try:
            stamp = json.loads(repo.read(stamp_path(name)))
        except PkError:
            continue
        cells |= {(origin[0] + i, origin[1] + j) for j, row in enumerate(stamp['cells'])
                  for i, c in enumerate(row) if c is not None}
    return cells


def structure_problems(repo, name, bp, e, stamp_name, keep, occupied, path, intended=None):
    """A structure must not cover water (unless its stamp says on=water|any), the vanilla core,
    another structure, or a cell where an NPC or trigger stands on a solid part of it."""
    from ..maps.layers import class_of
    grid, pair = load(repo, name)
    stamp = json.loads(repo.read(stamp_path(stamp_name)))
    origin = xy(e['pos'])
    where = f"{path}:{e['line']}: structure {e['id']}"
    if origin is None:
        return [f'{where}: position @x,y missing']
    cells = {(origin[0] + i, origin[1] + j): c for j, row in enumerate(stamp['cells'])
             for i, c in enumerate(row) if c is not None}
    errors = []
    if any(not grid.inside(x, y) for x, y in cells):
        return [f'{where}: does not fit the map']
    on = stamp.get('on', 'land')
    fresh = [c for c in cells if grid.at(*c)[0] != cells[c][0]]     # re-apply: already placed cells skip
    intended = intended or {}
    wet = [c for c in fresh if intended.get(c) == 'water'
           or (c not in intended and class_of(pair, *grid.at(*c)[:2])[0] in '~|')]
    if wet and on == 'land':
        errors.append(f'{where}: {stamp_name} covers {len(wet)} water cells, e.g. {wet[0][0]},{wet[0][1]} '
                      f'(land object; for piers/bridges mark the stamp --on water)')
    if on == 'water' and fresh and len(wet) < len(fresh) // 3:
        errors.append(f'{where}: {stamp_name} is a water object but stands mostly on land')
    core = sorted(cells.keys() & keep)
    if core:
        errors.append(f'{where}: covers the keep area at {core[0][0]},{core[0][1]}')
    clash = [(c, occupied[c]) for c in cells if c in occupied]
    if clash:
        errors.append(f'{where}: overlaps structure {clash[0][1]} at {clash[0][0][0]},{clash[0][0][1]}')
    occupied.update({c: e['id'] for c in cells})
    standing = set()
    data = json.loads(repo.read(f'data/maps/{name}/map.json'))
    for ev in (data.get('object_events') or []) + (data.get('coord_events') or []):
        standing.add((ev['x'], ev['y']))
    for section in ('npcs', 'trainers', 'triggers'):
        for n, w in bp['sections'][section]:
            for word in w:
                if xy(word) and word.startswith('@'):
                    standing.add(xy(word))
    blocked = sorted(c for c in standing if c in cells and cells[c][1] != 0)
    if blocked:
        errors.append(f'{where}: an NPC or trigger stands on its solid cell {blocked[0][0]},{blocked[0][1]}')
    return errors


def run(repo, tx, path, mode, opts):
    bp = parse(repo.read(path).decode('utf-8'), path)
    ops, warns = [], []
    pre_errors = duplicate_ids(bp, path)
    policy = json.loads(repo.read(POLICY)) if repo.root.joinpath(POLICY).exists() else {}
    if policy.get('no_new_items') and bp['sections']['items']:
        raise PkError('PARSE', f'{path}: policy forbids new items (blueprints/VISION.md); remove the items section')
    if policy.get('no_new_trainers') and bp['sections']['trainers']:
        raise PkError('PARSE', f'{path}: policy forbids new trainers; remove the trainers section')
    if policy.get('no_new_quests') and any(w and w[0] == 'var' for _, w in bp['sections']['declare']):
        raise PkError('PARSE', f'{path}: policy forbids new quest vars; remove "declare var"')
    if bp.get('base') not in (None, bp['map']):
        raise PkError('PARSE', f"{path}: base must be the map itself ({bp['map']}) or omitted")
    name, data, layout = repo.map(bp['map'])
    disk = Repo(repo.root)
    old_name, old_data, old_layout = disk.map(name)
    prefix = name[:-5] if name.endswith('_Frlg') else name
    if bp.get('tilesets') and bp['tilesets'] != (layout['primary_tileset'], layout['secondary_tileset']):
        raise PkError('CONFLICT', f"{path}: tilesets {bp['tilesets']} differ from the map's; change tilesets separately")
    # 1. size ------------------------------------------------------------------------------
    shift = (0, 0)
    if bp.get('size'):
        W, H = bp['size']
        w, h = layout['width'], layout['height']
        g = {k: bp['grow'].get(k, 0) for k in ('top', 'left', 'right', 'bottom')}
        if (w, h) == (W, H):
            if old_layout['width'] != W or old_layout['height'] != H:
                shift = (g['left'], g['top'])
        elif (w + g['left'] + g['right'], h + g['top'] + g['bottom']) == (W, H):
            r = map_resize.run(repo, tx, name, {'add': ','.join(f'{k}={v}' for k, v in g.items() if v),
                                                'fill': bp.get('fill', 'extend')})
            ops.append(r.summary)
            warns += [x[5:] for x in r.data if x.startswith('WARN ')]
            shift = (g['left'], g['top'])
            name, data, layout = repo.map(name)
        else:
            raise PkError('CONFLICT', f'{path}: map is {w}x{h}; size {W}x{H} with grow {bp["grow"]} does not match')
    # 2. header ------------------------------------------------------------------------------
    doc = JsonDocument(repo.read(f'data/maps/{name}/map.json'))
    for key, value in bp['header'].items():
        if key not in HEADER_FIELDS:
            raise PkError('PARSE', f'{path}: header {key}; known: {" ".join(HEADER_FIELDS)}')
        field, pre = HEADER_FIELDS[key]
        doc.value[field] = (value if value.startswith(pre) else pre + value) if pre else value in ('yes', 'true', '1')
    tx.stage(f'data/maps/{name}/map.json', doc.emit())
    # 3. terrain -----------------------------------------------------------------------------
    grid, pair = load(repo, name)
    rules = Rules(repo, pair, include_pk=False)
    keep, targets, forests = set(), {}, []

    def paint(cells, mat):
        # Later lines win: a brush after a forest carves it (e.g. a basin cut into a closed edge).
        targets.update({c: mat for c in cells})
        for _, trees, _ in forests:
            trees.difference_update(cells)
    for n, w in bp['sections']['terrain']:
        where = f'{path}:{n}'
        if not w:
            continue
        verb = w[0]
        if verb in ('copy', 'auto'):
            continue
        heightish = [x for x in w if re.fullmatch(r'h=?\d', x)]
        if heightish:
            warns.append(f'{where}: heights are not modelled (DECISIONS 4b); "{heightish[0]}" ignored')
        w = [x for x in w if not re.fullmatch(r'h=?\d|edge=\w+|density=[\d.]+', x)]
        if verb == 'keep':
            keep |= shape_cells(grid, ['rect', w[1] + ',' + w[2]])
            continue
        if verb == 'ramp':
            warns.append(f'{where}: ramp needs a "stairs" material; tag stairs metatiles and use brush')
            continue
        if verb == 'fill':
            if len(w) > 1:
                paint({(x, y) for y in range(grid.h) for x in range(grid.w)}, material(rules, w[1], warns, where))
            continue
        if verb == 'ledge':
            cells = shape_cells(grid, ['line', w[1], w[2]])
            paint(cells, 'ledge_s')
            continue
        if verb == 'river':
            cells = shape_cells(grid, ['path'] + [x for x in w[1:]])
            paint(cells, 'water')
            continue
        if verb in ('rect', 'circle', 'path'):
            if (verb in ('rect', 'circle') and len(w) < 4) or re.fullmatch(r'\d+,\d+|\d+x\d+|r?\d+|width=\d+', w[-1]):
                continue                     # height-only line (h=2): nothing to paint, already warned
            if verb == 'rect':
                shape = ['rect', f'{w[1]},{w[2]}']
            elif verb == 'circle':
                r = w[2].lstrip('r')
                shape = ['circle', f'{w[1]},{r}']
            else:
                shape = ['path'] + [x for x in w[1:-1]]
            cells = shape_cells(grid, shape)
            mat = w[-1]
            if mat == 'forest':
                cells = set(cells)
                forests.append((where, cells, (min(x for x, _ in cells), min(y for _, y in cells))))
                continue
            paint(cells, material(rules, mat, warns, where))
            continue
        raise PkError('PARSE', f'{where}: terrain verb {verb}; use keep fill rect circle path river ledge ramp')
    for c in keep:
        targets.pop(c, None)
    stamped = set()
    for where, cells, (xs, ys) in forests:       # tree grid stays aligned to the brush, not the carve
        cells -= keep
        stamped |= cells
        try:
            stamp = json.loads(repo.read(stamp_path('tree')))
            from ..maps.create import stamp_fits
            if not stamp_fits(stamp, pair):
                raise PkError('NOTFOUND', 'tree stamp for another pair')
        except PkError:
            warns.append(f'{where}: no "tree" stamp for this tileset pair (pk stamp extract ... tree); forest painted as solid')
            targets.update({c: 'solid' for c in cells})
            continue
        sw, sh = stamp['size']
        for (x, y) in cells:
            targets.pop((x, y), None)
            grid.set(x, y, tuple(stamp['cells'][(y - ys) % sh][(x - xs) % sw]))
        save(tx, grid)
    # Only cells whose material actually changes are re-solved: vanilla detail inside an
    # unchanged material survives, and a second apply finds nothing to do (idempotent).
    # Structure footprints are never re-solved: the stamp owns them (a pier over water would otherwise
    # be turned back into water and re-stamped on every apply, shifting the shore around it).
    footprint = structure_cells(repo, bp)
    intended = dict(targets)                 # what the blueprint wants under each structure
    targets = {c: m for c, m in targets.items() if rules.material(grid.at(*c)) != m and c not in footprint}
    if targets:
        r = terrain_apply(repo, tx, grid, pair, rules, targets, 'blueprint', frozen=stamped | keep | footprint)
        ops.append(r.summary)
        warns += [x[5:] for x in r.data if x.startswith('WARN ')]
    # Kitbash operations follow every material brush, in their original file order.
    from ..maps import edit as map_edit, terrain as map_terrain
    for n, words in bp['kitbash']:
        try:
            if words[0] == 'copy':
                r = map_edit.command(repo, tx, ['copy', name, words[2], words[3][1:]],
                                     {'from': words[1], 'repair': True})
            else:
                r = map_terrain.auto(repo, tx, name, words[1])
        except PkError as exc:
            raise PkError(exc.code, f'{path}:{n}: {exc}') from None
        ops.append(r.summary)
        warns += [x[5:] for x in r.data if x.startswith('WARN ')]
        ops += [x for x in r.data if x.startswith('INFO ')]
    # 4. structures --------------------------------------------------------------------------
    occupied = {}
    for e in entities(bp, 'structures'):
        if 'locked' in e['words']:
            continue
        stamp_name = e['words'][0] if e['words'] else None
        if not stamp_name or not repo.root.joinpath(stamp_path(stamp_name)).exists():
            warns.append(f"{path}:{e['line']}: stamp {stamp_name} missing; structure {e['id']} skipped")
            continue
        pre_errors += structure_problems(repo, name, bp, e, stamp_name, keep, occupied, path, intended)
        from ..maps.create import stamp as stamp_cmd
        stamp_cmd(repo, tx, ['place', name, stamp_name, e['pos']], {}, lambda o, d=20: (0, d))
        ops.append(f"structure {e['id']} {stamp_name} @{e['pos']}")
    # 4b. raw patches (exact metatiles for details the terrain solver cannot express) -------
    if bp['sections']['raw']:
        count = raw_patches(repo, tx, name, bp['sections']['raw'], path)
        ops.append(f'raw {count} cells')
    # 5. events -----------------------------------------------------------------------------
    doc = JsonDocument(repo.read(f'data/maps/{name}/map.json'))
    data = doc.value
    script = Script(repo, tx, name)
    sym = Symbols(repo, tx)
    text = Text(repo)
    frlg = layout.get('layout_version') == 'frlg'
    W, H = layout['width'], layout['height']

    def pos(e, where):
        m = re.fullmatch(r'(\d+),(\d+)', e['pos'] or '')
        if not m:
            raise PkError('PARSE', f'{where}: needs @x,y')
        x, y = int(m[1]), int(m[2])
        if not (0 <= x < W and 0 <= y < H):
            raise PkError('BOUNDS', f'{where}: @{x},{y} outside {W}x{H}')
        return x, y

    def say(label, plain, kind):
        source, _ = text.compose(plain)
        script.text_block(f'{prefix}_Text_BP_{label}', source)
        return [f'msgbox {prefix}_Text_BP_{label}, {"MSGBOX_SIGN" if kind == "sign" else "MSGBOX_NPC"}', 'end']

    managed, timed = set(), []
    for e in entities(bp, 'npcs'):
        where = f"{path}:{e['line']}"
        x, y = pos(e, where)
        gfx = sym.graphics(e['words'][0], frlg)
        move = sym.movement(e['words'][1] if len(e['words']) > 1 else 'FACE_DOWN')
        lid = f"LOCALID_BP_{snake(prefix)}_{snake(e['id'])}"
        managed.add(lid)
        label = f"{prefix}_EventScript_BP_{e['id']}"
        f = e['fields']
        if 'script' in f:
            target = sym.check('label', f['script'])
        elif 'text' in f:
            body = say(e['id'], f['text'], 'npc')
            if 'text_night' in f:
                say(e['id'] + '_Night', f['text_night'], 'npc')
                body = ['gettimeofday', f'goto_if_eq VAR_0x8000, 3, {label}_Night'] + body
                script.upsert(f'{label}_Night', [f"msgbox {prefix}_Text_BP_{e['id']}_Night, MSGBOX_NPC", 'end'])
            script.upsert(label, body)
            target = label
        else:
            raise PkError('PARSE', f'{where}: npc needs text= or script=')
        obj = next((o for o in data.get('object_events') or [] if o.get('local_id') == lid), None)
        new = {'local_id': lid, 'type': 'object', 'graphics_id': gfx, 'x': x, 'y': y,
               'elevation': int(f.get('elev', 3)), 'movement_type': move,
               'movement_range_x': 0, 'movement_range_y': 0, 'trainer_type': 'TRAINER_TYPE_NONE',
               'trainer_sight_or_berry_tree_id': '0', 'script': target, 'flag': '0'}
        if 'range' in f:
            rx, ry = f['range'].split('x')
            new['movement_range_x'], new['movement_range_y'] = int(rx), int(ry)
        if 'flag' in f:
            new['flag'] = sym.check('flag', f['flag'])
        if 'when' in f:
            if f['when'] not in WHEN:
                raise PkError('PARSE', f'{where}: when={f["when"]}; use {"|".join(WHEN)}')
            timed.append((lid, f['when']))
        if obj is None:
            data.setdefault('object_events', []).append(new)
            ops.append(f"npc {e['id']} add @{x},{y}")
        else:
            flag = obj.get('flag')
            obj.update(new)
            if f.get('when') and flag:
                obj['flag'] = flag
    for e in entities(bp, 'trainers'):
        where = f"{path}:{e['line']}"
        x, y = pos(e, where)
        f = e['fields']
        trainer = f.get('trainer', '')
        if trainer not in sym.trainers:
            raise PkError('NOTFOUND', f'{where}: {trainer or "trainer="} is not defined; the tech agent (GLM) must add '
                                      f'it with a party in src/data/trainers_frlg.party first')
        missing = [k for k in ('intro', 'defeat', 'after') if k not in f]
        if missing:
            raise PkError('PARSE', f'{where}: trainer needs intro= defeat= after= texts (missing {" ".join(missing)})')
        lid = f"LOCALID_BP_{snake(prefix)}_{snake(e['id'])}"
        managed.add(lid)
        label = f"{prefix}_EventScript_BP_{e['id']}"
        for part in ('intro', 'defeat', 'after'):
            source, _ = text.compose(f[part])
            script.text_block(f"{prefix}_Text_BP_{e['id']}_{part.capitalize()}", source)
        script.upsert(label, [f"trainerbattle_single {trainer}, {prefix}_Text_BP_{e['id']}_Intro, {prefix}_Text_BP_{e['id']}_Defeat",
                              f"msgbox {prefix}_Text_BP_{e['id']}_After, MSGBOX_AUTOCLOSE", 'end'])
        new = {'local_id': lid, 'type': 'object', 'graphics_id': sym.graphics(e['words'][0], frlg), 'x': x, 'y': y,
               'elevation': int(f.get('elev', 3)),
               'movement_type': sym.movement(e['words'][1] if len(e['words']) > 1 else 'FACE_DOWN'),
               'movement_range_x': 0, 'movement_range_y': 0, 'trainer_type': 'TRAINER_TYPE_NORMAL',
               'trainer_sight_or_berry_tree_id': str(int(f.get('sight', 3))), 'script': label, 'flag': '0'}
        obj = next((o for o in data.get('object_events') or [] if o.get('local_id') == lid), None)
        if obj is None:
            data.setdefault('object_events', []).append(new)
            ops.append(f"trainer {e['id']} {trainer} add @{x},{y}")
        else:
            obj.update(new)
    for kind, section in (('sign', 'signs'), ('trigger', 'triggers')):
        for e in entities(bp, section):
            where = f"{path}:{e['line']}"
            x, y = pos(e, where)
            label = f"{prefix}_EventScript_BP_{e['id']}"
            f = e['fields']
            if 'script' in f:
                target = sym.check('label', f['script'])
            elif 'text' in f and kind == 'sign':
                script.upsert(label, say(e['id'], f['text'], 'sign'))
                target = label
            else:
                raise PkError('PARSE', f'{where}: {kind} needs script= (or text= for signs)')
            if section == 'signs':
                key, new = 'bg_events', {'type': 'sign', 'x': x, 'y': y, 'elevation': 0,
                                         'player_facing_dir': 'BG_EVENT_PLAYER_FACING_ANY', 'script': target}
            else:
                m = re.fullmatch(r'(\w+)=(\w+)', f.get('var', ''))
                if not m:
                    raise PkError('PARSE', f'{where}: trigger needs var=VAR_X=value')
                key, new = 'coord_events', {'type': 'trigger', 'x': x, 'y': y, 'elevation': 3,
                                            'var': sym.check('var', m[1]), 'var_value': m[2], 'script': target}
            items = data.setdefault(key, [])
            match = next((o for o in items if o.get('script') == target and (o['x'], o['y']) == (x, y)), None) \
                or next((o for o in items if o.get('script') == target and '_BP_' in target), None)
            if match is None:
                items.append(new)
                ops.append(f"{kind} {e['id']} add @{x},{y}")
            else:
                match.update(new)
            managed.add(target)
    for e in entities(bp, 'warps'):
        where = f"{path}:{e['line']}"
        x, y = pos(e, where)
        if '->' not in e['words']:
            raise PkError('PARSE', f'{where}: warp is "id @x,y -> Map#n"')
        m = re.fullmatch(r'(\w+)#(\d+)', e['words'][e['words'].index('->') + 1])
        dest, ddata, _ = repo.map(m[1])
        items = data.setdefault('warp_events', [])
        if not any((w_['x'], w_['y']) == (x, y) and w_['dest_map'] == ddata['id'] and str(w_['dest_warp_id']) == m[2] for w_ in items):
            items.append({'x': x, 'y': y, 'elevation': 0, 'dest_map': ddata['id'], 'dest_warp_id': m[2]})
            ops.append(f"warp {e['id']} add @{x},{y} -> {dest}#{m[2]}")
    # vanilla tweaks
    for n, w in bp['sections']['vanilla']:
        where = f'{path}:{n}'
        if not w or w == ['keep', 'all']:
            continue
        verb, ref = w[0], w[1] if len(w) > 1 else ''
        if verb == 'text':
            label = ref.rstrip(':')
            source, _ = text.compose(w[2])
            t_path, t_text, block = text.locate(label)
            if t_path == script.path:
                script.text_block(label, source)
            else:
                tx.stage(t_path, replace_block(t_text, block, source).encode('utf-8'))
            ops.append(f'vanilla text {label}')
            continue
        target = find_ref(data, ref, where)
        if verb == 'move':
            m = re.fullmatch(r'@(\d+),(\d+)', w[2])
            target['x'], target['y'] = int(m[1]), int(m[2])
        elif verb == 'set':
            for item in w[2:]:
                k, v = item.split('=', 1)
                if k == 'move':
                    target['movement_type'] = sym.movement(v)
                elif k == 'gfx':
                    target['graphics_id'] = sym.graphics(v, frlg)
                elif k == 'range':
                    target['movement_range_x'], target['movement_range_y'] = map(int, v.split('x'))
                else:
                    raise PkError('PARSE', f'{where}: vanilla set supports move gfx range')
        elif verb == 'remove':
            for key in ('object_events', 'bg_events', 'coord_events'):
                if target in (data.get(key) or []):
                    data[key].remove(target)
        else:
            raise PkError('PARSE', f'{where}: vanilla verbs: keep all | move | set | remove | text')
        ops.append(f'vanilla {verb} {ref}')
    # prune blueprint-made events no longer in the blueprint
    for key in ('object_events', 'bg_events', 'coord_events'):
        before = len(data.get(key) or [])
        data[key] = [o for o in data.get(key) or []
                     if not (('_BP_' in str(o.get('local_id', '')) and o.get('local_id') not in managed)
                             or ('_EventScript_BP_' in str(o.get('script', '')) and o.get('script') not in managed
                                 and not o.get('local_id')))]
        if len(data[key]) != before:
            ops.append(f'pruned {before - len(data[key])} {key}')
    # time-of-day visibility
    if timed:
        hide_lines = ['gettimeofday']
        used = set(re.findall(r'FLAG_TEMP_[0-9A-F]+\b', script.text + json.dumps(data)))
        free = (f'FLAG_TEMP_{k:X}' for k in range(1, 0x20) if f'FLAG_TEMP_{k:X}' not in used)
        for lid, when in timed:
            obj = next(o for o in data['object_events'] if o.get('local_id') == lid)
            if not str(obj.get('flag', '0')).startswith('FLAG_TEMP_'):
                obj['flag'] = next(free, None)
                if obj['flag'] is None:
                    raise PkError('LIMIT', f'{name}: no free FLAG_TEMP_* for time-of-day NPCs')
            hide_lines.append(f'call {prefix}_EventScript_BP_Hide_{lid[len("LOCALID_BP_"):]}')
            body = [f'goto_if_eq VAR_0x8000, {t}, Common_EventScript_NopReturn' for t in WHEN[when]]
            script.upsert(f'{prefix}_EventScript_BP_Hide_{lid[len("LOCALID_BP_"):]}', body + [f"setflag {obj['flag']}", 'return'])
        script.upsert(f'{prefix}_EventScript_BP_TimeOfDay', hide_lines + ['return'])
        hook_transition(script, name, prefix)
        ops.append(f'time-of-day visibility for {len(timed)} npcs')
    # connections, texts
    for n, w in bp['sections']['connections']:
        if len(w) >= 3 and w[2].startswith('offset=') and w[2] != 'offset=auto':
            for c in data.get('connections') or []:
                if c['direction'] == w[0] and c['map'] == w[1]:
                    c['offset'] = int(w[2].split('=')[1])
    for n, w in bp['sections']['texts']:
        if len(w) != 2 or not w[0].endswith(':'):
            raise PkError('PARSE', f'{path}:{n}: texts lines are  Label: "plain text"')
        label = w[0][:-1]
        source, _ = text.compose(w[1])
        try:
            t_path, t_text, block = text.locate(label)
            if t_path == script.path:
                script.text_block(label, source)
            else:
                tx.stage(t_path, replace_block(t_text, block, source).encode('utf-8'))
        except PkError:
            script.text_block(label, source)
    if bp['sections']['scripts']:
        raw = '\n'.join(line for _, line in bp['sections']['scripts'])
        for m in re.finditer(r'^(\w+):\n((?:[ \t]+.*\n?)*)', raw, re.M):
            script.upsert(m[1], [l.strip() for l in m[2].splitlines() if l.strip()])
    tx.stage(f'data/maps/{name}/map.json', doc.emit())
    script.save()
    # 6. verification -------------------------------------------------------------------------
    errors = list(pre_errors)
    if opts.get('strict'):
        errors += [f'strict: {w}' for w in warns if 'seams never seen' in w]
    gates = reach.diff(repo, name, (old_data, old_layout, disk), (data, layout, repo), shift)
    errors += gates
    result = map_check.run(repo, tx, name, {}, lambda o, d=20: (0, 20))
    errors += [r[4:] for r in result.data if r.startswith('ERR ')]
    changed = sorted(k for k, v in tx.writes.items() if v != tx.original.get(k) and not k.startswith('.pk/'))
    summary = f"bp {mode} {name} files:{len(changed)} ops:{len(ops)} warn:{len(warns)} errors:{len(errors)}"
    if not changed:
        summary = f'bp {mode} {name}: nothing to do (map matches blueprint)'
    rows = [f'ERR {e}' for e in errors] + [f'WARN {w}' for w in warns] + ops
    if errors and mode in ('apply', 'check'):
        raise PkError('PARSE', summary + ' (' + errors[0] + ')', rows[:18])
    return Result(summary, rows[:18], more=18 if len(rows) > 18 else None)


def find_ref(data, ref, where):
    m = re.fullmatch(r'(obj|bg|coord)#(\d+)', ref)
    keys = {'obj': 'object_events', 'bg': 'bg_events', 'coord': 'coord_events'}
    if m:
        items = data.get(keys[m[1]]) or []
        if int(m[2]) < len(items):
            return items[int(m[2])]
    for key in keys.values():
        for o in data.get(key) or []:
            if ref in (o.get('local_id'), o.get('script')):
                return o
    raise PkError('NOTFOUND', f'{where}: vanilla ref {ref}; use script label, LOCALID_* or obj#n')


def hook_transition(script, name, prefix):
    """Make the map's ON_TRANSITION script call the time-of-day hider first."""
    call = f'call {prefix}_EventScript_BP_TimeOfDay'
    lines, i, j = script.find(f'{name}_MapScripts')
    if i is None:
        raise PkError('NOTFOUND', f'{name}_MapScripts label; cannot hook time-of-day NPCs')
    m = re.search(r'map_script MAP_SCRIPT_ON_TRANSITION,\s*(\w+)', script.text)
    if m:
        t_lines, ti, tj = script.find(m[1])
        if ti is None:
            raise PkError('NOTFOUND', f'{m[1]} (ON_TRANSITION) not in scripts.inc')
        if call not in '\n'.join(t_lines[ti:tj]):
            cr = '\r' if t_lines[ti].endswith('\r') else ''
            script.text = '\n'.join(t_lines[:ti + 1] + [f'\t{call}{cr}'] + t_lines[ti + 1:])
        return
    label = f'{prefix}_OnTransition_BP'
    cr = '\r' if lines[i].endswith('\r') else ''
    script.text = '\n'.join(lines[:i + 1] + [f'\tmap_script MAP_SCRIPT_ON_TRANSITION, {label}{cr}'] + lines[i + 1:])
    script.upsert(label, [call, 'end'])
