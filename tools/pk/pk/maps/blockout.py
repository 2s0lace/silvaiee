"""Experimental, name-independent blockouts. No vanilla-map writes are allowed.

API evidence: edit.load/save; layers.Grid.at/set/inside; lint.physical_class and
vanilla_outdoor; cards.aggregate/finalize; meta.draw/render_compare; terrain.solve
and Rules.pair_score; objects.Glue/check; kit.glued_sides/window_cuts; reach.World.
The colour classifier is deliberately documented as heuristic, not semantic truth.
"""
from collections import Counter
from copy import copy

from ..core.errors import PkError
from ..core.out import Result
from ..core.repo import Repo
from . import cards, kit, layout, objects, terrain
from .edit import load, save
from .layers import events
from .lint import physical_class, vanilla_outdoor, stratum_of
from .meta import draw
from .reach import World, STATES

HEADER = 'blockout 1'
LEGEND = 'legend T=trees .=lawn p=path g=tallgrass ~=water s=sand r=rock v=ledge_s ^=ledge_n <=ledge_w >=ledge_e f=flowers b=building'
LETTERS = frozenset('T.pg~srv^<>fb')
WALK = frozenset('.pgsf')
LEDGE_SYMBOL = {'ledge_s': 'v', 'ledge_n': '^', 'ledge_w': '<', 'ledge_e': '>'}
LEDGE_STEP = {'v': (0, 1), '^': (0, -1), '<': (-1, 0), '>': (1, 0)}


def parse(text, w, h):
    rows = text.splitlines()
    if rows[:2] != [HEADER, LEGEND]:
        raise PkError('PARSE', 'expected blockout 1 and the fixed legend (pk help blockout)')
    rows = rows[2:]
    if len(rows) != h or any(len(row) != w for row in rows):
        raise PkError('BOUNDS', f'blockout must have exactly {h} rows of {w} cells')
    for y, row in enumerate(rows):
        for x, char in enumerate(row):
            if char not in LETTERS:
                raise PkError('PARSE', f'unknown blockout letter {char!r} at {x},{y}')
    return rows


def serialize(rows):
    return '\n'.join([HEADER, LEGEND, *rows]) + '\n'


class Classifier:
    """Physical class + card evidence + rendered palette; never reads name/tags.

    Cards alone cannot separate road/lawn or tree/rock. Colour thresholds below
    are a provisional visual heuristic; export reports that limitation explicitly.
    """
    def __init__(self, repo, pair):
        self.repo, self.pair = repo, pair
        # Unlike load_cards this path writes no cache, including during --check.
        self.cards = cards.finalize(cards.aggregate(repo, pair), pair)
        self.memo = {}

    def letter(self, cell):
        mt, coll, _ = cell
        key = mt, coll
        if key in self.memo:
            return self.memo[key]
        cls = physical_class(self.pair.behavior_name(mt), coll)
        card = self.cards.get(mt, {})
        behavior = self.pair.behavior_name(mt)
        if cls == 'surf':
            char = '~'
        elif cls == 'grass_enc':
            char = 'g'
        elif cls.startswith('ledge_'):
            if cls not in LEDGE_SYMBOL:
                raise PkError('PARSE', f'legend cannot represent physical class {cls} at metatile 0x{mt:03X}')
            char = LEDGE_SYMBOL[cls]
        elif cls == 'door':
            char = 'b'
        elif 'SAND' in behavior and not coll:
            char = 's'
        else:
            pixels = [rgb for row in draw(self.repo, self.pair, mt) for rgb in row if rgb]
            n = max(1, len(pixels))
            green = sum(g > r * 1.08 and g > b * 1.1 for r, g, b in pixels) / n
            beige = sum(r > b * 1.25 and g > b * 1.2 and r >= g * .88 for r, g, b in pixels) / n
            bright = sum(r > g * 1.3 or b > g * 1.3 for r, g, b in pixels) / n
            if cls == 'wall':
                char = 'T' if green > .25 else 'r'
                if mt >= self.pair.limits['metatiles'] and green < .25 and card.get('object'):
                    char = 'b'
            elif green > .35 and .02 < bright < .35:
                char = 'f'
            elif beige > .5 and green < .35:
                char = 'p'
            else:
                char = '.'
        self.memo[key] = char
        return char

    def export(self, grid):
        return [''.join(self.letter(grid.at(x, y)) for x in range(grid.w)) for y in range(grid.h)]


