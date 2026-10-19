"""Vanilla outdoor chunk vocabulary and layout calibration, with layout-level holdout.

Two disjoint grids start at (0,0) and (2,2). Only complete 4x4 chunks are
observations; padding would invent vanilla evidence. Near duplicates join a
representative within Hamming distance two, never a transitive chain.
"""
from collections import Counter, defaultdict
import hashlib
import json
import math

from ..core.errors import PkError
from ..core.out import Result
from . import blockout
from .edit import load
from .lint import vanilla_outdoor

SIZE = 4
OFFSETS = (0, 2)
VERSION = 1
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
            if path.is_file() and '__pycache__' not in path.parts:
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
        maps[name] = dict(layout=grid.data['layout'], stratum=stratum, rows=list(rows), elevation=rows.elevation)
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
    if not args or args[0] not in ('learn', 'score', 'calibrate'):
        raise PkError('PARSE', 'usage: pk layout learn | score MAP FILE | calibrate (generation pending calibration)')
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
    passed = interval is not None and interval[0] > coast_seam
    if interval is None:
        notes.append('INFO LAYOUT vanilla median seam uncertainty unmeasured: insufficient maps for a 95% interval')
    else:
        notes.append(f'INFO LAYOUT vanilla median seam 95% interval:{interval[0]:.4%}..{interval[1]:.4%}')
    notes.append(f'INFO LAYOUT target vanilla p50 seam clearly higher than coast: {"PASS" if passed else "FAIL"} '
                 f'margin:{margin:.4%}; positive gap alone is insufficient, interval lower bound must exceed coast')
    if not passed:
        notes.append('INFO LAYOUT STOP at Part B target miss; generation and end-to-end briefs not attempted')
    tx.stage('.pk/chunks/calibration.json', (json.dumps(dict(vanilla=vanilla, limits=limits,
             comparisons=comparisons, seam_margin=margin, median_seam_interval_95=interval, passed=passed), indent=2) + '\n').encode())
    return Result('layout calibration' + ('' if passed else ' STOP: Part B target missed'), notes,
                  ok=passed, code='PARSE' if not passed else 'OK')
