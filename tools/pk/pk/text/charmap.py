"""charmap.txt and src/fonts.c: which characters exist and how wide they are."""
import re

from ..core.errors import PkError

CHAR = re.compile(r"^'((?:\\.|[^'\\])+)'\s*=\s*([0-9A-Fa-f]{2}(?:\s+[0-9A-Fa-f]{2})*)")
NAME = re.compile(r'^([A-Za-z_]\w*)\s*=\s*([0-9A-Fa-f]{2}(?:\s+[0-9A-Fa-f]{2})*)')
CONTROLS = {'\\n': 'n', '\\l': 'l', '\\p': 'p'}
# Typographic characters LLMs tend to produce, mapped to what the game font has.
SUGGEST = {'"': '“ or ”', '`': "'", '–': '-', '—': '-', '\t': ' '}


class Charmap:
    def __init__(self, raw):
        self.chars, self.names = {}, {}
        for line in raw.decode('utf-8-sig').splitlines():
            match = CHAR.match(line)
            if match:
                key = match[1]
                if key in CONTROLS:
                    continue
                if len(key) == 2 and key[0] == '\\':
                    key = key[1]
                # First definition wins: the Latin block precedes the Japanese duplicates.
                self.chars.setdefault(key, bytes.fromhex(match[2].replace(' ', '')))
                continue
            match = NAME.match(line)
            if match:
                self.names.setdefault(match[1], bytes.fromhex(match[2].replace(' ', '')))
        if not self.chars:
            raise PkError('PARSE', 'charmap.txt has no character entries')
        self.maxlen = max(len(k) for k in self.chars)

    def tokens(self, source):
        """Tokenize .string source text (escaped form): char, ctrl, ph (placeholder), end."""
        out, i = [], 0
        while i < len(source):
            c = source[i]
            if c == '\\':
                pair = source[i:i+2]
                if pair in CONTROLS:
                    out.append(('ctrl', CONTROLS[pair]))
                elif pair[1:] in self.chars:
                    out.append(('char', pair[1:]))
                else:
                    raise PkError('PARSE', f'unknown escape {pair}; use \\n \\l \\p or a charmap character')
                i += 2
            elif c == '{':
                j = source.find('}', i)
                if j < 0:
                    raise PkError('PARSE', 'unclosed { placeholder')
                name = source[i+1:j]
                if not name or name.split()[0] not in self.names:
                    raise PkError('PARSE', f'unknown placeholder {{{name}}}; see charmap.txt names')
                out.append(('ph', name))
                i = j + 1
            elif c == '$':
                out.append(('end', '$'))
                i += 1
            else:
                for size in range(self.maxlen, 0, -1):
                    key = source[i:i+size]
                    if key in self.chars:
                        out.append(('char', key))
                        i += size
                        break
                else:
                    hint = SUGGEST.get(c)
                    raise PkError('PARSE', f'character {c!r} not in charmap.txt' + (f'; use {hint}' if hint else ''))
        return out


def glyph_widths(fonts_c, table='gFontNormalLatinGlyphWidths'):
    match = re.search(r'const u8 %s\[\]\s*=\s*\{(.*?)\};' % re.escape(table), fonts_c, re.S)
    if not match:
        raise PkError('NOTFOUND', f'{table} in src/fonts.c')
    return [int(x) for x in re.findall(r'\d+', match[1])]


class Measure:
    """Pixel width of tokens in one font; placeholders use assumed widths."""
    def __init__(self, charmap, widths, placeholders):
        self.charmap, self.widths, self.placeholders = charmap, widths, placeholders

    def token(self, token):
        kind, value = token
        if kind == 'char':
            code = self.charmap.chars[value]
            return self.widths[code[0]] if len(code) == 1 and code[0] < len(self.widths) else 0
        if kind == 'ph':
            return self.placeholders.get(value.split()[0], 0)
        return 0

    def text(self, source):
        return sum(self.token(t) for t in self.charmap.tokens(source))
