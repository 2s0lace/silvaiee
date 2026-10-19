"""Vanilla outdoor chunk vocabulary and layout calibration, with layout-level holdout.

Two disjoint grids start at (0,0) and (2,2). Only complete 4x4 chunks are
observations; padding would invent vanilla evidence. Near duplicates join a
representative within Hamming distance two, never a transitive chain.
"""
from collections import Counter, defaultdict, deque
import hashlib
import json
import math
import random

from ..core.errors import PkError
from ..core.out import Result
from . import blockout, layout, paths
from .edit import load
from .lint import vanilla_outdoor

SIZE = 4
OFFSETS = (0, 2)
VERSION = 2
CACHE = '.pk/chunks/corpus.json'
ALPHABET = {c: i for i, c in enumerate(sorted(blockout.LETTERS))}


def cut(rows):
    """(grid offset, x, y, row-major letters, row-major elevation). No padding."""
    h, w = len(rows), len(rows[0]) if rows else 0
    elevation = getattr(rows, 'elevation', None)
    for offset in OFFSETS:
        for y in range(offset, h - SIZE + 1, SIZE):
            for x in range(offset, w - SIZE + 1, SIZE):
                letters = ''.join(row[x:x + SIZE] for row in rows[y:y + SIZE])
                heights = ''.join(row[x:x + SIZE] for row in elevation[y:y + SIZE]) if elevation else None
                yield offset, x, y, letters, heights


def edges(letters):
    return dict(N=letters[:4], S=letters[-4:], W=letters[::4], E=letters[3::4])


def code(letters):
    # One-hot letters: every unequal cell contributes exactly two XOR bits.
    return sum(1 << (i * len(ALPHABET) + ALPHABET[c]) for i, c in enumerate(letters))


def distance(a, b):
    return (a ^ b).bit_count() // 2


def signatures(letters):
    # At most two changed cells leave at least one of these three parts intact.
    return letters[:6], letters[6:11], letters[11:]


class Vocabulary:
    def __init__(self, corpus, exclude=()):
        dropped = {corpus[n]['layout'] for n in exclude if n in corpus}
        kept = {n: m for n, m in corpus.items() if m['layout'] not in dropped and not n.startswith('Pk')}
        self.excluded = sorted(n for n, m in corpus.items() if m['layout'] in dropped)
        observations = defaultdict(list)
        positions = {}
        for name, m in sorted(kept.items()):
            rows = blockout.Rows(m['rows'])
            rows.elevation = m['elevation']
            for offset, x, y, letters, heights in cut(rows):
                observations[letters].append(dict(map=name, x=x, y=y, elevation=heights))
                positions[name, offset, x, y] = letters
        self.kinds = []
        self.index = [defaultdict(set) for _ in range(3)]
        self.exact = {}
        self.memo = {}
        self.exact_count = len(observations)
        for letters in sorted(observations, key=lambda s: (-len(observations[s]), s)):
            bits = code(letters)
            candidates = self.near_candidates(letters)
            matches = [(distance(bits, self.kinds[k]['code']), k) for k in candidates]
            matches = [(d, k) for d, k in matches if d <= 2]
            if matches:
                _, k = min(matches)
            else:
                k = len(self.kinds)
                self.kinds.append(dict(letters=letters, code=bits, edges=edges(letters), sources=[], frequency=0))
                for index, signature in zip(self.index, signatures(letters)):
                    index[signature].add(k)
            kind = self.kinds[k]
            kind['sources'].extend(observations[letters])
            kind['frequency'] += len(observations[letters])
            self.exact[letters] = k
        if not self.kinds:
            raise PkError('NOTFOUND', 'no complete vanilla chunks after holdout')
        self.adjacency = Counter()
        for (name, offset, x, y), letters in positions.items():
            for side, dx, dy in (('E', 4, 0), ('S', 0, 4)):
                other = positions.get((name, offset, x + dx, y + dy))
                if other is not None:
                    a, b = self.exact[letters], self.exact[other]
                    self.adjacency[a, b, side] += 1
                    self.adjacency[b, a, {'E': 'W', 'S': 'N'}[side]] += 1

    def near_candidates(self, letters):
        return set().union(*(index.get(s, ()) for index, s in zip(self.index, signatures(letters))))

    def nearest(self, letters):
        if letters not in self.memo:
            bits = code(letters)
            candidates = self.near_candidates(letters)
            near = min(((distance(bits, self.kinds[k]['code']), k) for k in candidates), default=(17, -1))
            if near[0] > 2:
                near = min((distance(bits, kind['code']), k) for k, kind in enumerate(self.kinds))
            self.memo[letters] = near
        return self.memo[letters]

    def seam(self, a, b, side):
        opposite = {'E': 'W', 'S': 'N', 'W': 'E', 'N': 'S'}[side]
        return bool(self.adjacency[a, b, side]) or self.kinds[a]['edges'][side] == self.kinds[b]['edges'][opposite]


