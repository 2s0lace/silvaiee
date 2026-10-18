"""Vanilla shapes: water shapes retain their exact source tiles and full portable bank ring."""
import hashlib
import io
import json
import os
import re
from collections import Counter

from PIL import Image, ImageDraw, ImageFont

from ..core.errors import PkError
from ..core.out import Result
from .edit import load, save
from .layers import Grid, events
from .lint import physical_class, vanilla_outdoor
from .meta import render_png
from .objects import Glue, check as object_warnings
from .terrain import Rules, apply as terrain_apply, solve, _seen_rules, unseen
from .tiles import Pair

INDEX_REL = '.pk/shapes/index.json'
INDEX_VERSION = 5
SHEET_REL = '.pk/render/shapes.png'
KINDS = ('pond', 'cove', 'sandbar', 'beach', 'island', 'grove', 'tallgrass')
SIZE_NEAR = 2
DEFAULT_LIMIT, MAX_LIMIT = 10, 200
MASK_COLORS = {'water': (48, 128, 224), 'sand': (232, 208, 128), 'grass': (72, 176, 72),
               'tallgrass': (48, 144, 48), 'tree': (32, 112, 48), 'ground': (184, 152, 104)}


def is_water(pair, cell):
    """Game behavior, independent of unreliable manually assigned water names/tags."""
    behavior = pair.behavior_name(cell[0])
    return bool(re.search(r'WATER|OCEAN|POND|CURRENT|SEAWEED|PUDDLE', behavior)) and not re.search(
        r'WATERFALL|SHALLOW', behavior)


class ShapeRules(Rules):
    def material(self, cell):
        material = super().material(cell)
        if is_water(self.pair, cell):
            return 'water'
        if material == 'water':
            return 'solid' if cell[1] else 'ground'
        return material


def pattern(water, x, y):
    return tuple((x + dx, y + dy) in water for dy in (-1, 0, 1) for dx in (-1, 0, 1))


def shore_candidates(rules, signature, water_kind):
    """Prefer exact neighborhoods; a missing diagonal may use the closest matching cardinal shore."""
    def fitting(counts):
        return {cell: n for cell, n in counts.items() if not is_water(rules.pair, cell) or
                ('PUDDLE' in rules.pair.behavior_name(cell[0])) == (water_kind == 'puddle')}

    exact = fitting(rules.patterns.get(signature, {}))
    if exact:
        return exact
    alternatives, best = Counter(), 10
    for candidate, counts in rules.patterns.items():
        if any(signature[i] != candidate[i] for i in (1, 3, 4, 5, 7)):
            continue
        counts = fitting(counts)
        if not counts:
            continue
        distance = sum(a != b for a, b in zip(signature, candidate))
        if distance < best:
            alternatives, best = Counter(), distance
        if distance == best:
            alternatives.update(counts)
    return alternatives


def mask_ring(cells, grid):
    """The actual mask outline, including concave shores, rather than its bounding rectangle."""
    return {(x + dx, y + dy) for x, y in cells for dx in (-1, 0, 1) for dy in (-1, 0, 1)
            if grid.inside(x + dx, y + dy)} - set(cells)


def source_patch(repo, entry):
    """Return the unmodified source water cells and full eight-neighbour bank, in mask coordinates."""
    source, pair = load(repo, entry['source'])
    sx, sy, _, _ = entry['box']
    mask = {(sx + x, sy + y) for x, y, _ in entry['cells']}
    if not mask or any(not source.inside(*p) or not is_water(pair, source.at(*p)) for p in mask):
        raise PkError('CONFLICT', f'{entry["id"]} not placeable: source water mask changed')
    ring = {(x + dx, y + dy) for x, y in mask for dx in (-1, 0, 1) for dy in (-1, 0, 1)} - mask
    if any(not source.inside(*p) for p in ring):
        raise PkError('CONFLICT', f'{entry["id"]} not placeable: full source bank extends outside its map')
    water = {(x - sx, y - sy): source.at(x, y) for x, y in mask}
    banks = {(x - sx, y - sy): source.at(x, y) for x, y in ring}
    return water, banks, pair