def reference(repo):
    """Immutable disk reference, never the current transaction's wipe/paint overlay."""
    ref = getattr(repo, '_blockout_reference', None)
    if ref is None:
        ref = Repo(repo.root)
        repo._blockout_reference = ref
    return ref


def classifier(repo, pair):
    ref = reference(repo)
    cache = ref.__dict__.setdefault('_blockout_classifiers', {})
    key = pair.primary['symbol'], pair.secondary['symbol']
    if key not in cache:
        cache[key] = Classifier(ref, pair)
    return cache[key]


def export(repo, grid, pair):
    return classifier(repo, pair).export(grid)


def pattern(rows, x, y):
    return ''.join(rows[ny][nx] for ny in range(y - 1, y + 2) for nx in range(x - 1, x + 2))


def vocabulary(repo):
    ref = reference(repo)
    if not hasattr(ref, '_blockout_vocabulary'):
        vocab = set()
        for name, _ in vanilla_outdoor(ref):
            grid, pair = load(ref, name)
            rows = export(repo, grid, pair)
            vocab.update(pattern(rows, x, y) for y in range(1, grid.h - 1) for x in range(1, grid.w - 1))
        ref._blockout_vocabulary = vocab
    return ref._blockout_vocabulary


def connectivity(grid, pair, rows):
    """Check all original exit/door coordinates; do not erase exits with the sketch."""
    world = World(grid, pair, grid.data)
    groups = world.entrances()
    doors = {(x, y) for y in range(grid.h) for x in range(grid.w)
             if physical_class(pair.behavior_name(grid.at(x, y)[0]), grid.at(x, y)[1]) == 'door'}
    targets = set().union(*groups.values(), doors)
    if not targets:
        return ['INFO BLOCKOUT reach skipped: no map exits or doors']
    walk = {(x, y) for y, row in enumerate(rows) for x, char in enumerate(row) if char in WALK}
    walk |= {p for p in doors if rows[p[1]][p[0]] == 'b'}
    # Ledges have directed entry/exit, rather than pretending they are lawn.
    ledges = {(x, y): LEDGE_STEP[char] for y, row in enumerate(rows)
              for x, char in enumerate(row) if char in LEDGE_STEP}
    start = min(targets, key=lambda p: (p[1], p[0]))
    seen, todo = ({start}, [start]) if start in walk else (set(), [])
    while todo:
        x, y = todo.pop()
        directions = (ledges[x, y],) if (x, y) in ledges else ((0, 1), (0, -1), (1, 0), (-1, 0))
        for dx, dy in directions:
            p = x + dx, y + dy
            if p in seen or not grid.inside(*p):
                continue
            if p in walk or (p in ledges and (dx, dy) == ledges[p]):
                seen.add(p)
                todo.append(p)
    missing = sorted(targets - seen, key=lambda p: (p[1], p[0]))
    if not missing:
        return [f'INFO BLOCKOUT reach ok exits/doors:{len(targets)}']
    return [f'WARN BLOCKOUT unreachable {x},{y}: connect to {start[0]},{start[1]}' for x, y in missing[:8]] + (
        [f'INFO BLOCKOUT unreachable +{len(missing) - 8} more'] if len(missing) > 8 else [])


class LetterPair:
    """Adapter for existing layout.measure, with no metatile names or symbols."""
    def info(self, mt):
        return {}

    def behavior_name(self, mt):
        return {'~': 'POND_WATER', 'g': 'TALL_GRASS', 's': 'SAND', 'v': 'JUMP_SOUTH',
                '^': 'JUMP_NORTH', '<': 'JUMP_WEST', '>': 'JUMP_EAST',
                'b': 'DOOR'}.get(chr(mt), 'NORMAL')


def letter_measure(grid, rows):
    sketch = copy(grid)
    sketch.cells = [(ord(char), int(char in 'Trb'), 0) for row in rows for char in row]
    shares, mirror = layout.measure(sketch, LetterPair())
    return max(v for _, v in shares), mirror


