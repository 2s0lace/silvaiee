"""Copy NEW dialogue outputs to tools/pk/bench/dialogue/blind/<letter>.txt in random order, without the self line.

Outputs that already have a letter keep it. The key is in tools/pk/bench/dialogue/blind_key.txt; keep it hidden
from the reviewer until the scores are in. Run from repo root: python tools/pk/bench/dialogue/blind.py
"""
import glob
import os
import random
import string

D = 'tools/pk/bench/dialogue'
KEY = f'{D}/blind_key.txt'
key = {}
if os.path.exists(KEY):
    for line in open(KEY, encoding='utf-8'):
        if ' = ' in line:
            letter, model = line.strip().split(' = ', 1)
            key[letter] = model
done = {m.lower() for m in key.values()}
files = {os.path.basename(p)[:-4]: p for p in glob.glob(f'{D}/out/*.txt') if not os.path.basename(p).startswith('_')}
new = [m for m in files if m.lower() not in done]
random.shuffle(new)
os.makedirs(f'{D}/blind', exist_ok=True)
free = [c for c in string.ascii_uppercase if c not in key]
added = []
for letter, m in zip(free, new):
    lines = [l for l in open(files[m], encoding='utf-8') if not l.lower().startswith('self:')]
    open(f'{D}/blind/{letter}.txt', 'w', encoding='utf-8').write(''.join(lines))
    key[letter] = m
    added.append(letter)
open(KEY, 'w', encoding='utf-8').write(''.join(f'{k} = {v}\n' for k, v in sorted(key.items())))
print(f'new: {" ".join(added) or "none"} -> {D}/blind/ (key in {KEY}, keep it hidden)')