def portable_patch(repo, target_pair, entry, flip=None):
    """Validate every source tile, not just the water mask. No substitutions or solver fallback."""
    water, banks, source_pair = source_patch(repo, entry)
    if source_pair.version != target_pair.version or source_pair.primary['symbol'] != target_pair.primary['symbol']:
        raise PkError('CONFLICT', f'{entry["id"]} not placeable: source primary tileset differs')
    bad = {kind: [(p, c[0]) for p, c in sorted(cells.items())
                  if c[0] >= source_pair.limits['metatiles'] or not target_pair.valid(c[0])]
           for kind, cells in (('water', water), ('bank', banks))}
    if any(bad.values()):
        raise PkError('CONFLICT', f'{entry["id"]} not placeable: non-portable source tiles '
                      f'(water:{len(bad["water"])} bank:{len(bad["bank"])})',
                      [f'{kind} @{x},{y} 0x{mt:03X}' for kind, tiles in bad.items() for (x, y), mt in tiles])
    if flip:
        raise PkError('CONFLICT', f'{entry["id"]} not placeable: exact directional source banks cannot be flipped')
    return water, banks


def bank_errors(grid, pair, water, lawn_ids=()):
    """Require a solid bank neighbour at every outline cell and reject every open lawn-facing side."""
    rules = ShapeRules.__new__(ShapeRules)
    rules.pair = pair
    lawn_ids = set(lawn_ids)

    def bank(cell):
        if is_water(pair, cell):
            return False
        if cell[1]:
            return True
        return cell[0] not in lawn_ids and rules.material(cell) not in (
            'lawn', 'meadow', 'grass', 'tallgrass', 'tall_grass', 'ground')

    errors = []
    for x, y in sorted(water):
        outside = [(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                   if (x + dx, y + dy) not in water]
        if not outside:
            continue
        valid = [p for p in outside if grid.inside(*p) and bank(grid.at(*p))]
        if not valid:
            errors.append(f'water @{x},{y} has no non-water, non-lawn bank neighbour')
        for p in outside:
            if not grid.inside(*p) or not bank(grid.at(*p)):
                errors.append(f'open water/bank side @{x},{y} -> {p[0]},{p[1]}')
    return errors


def lawn_around(grid, pair, box):
    """Measure the actual walkable metatile just outside the destination box, before painting."""
    x0, y0, w, h = box
    border = {(x, y) for y in range(y0 - 1, y0 + h + 1) for x in range(x0 - 1, x0 + w + 1)
              if grid.inside(x, y) and not (x0 <= x < x0 + w and y0 <= y < y0 + h)}
    protected = set(events(grid.data))
    cells = [grid.at(*p) for p in border - protected
             if not is_water(pair, grid.at(*p)) and
             physical_class(pair.behavior_name(grid.at(*p)[0]), grid.at(*p)[1]) == 'walk']
    if not cells:
        raise PkError('NOTFOUND', 'no walkable target lawn around shape box; nothing written')
    counts = Counter(cell[0] for cell in cells)
    mt = min(counts, key=lambda m: (-counts[m], m))
    bits = Counter(cell[1:] for cell in cells if cell[0] == mt)
    coll, elev = min(bits, key=lambda b: (-bits[b], b))
    return mt, coll, elev


def placement_box(grid, box, margin=2):
    x0, y0, w, h = box
    return {(x, y) for y in range(max(0, y0 - margin), min(grid.h, y0 + h + margin))
            for x in range(max(0, x0 - margin), min(grid.w, x0 + w + margin))}


def neighbours(x, y, w, h):
    for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
        if 0 <= nx < w and 0 <= ny < h:
            yield nx, ny


def components(cells, w, h):
    """Deterministic 4-connected components of a set of coordinates."""
    left, out = set(cells), []
    while left:
        first = min(left, key=lambda p: (p[1], p[0]))
        left.remove(first)
        todo, found = [first], {first}
        while todo:
            x, y = todo.pop()
            for p in neighbours(x, y, w, h):
                if p in left:
                    left.remove(p)
                    found.add(p)
                    todo.append(p)
        out.append(found)
    return out


def box_of(cells):
    xs, ys = [p[0] for p in cells], [p[1] for p in cells]
    x0, y0 = min(xs), min(ys)
    return x0, y0, max(xs) - x0 + 1, max(ys) - y0 + 1


def touches_edge(cells, w, h):
    return any(x in (0, w - 1) or y in (0, h - 1) for x, y in cells)


def outline_curviness(cells):
    """Corner count of the mask outline (including concave corners), independent of rotation."""
    cells = set(cells)
    if not cells:
        return 0
    x0, y0, w, h = box_of(cells)
    turns = 0
    for y in range(y0, y0 + h + 1):
        for x in range(x0, x0 + w + 1):
            bits = ((x - 1, y - 1) in cells, (x, y - 1) in cells,
                    (x - 1, y) in cells, (x, y) in cells)
            n = sum(bits)
            if n in (1, 3):
                turns += 1
            elif n == 2 and bits[0] == bits[3]:       # two diagonal cells: two outline turns
                turns += 2
    return turns


def _entry(kind, name, pair, cells, materials, grid):
    x0, y0, w, h = box_of(cells)
    mask = [[x - x0, y - y0, materials[(x, y)]] for x, y in sorted(cells, key=lambda p: (p[1], p[0]))]
    signature = f'{kind}|{name}|{x0},{y0},{w}x{h}|{mask}'.encode()
    ident = f'{kind}-{hashlib.sha1(signature).hexdigest()[:10]}'
    portable = all(grid.at(x, y)[0] < pair.limits['metatiles'] for x, y in cells)
    bank = mask_ring(cells, grid) if kind in ('pond', 'cove') else set()
    nonportable_bank = [(x - x0, y - y0, grid.at(x, y)[0]) for x, y in sorted(bank)
                        if grid.at(x, y)[0] >= pair.limits['metatiles']]
    bank_complete = not touches_edge(cells, grid.w, grid.h)
    return dict(version=INDEX_VERSION, id=ident, kind=kind, w=w, h=h, source=name, box=[x0, y0, w, h],
                pair=f"{pair.primary['symbol']}+{pair.secondary['symbol']}",
                portable=portable and (kind not in ('pond', 'cove') or (bank_complete and not nonportable_bank)),
                bank_portable=bank_complete and not nonportable_bank, nonportable_bank=nonportable_bank,
                water_kind='puddle' if all('PUDDLE' in pair.behavior_name(grid.at(x, y)[0]) for x, y in cells) else 'surf',
                curviness=outline_curviness(cells), cells=mask)


def extract_grid(repo, name, grid, pair):
    """Extract shape entries from one vanilla grid using physical classes and Rules.material."""
    rules = ShapeRules(repo, pair, include_pk=False)
    phys, mats = {}, {}
    for y in range(grid.h):
        for x in range(grid.w):
            cell = grid.at(x, y)
            phys[(x, y)] = physical_class(pair.behavior_name(cell[0]), cell[1])
            mats[(x, y)] = rules.material(cell)
    out = []
    # Water masks use the terrain material, not collision-derived `surf`: FRLG deep-water cells may
    # carry collision bits and physical_class deliberately reports those as wall.
    water = {p for p, material in mats.items() if material == 'water'}
    for comp in components(water, grid.w, grid.h):
        if not touches_edge(comp, grid.w, grid.h):
            out.append(_entry('pond', name, pair, comp, mats, grid))
            continue
        sides = set()
        for x, y in comp:
            for nx, ny in neighbours(x, y, grid.w, grid.h):
                if (nx, ny) not in comp and (nx, ny) not in water:
                    if ny < y: sides.add('top')
                    if ny > y: sides.add('bottom')
                    if nx < x: sides.add('left')
                    if nx > x: sides.add('right')
        if len(sides) >= 2:
            out.append(_entry('cove', name, pair, comp, mats, grid))

    sand = {p for p, mat in mats.items() if mat == 'sand'}
    for comp in components(sand, grid.w, grid.h):
        border = {n for x, y in comp for n in neighbours(x, y, grid.w, grid.h) if n not in comp}
        water_n = border & water
        if not water_n:
            continue
        kind = 'sandbar' if not touches_edge(comp, grid.w, grid.h) and border == water_n else 'beach'
        out.append(_entry(kind, name, pair, comp, mats, grid))

    # A land island is a non-water component wholly bounded by water and not joined to the map edge.
    land = set(phys) - water
    for comp in components(land, grid.w, grid.h):
        if touches_edge(comp, grid.w, grid.h):
            continue
        border = {n for x, y in comp for n in neighbours(x, y, grid.w, grid.h) if n not in comp}
        if border and border <= water:
            out.append(_entry('island', name, pair, comp, mats, grid))

    # Object-card constraints distinguish multi-tile wall objects from generic collision alone.
    glue = Glue(repo, grid, pair, edges=False)
    from .objects import SIDES, constrained
    grove_cells = set()
    for p, cls in phys.items():
        rec = glue.raw.get(grid.at(*p)[0])
        if cls == 'wall' and rec is not None and any(constrained(Counter(rec['nmt'][side])) for side in SIDES):
            grove_cells.add(p)
    for comp in components(grove_cells, grid.w, grid.h):
        if len(comp) >= 4:
            out.append(_entry('grove', name, pair, comp, mats, grid))

    tall = {p for p, cls in phys.items() if cls == 'grass_enc'}
    for comp in components(tall, grid.w, grid.h):
        out.append(_entry('tallgrass', name, pair, comp, mats, grid))
    return out


def build_index(repo):
    maps, layouts = repo.maps(), repo.layouts()
    entries, notes, scanned = [], [], 0
    for name, _ in vanilla_outdoor(repo):
        try:
            data = maps[name]
            grid = Grid(repo, name, data, layouts[data['layout']])
            pair = Pair(repo, grid.layout)
            entries.extend(extract_grid(repo, name, grid, pair))
            scanned += 1
        except PkError as exc:
            notes.append(f'INFO map {name} skipped: {exc}')
    entries.sort(key=lambda e: (KINDS.index(e['kind']), e['source'], e['box'][1], e['box'][0], e['id']))
    return entries, scanned, notes


def write_index(repo, entries):
    path = repo.path(INDEX_REL)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(entries, separators=(',', ':')) + '\n', encoding='utf-8')
    os.replace(tmp, path)
    return path


