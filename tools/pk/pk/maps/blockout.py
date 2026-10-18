"""Experimental, name-independent blockouts. No vanilla-map writes are allowed.

API evidence: edit.load/save; layers.Grid.at/set/inside; lint.physical_class and
vanilla_outdoor; cards.aggregate/finalize; meta.draw/render_compare; terrain.solve
and Rules.pair_score; objects.Glue/check; kit.glued_sides/window_cuts; reach.World.
The colour classifier is deliberately documented as heuristic, not semantic truth.
"""
from collections import Counter, deque
from copy import copy

from ..core.errors import PkError
from ..core.out import Result
from ..core.repo import Repo
from . import cards, layout, objects, terrain
from .edit import load, save
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


def letter_context(rows, x, y, cardinal=False, connected=frozenset()):
    """3x3 or centre,N,E,S,W. Connected outside is '*', closed outside '_'."""
    offsets = ((0, 0), (0, -1), (1, 0), (0, 1), (-1, 0)) if cardinal else (
        (dx, dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1))
    out = []
    for dx, dy in offsets:
        nx, ny = x + dx, y + dy
        if 0 <= ny < len(rows) and 0 <= nx < len(rows[0]):
            out.append(rows[ny][nx])
        else:
            crossed = {side for side, yes in (('N', ny < 0), ('S', ny >= len(rows)),
                       ('W', nx < 0), ('E', nx >= len(rows[0]))) if yes}
            out.append('*' if crossed & connected else '_')
    return ''.join(out)


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
        self.context3, self.context4, self.letters = {}, {}, {}
        self.sources = []
        self.portable_cells = self.class_mismatches = 0
        for name, _ in vanilla_outdoor(repo):
            grid, source_pair = load(repo, name)
            if source_pair.primary['symbol'] != pair.primary['symbol'] or source_pair.version != pair.version:
                continue
            own = source_pair.secondary['symbol'] == pair.secondary['symbol']
            limit = pair.limits['metatiles']
            rows = export(repo, grid, source_pair)
            linked = objects.connected_sides(grid.data)
            self.sources.append(grid)
            for y in range(grid.h):
                for x in range(grid.w):
                    cell = grid.at(x, y)
                    mt = cell[0]
                    if not own and mt >= limit:
                        continue
                    char = self.material(cell)
                    if char != rows[y][x]:
                        self.class_mismatches += 1
                        continue
                    self.portable_cells += not own
                    self.freq[mt] += 1
                    self.by_mat.setdefault(char, set()).add(mt)
                    self.bits.setdefault(mt, Counter())[cell[1:]] += 1
                    self.letters.setdefault(char, Counter())[cell] += 1
                    self.context3.setdefault(letter_context(rows, x, y, connected=linked), Counter())[cell] += 1
                    self.context4.setdefault(letter_context(rows, x, y, True, linked), Counter())[cell] += 1
                    for dx, dy, table in ((1, 0, self.h), (0, 1, self.v), (1, -1, self.ne),
                                          (-1, -1, self.nw), (1, 1, self.se), (-1, 1, self.sw)):
                        if grid.inside(x + dx, y + dy):
                            other = grid.at(x + dx, y + dy)[0]
                            if own or other < limit:
                                table[mt, other] += 1

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
        cache[key]._blockout_reference = ref  # share immutable export/card vocabulary, not generation statistics
    return cache[key]


def learned(repo, pair, exclude=()):
    ref = learning_reference(repo, exclude) if exclude else reference(repo)
    cache = ref.__dict__.setdefault('_blockout_rules', {})
    key = pair.primary['symbol'], pair.secondary['symbol']
    if key not in cache:
        cache[key] = LetterRules(ref, pair, classifier(repo, pair))
    return cache[key]


CONTEXT_LEVELS = ('3x3', '4-neighbour', 'letter', 'any-non-door')