def score(vocab, rows):
    chunks = list(cut(rows))
    if not chunks:
        raise PkError('BOUNDS', 'layout scoring needs at least one complete 4x4 chunk')
    nearest = {(offset, x, y): vocab.nearest(letters) for offset, x, y, letters, _ in chunks}
    fits = sum(d <= 2 for d, _ in nearest.values())
    seams = total = 0
    for (offset, x, y), (_, a) in nearest.items():
        for side, dx, dy in (('E', 4, 0), ('S', 0, 4)):
            other = nearest.get((offset, x + dx, y + dy))
            if other is not None:
                total += 1
                seams += vocab.seam(a, other[1], side)
    worst = []
    for (offset, x, y), (d, k) in sorted(nearest.items(), key=lambda p: (-p[1][0], p[0]))[:5]:
        source = vocab.kinds[k]['sources'][0]
        worst.append(dict(x=x, y=y, offset=offset, distance=d, kind=k, source=source,
                          representative=vocab.kinds[k]['letters']))
    return dict(chunk_fit=fits / len(chunks), seam_fit=seams / total if total else None,
                chunks=len(chunks), fitting=fits, seams=total, fitting_seams=seams, worst=worst)


def fingerprint(repo):
    """Stat source inputs as well as classifier code/sidecars; never read game binaries here."""
    digest = hashlib.sha256()
    for directory in ('data/maps', 'data/layouts', 'data/tilesets', 'graphics/tilesets', 'tools/pk/pk', 'tools/pk/data'):
        for path in sorted((repo.root / directory).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and path.name != 'chunks.py':
                stat = path.stat()
                digest.update(f'{path.relative_to(repo.root)}:{stat.st_size}:{stat.st_mtime_ns}\n'.encode())
    return digest.hexdigest()


def corpus(repo, tx):
    stamp = fingerprint(repo)
    notes = []
    path = repo.path(CACHE)
    if path.is_file():
        try:
            doc = json.loads(repo.read(CACHE))
            if doc.get('version') == VERSION and doc.get('fingerprint') == stamp:
                return doc['maps'], ['INFO LAYOUT chunk corpus cache hit']
            notes.append('INFO LAYOUT chunk corpus cache stale; re-exporting vanilla')
        except (ValueError, KeyError):
            notes.append('INFO LAYOUT chunk corpus cache invalid; re-exporting vanilla')
    else:
        notes.append('INFO LAYOUT chunk corpus cache absent; exporting vanilla')
    maps = {}
    for name, stratum in vanilla_outdoor(repo):
        grid, pair = load(repo, name)
        rows = blockout.export(repo, grid, pair)
        shares, mirror = layout.measure(grid, pair)
        maps[name] = dict(layout=grid.data['layout'], stratum=stratum, rows=list(rows), elevation=rows.elevation,
                          open=max(v for _, v in shares), mirror=mirror)
    tx.stage(CACHE, (json.dumps(dict(version=VERSION, fingerprint=stamp, maps=maps)) + '\n').encode())
    return maps, notes


def score_lines(name, report):
    seam = 'n/a (no neighbouring chunks)' if report['seam_fit'] is None else f"{report['seam_fit']:.4%}"
    out = [f"INFO LAYOUT {name} chunk_fit:{report['chunk_fit']:.4%} ({report['fitting']}/{report['chunks']}) "
           f"seam_fit:{seam} ({report['fitting_seams']}/{report['seams']})"]
    for c in report['worst']:
        source = c['source']
        out.append(f"INFO LAYOUT worst {c['x']},{c['y']},4x4 distance:{c['distance']} kind:{c['kind']} "
                   f"closest:{source['map']} box:{source['x']},{source['y']},4x4 letters:{c['representative']}")
    return out


def percentile(values, q):
    values = sorted(v for v in values if v is not None)
    if not values:
        raise PkError('NOTFOUND', 'calibration has no measurable values')
    at = (len(values) - 1) * q
    low = int(at)
    return values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (at - low)


def holdout(maps, grid):
    """Resolve a target's layout even when its map name is outside the corpus."""
    return tuple(n for n, m in maps.items() if m['layout'] == grid.data['layout'])


def median_interval(values, confidence=.95):
    """Distribution-free order-statistic interval; None means insufficient maps.

    A positive point-estimate gap alone does not establish 'clearly higher'.
    Report uncertainty explicitly instead of silently accepting any positive gap.
    """
    values = sorted(v for v in values if v is not None)
    n = len(values)
    indices = [j for j in range(n // 2)
               if 2 * sum(math.comb(n, i) for i in range(j + 1)) / 2 ** n <= 1 - confidence]
    if not indices:
        return None
    j = max(indices)
    return [values[j], values[n - j - 1]]


def command(repo, tx, args, opts):
    if args and args[0] == 'generate':
        return generate_command(repo, tx, args, opts)
    if args and args[0] == 'check-paths':
        return check_paths_command(repo, tx, args, opts)
    if not args or args[0] not in ('learn', 'score', 'calibrate'):
        raise PkError('PARSE', 'usage: pk layout learn | score MAP FILE | calibrate | generate MAP --type route|town --exits N,S')
    expected = 3 if args[0] == 'score' else 1
    if len(args) != expected or opts:
        raise PkError('PARSE', 'usage: pk layout learn | score MAP FILE | calibrate')
    maps, notes = corpus(repo, tx)
    if args[0] == 'score':
        grid, _ = load(repo, args[1])
        rows = blockout.parse(repo.read(args[2]).decode('utf-8-sig'), grid.w, grid.h)
        vocab = Vocabulary(maps, holdout(maps, grid))
        report = score(vocab, rows)
        return Result(f'layout score {grid.name}', notes + [f'INFO LAYOUT excluded:{vocab.excluded}'] + score_lines(grid.name, report))
    vocab = Vocabulary(maps)
    notes.append(f'INFO LAYOUT maps:{len(maps)} exact_chunks:{vocab.exact_count} kinds:{len(vocab.kinds)} '
                 f'observations:{sum(k["frequency"] for k in vocab.kinds)} directed_pairs:{len(vocab.adjacency)}')
    if args[0] == 'learn':
        # Persist complete provenance and border/adjacency data, not just totals.
        doc = dict(kinds=[{k: v for k, v in kind.items() if k != 'code'} for kind in vocab.kinds],
                   adjacency=[[a, b, side, n] for (a, b, side), n in sorted(vocab.adjacency.items())])
        tx.stage('.pk/chunks/vocabulary.json', (json.dumps(doc) + '\n').encode())
        artifact, mask_notes = autotile_artifact(repo, maps)
        tx.stage('.pk/chunks/autotile.json', (json.dumps(artifact) + '\n').encode())
        notes.extend(mask_notes)
        return Result('layout learn', notes)
    vanilla = {}
    for name, m in sorted(maps.items()):
        held = Vocabulary(maps, (name,))
        vanilla[name] = score(held, m['rows'])
    limits = {metric: {p: percentile([s[metric] for s in vanilla.values()], q)
                       for p, q in (('p5', .05), ('p50', .5))} for metric in ('chunk_fit', 'seam_fit')}
    for metric, values in limits.items():
        notes.append(f'INFO LAYOUT vanilla LOO {metric} p5:{values["p5"]:.4%} p50:{values["p50"]:.4%}')
    comparisons = {}
    grid, _ = load(repo, 'PkBlockCoast_Frlg')
    coast = blockout.parse(repo.read('.pk/blockout/coast.txt').decode('utf-8-sig'), grid.w, grid.h)
    comparisons['coast'] = score(Vocabulary(maps, holdout(maps, grid)), coast)
    notes.extend(score_lines('coast', comparisons['coast']))
    for name in sorted(n for n in repo.maps() if n.startswith('PkV3') and n.endswith('_Frlg')):
        grid, pair = load(repo, name)
        rows = blockout.export(repo, grid, pair)
        comparisons[name] = score(Vocabulary(maps, holdout(maps, grid)), rows)
        notes.extend(score_lines(name, comparisons[name]))
    margin = limits['seam_fit']['p50'] - comparisons['coast']['seam_fit']
    interval = median_interval([s['seam_fit'] for s in vanilla.values()])
    coast_seam = comparisons['coast']['seam_fit']
    if interval is None:
        notes.append('INFO LAYOUT vanilla median seam uncertainty unmeasured: insufficient maps for a 95% interval')
    else:
        notes.append(f'INFO LAYOUT vanilla median seam 95% interval:{interval[0]:.4%}..{interval[1]:.4%}')
    notes.append(f'INFO LAYOUT REPORT ONLY seam margin:{margin:.4%}; chunk/seam fit do not measure quality; '
                 'common empty lawn/open sea and schematic layouts can outscore vanilla')
    tx.stage('.pk/chunks/calibration.json', (json.dumps(dict(vanilla=vanilla, limits=limits,
             comparisons=comparisons, seam_margin=margin, median_seam_interval_95=interval,
             diagnostic_only=True), indent=2) + '\n').encode())
    return Result('layout calibration (diagnostic only, no quality gate)', notes)


def kept_maps(maps, exclude):
    dropped = {maps[n]['layout'] for n in exclude if n in maps}
    return {n: m for n, m in maps.items() if not n.startswith('Pk') and m['layout'] not in dropped}


def generation_limits(maps, kind, exclude=()):
    """Same metatile measurements, per-map p95 and strata as layout.py; LOO."""
    values = [m for m in kept_maps(maps, exclude).values() if m['stratum'] == kind]
    if not values:
        raise PkError('NOTFOUND', f'no vanilla {kind} calibration after holdout')
    return dict(open=layout._p95([m['open'] for m in values]),
                mirror=layout._p95([m['mirror'] for m in values]), maps=len(values))


def ids(mask):
    while mask:
        bit = mask & -mask
        yield bit.bit_length() - 1
        mask ^= bit


def overlap_key(letters, heights, side):
    indices = {'N': range(8), 'S': range(8, 16), 'W': (0, 1, 4, 5, 8, 9, 12, 13),
               'E': (2, 3, 6, 7, 10, 11, 14, 15)}[side]
    return ''.join(letters[i] + heights[i] for i in indices)


def overlap_cut(rows):
    for y in range(0, len(rows) - 3, 2):
        for x in range(0, len(rows[0]) - 3, 2):
            yield (x, y, ''.join(r[x:x + 4] for r in rows[y:y + 4]),
                   ''.join(r[x:x + 4] for r in rows.elevation[y:y + 4]) if rows.elevation else None)


class Overlap:
    """Exact 4x4 observations, stride-two placement; no invented/merged patches."""
    def __init__(self, maps, kind='route', exclude=()):
        observations = defaultdict(list)
        for name, m in sorted(kept_maps(maps, exclude).items()):
            if m['stratum'] != kind:
                continue
            rows = blockout.Rows(m['rows'])
            rows.elevation = m['elevation']
            # The two diagnostic grids omit cross phases (0,2)/(2,0). A full
            # stride-two overlap lattice needs those actual vanilla patches too.
            for x, y, letters, heights in overlap_cut(rows):
                if 'b' not in letters:
                    if heights is None:
                        raise PkError('PARSE', f'vanilla chunk {name} {x},{y} has no elevation')
                    observations[letters, heights].append(dict(map=name, x=x, y=y))
        self.patterns = sorted(observations)
        if not self.patterns:
            raise PkError('NOTFOUND', f'no building-free vanilla {kind} chunks')
        self.sources = [observations[p] for p in self.patterns]
        self.weights = [len(s) for s in self.sources]
        self.all = (1 << len(self.patterns)) - 1
        self.cells = [defaultdict(int) for _ in range(16)]
        borders = {s: defaultdict(int) for s in 'NSEW'}
        for k, (letters, heights) in enumerate(self.patterns):
            bit = 1 << k
            for i, (c, z) in enumerate(zip(letters, heights)):
                self.cells[i][c + z] |= bit
            for side in borders:
                borders[side][overlap_key(letters, heights, side)] |= bit
        self.support = {}
        for side, opposite in (('N', 'S'), ('S', 'N'), ('W', 'E'), ('E', 'W')):
            self.support[side] = [(mask, borders[opposite].get(key, 0)) for key, mask in borders[side].items()]

    def allowed(self, domain, side):
        result = 0
        for mask, other in self.support[side]:
            if mask & domain:
                result |= other
        return result


def exit_segments(w, h, sides):
    if not sides or len(set(sides)) != len(sides) or any(s not in 'NSEW' or len(s) != 1 for s in sides):
        raise PkError('PARSE', '--exits must be distinct comma-separated N,S,E,W sides')
    # Two-cell, even-aligned openings; no path is silently painted after filling.
    x, y = min(w - 2, (w // 2) // 2 * 2), min(h - 2, (h // 2) // 2 * 2)
    segments = {'N': {(x, 0), (x + 1, 0)}, 'S': {(x, h - 1), (x + 1, h - 1)},
                'W': {(0, y), (0, y + 1)}, 'E': {(w - 1, y), (w - 1, y + 1)}}
    return {s: segments[s] for s in sides}


def parse_keep(text, w, h):
    # Validate the ordinary fixed header, alphabet, dimensions and elevation first.
    rows = blockout.parse(text.replace('?', '.'), w, h)
    original = text.splitlines()[2:2 + h]
    if any(c == 'b' for row in original for c in row):
        raise PkError('PARSE', 'generation cannot fix or invent buildings')
    return {(x, y): (c, rows.elevation[y][x] if rows.elevation else None)
            for y, row in enumerate(original) for x, c in enumerate(row) if c != '?'}


class ChunkSolver:
    """Overlap WFC plus global constraints inside bounded backtracking.

    Potential-cell connectivity is optimistic on partial domains. Every leaf
    passes the same constraint before acceptance; a failed leaf backtracks,
    never becomes a result to be repaired/carved afterwards.
    """
    def __init__(self, vocab, w, h, exits, limits, keep=None, seed=0, max_steps=20000, masks=None):
        if w < 4 or h < 4:
            raise PkError('BOUNDS', 'chunk generation needs dimensions at least 4x4')
        self.vocab, self.w, self.h = vocab, w, h
        self.pw, self.ph = (w + 3) // 4 * 4, (h + 3) // 4 * 4
        self.positions = [(x, y) for y in range(0, self.ph - 3, 2) for x in range(0, self.pw - 3, 2)]
        self.lookup = {p: i for i, p in enumerate(self.positions)}
        self.neighbours = [[] for _ in self.positions]
        self.owners = {}
        for k, (x, y) in enumerate(self.positions):
            for side, dx, dy in (('N', 0, -2), ('S', 0, 2), ('W', -2, 0), ('E', 2, 0)):
                other = self.lookup.get((x + dx, y + dy))
                if other is not None:
                    self.neighbours[k].append((side, other))
            for dy in range(4):
                for dx in range(4):
                    if x + dx < w and y + dy < h:
                        self.owners.setdefault((x + dx, y + dy), (k, dy * 4 + dx))
        self.exits = set().union(*exits.values())
        self.limits, self.keep = limits, keep or {}
        self.masks = masks
        self.rng, self.max_steps = random.Random(seed), max_steps
        self.steps = self.backtracks = self.propagations = 0
        self.rejects = Counter()
        self.screens = layout.screens(self)
        self.cell_cache = {}
        self.domains = [vocab.all for _ in self.positions]
        for k, (x, y) in enumerate(self.positions):
            for dy in range(4):
                for dx in range(4):
                    p = x + dx, y + dy
                    fixed = self.keep.get(p)
                    if fixed is None and p not in self.exits:
                        continue
                    allowed = 0
                    for token, mask in vocab.cells[dy * 4 + dx].items():
                        if p in self.exits and token[0] not in blockout.WALK:
                            continue
                        if fixed and (token[0] != fixed[0] or (fixed[1] is not None and token[1] != fixed[1])):
                            continue
                        allowed |= mask
                    self.domains[k] &= allowed

    def propagate(self, domains, queue):
        queue = deque(queue)
        queued = set(queue)
        while queue:
            k = queue.popleft()
            queued.discard(k)
            if not domains[k]:
                return False
            for side, other in self.neighbours[k]:
                narrowed = domains[other] & self.vocab.allowed(domains[k], side)
                self.propagations += 1
                if narrowed != domains[other]:
                    if not narrowed:
                        return False
                    domains[other] = narrowed
                    if other not in queued:
                        queue.append(other)
                        queued.add(other)
        return True

    def options(self, domains):
        out = {}
        for p, (k, i) in self.owners.items():
            key = i, domains[k]
            if key not in self.cell_cache:
                self.cell_cache[key] = frozenset(t for t, mask in self.vocab.cells[i].items() if mask & domains[k])
            out[p] = self.cell_cache[key]
        return out

    def component(self, start, allowed, tokens=None):
        seen, todo = {start}, [start]
        while todo:
            x, y = todo.pop()
            for p in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if p not in allowed or p in seen:
                    continue
                if tokens is not None:
                    a = {t[1] for t in tokens[x, y] if t[0] in blockout.WALK}
                    b = {t[1] for t in tokens[p] if t[0] in blockout.WALK}
                    if not (a & b or '0' in a or '0' in b):
                        continue
                seen.add(p)
                todo.append(p)
        return seen

    def constraint(self, domains):
        tokens = self.options(domains)
        possible_walk = {p for p, ts in tokens.items() if any(t[0] in blockout.WALK for t in ts)}
        if not self.exits <= possible_walk:
            return 'exit blocked'
        if self.exits and not self.exits <= self.component(min(self.exits), possible_walk, tokens):
            return 'exit connectivity'
        water = {p for p, ts in tokens.items() if all(t[0] == '~' for t in ts)}
        possible_water = {p for p, ts in tokens.items() if any(t[0] == '~' for t in ts)}
        if water:
            if not water <= self.component(min(water), possible_water):
                return 'water connectivity'
            for x, y in water:
                if not any(p in possible_water for p in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))):
                    return 'single water cell'
        fixed = {p: next(iter(ts))[0] for p, ts in tokens.items() if len(ts) == 1}
        if self.masks is not None:
            for (x, y), c in fixed.items():
                if c not in 'ps~':
                    continue
                neighbours = [(x + dx, y + dy) for dx, dy in MASK_OFFSETS]
                if all(p in fixed or p not in tokens for p in neighbours):
                    around = [fixed.get(p, '_') for p in neighbours]
                    if missing_shape(c, around, self.masks):
                        return 'missing path/edge mask'
        plain = set()
        for (x, y), c in fixed.items():
            if c in '.pgsf' and all(fixed.get((nx, ny)) == c
                    for ny in range(max(0, y - 1), min(self.h, y + 2))
                    for nx in range(max(0, x - 1), min(self.w, x + 2))):
                plain.add((x, y))
        for x0, y0, w, h in self.screens:
            if sum((x, y) in plain for y in range(y0, y0 + h) for x in range(x0, x0 + w)) > self.limits['open'] * w * h + 1e-9:
                return 'plain screen'
        # Optimistic mirror bound: unknown pairs may all become eligible mismatches.
        classes = {p: 'solid' if c in 'Tnr' else '~' if c == '~' else 'ground' if c in '.pf' else c for p, c in fixed.items()}
        possible_plain = set()
        for (x, y), c in fixed.items():
            if c in '.pgsf' and all(any(t[0] == c for t in tokens[nx, ny])
                    for ny in range(max(0, y - 1), min(self.h, y + 2))
                    for nx in range(max(0, x - 1), min(self.w, x + 2))):
                possible_plain.add((x, y))
        for axis in ('lr', 'tb'):
            same = total = 0
            for y in range(self.h):
                for x in range(self.w):
                    p = x, y
                    q = (self.w - 1 - x, y) if axis == 'lr' else (x, self.h - 1 - y)
                    if q <= p:
                        continue
                    if p in classes and q in classes:
                        if p in plain and q in plain:
                            continue
                        if classes[p] in ('solid', '~') and classes[q] in ('solid', '~'):
                            continue
                        same += classes[p] == classes[q] and not (p in possible_plain and q in possible_plain)
                    total += 1
            if total and same > self.limits['mirror'] * total + 1e-9:
                return 'mirror'
        return None

    def search(self, domains):
        reason = self.constraint(domains)
        if reason:
            self.rejects[reason] += 1
            return None
        undecided = [i for i, d in enumerate(domains) if d.bit_count() > 1]
        if not undecided:
            return domains
        k = min(undecided, key=lambda i: (domains[i].bit_count(), self.rng.random()))
        choices = sorted(ids(domains[k]), key=lambda i: -math.log(max(self.rng.random(), 1e-15)) / self.vocab.weights[i])
        for choice in choices:
            if self.steps >= self.max_steps:
                return None
            self.steps += 1
            trial = list(domains)
            trial[k] = 1 << choice
            if self.propagate(trial, [k]):
                result = self.search(trial)
                if result is not None:
                    return result
            self.backtracks += 1
        return None

    def solve(self):
        domains = list(self.domains)
        if not self.propagate(domains, range(len(domains))):
            raise PkError('CONFLICT', 'INFO LAYOUT exact overlap contradicts fixed cells/exits; no fallback or output')
        result = self.search(domains)
        if result is None:
            raise PkError('LIMIT' if self.steps >= self.max_steps else 'CONFLICT',
                          f'INFO LAYOUT no solution steps:{self.steps} backtracks:{self.backtracks} '
                          f'rejections:{dict(self.rejects)}; constraints unchanged, no output')
        letters, elevation = [[''] * self.w for _ in range(self.h)], [[''] * self.w for _ in range(self.h)]
        sources = []
        for k, mask in enumerate(result):
            i = next(ids(mask))
            x0, y0 = self.positions[k]
            source = self.vocab.sources[i][0]
            sources.append(dict(x=x0, y=y0, pattern=i, source=source))
            a, z = self.vocab.patterns[i]
            for dy in range(4):
                for dx in range(4):
                    x, y = x0 + dx, y0 + dy
                    if x < self.w and y < self.h:
                        if letters[y][x] and (letters[y][x], elevation[y][x]) != (a[dy * 4 + dx], z[dy * 4 + dx]):
                            raise AssertionError('overlap propagation lost shared-cell agreement')
                        letters[y][x], elevation[y][x] = a[dy * 4 + dx], z[dy * 4 + dx]
        rows = blockout.Rows([''.join(r) for r in letters])
        rows.elevation = [''.join(r) for r in elevation]
        return rows, sources


def generate_command(repo, tx, args, opts):
    if len(args) != 2 or set(opts) - {'type', 'exits', 'seed', 'out', 'keep', 'via'} or opts.get('type') not in ('route', 'town') or 'exits' not in opts:
        raise PkError('PARSE', 'usage: pk layout generate MAP --type route|town --exits N,S [--seed n] [--out FILE] [--keep FILE]')
    grid, pair = load(repo, args[1])
    maps, notes = corpus(repo, tx)
    excluded = holdout(maps, grid)
    limits = generation_limits(maps, opts['type'], excluded)
    vocab = Overlap(maps, opts['type'], excluded)
    keep = parse_keep(repo.read(opts['keep']).decode('utf-8-sig'), grid.w, grid.h) if opts.get('keep') else {}
    exits = exit_segments(grid.w, grid.h, opts['exits'].split(','))
    seed = int(opts.get('seed', 0))
    via = []
    if opts.get('via'):
        for text in opts['via'].split(';'):
            values = text.split(',')
            if len(values) != 2:
                raise PkError('PARSE', '--via x,y[;x,y...]')
            p = tuple(map(int, values))
            if not grid.inside(*p):
                raise PkError('BOUNDS', f'via {p} is outside map')
            via.append(p)
    masks = shape_table(maps, excluded)
    # Routing and chunk fill are one bounded search: an A* bend can be locally
    # walkable yet have no observed chunk. Backtrack the route, never its cells
    # inside an accepted layout, and keep every endpoint/owner constraint fixed.
    for plan in range(1, 17):
        network, edges = path_network(grid, exits, keep, via, seed, option=plan)
        trial_keep = dict(keep)
        for p in network:
            if p in keep and keep[p][0] != 'p':
                raise PkError('CONFLICT', f'path network conflicts with --keep at {p}')
            trial_keep[p] = ('p', keep[p][1] if p in keep else None)
        solver = ChunkSolver(vocab, grid.w, grid.h, exits, limits, trial_keep, seed, masks=masks)
        try:
            rows, sources = solver.solve()
            keep = trial_keep
            break
        except PkError as exc:
            if exc.code not in ('LIMIT', 'CONFLICT'):
                raise
            notes.append(f'INFO LAYOUT path plan:{plan} rejected by exact chunk/global constraints: {exc}')
            if plan == 16:
                raise PkError(exc.code, 'no joint route/chunk solution; no constraints relaxed or output written', notes) from None
    out = opts.get('out', f'.pk/chunks/{grid.name}_{seed}.blockout')
    if not out.replace('\\', '/').startswith('.pk/'):
        raise PkError('PARSE', 'generated blockouts must stay under .pk/')
    tx.stage(out, blockout.serialize(rows).encode())
    opened, mirror = blockout.letter_measure(grid, rows)
    stats = dict(map=grid.name, seed=seed, limits=limits, open=opened, mirror=mirror,
                 exits={s: sorted(ps) for s, ps in exits.items()}, steps=solver.steps, backtracks=solver.backtracks,
                 propagations=solver.propagations, rejections=dict(solver.rejects), excluded=list(excluded), sources=sources)
    stats['network'] = dict(width=paths.VANILLA_WIDTH_MEDIAN, straight_p95=paths.VANILLA_STRAIGHT_P95,
                            cells=sorted(network), mst=edges, via=via, plan=plan)
    tx.stage(out + '.json', (json.dumps(stats, indent=2) + '\n').encode())
    notes += [f'INFO LAYOUT path MST edges:{len(edges)} cells:{len(network)} width:{paths.VANILLA_WIDTH_MEDIAN} '
              f'straight_p95:{paths.VANILLA_STRAIGHT_P95} laid before chunk fill; missing masks:0',
              f'INFO LAYOUT generation exact patterns:{len(vocab.patterns)} chunks:{len(sources)} '
              f'steps:{solver.steps} backtracks:{solver.backtracks} rejections:{dict(solver.rejects)}',
              f'INFO LAYOUT constraints exits:{len(solver.exits)} connected water:one-or-none buildings:0 '
              f'overlap:letters+elevation keep:{len(keep)} padded:{solver.pw}x{solver.ph}',
              f'INFO LAYOUT anti-blandness open:{opened:.4%} <= vanilla {opts["type"]} p95:{limits["open"]:.4%} '
              f'mirror:{mirror:.4%} <= p95:{limits["mirror"]:.4%} calibration_maps:{limits["maps"]}', f'out {out}']
    notes += blockout.check(repo, grid, pair, rows)
    return Result(f'layout generate {grid.name}', notes)


# Clockwise N, NE, E, SE, S, SW, W, NW. Diagonals matter only when both
# touching cardinals match, yielding the standard 47 blob configurations.
MASK_OFFSETS = ((0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1))


def blob_mask(c, around):
    mask = sum(1 << i for i, other in enumerate(around) if c == other)
    for d, a, b in ((1, 0, 2), (3, 2, 4), (5, 4, 6), (7, 6, 0)):
        if not (mask & (1 << a) and mask & (1 << b)):
            mask &= ~(1 << d)
    return mask


def shape_keys(c, around):
    if c == 'p':
        return [('p', '*', blob_mask(c, around))]
    if c == 's' and '.' in around:
        return [('s', '.', blob_mask(c, around))]
    if c == '~':
        return [('~', other, blob_mask(c, around)) for other in '.s' if other in around]
    return []


def missing_shape(c, around, table):
    if c == 'p' and any(around[i] == 'p' for i in (1, 3, 5, 7)) and not any(around[i] == 'p' for i in (0, 2, 4, 6)):
        return True  # A diagonal-only path is not connected, irrespective of blob normalization.
    return any(not table.get(key) for key in shape_keys(c, around))


def around_cell(rows, x, y):
    return [rows[y + dy][x + dx] if 0 <= y + dy < len(rows) and 0 <= x + dx < len(rows[0]) else '_'
            for dx, dy in MASK_OFFSETS]


def shape_table(maps, exclude=()):
    table = Counter()
    for m in kept_maps(maps, exclude).values():
        rows = m['rows']
        for y, row in enumerate(rows):
            for x, c in enumerate(row):
                table.update(shape_keys(c, around_cell(rows, x, y)))
    return table


def missing_masks(rows, table, letters='ps~'):
    return [(x, y, c, blob_mask(c, around_cell(rows, x, y)))
            for y, row in enumerate(rows) for x, c in enumerate(row)
            if c in letters and missing_shape(c, around_cell(rows, x, y), table)]


def parse_file(text):
    lines = text.splitlines()
    body = lines[2:]
    h = body.index(blockout.ELEVATION_HEADER) if blockout.ELEVATION_HEADER in body else len(body)
    if not h:
        raise PkError('PARSE', 'empty blockout')
    return blockout.parse(text, len(body[0]), h)


def check_paths_command(repo, tx, args, opts):
    if len(args) != 2 or set(opts) - {'map'}:
        raise PkError('PARSE', 'usage: pk layout check-paths FILE [--map MAP]')
    maps, notes = corpus(repo, tx)
    excluded = ()
    if opts.get('map'):
        grid, _ = load(repo, opts['map'])
        excluded = holdout(maps, grid)
    rows = parse_file(repo.read(args[1]).decode('utf-8-sig'))
    table = shape_table(maps, excluded)
    missing = missing_masks(rows, table)
    for c, other in sorted({key[:2] for key in table}):
        notes.append(f'INFO LAYOUT masks {c}/{other}:{sum(key[:2] == (c, other) for key in table)}')
    notes += [f'WARN BLOCKOUT reshape {x},{y} (no vanilla tile for this path/edge shape) letter:{c} mask:0x{mask:02X}'
              for x, y, c, mask in missing]
    notes.append(f'INFO LAYOUT check-paths missing:{len(missing)} path_cells:{sum(r.count("p") for r in rows)} exclude:{list(excluded)}')
    return Result('layout check-paths', notes, ok=not missing, code='PARSE' if missing else 'OK')


def path_network(grid, exits, keep, via=(), seed=0, option=1):
    """Prim MST plus the existing directional A* with a measured straight ceiling.

    The width-two footprint is fixed BEFORE overlap solving. Fixed non-path
    keep cells cannot be overwritten even when they would otherwise be walkable.
    """
    width = paths.VANILLA_WIDTH_MEDIAN
    anchors = []
    for cells in exits.values():
        x, y = min(cells)
        anchors.append((min(x, grid.w - width), min(y, grid.h - width)))
    anchors += [(min(x, grid.w - width), min(y, grid.h - width)) for x, y in via]
    # Door metadata can be supplied by a future structure stage; do not erase it.
    anchors += [(min(e['x'], grid.w - width), min(e['y'], grid.h - width))
                for e in grid.data.get('warp_events') or []]
    anchors = sorted(set(anchors))
    allowed = {(x, y) for y in range(grid.h - width + 1) for x in range(grid.w - width + 1)
               if all(keep.get((x + dx, y + dy), ('p', None))[0] == 'p'
                      for dy in range(width) for dx in range(width))}
    if any(a not in allowed for a in anchors):
        raise PkError('CONFLICT', 'INFO LAYOUT a path endpoint is blocked by fixed keep cells; no carve fallback')
    joined = {anchors[0]}
    remaining = set(anchors[1:])
    network, edges = set(), []
    old_path = {p for p, (c, _) in keep.items() if c == 'p'}
    for a in anchors:
        network.update(paths._footprint(a, width))
    while remaining:
        _, a, b = min((abs(a[0] - b[0]) + abs(a[1] - b[1]), a, b) for a in joined for b in remaining)
        route = paths._astar(grid, {a}, {b}, allowed, str(seed), option, old_path | network, frozenset())
        for p in route:
            network.update(paths._footprint(p, width))
        edges.append(dict(start=a, end=b, anchors=route))
        joined.add(b)
        remaining.remove(b)
    return network, edges


def autotile_table(repo, grid, pair, maps=None, exclude=()):
    """Exact-mask tuples, same pair or compatible primary portability only."""
    if maps is None:
        # The tiler has an immutable export reference already; share it in memory.
        ref = blockout.reference(repo)
        maps = {}
        for name, stratum in vanilla_outdoor(ref):
            source, source_pair = load(ref, name)
            rows = blockout.export(ref, source, source_pair)
            maps[name] = dict(layout=source.data['layout'], rows=rows, stratum=stratum)
    target_layout = grid.data['layout']
    excluded = set(exclude) | {n for n, m in maps.items() if m['layout'] == target_layout}
    ref = blockout.reference(repo)
    key = pair.primary['symbol'], pair.secondary['symbol'], tuple(sorted(excluded))
    cache = ref.__dict__.setdefault('_chunk_autotile', {})
    if key in cache:
        return cache[key]
    table = defaultdict(Counter)
    classify = blockout.classifier(repo, pair)
    for name, m in sorted(kept_maps(maps, excluded).items()):
        source, other = load(ref, name)
        if other.primary['symbol'] != pair.primary['symbol']:
            continue
        own = other.secondary['symbol'] == pair.secondary['symbol']
        rows = m['rows']
        for y, row in enumerate(rows):
            for x, c in enumerate(row):
                cell = source.at(x, y)
                if not own and (cell[0] >= pair.limits['metatiles'] or classify.letter(cell) != c):
                    continue
                for mask in shape_keys(c, around_cell(rows, x, y)):
                    table[mask][cell] += 1
    cache[key] = table
    return table


def mask_domains(repo, grid, pair, rows, exclude=()):
    table = autotile_table(repo, grid, pair, exclude=exclude)
    domains, notes = {}, []
    for y, row in enumerate(rows):
        for x, c in enumerate(row):
            around = around_cell(rows, x, y)
            keys = shape_keys(c, around)
            if not keys:
                continue
            pools = [table.get(key, Counter()) for key in keys]
            common = set(pools[0]).intersection(*(set(p) for p in pools[1:]))
            if missing_shape(c, around, table):
                common = set()
            domains[x, y] = Counter({cell: sum(p[cell] for p in pools) for cell in common})
            if not common:
                notes.append(f'WARN BLOCKOUT reshape {x},{y} (no vanilla tile for this path/edge shape)')
    notes.append(f'INFO BLOCKOUT exact-mask domains:{len(domains)} missing:{sum(not d for d in domains.values())}')
    return domains, notes, table


def autotile_artifact(repo, maps):
    pairs = defaultdict(lambda: defaultdict(Counter))
    for name, m in sorted(maps.items()):
        source, pair = load(repo, name)
        key = pair.primary['symbol'] + '+' + pair.secondary['symbol']
        for y, row in enumerate(m['rows']):
            for x, c in enumerate(row):
                for letter, other, mask in shape_keys(c, around_cell(m['rows'], x, y)):
                    pairs[key][f'{letter}/{other}/{mask:02X}'][str(source.at(x, y)[0])] += 1
    shapes = shape_table(maps)
    notes = [f'INFO LAYOUT learned masks {c}/{other}:{sum(k[:2] == (c, other) for k in shapes)}'
             for c, other in sorted({k[:2] for k in shapes})]
    return dict(version=1, bits='N,NE,E,SE,S,SW,W,NW; diagonals require both touching cardinals',
                pairs=dict(pairs)), notes


def path_seams(grid, rows, rules):
    bad = set()
    for y, row in enumerate(rows):
        for x, c in enumerate(row):
            for dx, dy, table in ((1, 0, rules.h), (0, 1, rules.v)):
                nx, ny = x + dx, y + dy
                if nx >= grid.w or ny >= grid.h or 'p' not in (c, rows[ny][nx]):
                    continue
                a, b = grid.at(x, y)[0], grid.at(nx, ny)[0]
                if (a, b) not in table:
                    bad.add((x, y, nx, ny, a, b))
    return bad