def load_index(repo, notes=None):
    path = repo.path(INDEX_REL)
    if path.is_file():
        try:
            entries = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(entries, list) and entries and all(isinstance(e, dict) and
                    e.get('version') == INDEX_VERSION and
                    set(('id', 'kind', 'w', 'h', 'source', 'box', 'curviness', 'cells')) <= set(e) for e in entries):
                return entries
        except (OSError, ValueError, TypeError):
            pass
        if notes is not None:
            notes.append('INFO stale or corrupt shape index; rebuilding')
    entries, scanned, built_notes = build_index(repo)
    if not getattr(getattr(repo, 'transaction', None), 'dry_run', False):
        write_index(repo, entries)
    if notes is not None:
        notes.extend(built_notes)
        notes.append(f'INFO index rebuilt: {len(entries)} shapes from {scanned} maps')
    return entries


def parse_size(text):
    match = re.fullmatch(r'(\d+)x(\d+)', text or '')
    if not match or not int(match[1]) or not int(match[2]):
        raise PkError('PARSE', f'--size {text!r} must be WxH, e.g. 8x6')
    return int(match[1]), int(match[2])


def parse_point(text):
    match = re.fullmatch(r'(\d+),(\d+)', text or '')
    if not match:
        raise PkError('PARSE', f'point {text!r} must be x,y')
    return int(match[1]), int(match[2])


