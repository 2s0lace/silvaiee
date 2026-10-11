import difflib
import json
from pathlib import Path
import re

from .errors import PkError


def resolve(query, aliases):
    """Aliases map names to canonical IDs; never choose between plausible matches."""
    if query in aliases:
        return aliases[query]
    q = query.casefold()
    matches = {v for k, v in aliases.items() if k.casefold() == q}
    if not matches:
        matches = {v for k, v in aliases.items() if q in k.casefold()}
    if not matches:
        normalized = lambda s: re.sub(r'[^a-z0-9]', '', s.casefold())
        matches = {v for k, v in aliases.items()
                   if difflib.SequenceMatcher(None, normalized(query), normalized(k)).ratio() >= .8}
    if len(matches) == 1:
        return next(iter(matches))
    if matches:
        raise PkError('AMBIGUOUS', f'{query}; use exact name', sorted(matches)[:10])
    raise PkError('NOTFOUND', f'{query}; use pk find')


class JsonDocument:
    """Retain original bytes for no-op saves and original newline style for edits."""
    def __init__(self, raw):
        self.raw = raw
        self.value = json.loads(raw)
        self.original = json.loads(raw)

    def emit(self):
        if self.value == self.original:
            return self.raw
        newline = '\r\n' if b'\r\n' in self.raw else '\n'
        suffix = newline if self.raw.endswith(b'\n') else ''
        if isinstance(self.value, dict) and list(self.value) == list(self.original):
            # Replace only changed top-level value spans; preserve all other whitespace.
            text = self.raw.decode('utf-8')
            decoder = json.JSONDecoder()
            position = text.index('{') + 1
            replacements = []
            while True:
                while text[position].isspace() or text[position] == ',':
                    position += 1
                if text[position] == '}':
                    break
                key, position = decoder.raw_decode(text, position)
                while text[position].isspace() or text[position] == ':':
                    position += 1
                start = position
                _, position = decoder.raw_decode(text, position)
                if self.value[key] != self.original[key]:
                    value = json.dumps(self.value[key], ensure_ascii=False, indent=2)
                    value = value.replace('\n', newline + '  ')
                    replacements.append((start, position, value))
            for start, end, value in reversed(replacements):
                text = text[:start] + value + text[end:]
            return text.encode('utf-8')
        return (json.dumps(self.value, ensure_ascii=False, indent=2).replace('\n', newline) + suffix).encode('utf-8')


class Repo:
    def __init__(self, root, transaction=None):
        self.root = Path(root).resolve()
        self.transaction = transaction

    @classmethod
    def discover(cls, start=None):
        start = Path(start or __file__).resolve()
        for root in [start, *start.parents]:
            if (root / 'data/layouts/layouts.json').is_file() and (root / 'build_fr.sh').is_file():
                return cls(root)
        raise PkError('NOTFOUND', 'repository root; run the project tools/pk/pk.py')

    def path(self, relative):
        candidate = self.root / relative
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root) or resolved == self.root:
            raise PkError('PARSE', f'path must be inside repository: {relative}')
        # Reject links/junctions even when they point within the repository.
        for part in [candidate, *candidate.parents]:
            if part == self.root:
                break
            if part.is_symlink() or part.is_junction():
                raise PkError('PARSE', f'linked path unsupported: {relative}')
        return resolved

    def read(self, relative):
        if self.transaction:
            return self.transaction.read(relative)
        try:
            return self.path(relative).read_bytes()
        except FileNotFoundError:
            raise PkError('NOTFOUND', f'{relative}; restore missing source') from None

    def json(self, relative):
        try:
            return JsonDocument(self.read(relative)).value
        except (ValueError, UnicodeError) as exc:
            raise PkError('PARSE', f'{relative}: {exc}') from None

    def layouts(self):
        return {x['id']: x for x in self.json('data/layouts/layouts.json')['layouts']}

    def maps(self):
        return {p.parent.name: self.json(p.relative_to(self.root).as_posix())
                for p in sorted((self.root / 'data/maps').glob('*/map.json'))}

    def map(self, query):
        # Fast path: an exact map directory name needs only its own map.json (maps() reads 939).
        if re.fullmatch(r'\w+', query) and (self.root / 'data/maps' / query / 'map.json').is_file():
            data = self.json(f'data/maps/{query}/map.json')
            if data.get('name', query) == query:
                return query, data, self.layouts()[data['layout']]
        maps = self.maps()
        aliases = {name: name for name in maps}
        by_layout = {}
        for name, data in maps.items():
            aliases[data['id']] = name
            by_layout.setdefault(data['layout'], []).append(name)
        if query in by_layout and len(by_layout[query]) > 1:
            raise PkError('AMBIGUOUS', f'{query} shared layout; choose map', sorted(by_layout[query])[:10])
        aliases.update({k: v[0] for k, v in by_layout.items() if len(v) == 1})
        name = resolve(query, aliases)
        return name, maps[name], self.layouts()[maps[name]['layout']]

    @staticmethod
    def version(layout):
        version = layout.get('layout_version')
        if version not in ('emerald', 'frlg'):
            raise PkError('PARSE', f"{layout['id']} needs explicit layout_version emerald|frlg")
        return version

    def structural_check(self):
        layouts, maps = self.layouts(), self.maps()
        used = {data['layout'] for data in maps.values()}
        for layout in layouts.values():
            version = self.version(layout)
            if layout['id'] not in used:
                continue
            w, h = layout['width'], layout['height']
            if not isinstance(w, int) or not isinstance(h, int) or w <= 0 or h <= 0:
                raise PkError('BOUNDS', f"{layout['id']} needs positive dimensions")
            for field, count in [('blockdata_filepath', w*h), ('border_filepath',
                    layout.get('border_width', 2)*layout.get('border_height', 2))]:
                if len(self.read(layout[field])) != count*2:
                    raise PkError('PARSE', f"{layout[field]} byte count does not match dimensions")
        for name, data in maps.items():
            if data['layout'] not in layouts:
                raise PkError('NOTFOUND', f"{name} layout {data['layout']}; repair map header")
        groups = self.json('data/maps/map_groups.json')
        for group in groups['group_order']:
            for name in groups[group]:
                if name not in maps:
                    raise PkError('NOTFOUND', f'{group} map {name}; repair map_groups.json')
        return len(maps), len(layouts)
