"""pk text: dialogue as plain text in, wrapped `.string` blocks out."""
import json
import re

from ..core.errors import PkError
from ..core.out import Result, page
from ..core.repo import resolve
from ..index.builder import ensure
from .charmap import Charmap, glyph_widths, Measure
from .strings import blocks, replace, emit, structure, to_plain, wrap, overflows

LIMITS = 'tools/pk/data/text_limits.json'
TEXT_GLOBS = ('data/maps/*/scripts.inc', 'data/text/**/*.inc', 'data/scripts/**/*.inc')
# Assumed widths: PLAYER/RIVAL = PLAYER_NAME_LENGTH 7 x 6px; STR_VAR_n = 10 x 6px (item/species names).
DEFAULT_PLACEHOLDERS = {'PLAYER': 42, 'RIVAL': 42, 'STR_VAR_1': 60, 'STR_VAR_2': 60, 'STR_VAR_3': 60, 'KUN': 0}
NORMAL_FONTS = {'FONT_NORMAL', 'FONT_MALE', 'FONT_FEMALE'}


class Text:
    def __init__(self, repo, need_limits=True):
        self.repo = repo
        self.charmap = Charmap(repo.read('charmap.txt'))
        self.widths = glyph_widths(repo.read('src/fonts.c').decode('utf-8'))
        if need_limits:
            try:
                self.limits = json.loads(repo.read(LIMITS))
            except PkError:
                raise PkError('NOTFOUND', f'{LIMITS}; run pk text calibrate') from None
            self.limit = self.limits['line_px']
            self.lines_per_box = self.limits['lines_per_box']
            placeholders = self.limits['placeholder_px']
        else:
            placeholders = DEFAULT_PLACEHOLDERS
        self.measure = Measure(self.charmap, self.widths, placeholders)

    def font(self, source):
        fonts = {name for kind, name in self.charmap.tokens(source) if kind == 'ph' and name.startswith('FONT_')}
        other = fonts - NORMAL_FONTS
        return sorted(other)[0] if other else 'FONT_NORMAL'

    def files(self):
        root = self.repo.root
        found = set()
        for pattern in TEXT_GLOBS:
            found.update(p.relative_to(root).as_posix() for p in root.glob(pattern))
        return sorted(found)

    def locate(self, label):
        """(file, text, block) for a label; exact or unique fuzzy match via the index."""
        db = ensure(self.repo, self.repo.transaction)
        try:
            names = {name for (name,) in db.execute('SELECT name FROM texts')}
            if label not in names:
                label = resolve(label, {n: n for n in names})
            files = sorted({f for (f,) in db.execute('SELECT file FROM texts WHERE name=?', (label,))})
        finally:
            db.close()
        if len(files) != 1:
            raise PkError('AMBIGUOUS', f'{label} defined in {len(files)} files', files[:10])
        text = self.repo.read(files[0]).decode('utf-8')
        for block in blocks(text):
            if block.label == label:
                return files[0], text, block
        raise PkError('NOTFOUND', f'{label} has no .string block in {files[0]}; it may be a script label')

    def scope(self, target):
        if not target or target == 'all':
            return self.files()
        if '/' in target or target.endswith('.inc'):
            self.repo.read(target)
            return [target]
        name, _, _ = self.repo.map(target)
        return [f'data/maps/{name}/scripts.inc']

    def compose(self, plain, keep_breaks=False):
        if keep_breaks:
            source = plain if plain.endswith('$') else plain + '$'
            self.charmap.tokens(source)
            bad = overflows(source, self.measure, self.limit)
            if bad:
                b, n, w, _ = bad[0]
                raise PkError('TEXTFIT', f'box {b} line {n} is {w}px > {self.limit}px; rewrap without --keep-breaks')
            return source, structure(source)
        self.charmap.tokens(plain.replace('|', ' '))   # validate characters before wrapping
        return wrap(plain, self.measure, self.limit, self.lines_per_box)


def shape(boxes):
    lines = sum(len(b) for b in boxes)
    return f'{len(boxes)}box{"es" if len(boxes) != 1 else ""} {lines}line{"s" if lines != 1 else ""}'


def show(t, source):
    rows = []
    for b, box in enumerate(structure(source), 1):
        for n, line in enumerate(box):
            w = t.measure.text(line) if t.font(source) == 'FONT_NORMAL' else 0
            mark = f' !{w}px' if w > t.limit else ''
            # Output lines are whitespace-compacted, so number lines instead of indenting them.
            rows.append(f'[{b}.{n+1}] {line}{mark}')
    return rows


