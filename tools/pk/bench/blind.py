"""Copy NEW benchmark renders to tools/pk/bench/blind/<letter>.png in random order.

Renders that already have a letter keep it, so you can rate in batches: the first batch gets A-E, the next
one F, G... The key is in tools/pk/bench/blind_key.txt. Don't show it to the reviewer until the scores are in.
Run from repo root: python tools/pk/bench/blind.py
"""
import glob
import os
import random
import shutil
import string

B = 'tools/pk/bench'
KEY = f'{B}/blind_key.txt'
key = {}
if os.path.exists(KEY):
    for line in open(KEY, encoding='utf-8'):
        if ' = ' in line:
            letter, model = line.strip().split(' = ', 1)
            key[letter] = model
done = set(key.values())
models = [os.path.basename(p)[len('PkPallet'):-len('_Frlg.png')] for p in glob.glob('.pk/render/PkPallet*_Frlg.png')]
new = [m for m in models if m not in done and not m.startswith('Draft')]
random.shuffle(new)
os.makedirs(f'{B}/blind', exist_ok=True)
free = [c for c in string.ascii_uppercase if c not in key]
added = []
for letter, m in zip(free, new):
    shutil.copy(f'.pk/render/PkPallet{m}_Frlg.png', f'{B}/blind/{letter}.png')
    key[letter] = m
    added.append(letter)
open(KEY, 'w', encoding='utf-8').write(''.join(f'{k} = {v}\n' for k, v in sorted(key.items())))
print(f'new: {" ".join(added) or "none"} -> {B}/blind/ (key in {KEY}, keep it hidden)')
