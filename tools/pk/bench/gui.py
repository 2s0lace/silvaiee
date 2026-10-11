"""Build the blind-test page: tools/pk/bench/blind/index.html (open it in a browser).

Two tabs, Maps and Dialogue. You score every entry without knowing the model. Reveal (per tab) shows the model
next to Claude's blind score and the model's self-rating. Your scores stay in the browser (localStorage);
"Copy results" puts a text table on the clipboard for pasting into the chat.
Run from repo root after blind.py and dialogue/blind.py: python tools/pk/bench/gui.py
"""
import base64
import glob
import json
import os
import re
import shutil

B = 'tools/pk/bench'
D = f'{B}/dialogue'


def read_key(path):
    key = {}
    if os.path.exists(path):
        for line in open(path, encoding='utf-8'):
            if ' = ' in line:
                letter, model = line.strip().split(' = ', 1)
                key[letter] = model
    return key


def load(path):
    return json.load(open(path, encoding='utf-8')) if os.path.exists(path) else {}


def uri(path):
    if not os.path.exists(path):
        return ''
    return 'data:image/png;base64,' + base64.b64encode(open(path, 'rb').read()).decode()


def parse_dialogue(path):
    voice, sections, cur = '', [], None
    for raw in open(path, encoding='utf-8'):
        line = raw.strip()
        m = re.fullmatch(r'\[(\w+)\]', line)
        if m:
            cur = {'id': m[1], 'texts': []}
            sections.append(cur)
            continue
        k, _, v = line.partition(':')
        k = k.strip().lower()
        if k == 'voice':
            voice = v.strip()
        elif cur and k in ('day', 'night', 'text'):
            cur['texts'].append({'kind': k, 'text': v.strip()})
    return {'voice': voice, 'sections': sections}


def self_ratings(paths, pattern):
    out = {}
    for p in paths:
        m = re.search(pattern, open(p, encoding='utf-8').read(), re.I | re.M)
        if m:
            out[os.path.splitext(os.path.basename(p))[0].lower()] = float(m[1])
    return out


map_key = read_key(f'{B}/blind_key.txt')
map_scores = load(f'{B}/scores.json')
if os.path.exists('.pk/render/PalletTown_Frlg.png'):
    shutil.copy('.pk/render/PalletTown_Frlg.png', f'{B}/blind/vanilla.png')
maps = {'key': map_key, 'claude': map_scores.get('claude', {}), 'notes': map_scores.get('claude_notes', {}),
        'self': {k.lower(): v for k, v in map_scores.get('self', {}).items()},
        'images': {L: uri(f'{B}/blind/{L}.png') for L in map_key}, 'vanilla': uri(f'{B}/blind/vanilla.png')}

dlg_key = read_key(f'{D}/blind_key.txt')
dlg_scores = load(f'{D}/scores.json')
dlg = {'key': dlg_key, 'claude': dlg_scores.get('claude', {}), 'notes': dlg_scores.get('claude_notes', {}),
       'self': self_ratings(glob.glob(f'{D}/out/*.txt'), r'^self:\s*([\d.]+)'),
       'texts': {L: parse_dialogue(f'{D}/blind/{L}.txt') for L in dlg_key if os.path.exists(f'{D}/blind/{L}.txt')}}

# Base64 so the key isn't readable at a glance in the page source.
data = base64.b64encode(json.dumps({'maps': maps, 'dlg': dlg}).encode()).decode()
html = open(f'{B}/gui_template.html', encoding='utf-8').read().replace('__DATA__', data)
os.makedirs(f'{B}/blind', exist_ok=True)
open(f'{B}/blind/index.html', 'w', encoding='utf-8').write(html)
print(f'OK {B}/blind/index.html (maps:{len(map_key)} dialogue:{len(dlg_key)})')