def check(repo, grid, pair, rows):
    old = export(repo, grid, pair)
    for y, row in enumerate(rows):
        for x, char in enumerate(row):
            if char == 'b' and old[y][x] != 'b':
                raise PkError('PARSE', f'b at {x},{y} is not an existing building')
    out = connectivity(grid, pair, rows)
    vocab = vocabulary(repo)
    absent = [(x, y) for y in range(1, grid.h - 1) for x in range(1, grid.w - 1)
              if pattern(rows, x, y) not in vocab]
    out.extend(f'INFO BLOCKOUT SHAPE {x},{y}: reshape this 3x3 neighbourhood' for x, y in absent[:8])
    if len(absent) > 8:
        out.append(f'INFO BLOCKOUT SHAPE +{len(absent) - 8} more')
    opened, mirror = letter_measure(grid, rows)
    out.append(f'INFO LAYOUT letters open:{opened:.1%} mirror:{mirror:.1%} (raw, not metatile-calibrated)')
    out.append('INFO LAYOUT blocky/path_straight/variety/focal skipped: Part 3 not implemented')
    return out


class LetterRules(terrain.Rules):
    """Only vanilla_outdoor references; never Rules' broader indoor-layout scan."""
    def __init__(self, repo, pair, classify):
        self.pair, self.classify = pair, classify
        self.h, self.v, self.ne, self.nw, self.se, self.sw = (Counter() for _ in range(6))
        self.freq, self.by_mat, self.bits = Counter(), {}, {}
        self.sources = []
        for name, _ in vanilla_outdoor(repo):
            grid, source_pair = load(repo, name)
            if (source_pair.primary['symbol'], source_pair.secondary['symbol']) != (
                    pair.primary['symbol'], pair.secondary['symbol']):
                continue
            self.sources.append(grid)
            for y in range(grid.h):
                for x in range(grid.w):
                    cell = grid.at(x, y)
                    mt = cell[0]
                    char = self.material(cell)
                    self.freq[mt] += 1
                    self.by_mat.setdefault(char, set()).add(mt)
                    self.bits.setdefault(mt, Counter())[cell[1:]] += 1
                    for dx, dy, table in ((1, 0, self.h), (0, 1, self.v), (1, -1, self.ne),
                                          (-1, -1, self.nw), (1, 1, self.se), (-1, 1, self.sw)):
                        if grid.inside(x + dx, y + dy):
                            table[mt, grid.at(x + dx, y + dy)[0]] += 1

    def material(self, cell):
        return self.classify.letter(cell)


class LearningRepo(Repo):
    """Outdoor-only, layout-level leave-one-out view, including WFC pair scoring.

    wfc.learn excludes windows, but wfc.fill also constructs terrain.Rules through
    its private _pair_rules. Filtering the Repo keeps both learners honest without
    modifying WFC or planting its private cache attributes.
    """
    def __init__(self, root, exclude):
        super().__init__(root)
        clean = Repo(root)
        maps, layouts = clean.maps(), clean.layouts()
        dropped = {maps[name]['layout'] for name in exclude if name in maps}
        self._maps = {name: maps[name] for name, _ in vanilla_outdoor(clean)
                      if maps[name]['layout'] not in dropped}
        kept = {data['layout'] for data in self._maps.values()}
        self._layouts = {lid: value for lid, value in layouts.items() if lid in kept}

    def maps(self):
        return self._maps

    def layouts(self):
        return self._layouts


def learning_reference(repo, exclude):
    ref = reference(repo)
    cache = ref.__dict__.setdefault('_blockout_learning', {})
    key = tuple(sorted(set(exclude)))
    if key not in cache:
        cache[key] = LearningRepo(ref.root, key)
    return cache[key]


def learned(repo, pair, exclude=()):
    ref = learning_reference(repo, exclude) if exclude else reference(repo)
    cache = ref.__dict__.setdefault('_blockout_rules', {})
    key = pair.primary['symbol'], pair.secondary['symbol']
    if key not in cache:
        cache[key] = LetterRules(ref, pair, classifier(repo, pair))
    return cache[key]


