"""Locate, render, wrap and re-emit `.string` blocks without touching surrounding bytes."""
from dataclasses import dataclass
import re

from ..core.errors import PkError

LABEL_LINE = re.compile(r'^([A-Za-z_]\w*)::?\s*$')
STRING_LINE = re.compile(r'^(\s*)\.string\s+"((?:[^"\\]|\\.)*)"\s*$')
BREAK = re.compile(r'\\[nlp]')


@dataclass
class Block:
    label: str
    line: int      # 0-based index of the label line
    first: int     # first .string line
    end: int       # one past the last .string line
    source: str    # concatenated .string contents, escaped as in the file
    indent: str


def blocks(text):
    lines = text.split('\n')
    found = []
    for i, line in enumerate(lines):
        match = LABEL_LINE.match(line.rstrip('\r'))
        if not match:
            continue
        j, parts, indent = i + 1, [], '\t'
        while j < len(lines):
            string = STRING_LINE.match(lines[j].rstrip('\r'))
            if not string:
                break
            if not parts:
                indent = string[1] or '\t'
            parts.append(string[2])
            j += 1
        if parts:
            found.append(Block(match[1], i, i + 1, j, ''.join(parts), indent))
    return found


def replace(text, block, source):
    """Swap one block's .string lines; every other byte of the file is preserved."""
    lines = text.split('\n')
    crlf = lines[block.first].endswith('\r')
    pieces = [p for p in re.split(r'(?<=\\[nlp])', source) if p]
    new = [f'{block.indent}.string "{p}"' + ('\r' if crlf else '') for p in pieces]
    return '\n'.join(lines[:block.first] + new + lines[block.end:])


def emit(label, source, indent='\t', newline='\n'):
    pieces = [p for p in re.split(r'(?<=\\[nlp])', source) if p]
    return newline.join([f'{label}::'] + [f'{indent}.string "{p}"' for p in pieces]) + newline


def structure(source):
    """Boxes (split on \\p) of lines (split on \\n / \\l), '$' removed."""
    body = source[:-1] if source.endswith('$') else source
    return [BREAK.split(box) for box in body.split('\\p')]


def to_plain(source):
    """Inverse of the input language: '||' new box, words joined with spaces."""
    return ' || '.join(' '.join(line.strip() for line in box if line.strip()) for box in structure(source))


def wrap(plain, measure, limit, lines_per_box=2):
    """Greedy pixel-width word wrap.

    Input: '||' starts a new box (\\p), '|' forces a line break, other whitespace joins words.
    Output follows vanilla style: line 2 of a box after \\n, further lines scroll with \\l.
    """
    if '\\' in plain or '$' in plain:
        raise PkError('PARSE', 'plain text cannot contain \\ or $; use | and || for breaks or --keep-breaks')
    space = measure.text(' ')
    boxes = []
    for box in re.split(r'\s*\|\|\s*', plain.strip()):
        if not box:
            raise PkError('PARSE', 'empty box; remove the extra ||')
        lines = []
        for forced in re.split(r'\s*\|\s*', box):
            current, width = '', 0
            for word in re.findall(r'(?:\{[^}]*\}|[^\s{])+', forced):   # {COLOR BLUE} stays one word
                w = measure.text(word)
                if w > limit:
                    raise PkError('TEXTFIT', f'word {word!r} is {w}px, line limit {limit}px; shorten it')
                if current and width + space + w <= limit:
                    current, width = current + ' ' + word, width + space + w
                elif current:
                    lines.append(current)
                    current, width = word, w
                else:
                    current, width = word, w
            lines.append(current)
        boxes.append(lines)
    joined = []
    for lines in boxes:
        text = lines[0]
        for k, line in enumerate(lines[1:], 1):
            text += ('\\n' if k < lines_per_box else '\\l') + line
        joined.append(text)
    return '\\p'.join(joined) + '$', boxes


def overflows(source, measure, limit, estimates=True):
    """[(box, line, width, estimated)] for lines wider than the limit (1-based).
    Lines with placeholders ({PLAYER}, {STR_VAR_1}) use assumed widths: estimated=True."""
    bad = []
    for b, box in enumerate(structure(source), 1):
        for n, line in enumerate(box, 1):
            w = measure.text(line)
            estimated = '{' in line
            if w > limit and (estimates or not estimated):
                bad.append((b, n, w, estimated))
    return bad
