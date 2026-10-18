"""Experimental, name-independent blockouts. No vanilla-map writes are allowed.

API evidence: edit.load/save; layers.Grid.at/set/inside; lint.physical_class and
vanilla_outdoor; cards.aggregate/finalize; meta.draw/render_compare; terrain.solve
and Rules.pair_score; objects.Glue/check; kit.glued_sides/window_cuts; reach.World.
The colour classifier is deliberately documented as heuristic, not semantic truth.
"""
from collections import Counter, deque
from copy import copy
import time

from ..core.errors import PkError
from ..core.out import Result
from ..core.repo import Repo
from . import cards, layout, objects, terrain
from .edit import load, save
from .lint import physical_class, vanilla_outdoor, stratum_of
from .meta import draw
from .grid import decode
from .reach import World, STATES

HEADER = 'blockout 1'
LEGEND = 'legend T=trees n=fence .=lawn p=path g=tallgrass ~=water s=sand r=rock v=ledge_s ^=ledge_n <=ledge_w >=ledge_e f=flowers b=building'
LETTERS = frozenset('Tn.pg~srv^<>fb')
OBJECT_LETTERS = frozenset('Trn')
WALK = frozenset('.pgsf')
LEDGE_SYMBOL = {'ledge_s': 'v', 'ledge_n': '^', 'ledge_w': '<', 'ledge_e': '>'}
LEDGE_STEP = {'v': (0, 1), '^': (0, -1), '<': (-1, 0), '>': (1, 0)}


class Rows(list):
    """Letter rows, plus an optional `elevation` layer (one hex digit per cell, or None). Letters can't say
    how high a path is: on One Island Kindle Road a plateau path at elevation 4 rebuilt at 3 and was cut off
    from the rest (306 cells unreachable with every letter right)."""
    elevation = None


ELEVATION_HEADER = 'elevation'


def parse(text, w, h):
    lines = text.splitlines()
    if lines[:2] != [HEADER, LEGEND]:
        raise PkError('PARSE', 'expected blockout 1 and the fixed legend (pk help blockout)')
    lines = lines[2:]
    elevation = None
    if ELEVATION_HEADER in lines:
        k = lines.index(ELEVATION_HEADER)
        lines, elevation = lines[:k], lines[k + 1:]
        if len(elevation) != h or any(len(row) != w for row in elevation):
            raise PkError('BOUNDS', f'elevation layer must have exactly {h} rows of {w} hex digits')
        for y, row in enumerate(elevation):
            for x, char in enumerate(row):
                if char not in '0123456789abcdefABCDEF':
                    raise PkError('PARSE', f'elevation must be one hex digit per cell, got {char!r} at {x},{y}')
    if len(lines) != h or any(len(row) != w for row in lines):
        raise PkError('BOUNDS', f'blockout must have exactly {h} rows of {w} cells')
    for y, row in enumerate(lines):
        for x, char in enumerate(row):
            if char not in LETTERS:
                raise PkError('PARSE', f'unknown blockout letter {char!r} at {x},{y}')
    rows = Rows(lines)
    rows.elevation = [row.upper() for row in elevation] if elevation else None
    return rows


def serialize(rows):
    body = [HEADER, LEGEND, *rows]
    if getattr(rows, 'elevation', None):
        body += [ELEVATION_HEADER, *rows.elevation]
    return '\n'.join(body) + '\n'