class ContextDomains(dict):
    """Domains plus the finite, coordinate-audited widening ladder."""
    def __init__(self, rows, rules, connected):
        super().__init__()
        self.rows, self.connected = rows, connected
        self.bands, self.levels, self.cell_bits, self.history = {}, {}, {}, []
        self.cache = {}
        self.tables = (rules.context3, rules.context4)
        self.index = []
        for i, table in enumerate(self.tables):
            groups = {}
            for key, values in table.items():
                groups.setdefault(key[4 if i == 0 else 0], []).append((key, values))
            self.index.append(groups)
        non_door = Counter()
        for values in rules.letters.values():
            for cell, n in values.items():
                if physical_class(rules.pair.behavior_name(cell[0]), cell[1]) != 'door':
                    non_door[cell] += n
        for y, row in enumerate(rows):
            for x, char in enumerate(row):
                if char == 'b':
                    continue
                p = x, y
                key3 = letter_context(rows, x, y, connected=connected)
                key4 = letter_context(rows, x, y, True, connected)
                self.bands[p] = (self.lookup(0, key3), self.lookup(1, key4),
                                 rules.letters.get(char, Counter()), non_door)
                self.activate(p, 0)
                while not self[p] and self.levels[p] < len(CONTEXT_LEVELS) - 1:
                    self.widen(p, 'no candidates at current context level')

    def lookup(self, level, key):
        cache_key = level, key
        if cache_key not in self.cache:
            found = Counter()
            center = key[4 if level == 0 else 0]
            if '*' not in key:
                found.update(self.tables[level].get(key, {}))
            for source, values in self.index[level].get(center, ()):
                if '*' not in key and '*' not in source:
                    continue
                if all(a == b or a == '*' or b == '*' for a, b in zip(key, source)):
                    found.update(values)
            self.cache[cache_key] = found
        return self.cache[cache_key]

    def activate(self, p, level):
        self.levels[p] = level
        by_mt = {}
        for cell, n in self.bands[p][level].items():
            by_mt.setdefault(cell[0], Counter())[cell] += n
        self[p] = sorted(by_mt, key=lambda mt: (-sum(by_mt[mt].values()), mt))
        self.cell_bits[p] = {mt: by_mt[mt].most_common(1)[0][0] for mt in self[p]}

    def widen(self, p, reason):
        level = self.levels[p]
        if level == len(CONTEXT_LEVELS) - 1:
            return None
        self.activate(p, level + 1)
        row = (f'INFO BLOCKOUT widen {p[0]},{p[1]} {CONTEXT_LEVELS[level]}->{CONTEXT_LEVELS[level + 1]} '
               f'candidates:{len(self[p])} reason:{reason}')
        self.history.append(row)
        return row

    def restore(self):
        # A widened neighbour may revive previously pruned options elsewhere.
        for p, level in self.levels.items():
            self.activate(p, level)

    def summary(self, label='context'):
        counts, per_letter, empty_patterns = Counter(), {}, Counter()
        for p, level in self.levels.items():
            name = CONTEXT_LEVELS[level] if self[p] else 'empty'
            counts[name] += 1
            per_letter.setdefault(self.rows[p[1]][p[0]], Counter())[name] += 1
            if not self[p]:
                empty_patterns[letter_context(self.rows, *p, connected=self.connected)] += 1
        names = (*CONTEXT_LEVELS, 'empty')
        notes = [f'INFO BLOCKOUT {label} ' + ' '.join(f'{name}:{counts[name]}' for name in names)]
        for char, counter in sorted(per_letter.items()):
            notes.append(f'INFO BLOCKOUT {label} letter:{char} ' +
                         ' '.join(f'{name}:{counter[name]}' for name in names))
        for key, n in sorted(empty_patterns.items()):
            notes.append(f'INFO BLOCKOUT context EMPTY pattern:{key[:3]}/{key[3:6]}/{key[6:]} cells:{n}')
        return notes


def context_candidates(rows, rules, connected=frozenset()):
    choices = ContextDomains(rows, rules, connected)
    return choices, choices.cell_bits, choices.summary() + choices.history


def resolve_contexts(repo, grid, pair, rows, choices, cell_bits):
    """Widen only exhausted cells, one level per retry; always attempt the rest."""
    notes, exhausted = [], set()
    while True:
        choices.restore()
        notes.extend(supported_contexts(repo, grid, pair, rows, choices, cell_bits, exhausted))
        empty = sorted((p for p in choices if not choices[p]), key=lambda p: (p[1], p[0]))
        if not empty:
            break
        progress = False
        for p in empty:
            row = choices.widen(p, 'object/border support emptied domain')
            if row:
                notes.append(row)
                progress = True
            elif p not in exhausted:
                exhausted.add(p)
                progress = True
                notes.append(f'INFO BLOCKOUT widen-exhausted {p[0]},{p[1]} any-non-door: '
                             'attempt broad domain and solve rest; final OBJECT guard unchanged')
        if not progress:
            break
    for p in exhausted:
        if not choices[p]:
            choices.activate(p, len(CONTEXT_LEVELS) - 1)
        if not choices[p]:
            notes.append(f'INFO BLOCKOUT unresolved {p[0]},{p[1]} no learned non-door tiles; solve rest')
    widened = {p for p in choices if choices.levels[p] > 0}
    notes.append(f'INFO BLOCKOUT widen totals cells:{len(widened)} steps:{len(choices.history)}')
    notes.extend(choices.summary('context-final'))
    return notes


