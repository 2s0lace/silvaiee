"""Blueprint (.bp) parser (SPEC §14.2). Produces a plain dict; never touches the repo."""
import re
import shlex

from ..core.errors import PkError

SECTIONS = {'terrain', 'structures', 'npcs', 'signs', 'warps', 'triggers', 'connections', 'texts', 'items',
            'trainers', 'vanilla', 'declare', 'scripts', 'gates', 'raw', 'notes'}
HEADER = {'blueprint', 'base', 'size', 'tilesets', 'header', 'intent'}


def words(line, where):
    try:
        return shlex.split(line, posix=True)
    except ValueError as exc:
        raise PkError('PARSE', f'{where}: {exc}') from None


def kv(items, where):
    out, pos, rest = {}, None, []
    for w in items:
        if w.startswith('@'):
            pos = w[1:]
        elif '=' in w and re.match(r'^[a-z_]+=', w):
            k, v = w.split('=', 1)
            out[k] = v
        else:
            rest.append(w)
    return out, pos, rest


def parse(text, path='blueprint'):
    bp = dict(path=path, header={}, sections={s: [] for s in SECTIONS}, kitbash=[])
    section = None
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        n = i + 1
        i += 1
        if not raw.strip() or raw.lstrip().startswith('#'):
            continue
        where = f'{path}:{n}'
        if not raw.startswith(' '):
            head = raw.split('#', 1)[0].strip() if not raw.startswith('intent') else raw.strip()
            first = head.split()[0]
            if first in SECTIONS:
                section = first
                if first == 'raw' and len(head.split()) > 1:      # "raw x,y" starts a patch
                    bp['sections']['raw'].append((n, 'at ' + head.split()[1]))
                continue
            if first not in HEADER:
                raise PkError('PARSE', f'{where}: unknown top-level line {first!r}')
            section = None
            w = words(head, where)
            if first == 'blueprint':
                bp['map'] = w[1]
                bp['version'] = w[2] if len(w) > 2 else 'v1'
            elif first == 'base':
                bp['base'] = w[1]
            elif first == 'size':
                m = re.fullmatch(r'(\d+)x(\d+)', w[1])
                if not m:
                    raise PkError('PARSE', f'{where}: size WxH')
                bp['size'] = (int(m[1]), int(m[2]))
                opts = dict(x.split('=', 1) for x in w[2:] if '=' in x)
                bp['grow'] = {k: int(v) for k, v in (g.split(':') for g in opts.get('grow', '').split(',') if g)}
                bp['fill'] = opts.get('fill', 'extend')
            elif first == 'tilesets':
                bp['tilesets'] = (w[1], w[2])
            elif first == 'header':
                bp['header'].update(dict(x.split('=', 1) for x in w[1:]))
            elif first == 'intent':
                bp['intent'] = ' '.join(w[1:])
            continue
        if section is None:
            raise PkError('PARSE', f'{where}: indented line outside a section')
        body = raw.strip()
        if section == 'notes':
            bp['sections']['notes'].append(body)
            continue
        if section in ('scripts', 'raw'):
            bp['sections'][section].append((n, raw[2:] if raw.startswith('  ') else raw))
            continue
        comment_free = re.sub(r'\s+#(?![0-9A-Fa-f]{6}\b).*$', '', body)   # keep #rrggbb, drop comments
        tokens = words(comment_free, where)
        if section == 'terrain' and tokens and tokens[0] in ('copy', 'auto'):
            box_pattern = r'\d+,\d+,[1-9]\d*x[1-9]\d*'
            if tokens[0] == 'copy':
                if len(tokens) != 4 or not re.fullmatch(box_pattern, tokens[2]) or not re.fullmatch(r'@\d+,\d+', tokens[3]):
                    raise PkError('PARSE', f'{where}: copy SRC_MAP x,y,WxH @tx,ty')
            elif len(tokens) != 2 or not re.fullmatch(box_pattern, tokens[1]):
                raise PkError('PARSE', f'{where}: auto x,y,WxH')
            bp['kitbash'].append((n, tokens))
        bp['sections'][section].append((n, tokens))
    if 'map' not in bp:
        raise PkError('PARSE', f'{path}: first line must be "blueprint <MapName> v<N>"')
    return bp


def entities(bp, section):
    """[(line, id, kind_or_first_word, fields, position, rest_words)] for event-like sections."""
    out = []
    for n, w in bp['sections'][section]:
        if not w:
            continue
        ident, *rest = w
        fields, pos, plain = kv(rest, f"{bp['path']}:{n}")
        out.append(dict(line=n, id=ident, fields=fields, pos=pos, words=plain))
    return out
