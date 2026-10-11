"""Gate check for the dialogue benchmark: python tools/pk/bench/dialogue/check.py [Model ...]

Reads tools/pk/bench/dialogue/out/<Model>.txt and prints FAIL and WARN lines plus a summary per model.
FAIL lines break the gate:
- a missing section or line;
- text that won't wrap into the real font (208 px, 2 lines per box) or uses characters the game doesn't have;
- more than 3 boxes for one text;
- policy: item gifts, quest requests;
- a copied vanilla line (8 or more words in a row);
- night text that is just the day text.
WARN lines are style: game terms not written in CAPS, modern slang, and box counts.
"""
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', '..')))
from pk.core.errors import PkError          # noqa: E402
from pk.core.repo import Repo               # noqa: E402
from pk.text.ops import Text                # noqa: E402
from pk.text.strings import wrap, to_plain, blocks  # noqa: E402

REQUIRED = {'old_timer': ('day', 'night'), 'kid': ('day',), 'pond_lady': ('day', 'night'),
            'league_hiker': ('day',), 'tips_sign': ('text',)}
CAPS = ['POKéMON', 'POKé BALL', 'POKé MART', 'POKéMON CENTER', 'GYM', 'GYM LEADER', 'VIRIDIAN CITY',
        'VIRIDIAN FOREST', 'PALLET TOWN', 'PEWTER CITY', 'POKéMON LEAGUE', 'INDIGO PLATEAU', 'PROF. OAK',
        'TEAM ROCKET', 'TRAINER', 'TRAINER TIPS', 'BADGE', 'ROUTE']
POLICY = [r'\b(here,? )?(take|have) (this|these|it)\b', r'\bgive (you|ya)\b', r'\bfor you\b.*\b(item|potion|ball)\b',
          r'\bcould you (please )?(find|bring|get|deliver|help)\b', r'\bbring (me|it back)\b', r'\bquest\b',
          r'\breceived\b', r'\bobtained\b']
SLANG = r'\b(lol|lmao|omg|vibes?|literally|bro|dude|sus|lit|cringe|okay so|ngl|tbh|gonna|wanna|kinda)\b'


def parse(path):
    sections, current, voice, self_rating = {}, None, None, None
    for raw in open(path, encoding='utf-8'):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        m = re.fullmatch(r'\[(\w+)\]', line)
        if m:
            current = m[1]
            sections[current] = {}
            continue
        key, _, value = line.partition(':')
        key, value = key.strip().lower(), value.strip()
        if key == 'voice':
            voice = value
        elif key == 'self':
            self_rating = value
        elif current and key in ('day', 'night', 'text'):
            sections[current][key] = value
    return voice, sections, self_rating


def vanilla_windows(repo, text):
    """Set of 8-word windows from every vanilla string, for spotting copied lines."""
    windows = set()
    for f in text.files():
        try:
            src = repo.read(f).decode('utf-8', 'replace')
        except PkError:
            continue
        for block in blocks(src):
            words = re.findall(r"[a-z']+", to_plain(block.source).lower())
            windows.update(' '.join(words[i:i + 8]) for i in range(len(words) - 7))
    return windows


def check(repo, text, windows, model):
    path = os.path.join(HERE, 'out', f'{model}.txt')
    if not os.path.exists(path):
        return [f'FAIL {model}: no file out/{model}.txt'], 1, 0
    voice, sections, self_rating = parse(path)
    out, fails, warns = [], 0, 0
    def fail(msg):
        nonlocal fails; fails += 1; out.append(f'FAIL {msg}')
    def warn(msg):
        nonlocal warns; warns += 1; out.append(f'WARN {msg}')
    if not voice:
        fail('missing "voice:" line')
    if self_rating is None:
        warn('missing "self:" line')
    for sec, keys in REQUIRED.items():
        for k in keys:
            if not sections.get(sec, {}).get(k):
                fail(f'[{sec}] missing {k}:')
    for sec, lines in sections.items():
        for k, value in lines.items():
            where = f'[{sec}] {k}'
            try:
                _, boxes = wrap(value, text.measure, text.limit, text.lines_per_box)
                nboxes = sum((len(b) + 1) // 2 for b in boxes)
                if nboxes > 3:
                    fail(f'{where}: {nboxes} boxes (max 3)')
            except PkError as exc:
                fail(f'{where}: {exc.message if hasattr(exc, "message") else exc}')
            except KeyError as exc:
                fail(f'{where}: character {exc} does not exist in the game font')
            low = value.lower()
            for pat in POLICY:
                if re.search(pat, low):
                    fail(f'{where}: policy (item gift or quest?): "{re.search(pat, low)[0]}"')
            words = re.findall(r"[a-z']+", low)
            for i in range(len(words) - 7):
                if ' '.join(words[i:i + 8]) in windows:
                    fail(f'{where}: copies a vanilla line: "{" ".join(words[i:i + 8])}"')
                    break
            for term in CAPS:
                for m in re.finditer(re.escape(term).replace('é', '[ée]'), value, re.I):
                    if m[0] != term:
                        warn(f'{where}: write "{term}", not "{m[0]}"')
            if re.search(SLANG, low):
                warn(f'{where}: modern slang "{re.search(SLANG, low)[0]}"')
        if 'day' in lines and 'night' in lines:
            a = set(re.findall(r"[a-z']+", lines['day'].lower()))
            b = set(re.findall(r"[a-z']+", lines['night'].lower()))
            if a and b and len(a & b) / len(a | b) > 0.6:
                fail(f'[{sec}] night text is almost the same as the day text')
    return out, fails, warns


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    repo = Repo.discover(HERE)
    text = Text(repo)
    windows = vanilla_windows(repo, text)
    models = sys.argv[1:] or sorted(os.path.basename(p)[:-4] for p in glob.glob(os.path.join(HERE, 'out', '*.txt')))
    for model in models:
        out, fails, warns = check(repo, text, windows, model)
        for line in out:
            print(f'  {line}')
        print(f'{"OK" if not fails else "FAIL"} {model} gate:{"pass" if not fails else "fail"} fails:{fails} warns:{warns}')


if __name__ == '__main__':
    main()