def fence_card(card):
    """Wall-card evidence for a one-cell line, not a two-by-two canopy.

    Opposite transverse sides mostly see non-wall; at least one longitudinal
    side mostly sees wall. End pieces may have only one continuation. Require
    card-level observations, never a name or a target-map coordinate.
    """
    if card.get('cls') != 'wall' or card.get('uses', 0) < cards.ROLE_MIN:
        return False
    nbr = card.get('nbr', {})
    def wall(side):
        counts = nbr.get(side, {})
        return counts.get('wall', 0) / max(1, sum(counts.values()))
    return any(max(wall(a), wall(b)) < .4 and max(wall(c), wall(d)) > .6
               for a, b, c, d in (('N', 'S', 'E', 'W'), ('E', 'W', 'N', 'S')))


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
                char = 'n' if fence_card(card) else ('T' if green > .25 else 'r')
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

    def layout_uses(self):
        """{metatile: {layout ids}} over the vanilla layouts of this pair (never Pk*)."""
        if getattr(self, '_uses', None) is None:
            key = (self.pair.primary['symbol'], self.pair.secondary['symbol'])
            uses = {}
            for lid, layout in self.repo.layouts().items():
                if str(lid).startswith('LAYOUT_PK_') or                         (layout.get('primary_tileset'), layout.get('secondary_tileset')) != key:
                    continue
                try:
                    cells = decode(self.repo.read(layout['blockdata_filepath']))
                except PkError:
                    continue
                for mt in {c[0] for c in cells}:
                    uses.setdefault(mt, set()).add(lid)
            self._uses = uses
        return self._uses

    def export(self, grid):
        rows = [[self.letter(grid.at(x, y)) for x in range(grid.w)] for y in range(grid.h)]
        elevation = [''.join(f'{grid.at(x, y)[2]:X}' for x in range(grid.w)) for y in range(grid.h)]
        # A building is the whole structure, not just the cells next to its door: wall-class cells
        # (r/T/n) connected to a 'b' cell whose metatile is secondary-specific and used by no other
        # vanilla layout of the pair can't be rebuilt from other maps, so they count as 'b' (frozen).
        # Route 22's gate roof ends exported as 'r' before and could not be re-solved leave-one-out.
        own = (getattr(grid, 'data', None) or {}).get('layout')
        uses, limit = self.layout_uses(), self.pair.limits['metatiles']
        unique = lambda mt: mt >= limit and not (uses.get(mt, set()) - {own})
        queue = [(x, y) for y in range(grid.h) for x in range(grid.w) if rows[y][x] == 'b']
        while queue:
            x, y = queue.pop()
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < grid.w and 0 <= ny < grid.h and rows[ny][nx] in OBJECT_LETTERS                         and unique(grid.at(nx, ny)[0]):
                    rows[ny][nx] = 'b'
                    queue.append((nx, ny))
        out = Rows(''.join(row) for row in rows)
        out.elevation = elevation
        return out


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
    sketch.cells = [(ord(char), int(char in 'Tnrb'), 0) for row in rows for char in row]
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
        self.pair = rules.pair
        self.bands, self.levels, self.cell_bits, self.history = {}, {}, {}, []
        self.conflicts = {}
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
        walk = self.rows[p[1]][p[0]] in WALK
        for cell, n in self.bands[p][level].items():
            # A walkable letter never takes a blocking tile, at any widening level: one lawn cell
            # turned to rock in Route 22's fence gap cut the whole gate area off (reach lost 654).
            if walk and (cell[1] or self.blocking(cell)):
                continue
            by_mt.setdefault(cell[0], Counter())[cell] += n
        self[p] = sorted(by_mt, key=lambda mt: (-sum(by_mt[mt].values()), mt))
        self.cell_bits[p] = {mt: by_mt[mt].most_common(1)[0][0] for mt in self[p]}

    def blocking(self, cell):
        cls = physical_class(self.pair.behavior_name(cell[0]), cell[1])
        return cls in ('wall', 'door') or cls.startswith('ledge')

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
    """Context may widen; an object rule may NEVER be dropped."""
    notes = []
    while True:
        choices.restore()
        notes.extend(supported_contexts(repo, grid, pair, rows, choices, cell_bits, frozenset()))
        empty = sorted((p for p in choices if not choices[p]), key=lambda p: (p[1], p[0]))
        if not empty:
            break
        progress = False
        for p in empty:
            row = choices.widen(p, 'object/border support emptied domain')
            if row:
                notes.append(row)
                progress = True
            else:
                # A broad cell cannot compensate for a neighbour whose narrow
                # domain demands an incompatible socket. Widen that neighbour,
                # not the object rule. Prefer the actual failed arc's endpoint.
                q = choices.conflicts.get(p)
                adjacent = [(p[0] + dx, p[1] + dy) for dx, dy in objects.SIDES.values()]
                neighbours = [n for n in adjacent if n in choices and choices.levels[n] < 3]
                if q in neighbours:
                    neighbours.remove(q)
                    neighbours.insert(0, q)
                if neighbours:
                    n = neighbours[0]
                    notes.append(choices.widen(n, f'hard socket blocks exhausted neighbour {p[0]},{p[1]}'))
                    progress = True
                else:
                    notes.append(f'INFO BLOCKOUT unresolved-hard {p[0]},{p[1]} '
                                 'no compatible object assignment; rules remain hard')
        if not progress:
            break
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
    domains = {p: set(values) for p, values in choices.items()}
    conflicts = getattr(choices, 'conflicts', {})
    conflicts.clear()
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
        if p in fixed:
            continue
        x, y = p
        for side, (dx, dy) in objects.SIDES.items():
            if not grid.inside(x + dx, y + dy):
                if glue.neighbour(x, y, side) is None:
                    continue  # same connected-side/no-border rule as lint
                domains[p] = {mt for mt in domains[p]
                              if glue.broken_side(x, y, side, bits[p][mt]) is None}
    queue = deque((p, side) for p in choices
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
                conflicts[p] = q
                break  # report the contradiction's origin, not a cascade of empty domains
            for incoming, (ox, oy) in objects.SIDES.items():
                neighbour = p[0] + ox, p[1] + oy
                if neighbour in choices and neighbour != q:
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


def connected_regions(cells, directions=((1, 0), (-1, 0), (0, 1), (0, -1))):
    unseen, out = set(cells), []
    while unseen:
        start = min(unseen, key=lambda p: (p[1], p[0]))
        unseen.remove(start)
        region, todo = {start}, [start]
        while todo:
            x, y = todo.pop()
            for dx, dy in directions:
                q = x + dx, y + dy
                if q in unseen:
                    unseen.remove(q)
                    region.add(q)
                    todo.append(q)
        out.append(region)
    return out


class HardObjects:
    """Exact binary/border constraints through Glue.broken_side, never penalties."""
    reverse = {'N': 'S', 'S': 'N', 'E': 'W', 'W': 'E'}

    def __init__(self, repo, grid, pair, cell_bits, free=frozenset()):
        self.grid, self.bits = grid, cell_bits
        self.glue = objects.Glue(repo, grid, pair)
        self.cache = {}
        self.free = free          # not-yet-solved cells outside the region: no constraint from them

    def pair_ok(self, p, cell, q, other, side):
        if not self.glue.objectish(cell) and not self.glue.objectish(other):
            return True
        key = cell, other, side
        if key not in self.cache:
            before, next_before = self.grid.at(*p), self.grid.at(*q)
            try:
                self.grid.set(*p, cell)
                self.grid.set(*q, other)
                self.cache[key] = (self.glue.broken_side(*p, side, cell) is None and
                                   self.glue.broken_side(*q, self.reverse[side], other) is None)
            finally:
                self.grid.set(*p, before)
                self.grid.set(*q, next_before)
        return self.cache[key]

    def boundary_ok(self, p, mt, variables):
        cell = self.bits[p][mt]
        for side, (dx, dy) in objects.SIDES.items():
            q = p[0] + dx, p[1] + dy
            if not self.grid.inside(*q):
                if self.glue.broken_side(*p, side, cell) is not None:
                    return False
            elif q not in variables and q not in self.free and not self.pair_ok(p, cell, q, self.grid.at(*q), side):
                return False
        return True

    def validates(self, assignment):
        saved = {p: self.grid.at(*p) for p in assignment}
        try:
            for p, mt in assignment.items():
                self.grid.set(*p, self.bits[p][mt])
            for p in assignment:
                for side, (dx, dy) in objects.SIDES.items():
                    q = p[0] + dx, p[1] + dy
                    if q in self.free:
                        continue
                    if self.glue.broken_side(*p, side) is not None:
                        return False
                    if (self.grid.inside(*q) and q not in assignment and
                            self.glue.broken_side(*q, self.reverse[side]) is not None):
                        return False
            return True
        finally:
            for p, cell in saved.items():
                self.grid.set(*p, cell)


def hard_solve(repo, grid, pair, rows, rules, choices, cell_bits, region=None,
               preferred=None, letters=True, step_limit=30000, time_limit=8., free=frozenset()):
    """Arc consistency + bounded iterative backtracking with exact hard sockets.

    Outside region is frozen. Both sides of every incident edge, including a
    frozen neighbour's side, must satisfy Glue. No soft pair score can override it.
    """
    region = set(choices) if region is None else set(region)
    model = HardObjects(repo, grid, pair, cell_bits, free)
    preferred = preferred or {p: grid.at(*p)[0] for p in region}
    deadline, steps = time.monotonic() + time_limit, 0
    domains = {p: {mt for mt in choices[p]
                  if (not letters or rules.material(cell_bits[p][mt]) == rows[p[1]][p[0]])
                  and model.boundary_ok(p, mt, region)} for p in region}
    neighbours = {p: [(q, side) for side, (dx, dy) in objects.SIDES.items()
                      if (q := (p[0] + dx, p[1] + dy)) in region] for p in region}
    conflict = set()

    def tick():
        nonlocal steps
        steps += 1
        return steps <= step_limit and time.monotonic() < deadline

    bank_h, bank_v = getattr(rules, 'h', {}), getattr(rules, 'v', {})

    def bank_ok(p, a, q, b, side):
        """Water next to a walkable letter must be a contact vanilla uses (a pond rim, a shore); plain
        open water against lawn is never a bank. Hard, like the object rules."""
        lp, lq = rows[p[1]][p[0]], rows[q[1]][q[0]]
        if not ((lp == '~' and lq in WALK) or (lq == '~' and lp in WALK)):
            return True
        if side == 'E':
            return (a, b) in bank_h
        if side == 'W':
            return (b, a) in bank_h
        if side == 'S':
            return (a, b) in bank_v
        return (b, a) in bank_v

    def compatible(p, a, q, b, side):
        return model.pair_ok(p, cell_bits[p][a], q, cell_bits[q][b], side) and bank_ok(p, a, q, b, side)

    def propagate(current, queue):
        queue = deque(queue)
        while queue:
            if not tick():
                return False
            p, q, side = queue.popleft()
            revised = {a for a in current[p] if any(compatible(p, a, q, b, side) for b in current[q])}
            if not revised:
                conflict.update((p, q))
                return False
            if revised != current[p]:
                current[p] = revised
                for n, outgoing in neighbours[p]:
                    if n != q:
                        queue.append((n, p, model.reverse[outgoing]))
        return True

    def order(p, values):
        freq = getattr(rules, 'freq', {})
        return sorted(values, key=lambda mt: (
            rules.material(cell_bits[p][mt]) != rows[p[1]][p[0]],
            mt != preferred.get(p), -freq.get(mt, 0), mt))

    initial = [(p, q, side) for p in region for q, side in neighbours[p]]
    empty = [p for p, values in domains.items() if not values]
    if empty or not propagate(domains, initial):
        why = f'empty hard domain {empty[0]}' if empty else ('budget' if not tick() else 'hard contradiction')
        return dict(ok=False, assignment={}, steps=steps, reason=why, conflict=sorted(set(empty) | conflict))

    # Variables whose every option is compatible with every neighbour option are
    # independent for hard rules; collapse them without spending a branch each.
    for p in sorted(region, key=lambda p: (p[1], p[0])):
        if len(domains[p]) > 1 and all(all(compatible(p, a, q, b, side)
            for a in domains[p] for b in domains[q]) for q, side in neighbours[p]):
            domains[p] = {order(p, domains[p])[0]}

    stack = [[domains, None, None, 0]]
    while stack:
        current, selected, values, index = stack[-1]
        if selected is None:
            unresolved = [p for p in region if len(current[p]) > 1]
            if not unresolved:
                assignment = {p: next(iter(current[p])) for p in region}
                if model.validates(assignment):
                    return dict(ok=True, assignment=assignment, steps=steps, reason='', conflict=[])
                return dict(ok=False, assignment={}, steps=steps, reason='Glue validation failed', conflict=[])
            selected = min(unresolved, key=lambda p: (len(current[p]),
                -sum(len(current[q]) > 1 for q, _ in neighbours[p]), p[1], p[0]))
            values = order(selected, current[selected])
            stack[-1][1:3] = [selected, values]
        if index >= len(values):
            stack.pop()
            continue
        stack[-1][3] += 1
        if not tick():
            break
        child = {p: set(v) for p, v in current.items()}
        child[selected] = {values[index]}
        queue = [(q, selected, model.reverse[side]) for q, side in neighbours[selected]]
        if propagate(child, queue):
            stack.append([child, None, None, 0])
    return dict(ok=False, assignment={}, steps=steps, reason='budget or no hard solution',
                conflict=sorted(conflict))


def restricted_vocabulary(grid, region, forced, vocab, wfc):
    """Keep only learned windows that could fit somewhere, not invented patterns.

    This exact conditioning avoids WFC's large irrelevant pattern-beam index.
    Vocabulary.counts/key/pos_index are defined in wfc.py:87.
    """
    centers = {q for x, y in region for dx in (-1, 0, 1) for dy in (-1, 0, 1)
               if 1 <= (q := (x + dx, y + dy))[0] < grid.w - 1 and 1 <= q[1] < grid.h - 1}
    kept = {}
    for x, y in centers:
        cells = [(x + dx, y + dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
        domains = [set(forced[p]) if p in region else {grid.at(*p)[0]} for p in cells]
        pivot = min(range(9), key=lambda k: len(domains[k]))
        for value in domains[pivot]:
            for pattern in vocab.pos_index[pivot].get(value, ()):
                if all(pattern[k] in domains[k] for k in range(9)):
                    kept[pattern] = vocab.counts[pattern]
    return wfc.Vocabulary(kept, vocab.key)


def broken_sides(repo, grid, pair):
    glue = objects.Glue(repo, grid, pair)
    return [(x, y, side) for y in range(grid.h) for x in range(grid.w)
            for side in objects.SIDES if glue.broken_side(x, y, side) is not None]


def local_repair(repo, grid, pair, rows, rules, choices, cell_bits):
    """Letters fixed, hard objects; try 3x3 then 5x5, never alter a b cell."""
    before = set(objects.check(repo, grid, pair))
    if not before:
        return ['INFO BLOCKOUT repair skipped: no remaining broken sides']
    notes, fixed, attempted = [], 0, set()
    for x, y, side in broken_sides(repo, grid, pair):
        if (x, y, side) in attempted:
            continue
        attempted.add((x, y, side))
        if (x, y, side) not in broken_sides(repo, grid, pair):
            continue
        success = False
        for radius in (1, 2):
            region = {p for dx in range(-radius, radius + 1) for dy in range(-radius, radius + 1)
                      if (p := (x + dx, y + dy)) in choices}
            trial_choices, trial_bits = dict(choices), dict(cell_bits)
            widening = []
            if radius == 2:
                for p in region:
                    char = rows[p[1]][p[0]]
                    expanded = dict(cell_bits[p])
                    for cell, _ in rules.letters.get(char, {}).items():
                        expanded.setdefault(cell[0], cell)
                    options = [mt for mt in expanded if rules.material(expanded[mt]) == char]
                    if set(options) - set(choices[p]):
                        widening.append(f'INFO BLOCKOUT repair-widen {p[0]},{p[1]} context->letter '
                                        f'candidates:{len(options)} letters:fixed')
                    trial_choices[p], trial_bits[p] = options, expanded
            result = hard_solve(repo, grid, pair, rows, rules, trial_choices, trial_bits,
                                region, letters=True, step_limit=5000, time_limit=2.)
            if not result['ok']:
                notes.append(f'INFO BLOCKOUT repair {x},{y} {2 * radius + 1}x{2 * radius + 1} '
                             f'no-fit steps:{result["steps"]} reason:{result["reason"]}')
                continue
            old = list(grid.cells)
            previous = set(objects.check(repo, grid, pair))
            apply_candidates(grid, result['assignment'], trial_choices, trial_bits)
            after = set(objects.check(repo, grid, pair))
            if after - previous or len(after) >= len(previous):
                grid.cells[:] = old
                notes.append(f'INFO BLOCKOUT repair {x},{y} {2 * radius + 1}x{2 * radius + 1} '
                             'refused: no strict warning reduction or new broken side')
                continue
            for p in region:
                choices[p], cell_bits[p] = trial_choices[p], trial_bits[p]
            n = len(previous - after)
            fixed += n
            notes.extend(widening)
            notes.append(f'INFO BLOCKOUT repair {x},{y} {2 * radius + 1}x{2 * radius + 1} '
                         f'fixed:{n} steps:{result["steps"]} letters:fixed OBJECT:hard')
            success = True
            break
        if not success:
            notes.append(f'INFO BLOCKOUT repair unresolved {x},{y} side:{side} after 5x5')
    notes.append(f'INFO BLOCKOUT repair total fixed:{fixed} remaining:{len(objects.check(repo, grid, pair))}')
    return notes


def region_interior(component, depth=2):
    """Chebyshev erosion: every interior cell is >=depth from the outline."""
    interior = set(component)
    for _ in range(depth):
        interior = {p for p in interior if all((p[0] + dx, p[1] + dy) in interior
                    for dx in (-1, 0, 1) for dy in (-1, 0, 1))}
    return interior


def interior_plan(repo, grid, pair, rows, rules, forced, cell_bits, component, symbol):
    """LOO homogeneous-context dominant tile; only pin a self-compatible tuple."""
    if len(component) < 128:
        return {}, 'small region'
    interior = region_interior(component)
    if not interior:
        return {}, 'no depth-2 interior'
    counts = getattr(rules, 'context3', {}).get(symbol * 9, Counter())
    model = HardObjects(repo, grid, pair, cell_bits)
    ranked = counts.most_common()
    for cell, _ in ranked:
        mt = cell[0]
        if rules.material(cell) != symbol:
            continue
        # A dominant tile must exist in every pinned cell's current domain and
        # keep its observed tuple. No per-cell context is silently discarded.
        if not all(mt in forced[p] and cell_bits[p][mt] == cell for p in interior):
            continue
        p = min(interior)
        if not all(model.pair_ok(p, cell, q, cell, side)
                   for side, (dx, dy) in objects.SIDES.items()
                   if grid.inside(*(q := (p[0] + dx, p[1] + dy)))):
            continue
        return {p: mt for p in interior}, f'dominant:0x{mt:03X} depth:2'
    return {}, f'no common self-compatible context interior tile candidates:{len(ranked)}'


def object_regions(repo, grid, pair, rows, rules, choices, cell_bits, exclude=(), symbols=OBJECT_LETTERS):
    """Atomic connected objects, with pinned interiors and a joint boundary band."""
    all_objects = {p for p in choices if rows[p[1]][p[0]] in OBJECT_LETTERS}
    regions = [(symbol, component) for symbol in sorted(symbols)
               for component in connected_regions({p for p in all_objects if rows[p[1]][p[0]] == symbol})]
    label = 'forest' if set(symbols) == {'T'} else 'objects'
    if not regions:
        return [f'INFO BLOCKOUT {label} skipped: no object cells']
    notes, vocab, wfc = [f'INFO BLOCKOUT {label} connected-regions:{len(regions)}'], None, None
    try:
        from . import wfc
        ref = learning_reference(repo, exclude)
        vocab = wfc.learn(ref, pair, use_cache=False, exclude=tuple(exclude))
    except (PkError, ImportError) as exc:
        notes.append(f'INFO BLOCKOUT {label} WFC fallback: {exc}')
    for number, (symbol, component) in enumerate(regions, 1):
        region = component | {q for x, y in component for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                              if (q := (x + dx, y + dy)) in choices and q not in all_objects
                              and rows[q[1]][q[0]] not in LEDGE_STEP}
        forced = {p: [mt for mt in choices[p] if rules.material(cell_bits[p][mt]) == rows[p[1]][p[0]]]
                  for p in region}
        if any(not v for v in forced.values()):
            notes.append(f'INFO BLOCKOUT {label} region:{number} symbol:{symbol} size:{len(component)} '
                         'no letter-fixed domain; previous assignment retained')
            continue
        interior, detail = interior_plan(repo, grid, pair, rows, rules, forced, cell_bits, component, symbol)
        band = region - set(interior)
        notes.append(f'INFO BLOCKOUT {label} region:{number}/{len(regions)} symbol:{symbol} '
                     f'size:{len(component)} interior:{len(interior)} boundary/rim:{len(band)} {detail}')
        original = {p: grid.at(*p) for p in region}
        for p, mt in interior.items():
            grid.set(*p, cell_bits[p][mt])
        proposal, kind, wfc_steps = None, 'seen-pair fallback', 0
        try:
            if vocab is not None and band:
                try:
                    band_forced = {p: forced[p] for p in band}
                    relevant = restricted_vocabulary(grid, band, band_forced, vocab, wfc)
                    result = wfc.fill(ref, grid, pair, sorted(band, key=lambda p: (p[1], p[0])),
                                      forced=band_forced, vocabulary=relevant)
                    wfc_steps = result.steps
                    if result.complete and set(result) == band and all(mt in forced[p] for p, mt in result.items()):
                        proposal, kind = dict(result), 'WFC'
                    else:
                        notes.append(f'INFO BLOCKOUT {label} region:{number} WFC fallback '
                                     f'unresolved:{len(result.unsatisfied)} steps:{wfc_steps} reason:{result.reason}')
                except PkError as exc:
                    notes.append(f'INFO BLOCKOUT {label} region:{number} WFC fallback: {exc}')
            if proposal is None:
                proposal = terrain.solve(grid, rules, {p: rows[p[1]][p[0]] for p in band},
                                         margin=0, choices={p: forced[p] for p in band},
                                         seed={p: grid.at(*p)[0] if grid.at(*p)[0] in forced[p] else forced[p][0]
                                               for p in band}, passes=24, diag_weight=.5) if band else {}
            hard = hard_solve(repo, grid, pair, rows, rules, forced, cell_bits, band,
                              preferred=proposal, letters=True)
            if hard['ok']:
                # Interior is frozen during band solving; validate the combined
                # result atomically, including interior/interior and border edges.
                assignment = interior | hard['assignment']
                if not HardObjects(repo, grid, pair, cell_bits).validates(assignment):
                    hard = dict(ok=False, reason='combined interior/band validation', steps=hard['steps'])
                else:
                    apply_candidates(grid, assignment, choices, cell_bits)
            if hard['ok']:
                notes.append(f'INFO BLOCKOUT {label} {kind} solved:{len(region)} region:{number}/{len(regions)} '
                             f'{symbol}:{len(component)} steps:{hard["steps"]} WFC-steps:{wfc_steps} OBJECT:hard')
            else:
                for p, cell in original.items():
                    grid.set(*p, cell)
                notes.append(f'INFO BLOCKOUT {label} region:{number} refused:{hard["reason"]} '
                             f'steps:{hard["steps"]} WFC-steps:{wfc_steps} '
                             'previous assignment retained; object rules never relaxed')
        except Exception:
            for p, cell in original.items():
                grid.set(*p, cell)
            raise
    return notes


def forest(repo, grid, pair, rows, rules, choices, cell_bits, exclude=()):
    """Compatibility entry point for the T-only solver contract tests."""
    return object_regions(repo, grid, pair, rows, rules, choices, cell_bits, exclude, symbols='T')


def ledge_rows(repo, grid, pair, rows, rules, choices, cell_bits):
    notes, count = [], 0
    all_ledges = {p for p in choices if rows[p[1]][p[0]] in LEDGE_STEP}
    for symbol in 'v^<>':
        direction = ((1, 0), (-1, 0)) if symbol in 'v^' else ((0, 1), (0, -1))
        for component in connected_regions({p for p in all_ledges if rows[p[1]][p[0]] == symbol}, direction):
            count += 1
            region = component | {q for x, y in component for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                                  if (q := (x + dx, y + dy)) in choices and q not in all_ledges
                                  and rows[q[1]][q[0]] not in OBJECT_LETTERS}
            result = hard_solve(repo, grid, pair, rows, rules, choices, cell_bits, region, letters=True)
            if result['ok']:
                apply_candidates(grid, result['assignment'], choices, cell_bits)
                x, y = min(component)
                notes.append(f'INFO BLOCKOUT ledge row {x},{y} symbol:{symbol} length:{len(component)} '
                             f'solved:{len(region)} steps:{result["steps"]} OBJECT:hard')
            else:
                notes.append(f'INFO BLOCKOUT ledge row symbol:{symbol} cells:{len(component)} '
                             f'refused:{result["reason"]}; coherent seed retained')
    if not count:
        notes.append('INFO BLOCKOUT ledge rows skipped: none')
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




SECTION = 24          # window size for sectioned solving
SECTION_STEP = 16     # stride: windows overlap by 8 cells
SECTION_MIN = 8       # a failing window is split down to this size


def sectioned_solve(repo, grid, pair, rows, rules, domains, active, cell_bits, cells, preferred,
                    letters, label):
    """Solve `cells` window by window (top to bottom, left to right). Each window sees cells already
    solved as fixed and cells not yet reached as free; a failing window is split into quarters down to
    SECTION_MIN. Solved windows are applied at once. Returns (solved cells, notes)."""
    cells = set(cells)
    if not cells:
        return set(), []
    xs = [x for x, _ in cells]
    ys = [y for _, y in cells]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    solved, notes, failed = set(), [], []

    def starts(a, b, size, step):
        """Window starts covering a..b; the last window always ends exactly at b."""
        if b - a + 1 <= size:
            return [a]
        out = list(range(a, b - size + 2, step))
        if out[-1] != b - size + 1:
            out.append(b - size + 1)
        return out

    def windows(ax, ay, bx, by, size, step):
        for wy in starts(ay, by, size, step):
            for wx in starts(ax, bx, size, step):
                yield wx, wy, min(bx, wx + size - 1), min(by, wy + size - 1)

    def attempt(ax, ay, bx, by, size):
        region = {p for p in cells if ax <= p[0] <= bx and ay <= p[1] <= by}
        if not region:
            return True
        free = frozenset(cells - region - solved)
        want = {p: grid.at(*p)[0] if p in solved else preferred.get(p) for p in region}
        result = hard_solve(repo, grid, pair, rows, rules, domains, cell_bits, region,
                            preferred={p: v for p, v in want.items() if v is not None}, letters=letters,
                            step_limit=20000, time_limit=4., free=free)
        if result['ok']:
            apply_candidates(grid, result['assignment'], active, cell_bits)
            solved.update(region)
            return True
        if size <= SECTION_MIN:
            failed.append((ax, ay, bx, by, result['reason']))
            return False
        half = max(SECTION_MIN, size // 2)
        ok = True
        for sub in windows(ax, ay, bx, by, half, half):
            ok &= attempt(*sub, half)
        return ok

    for win in windows(x0, y0, x1, y1, SECTION, SECTION_STEP):
        attempt(*win, SECTION)
    left = cells - solved
    if left:
        # Remnants no window could solve under hard rules (e.g. a tiny rock bump in a beach with no
        # vanilla context left after leave-one-out): soft solve by letter and vanilla pairs, then keep a
        # cell's new tile only if it breaks no object (object rules stay hard). Never leave wiped lawn
        # inside a beach or path, which blocks the way.
        picked = terrain.solve(grid, rules, {p: rows[p[1]][p[0]] for p in left}, margin=0,
                               choices={p: list(domains[p]) or [grid.at(*p)[0]] for p in left},
                               seed={p: grid.at(*p)[0] for p in left}, passes=24, diag_weight=.5)
        glue = objects.Glue(repo, grid, pair)
        kept = 0
        for p in sorted(left, key=lambda q: (q[1], q[0])):
            if p not in picked or picked[p] not in cell_bits.get(p, {}):
                continue
            old = grid.at(*p)
            before = objects.count_in(glue, [p])
            grid.set(*p, cell_bits[p][picked[p]])
            if objects.count_in(glue, [p]) > before:
                grid.set(*p, old)
            else:
                kept += 1
        notes.append(f'INFO BLOCKOUT {label} soft remnant fill: {kept}/{len(left)} cells (object rules hard)')
    notes.append(f'INFO BLOCKOUT {label} sectioned: solved:{len(solved)}/{len(cells)} '
                 f'window:{SECTION} step:{SECTION_STEP} failed-windows:{len(failed)}')
    for ax, ay, bx, by, why in failed[:8]:
        notes.append(f'INFO BLOCKOUT {label} section {ax},{ay}-{bx},{by} unsolved: {why}')
    return solved, notes


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
        seed_domains = dict(active)
        for symbol in sorted(OBJECT_LETTERS):
            for component in connected_regions({p for p in active if rows[p[1]][p[0]] == symbol}):
                interior, detail = interior_plan(repo, grid, pair, rows, rules, active, cell_bits, component, symbol)
                if len(component) >= 128:
                    out.append(f'INFO BLOCKOUT seed region symbol:{symbol} size:{len(component)} '
                               f'interior:{len(interior)} boundary:{len(component) - len(interior)} {detail}')
                seed_domains.update({p: [mt] for p, mt in interior.items()})
        coherent = hard_solve(repo, grid, pair, rows, rules, seed_domains, cell_bits,
                              preferred=seed, letters=False, step_limit=50000, time_limit=12.)
        if coherent['ok']:
            apply_candidates(grid, coherent['assignment'], active, cell_bits)
            out.append(f'INFO BLOCKOUT hard coherent seed solved:{len(active)} '
                       f'steps:{coherent["steps"]} OBJECT:hard')
        else:
            out.append(f'INFO BLOCKOUT hard coherent seed refused:{coherent["reason"]} '
                       'no invalid assignment applied; solving in sections')
            _, notes = sectioned_solve(repo, grid, pair, rows, rules, seed_domains, active, cell_bits,
                                       set(active), seed, False, 'coherent seed')
            out.extend(notes)
        out.append(f'INFO BLOCKOUT solve editable:{len(active)} '
                   f'frozen-b:{grid.w * grid.h - len(choices)} (no modal-lawn or event freeze)')
        out.extend(object_regions(repo, grid, pair, rows, rules, active, cell_bits, exclude))
        out.extend(ledge_rows(repo, grid, pair, rows, rules, active, cell_bits))
        rest = {p: rows[p[1]][p[0]] for p in active
                if rows[p[1]][p[0]] not in OBJECT_LETTERS and rows[p[1]][p[0]] not in LEDGE_STEP}
        if rest:
            picked = terrain.solve(grid, rules, rest, margin=0,
                                   choices={p: active[p] for p in rest},
                                   seed={p: grid.at(*p)[0] for p in rest}, passes=24, diag_weight=.5)
            hard = hard_solve(repo, grid, pair, rows, rules, active, cell_bits,
                              set(rest), preferred=picked, letters=True)
            if hard['ok']:
                apply_candidates(grid, hard['assignment'], active, cell_bits)
                out.append(f'INFO BLOCKOUT rest solved:{len(rest)} steps:{hard["steps"]} OBJECT:hard')
            else:
                out.append(f'INFO BLOCKOUT rest refused:{hard["reason"]}; solving the rest in sections')
                _, notes = sectioned_solve(repo, grid, pair, rows, rules, active, active, cell_bits,
                                           set(rest), picked, True, 'rest')
                out.extend(notes)
        else:
            out.append('INFO BLOCKOUT rest skipped: no non-tree editable cells')
        out.extend(local_repair(repo, grid, pair, rows, rules, active, cell_bits))
    else:
        out.append('INFO BLOCKOUT forest/rest skipped: no non-door candidates for any editable cell')
    out.append('INFO BLOCKOUT unrestricted terrain auto skipped: joins already solved in context domains')
    elevation = getattr(rows, 'elevation', None)
    if elevation:
        changed = 0
        for y in range(grid.h):
            for x in range(grid.w):
                if rows[y][x] == 'b':
                    continue
                mt, coll, elev = grid.at(x, y)
                want = int(elevation[y][x], 16)
                if elev != want:
                    grid.set(x, y, (mt, coll, want))
                    changed += 1
        out.append(f'INFO BLOCKOUT elevation layer applied: {changed} cells changed height')
    else:
        out.append("INFO BLOCKOUT no elevation layer: heights are each tile's usual vanilla elevation")
    actual = export(repo, grid, pair)
    matched = sum(a == b for row, other in zip(rows, actual) for a, b in zip(row, other))
    fidelity = matched / (grid.w * grid.h)
    added = sorted(set(objects.check(repo, grid, pair)) - set(objects.check(repo, before, pair)))
    losses = reach_losses(before, grid, pair)
    # Open bank = a water cell next to lawn with a tile pair vanilla never uses there. Pond rims are
    # drawn inside surfable tiles, so plain "water letter next to lawn letter" fired 445 times on 36
    # vanilla maps; the vanilla-pair rule is 0 on vanilla by construction (same as SEAM).
    full = learned(repo, pair)       # all vanilla outdoor layouts + portable primary pairs (the solver's source)
    def unseen_contact(x, y, nx, ny):
        a, b = grid.at(x, y)[0], grid.at(nx, ny)[0]
        if nx != x:
            return ((a, b) if nx > x else (b, a)) not in full.h
        return ((a, b) if ny > y else (b, a)) not in full.v
    open_bank = [(x, y) for y, row in enumerate(actual) for x, char in enumerate(row) if char == '~'
                 and any(grid.inside(nx, ny) and actual[ny][nx] == '.' and unseen_contact(x, y, nx, ny)
                         for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)))]
    out.extend(added)
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