def supported_contexts(repo, grid, pair, rows, choices, cell_bits, exhausted=frozenset()):
    """Prune one current level; the caller widens exhausted cells, never the guard.

    Glue checks literal map edges. Its already-defined constrained-side criterion
    supplies arc support between variable neighbours, so lawn can change instead
    of being tested against a frozen, modal lawn tile. Final OBJECT guard remains.
    """
    fixed = {(x, y) for y, row in enumerate(rows) for x, c in enumerate(row) if c == 'b'}
    domains = {p: set(values) for p, values in choices.items() if values or p not in exhausted}
    bits = dict(cell_bits)
    for p in fixed:
        cell = grid.at(*p)
        domains[p], bits[p] = {cell[0]}, {cell[0]: cell}
    glue = objects.Glue(repo, grid, pair)
    reverse = {'N': 'S', 'S': 'N', 'E': 'W', 'W': 'E'}
    sockets = {}

    def allowed(p, mt, side):
        cell = bits[p][mt]
        key = mt, cell[1], side
        if key not in sockets:
            rec = glue.raw.get(mt)
            if rec is None or not glue.objectish(cell):
                sockets[key] = None
            else:
                counts = Counter(rec['nmt'][side])
                sockets[key] = set(counts) if objects.constrained(counts) is not None else None
        return sockets[key]

    def fits(p, mt, q, other, side):
        forward, backward = allowed(p, mt, side), allowed(q, other, reverse[side])
        return (forward is None or other in forward) and (backward is None or mt in backward)

    initial = sum(len(d) for d in domains.values())
    for p, values in domains.items():
        if p in fixed or p in exhausted:
            continue
        x, y = p
        for side, (dx, dy) in objects.SIDES.items():
            if not grid.inside(x + dx, y + dy):
                if glue.neighbour(x, y, side) is None:
                    continue  # same connected-side/no-border rule as lint
                domains[p] = {mt for mt in domains[p]
                              if glue.broken_side(x, y, side, bits[p][mt]) is None}
    queue = deque((p, side) for p in choices if p not in exhausted
                  for side, (dx, dy) in objects.SIDES.items()
                  if (p[0] + dx, p[1] + dy) in domains) if all(domains[p] for p in domains) else deque()
    while queue:
        p, side = queue.popleft()
        dx, dy = objects.SIDES[side]
        q = p[0] + dx, p[1] + dy
        revised = {mt for mt in domains[p] if any(fits(p, mt, q, other, side) for other in domains[q])}
        if revised != domains[p]:
            domains[p] = revised
            if not revised:
                break  # report the contradiction's origin, not a cascade of empty domains
            for incoming, (ox, oy) in objects.SIDES.items():
                neighbour = p[0] + ox, p[1] + oy
                if neighbour in choices and neighbour not in exhausted and neighbour != q:
                    queue.append((neighbour, reverse[incoming]))
    linked = objects.connected_sides(getattr(grid, 'data', None))
    empty = Counter(letter_context(rows, *p, connected=linked) for p in choices if not domains.get(p))
    notes = [f'INFO BLOCKOUT context object-support removed:{initial - sum(len(d) for d in domains.values())} '
             f'empty-cells:{sum(empty.values())} (current level only)']
    for key, n in sorted(empty.items()):
        notes.append(f'INFO BLOCKOUT context UNSUPPORTED pattern:{key[:3]}/{key[3:6]}/{key[6:]} cells:{n}')
    for p in choices:
        choices[p] = [mt for mt in choices[p] if mt in domains.get(p, ())]
    return notes


def apply_candidates(grid, assignment, choices, cell_bits):
    if any(p not in choices or mt not in choices[p] for p, mt in assignment.items()):
        raise PkError('INTERNAL', 'context solver returned an out-of-domain cell')
    for p, mt in assignment.items():
        grid.set(*p, cell_bits[p][mt])