def find(entries, kind, size=None, limit=DEFAULT_LIMIT):
    if kind not in KINDS:
        raise PkError('PARSE', f'--kind {kind}; use one of {" ".join(KINDS)}')
    matches = []
    for entry in entries:
        if entry['kind'] != kind:
            continue
        if size is None:
            tier = 0
        elif (entry['w'], entry['h']) == size:
            tier = 0
        elif abs(entry['w'] - size[0]) <= SIZE_NEAR and abs(entry['h'] - size[1]) <= SIZE_NEAR:
            tier = 1
        else:
            continue
        matches.append((tier, entry))
    matches.sort(key=lambda item: (item[0], -item[1]['curviness'], item[1]['source'], item[1]['id']))
    return matches[:limit], len(matches)


def _font():
    try:
        return ImageFont.load_default(size=14)
    except TypeError:
        return ImageFont.load_default()


def sheet(repo, hits):
    pieces, notes = [], []
    for rank, (_, entry) in enumerate(hits, 1):
        try:
            grid, pair = load(repo, entry['source'])
            original = Image.open(io.BytesIO(render_png(repo, grid, pair, tuple(entry['box']), 2))).convert('RGB')
        except (PkError, OSError) as exc:
            notes.append(f'INFO {entry["id"]} not rendered: {exc}')
            continue
        mask = Image.new('RGB', original.size, (24, 24, 24))
        draw = ImageDraw.Draw(mask)
        sx, sy = original.width // entry['w'], original.height // entry['h']
        for x, y, material in entry['cells']:
            color = MASK_COLORS.get(material, (208, 96, 192))
            draw.rectangle((x * sx, y * sy, (x + 1) * sx - 1, (y + 1) * sy - 1), fill=color)
        pieces.append((rank, entry, original, mask))
    if not pieces:
        return None, notes
    gap, label_h = 8, 20
    font = _font()
    widths = [a.width + 4 + b.width for _, _, a, b in pieces]
    cell_w, cell_h = max(widths), label_h + max(a.height for _, _, a, _ in pieces)
    cols, rows = min(3, len(pieces)), -(-len(pieces) // min(3, len(pieces)))
    canvas = Image.new('RGB', (gap + cols * (cell_w + gap), gap + rows * (cell_h + gap)), (24, 24, 24))
    draw = ImageDraw.Draw(canvas)
    for i, (rank, entry, original, mask) in enumerate(pieces):
        x, y = gap + (i % cols) * (cell_w + gap), gap + (i // cols) * (cell_h + gap)
        draw.text((x, y + 2), f'{rank} {entry["id"]} {entry["source"]} {entry["w"]}x{entry["h"]}',
                  fill='white', font=font)
        canvas.paste(original, (x, y + label_h))
        canvas.paste(mask, (x + original.width + 4, y + label_h))
    buf = io.BytesIO()
    canvas.save(buf, 'PNG')
    return buf.getvalue(), notes


def command_find(repo, args, opts):
    if args != ['find'] or 'kind' not in opts:
        raise PkError('PARSE', 'usage: pk shape find --kind KIND [--size WxH] [--limit 10] [--sheet]')
    try:
        limit = int(opts.get('limit', DEFAULT_LIMIT))
    except ValueError:
        raise PkError('PARSE', '--limit must be an integer') from None
    if not 1 <= limit <= MAX_LIMIT:
        raise PkError('BOUNDS', f'--limit 1..{MAX_LIMIT}')
    notes = []
    entries = load_index(repo, notes)
    hits, total = find(entries, opts['kind'], parse_size(opts['size']) if 'size' in opts else None, limit)
    rows = [f"{e['id']} {e['source']} {e['box'][0]},{e['box'][1]} {e['w']}x{e['h']} curvy:{e['curviness']}"
            + (' NOT PLACEABLE: full bank ring is incomplete or non-portable'
               if e['kind'] in ('pond', 'cove') and not e.get('bank_portable', False) else '')
            for _, e in hits]
    rows += notes
    if opts.get('sheet') and not getattr(getattr(repo, 'transaction', None), 'dry_run', False):
        png, sheet_notes = sheet(repo, hits)
        rows += sheet_notes
        if png is not None:
            path = repo.path(SHEET_REL)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(png)
            rows.append(f'wrote {SHEET_REL}')
    return Result(f'shape find {opts["kind"]} matched:{total} shown:{len(hits)}', rows)


def _flipped_cells(entry, flip):
    for x, y, material in entry['cells']:
        if flip == 'h':
            x = entry['w'] - 1 - x
        elif flip == 'v':
            y = entry['h'] - 1 - y
        yield x, y, material


def placement_rules(repo, pair, entry):
    """Learn legal primary transitions and directional water/land patterns from vanilla only."""
    cache = getattr(repo, '_pk_shape_rules', {})
    key = pair.primary['symbol'], pair.secondary['symbol']
    if key in cache:
        return cache[key]
    rules = ShapeRules(repo, pair, include_pk=False)
    rules.patterns, rules.cell_counts = {}, {}
    maps, layouts = repo.maps(), repo.layouts()
    limit = pair.limits['metatiles']
    for name, _ in vanilla_outdoor(repo):
        data = maps[name]
        layout = layouts[data['layout']]
        if layout['primary_tileset'] != key[0]:
            continue
        same = layout['secondary_tileset'] == key[1]
        source = Grid(repo, name, data, layout)
        water = {(x, y) for y in range(source.h) for x in range(source.w)
                 if (same or source.at(x, y)[0] < limit) and is_water(pair, source.at(x, y))}
        for y in range(source.h):
            for x in range(source.w):
                cell = source.at(x, y)
                mt = cell[0]
                if not same and mt >= limit:
                    continue
                rules.cell_counts.setdefault(mt, Counter())[cell[1:]] += 1
                if not same:
                    rules.freq[mt] = rules.freq.get(mt, 0) + 1
                    rules.by_mat.setdefault(rules.material(cell), set()).add(mt)
                    for attr, dx, dy in (('h', 1, 0), ('v', 0, 1), ('ne', 1, -1),
                                         ('nw', -1, -1), ('se', 1, 1), ('sw', -1, 1)):
                        if source.inside(x + dx, y + dy):
                            nb = source.at(x + dx, y + dy)[0]
                            if nb < limit:
                                table = getattr(rules, attr)
                                table[(mt, nb)] = table.get((mt, nb), 0) + 1
                if not (0 < x < source.w - 1 and 0 < y < source.h - 1):
                    continue
                window = [source.at(x + dx, y + dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
                if not same and any(c[0] >= limit for c in window):
                    continue
                cls = physical_class(pair.behavior_name(mt), cell[1])
                if cls not in ('walk', 'surf') or (not is_water(pair, cell) and rules.material(cell) in ('sand', 'path')):
                    continue
                signature = pattern(water, x, y)
                rules.patterns.setdefault(signature, Counter())[cell] += 1
    cache[key] = rules
    repo._pk_shape_rules = cache
    return rules


def auto_outline(repo, tx, grid, pair, rules, box, frozen, water_kind='surf', lawn=None):
    """Solve ONLY the outer lawn ring; every copied source water/bank tuple is frozen."""
    if lawn is None:
        lawn = lawn_around(grid, pair, box)
    editable = mask_ring(frozen, grid)
    fixed = {p: grid.at(*p) for p in frozen}
    fill = solve(grid, _seen_rules(rules), {p: rules.material(lawn) for p in editable},
                 margin=0, choices={p: [lawn[0]] for p in editable}, passes=6)
    for p, mt in fill.items():
        grid.set(*p, (mt, lawn[1], lawn[2]))
    if any(grid.at(*p) != cell for p, cell in fixed.items()):
        raise PkError('CONFLICT', 'source water/bank changed during outer-lawn solve')
    save(tx, grid)
    return f'outer lawn cells:{len(editable)}; source water/bank frozen:{len(frozen)}'


def command_place(repo, tx, args, opts):
    """Keep a failed placement atomic even when called within a larger in-memory transaction."""
    prior_writes = dict(tx.writes)
    try:
        return _command_place(repo, tx, args, opts)
    except Exception:
        tx.writes.clear()
        tx.writes.update(prior_writes)
        raise


def _command_place(repo, tx, args, opts):
    if len(args) != 4:
        raise PkError('PARSE', 'usage: pk shape place MAP KIND_OR_ID x,y [--flip h|v] [--dry-run]')
    _, query, wanted, point_text = args
    flip = opts.get('flip')
    if flip not in (None, 'h', 'v'):
        raise PkError('PARSE', '--flip must be h or v')
    entries = load_index(repo)
    x0, y0 = parse_point(point_text)
    grid, pair = load(repo, query)
    choices = [e for e in entries if e['id'] == wanted]
    if not choices:
        if wanted not in KINDS:
            raise PkError('NOTFOUND', f'shape {wanted}; use pk shape find --kind KIND')
        ranked = find(entries, wanted, limit=len(entries))[0]
        choices = [e for _, e in ranked if grid.inside(x0 + e['w'] - 1, y0 + e['h'] - 1)
                   and (e['kind'] not in ('pond', 'cove') or (e.get('bank_portable') and e['portable']))][:1]
    if not choices:
        raise PkError('NOTFOUND', f'no vanilla {wanted} shape fits {grid.name} at {x0},{y0}')
    entry = choices[0]
    if not (grid.inside(x0, y0) and grid.inside(x0 + entry['w'] - 1, y0 + entry['h'] - 1)):
        raise PkError('BOUNDS', f'{entry["w"]}x{entry["h"]} shape at {x0},{y0} outside {grid.name} {grid.w}x{grid.h}')
    water_shape = entry['kind'] in ('pond', 'cove')
    source_water, source_bank = portable_patch(repo, pair, entry, flip) if water_shape else ({}, {})
    copied = {(x0 + x, y0 + y): cell for (x, y), cell in {**source_water, **source_bank}.items()}
    if any(not grid.inside(*p) for p in copied):
        raise PkError('BOUNDS', f'{entry["id"]} not placeable: full source bank does not fit destination')
    before_cells = list(grid.cells)
    before_objects = set(object_warnings(repo, grid, pair))
    targets = {(x0 + x, y0 + y): material for x, y, material in _flipped_cells(entry, flip)}
    protected = set(events(grid.data))
    affected = set(copied) if water_shape else set(targets) | mask_ring(targets, grid)
    affected |= mask_ring(affected, grid)
    if affected & protected:
        raise PkError('CONFLICT', 'shape outline overlaps map events; choose another position')
    box = (x0, y0, entry['w'], entry['h'])
    rules = placement_rules(repo, pair, entry)
    lawn = lawn_around(grid, pair, box) if water_shape else None
    background = affected if water_shape else set()
    if background & protected:
        raise PkError('CONFLICT', 'shape box overlaps map events; choose another position')
    for p in background:
        if physical_class(pair.behavior_name(grid.at(*p)[0]), grid.at(*p)[1]) != 'walk':
            raise PkError('CONFLICT', f'shape lawn would overwrite terrain at {p[0]},{p[1]}; nothing written')
    if water_shape:
        for p, cell in copied.items():
            grid.set(*p, cell)
        errors = bank_errors(grid, pair, set(targets), [lawn[0]])
        if errors:
            raise PkError('CONFLICT', f'{entry["id"]} not placeable: source bank has open water/lawn sides', errors)
        auto_summary = auto_outline(repo, tx, grid, pair, rules, box, set(copied),
                                    entry.get('water_kind', 'surf'), lawn)
        if any(grid.at(*p) != cell for p, cell in copied.items()):
            raise PkError('CONFLICT', 'source water/bank was changed; nothing written')
        errors = bank_errors(grid, pair, set(targets), [lawn[0]])
        if errors:
            raise PkError('CONFLICT', 'outer lawn solve left an open bank; nothing written', errors)
    else:
        terrain_apply(repo, tx, grid, pair, rules, targets, f'shape {entry["id"]}')
        auto_summary = 'material outline preserved'

    from . import lint, reach
    mask_cells = list(targets)
    fidelity = sum(rules.material(grid.at(*p)) == targets[p] for p in mask_cells) / len(mask_cells)
    ring = list(mask_ring(targets, grid) | set(targets))
    seams = len(unseen(grid, rules, ring))
    objects = object_warnings(repo, grid, pair)
    reach_row = reach.check_write(repo, pair, before_cells, grid)
    if set(objects) - before_objects or reach_row.startswith('WARN reach'):
        raise PkError('CONFLICT', 'shape would break objects or reachability; nothing written',
                      list(set(objects) - before_objects) + [reach_row])
    lint_result = lint.run(repo, grid.name)
    rows = [lint_result.summary,
            f'placed {entry["id"]} source:{entry["source"]} size:{entry["w"]}x{entry["h"]} '
            f'cells:{len(mask_cells)} fidelity:{fidelity:.3f} seams:{seams}',
            f'outline {auto_summary}']
    if lawn is not None:
        rows.append(f'lawn 0x{lawn[0]:03X} coll:{lawn[1]} elev:{lawn[2]} (measured destination ring)')
        rows.append(f'source tiles preserved: water:{len(source_water)} bank:{len(source_bank)}; open bank sides:0')
    rows += objects
    rows.append(reach_row)
    return Result(f'shape place {grid.name} {entry["id"]} at {x0},{y0}', rows)


def command(repo, tx, args, opts):
    if args and args[0] == 'find':
        return command_find(repo, args, opts)
    if args and args[0] == 'place':
        return command_place(repo, tx, args, opts)
    raise PkError('PARSE', 'usage: pk shape find --kind KIND | pk shape place MAP KIND_OR_ID x,y')