def command(repo, tx, args, opts, pagination):
    if not args:
        raise PkError('PARSE', 'usage: pk text ls|get|set|add|replace|grep|check|calibrate; see pk help text')
    verb, rest = args[0], args[1:]
    if verb == 'calibrate' and not rest:
        return calibrate(repo, tx)
    t = Text(repo)
    if verb == 'ls' and len(rest) == 1:
        rows = []
        for path in t.scope(rest[0]):
            for block in blocks(repo.read(path).decode('utf-8')):
                plain = to_plain(block.source)
                if opts.get('grep') and opts['grep'].casefold() not in plain.casefold():
                    continue
                rows.append(f'{block.label} {plain[:50]}')
        return page(f'text ls {len(rows)}', rows, *pagination(opts))
    if verb == 'get' and len(rest) == 1:
        path, text, block = t.locate(rest[0])
        font = t.font(block.source)
        return Result(f'text {block.label} {path}:{block.line+1} font:{font}', show(t, block.source))
    if verb == 'set' and len(rest) == 2:
        path, text, block = t.locate(rest[0])
        source, boxes = t.compose(rest[1], opts.get('keep-breaks', False))
        tx.stage(path, replace(text, block, source).encode('utf-8'))
        return Result(f'text {block.label} {shape(boxes)}', [source])
    if verb == 'add' and len(rest) == 3:
        target, label, plain = rest
        if not re.fullmatch(r'[A-Za-z_]\w*', label):
            raise PkError('PARSE', f'label {label!r} must be an identifier')
        db = ensure(repo, tx)
        try:
            taken = db.execute('SELECT 1 FROM texts WHERE name=? UNION SELECT 1 FROM scripts WHERE name=?',
                               (label, label)).fetchone()
        finally:
            db.close()
        if taken:
            raise PkError('CONFLICT', f'label {label} already exists; choose another or use text set')
        path = t.scope(target)[0] if target != 'all' else None
        if not path:
            raise PkError('PARSE', 'text add needs a map or .inc file, not all')
        source, boxes = t.compose(plain, opts.get('keep-breaks', False))
        text = repo.read(path).decode('utf-8')
        newline = '\r\n' if '\r\n' in text else '\n'
        if text and not text.endswith(newline):
            text += newline
        text += newline + emit(label, source, newline=newline)
        tx.stage(path, text.encode('utf-8'))
        return Result(f'text add {label} {path} {shape(boxes)}', [source])
    if verb == 'replace' and len(rest) == 2:
        try:
            pattern = re.compile(rest[0])
        except re.error as exc:
            raise PkError('PARSE', f'regex: {exc}') from None
        changed, samples, files = 0, [], 0
        for path in t.scope(opts.get('scope', 'all')):
            text = repo.read(path).decode('utf-8')
            original = text
            for block in reversed(blocks(text)):   # bottom-up keeps earlier line numbers valid
                plain = to_plain(block.source)
                new = pattern.sub(rest[1], plain)
                if new == plain:
                    continue
                source, _ = t.compose(new)
                text = replace(text, block, source)
                changed += 1
                if len(samples) < 3:
                    samples.append(f'{block.label} {new[:60]}')
            if text != original:
                files += 1
                tx.stage(path, text.encode('utf-8'))
        return Result(f'text replace {changed} texts {files} files', samples)
    if verb == 'grep' and len(rest) == 1:
        try:
            pattern = re.compile(rest[0], re.I)
        except re.error as exc:
            raise PkError('PARSE', f'regex: {exc}') from None
        db = ensure(repo, tx)
        try:
            rows = []
            for name, file, line, snippet in db.execute('SELECT name,file,line,snippet FROM texts ORDER BY file,line'):
                match = pattern.search(snippet)
                if match:
                    start = max(0, match.start() - 20)
                    rows.append(f'{name} {file}:{line} {snippet[start:start+60]}')
        finally:
            db.close()
        return page(f'text grep {len(rows)}', rows, *pagination(opts, 10))
    if verb == 'check' and len(rest) <= 1:
        rows = []
        for path in t.scope(rest[0] if rest else 'all'):
            for block in blocks(repo.read(path).decode('utf-8')):
                if t.font(block.source) != 'FONT_NORMAL':
                    continue
                for b, n, w, est in overflows(block.source, t.measure, t.limit, opts.get('estimates', False)):
                    rows.append(f'{block.label} box{b} line{n} {"~" if est else ""}{w}px {path}:{block.line+1}')
        kind = 'incl. ~estimated placeholder lines' if opts.get('estimates') else 'definite; --estimates adds placeholder lines'
        return page(f'text check {len(rows)} overflow lines limit {t.limit}px {kind}', rows, *pagination(opts))
    raise PkError('PARSE', f'usage error for text {verb}; see pk help text')


def calibrate(repo, tx):
    """Measure every vanilla FONT_NORMAL line without placeholders; the limit is the
    widest width that occurs at least 5 times (isolated wider lines are treated as outliers)."""
    t = Text(repo, need_limits=False)
    counts, widest = {}, []
    for path in t.files():
        for block in blocks(repo.read(path).decode('utf-8')):
            if t.font(block.source) != 'FONT_NORMAL':
                continue
            for box in structure(block.source):
                for line in box:
                    if '{' in line:
                        continue
                    w = t.measure.text(line)
                    counts[w] = counts.get(w, 0) + 1
                    widest.append((w, block.label))
    limit = max(w for w, c in counts.items() if c >= 5)
    outliers = sorted({label for w, label in widest if w > limit})
    data = dict(font='FONT_NORMAL', line_px=limit, lines_per_box=2, placeholder_px=DEFAULT_PLACEHOLDERS,
                lines_measured=sum(counts.values()), lines_at_limit=counts.get(limit, 0),
                max_seen_px=max(counts), outliers=outliers,
                source='pk text calibrate: vanilla .string lines, gFontNormalLatinGlyphWidths, charmap.txt')
    tx.stage(LIMITS, (json.dumps(data, indent=2, sort_keys=True) + '\n').encode('utf-8'))
    return Result(f'text calibrate line_px:{limit} lines:{data["lines_measured"]} at_limit:{data["lines_at_limit"]}',
                  [f'outlier {label}' for label in outliers[:5]])