def forest(repo, grid, pair, rows, rules, choices, cell_bits, exclude=()):
    """Forest WFC first, with its mutable non-building edge ring; no modal lawn."""
    trees = {p for p in choices if rows[p[1]][p[0]] == 'T'}
    if not trees:
        return ['INFO BLOCKOUT forest skipped: no T cells']
    region = trees | {q for x, y in trees for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                      if (q := (x + dx, y + dy)) in choices}
    forced = {p: choices[p] for p in region}
    notes = [f'INFO BLOCKOUT forest region T:{len(trees)} mutable-ring:{len(region - trees)} b:frozen']
    try:
        from . import wfc
        ref = learning_reference(repo, exclude)
        vocab = wfc.learn(ref, pair, use_cache=False, exclude=tuple(exclude))
        proposal = wfc.fill(ref, grid, pair, sorted(region, key=lambda p: (p[1], p[0])),
                            forced=forced, vocabulary=vocab)
        if proposal.complete and set(proposal) == region and all(mt in forced[p] for p, mt in proposal.items()):
            apply_candidates(grid, proposal, choices, cell_bits)
            return notes + [f'INFO BLOCKOUT forest WFC solved:{len(region)} patterns:{len(vocab)} '
                            f'exclude:{",".join(exclude) or "none"} (final OBJECT guard pending)']
        reason = proposal.reason or f'incomplete/invalid ({len(proposal.unsatisfied)} unresolved cells)'
        notes.append(f'INFO BLOCKOUT forest WFC fallback: {reason}')
    except (PkError, ImportError) as exc:
        notes.append(f'INFO BLOCKOUT forest WFC fallback: {exc}')
    targets = {p: rows[p[1]][p[0]] for p in region}
    proposal = terrain.solve(grid, rules, targets, margin=0, choices=forced,
                             seed={p: grid.at(*p)[0] for p in region}, passes=24, diag_weight=.5)
    apply_candidates(grid, proposal, choices, cell_bits)
    return notes + [f'INFO BLOCKOUT forest seen-pair fallback solved:{len(region)} (final OBJECT guard pending)']


def reach_losses(before, after, pair):
    old, new = World(before, pair, before.data), World(after, pair, after.data)
    lost = {}
    for entrance, starts in old.entrances().items():
        for state, abilities in STATES.items():
            loss = old.reach(starts, abilities) - new.reach(starts, abilities)
            if loss:
                lost[f'{entrance}/{state}'] = len(loss)
    return lost




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
    out = [f'INFO BLOCKOUT context references maps:{len(rules.sources)} portable-cells:{rules.portable_cells} '
           f'incompatible-class-cells:{rules.class_mismatches} exclude:{",".join(exclude) or "none"}',
           'INFO BLOCKOUT shape/path material brushes replaced by context-restricted full-map solve']
    linked = objects.connected_sides(grid.data)
    out.append(f'INFO BLOCKOUT context connected-sides:{",".join(sorted(linked)) or "none"} outside:wildcard')
    choices, cell_bits, notes = context_candidates(rows, rules, linked)
    out.extend(notes)
    out.extend(resolve_contexts(repo, grid, pair, rows, choices, cell_bits))
    valid_context = all(choices.values())
    active = {p: values for p, values in choices.items() if values}
    if active:
        # Only b cells are initially fixed. Every other cell, including lawn and
        # cells under events, receives a legal context seed and participates.
        seed = {p: grid.at(*p)[0] if grid.at(*p)[0] in values else values[0]
                for p, values in active.items()}
        apply_candidates(grid, seed, active, cell_bits)
        out.append(f'INFO BLOCKOUT solve editable:{len(active)} '
                   f'frozen-b:{grid.w * grid.h - len(choices)} (no modal-lawn or event freeze)')
        out.extend(forest(repo, grid, pair, rows, rules, active, cell_bits, exclude))
        rest = {p: rows[p[1]][p[0]] for p in active if rows[p[1]][p[0]] != 'T'}
        if rest:
            picked = terrain.solve(grid, rules, rest, margin=0,
                                   choices={p: active[p] for p in rest},
                                   seed={p: grid.at(*p)[0] for p in rest}, passes=24, diag_weight=.5)
            apply_candidates(grid, picked, active, cell_bits)
            out.append(f'INFO BLOCKOUT rest solved:{len(rest)} (including lawn/forest joins)')
        else:
            out.append('INFO BLOCKOUT rest skipped: no non-tree editable cells')
    else:
        out.append('INFO BLOCKOUT forest/rest skipped: no non-door candidates for any editable cell')
    out.append('INFO BLOCKOUT unrestricted terrain auto skipped: joins already solved in context domains')
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
    safe = valid_context and not added and not losses and not open_bank and fidelity >= .95
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