def forest(repo, grid, pair, rows, rules, fixed=frozenset(), exclude=()):
    """Solve the full T mask, never individual trees, and never touch non-T cells.

    wfc.py learn:113 / fill:544 accept an arbitrary coordinate set and forced
    domains. objects.py Glue:121 / broken_side:151 checks the real map border.
    terrain.py solve:83 is the deterministic seen-pair fallback. Every proposal
    must satisfy its T domain and pass the unchanged object guard before commit.
    """
    cells = {(x, y) for y, row in enumerate(rows) for x, char in enumerate(row)
             if char == 'T' and (x, y) not in fixed}
    if not cells:
        return ['INFO BLOCKOUT forest skipped: no editable T cells']
    original = list(grid.cells)
    before_objects = set(objects.check(repo, grid, pair))
    glue = objects.Glue(repo, grid, pair)
    candidates = sorted(mt for mt in rules.by_mat.get('T', ())
                        if rules.material((mt, *rules.bits[mt].most_common(1)[0][0])) == 'T')
    bits = {mt: (mt, *rules.bits[mt].most_common(1)[0][0]) for mt in candidates}
    choices = {}
    reverse = {'N': 'S', 'S': 'N', 'E': 'W', 'W': 'E'}
    # Enforce both directions of each object socket against frozen neighbours;
    # for map edges Glue reads border.bin or skips an actual connected side.
    for x, y in sorted(cells, key=lambda p: (p[1], p[0])):
        allowed = []
        saved = grid.at(x, y)
        for mt in candidates:
            grid.set(x, y, bits[mt])
            valid = True
            for side, (dx, dy) in objects.SIDES.items():
                q = x + dx, y + dy
                if q in cells:
                    continue
                if glue.broken_side(x, y, side, bits[mt]) is not None:
                    valid = False
                    break
                if grid.inside(*q) and glue.broken_side(*q, reverse[side]) is not None:
                    valid = False
                    break
            if valid:
                allowed.append(mt)
        grid.set(x, y, saved)
        choices[x, y] = allowed
    empty = [p for p, domain in choices.items() if not domain]
    if empty:
        x, y = empty[0]
        return [f'INFO BLOCKOUT forest refused: {len(empty)} T cells have no border/frozen-neighbour fit; first {x},{y}']

    def accept(assignment):
        if set(assignment) != cells or any(mt not in choices[p] for p, mt in assignment.items()):
            return False, 'incomplete or out-of-domain T assignment'
        for p, mt in assignment.items():
            grid.set(*p, bits[mt])
        if any(grid.cells[i] != cell for i, cell in enumerate(original)
               if (i % grid.w, i // grid.w) not in cells):
            grid.cells[:] = original
            return False, 'changed a frozen non-T cell'
        added = set(objects.check(repo, grid, pair)) - before_objects
        if added:
            grid.cells[:] = original
            return False, f'would add {len(added)} WARN OBJECT rows (including edges)'
        return True, ''

    notes = []
    try:
        from . import wfc
        ref = learning_reference(repo, exclude)
        vocab = wfc.learn(ref, pair, use_cache=False, exclude=tuple(exclude))
        proposal = wfc.fill(ref, grid, pair, sorted(cells, key=lambda p: (p[1], p[0])),
                            forced=choices, vocabulary=vocab)
        if proposal.complete:
            ok, reason = accept(proposal)
            if ok:
                return [f'INFO BLOCKOUT forest WFC cells:{len(cells)} patterns:{len(vocab)} '
                        f'exclude:{",".join(exclude) or "none"} non-T:frozen OBJECT:new:0']
        else:
            reason = proposal.reason or f'incomplete ({len(proposal.unsatisfied)} unresolved cells)'
        notes.append(f'INFO BLOCKOUT forest WFC fallback: {reason}')
    except (PkError, ImportError) as exc:
        notes.append(f'INFO BLOCKOUT forest WFC fallback: {exc}')
    # WFC may retain an invalid original in an incomplete fill: never use it as
    # a seed. Every fallback seed is itself an allowed T metatile.
    picked = terrain.solve(grid, rules, {p: 'T' for p in cells}, margin=0,
                           choices=choices, seed={p: choices[p][0] for p in cells},
                           passes=24, diag_weight=.5)
    ok, reason = accept(picked)
    if ok:
        notes.append(f'INFO BLOCKOUT forest seen-pair fallback cells:{len(cells)} '
                     f'exclude:{",".join(exclude) or "none"} non-T:frozen OBJECT:new:0')
    else:
        notes.append(f'INFO BLOCKOUT forest fallback refused: {reason}; original T-region cells retained')
    return notes


def reach_losses(before, after, pair):
    old, new = World(before, pair, before.data), World(after, pair, after.data)
    lost = {}
    for entrance, starts in old.entrances().items():
        for state, abilities in STATES.items():
            loss = old.reach(starts, abilities) - new.reach(starts, abilities)
            if loss:
                lost[f'{entrance}/{state}'] = len(loss)
    return lost


def object_pieces(repo, pair, rules):
    """Small whole vanilla kit windows only; no cut object perimeter is allowed."""
    raw = objects.Glue(repo, rules.sources[0], pair, edges=False).raw
    pieces, seen = [], set()
    for source in rules.sources:
        letters = export(repo, source, pair)
        glued = kit.glued_sides(repo, source, pair, raw)
        excluded = events(source.data)
        for w, h in ((2, 2), (2, 3), (3, 2)):
            for y in range(source.h - h + 1):
                for x in range(source.w - w + 1):
                    chars = ''.join(letters[y + dy][x:x + w] for dy in range(h))
                    if not (set(chars) & set('Tr')) or set(chars) - set('Tr.'):
                        continue
                    if any((x + dx, y + dy) in excluded for dy in range(h) for dx in range(w)):
                        continue
                    if kit.window_cuts(glued, x, y, w, h):
                        continue
                    cells = tuple(source.at(x + dx, y + dy) for dy in range(h) for dx in range(w))
                    key = w, h, cells
                    if key not in seen:
                        seen.add(key)
                        pieces.append((w, h, chars, cells))
    return pieces


def tile(repo, grid, pair, rows, exclude=()):
    """In-memory proposal + safety report. Caller stages only a safe proposal."""
    before = copy(grid)
    before.cells = list(grid.cells)
    if export(repo, grid, pair) == rows:
        return dict(safe=True, fidelity=1., added=0, losses={}, open_bank=0,
                    rows=['INFO BLOCKOUT unchanged: exact existing tiles retained',
                          'INFO OBJECT new:0', 'INFO reach unchanged',
                          f'INFO BLOCKOUT fidelity {grid.w * grid.h}/{grid.w * grid.h} = 100.00%',
                          'INFO BLOCKOUT tiling/joins skipped: no changed letters'])
    rules = learned(repo, pair, exclude)
    if not rules.sources:
        raise PkError('NOTFOUND', 'no vanilla outdoor reference for target pair')
    out = ['INFO BLOCKOUT shapes fallback: existing shape benchmark is not accepted; material solver used',
           'INFO BLOCKOUT paths fallback: existing path benchmark is not accepted; drawn-cell brush used']
    wanted = {(x, y): char for y, row in enumerate(rows) for x, char in enumerate(row)}
    fixed = {p for p, char in wanted.items() if char == 'b'} | set(events(grid.data))
    # Trees are solved jointly as a forest; rocks still use whole source pieces.
    lawn_cells = Counter(cell for source in rules.sources for cell in source.cells if rules.material(cell) == '.')
    if not lawn_cells:
        raise PkError('NOTFOUND', 'no observed target-pair lawn; no generic grass fallback')
    lawn = lawn_cells.most_common(1)[0][0]
    for p in wanted.keys() - fixed:
        grid.set(*p, lawn)
    # Bulk materials, then paths, then objects, then encounter grass/ledges/flowers.
    for chars in ('~s', 'p', 'gv^<>f'):
        targets = {p: char for p, char in wanted.items() if char in chars and p not in fixed}
        missing = sorted(set(targets.values()) - rules.by_mat.keys())
        for char in missing:
            out.append(f'INFO BLOCKOUT material {char} skipped: no observed target-pair tiles')
        targets = {p: char for p, char in targets.items() if char not in missing}
        if not targets:
            continue
        choices = {p: sorted(rules.by_mat[char]) for p, char in targets.items()}
        picked = terrain.solve(grid, rules, targets, margin=0, choices=choices,
                               seed={p: choices[p][0] for p in targets}, passes=12, diag_weight=.5)
        for p, mt in picked.items():
            coll, elev = rules.bits[mt].most_common(1)[0][0]
            grid.set(*p, (mt, coll, elev))
    out.extend(forest(repo, grid, pair, rows, rules, fixed, exclude))
    pending = {p for p, char in wanted.items() if char == 'r' and p not in fixed}
    initial_objects = set(objects.check(repo, grid, pair))
    pieces = object_pieces(reference(repo), pair, rules) if pending else []
    for w, h, chars, cells in pieces:
        for x, y in sorted(list(pending), key=lambda p: (p[1], p[0])):
            positions = [(x + dx, y + dy) for dy in range(h) for dx in range(w)]
            if any(not grid.inside(*p) or p in fixed or wanted[p] == 'T' for p in positions):
                continue
            if ''.join(wanted[p] for p in positions) != chars:
                continue
            saved = [grid.at(*p) for p in positions]
            for p, cell in zip(positions, cells):
                grid.set(*p, cell)
            if set(objects.check(repo, grid, pair)) - initial_objects:
                for p, cell in zip(positions, saved):
                    grid.set(*p, cell)
            else:
                pending.difference_update(positions)
    if pending:
        p = min(pending)
        out.append(f'INFO BLOCKOUT whole objects unfit:{len(pending)} cells; lawn retained (first {p[0]},{p[1]})')
    out.append('INFO BLOCKOUT terrain auto skipped: unrestricted joins could change letter classes or source objects')
    actual = export(repo, grid, pair)
    matched = sum(a == b for row, other in zip(rows, actual) for a, b in zip(row, other))
    fidelity = matched / (grid.w * grid.h)
    added = sorted(set(objects.check(repo, grid, pair)) - set(objects.check(repo, before, pair)))
    losses = reach_losses(before, grid, pair)
    # Every water/lawn cardinal contact is rejected, not disguised as a complete shore.
    open_bank = [(x, y) for y, row in enumerate(actual) for x, char in enumerate(row) if char == '~'
                 and any(grid.inside(nx, ny) and actual[ny][nx] == '.'
                         for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)))]
    out.extend(added[:8])
    out.extend(f'WARN reach {entrance} lost:{n}' for entrance, n in list(losses.items())[:8])
    if not added:
        out.append('INFO OBJECT new:0')
    if not losses:
        out.append('INFO reach unchanged (each entrance, all ability states)')
    if open_bank:
        out.append(f'WARN BLOCKOUT bank open-water/lawn:{len(open_bank)} first:{open_bank[0]}')
    out.append(f'INFO BLOCKOUT fidelity {matched}/{grid.w * grid.h} = {fidelity:.2%}')
    safe = not added and not losses and not open_bank and fidelity >= .95
    return dict(safe=safe, fidelity=fidelity, added=len(added), losses=losses,
                open_bank=len(open_bank), rows=out)


