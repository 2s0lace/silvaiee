import difflib
import re

from .builder import TABLES
from ..core.out import page, compact
from ..core.errors import PkError


def find(db, query, kind=None, offset=0, limit=20):
    if not query.strip():
        raise PkError('PARSE', 'find needs nonempty query')
    if kind and kind not in TABLES:
        raise PkError('PARSE', 'unknown find kind; use pk help find')
    q = query.casefold()
    hits = []
    for k, table in TABLES.items():
        if kind and k != kind:
            continue
        for name, file, line, snippet in db.execute(f'SELECT name,file,line,snippet FROM {table}'):
            n = name.casefold()
            rank = 0 if n == q else 1 if n.startswith(q) else 2 if q in n else 3 if q in snippet.casefold() else 4
            if rank == 4 and difflib.SequenceMatcher(None, q, n).ratio() < .8:
                continue
            hits.append((rank, k, name, file, line, snippet[:60]))
    hits.sort()
    return page(f'find {len(hits)}', [f'{k} {n} {f}:{line} {s}'.rstrip() for _, k, n, f, line, s in hits], offset, limit)


def refs(repo, db, query, offset=0, limit=18):
    if not re.fullmatch(r'[A-Za-z_]\w*', query):
        raise PkError('PARSE', 'refs needs a label or constant identifier')
    pattern = re.compile(r'(?<!\w)' + re.escape(query) + r'(?!\w)')
    rows = []
    for (path,) in db.execute('SELECT path FROM sources ORDER BY path'):
        if path.endswith('.json') or path == 'charmap.txt':
            # Map event/header references are intentional; charmap cannot refer to labels.
            if path == 'charmap.txt':
                continue
        for n, line in enumerate(repo.read(path).decode('utf-8-sig').splitlines(), 1):
            if pattern.search(line):
                if re.match(r'^\s*(?:#define\s+|\.equ\s+)' + re.escape(query) + r'\b', line) or re.match(r'^'+re.escape(query)+':', line):
                    continue
                rows.append(f'{path}:{n} {compact(line)[:60]}')
    # Status + 18 matches + MORE <= the specification's 20-line budget.
    return page(f'refs {query} {len(rows)}', rows, offset, limit, maximum=18)