def command(repo, tx, args, opts):
    if len(args) not in (2, 3):
        raise PkError('PARSE', 'usage: pk map blockout MAP --export [--out FILE] | MAP FILE [--check]')
    grid, pair = load(repo, args[1])
    if opts.get('export'):
        if len(args) != 2 or opts.get('check'):
            raise PkError('PARSE', '--export cannot be combined with FILE or --check')
        rows = export(repo, grid, pair)
        if opts.get('out'):
            tx.stage(opts['out'], serialize(rows).encode('utf-8'))
            body = [f'out {opts["out"]}']
        else:
            body = serialize(rows).splitlines()
        return Result(f'blockout {grid.name} {grid.w}x{grid.h}',
                      ['INFO BLOCKOUT visual categories are heuristic; inspect before tiling', *body])
    if len(args) != 3 or opts.get('out'):
        raise PkError('PARSE', 'a blockout file is required; --out is export-only')
    rows = parse(repo.read(args[2]).decode('utf-8-sig'), grid.w, grid.h)
    notes = check(repo, grid, pair, rows)
    if opts.get('check'):
        return Result(f'blockout check {grid.name}', notes)
    if not grid.name.startswith('PkBlock') or not grid.name.endswith('_Frlg'):
        raise PkError('PARSE', 'experimental tiling writes only your PkBlock*_Frlg drafts; vanilla is read-only')
    report = tile(repo, grid, pair, rows)
    if not report['safe']:
        return Result(f'blockout {grid.name} refused; no map writes', notes + report['rows'], False, 'PARSE')
    save(tx, grid)
    from .lint import run
    lint = run(repo, grid.name, show_all=True)
    return Result(f'blockout {grid.name} tiled', notes + report['rows'] + [lint.summary] + lint.data)
