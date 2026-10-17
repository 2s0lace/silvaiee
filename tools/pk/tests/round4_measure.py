"""Reproducible Round 4 measurements. Never writes maps or the frozen snapshot.

`corners` wipes 10 in-memory boxes (5 where grass meets sand, 5 where a pond ends) with a plain
ground ("grass") tile and re-runs `terrain.auto` at four diagonal weights, counting the unseen
adjacency seams that remain. Same idea as tests/test_terrain_auto.py; nothing is committed.
"""
import re
import sys
from pathlib import Path

HOME = Path(__file__).resolve().parents[1]
ROOT = HOME.parents[1]
sys.path.insert(0, str(HOME))
from pk.core.repo import Repo            # noqa: E402
from pk.core.journal import Transaction  # noqa: E402
from pk.maps import edit, terrain        # noqa: E402
from pk.maps.edit import load            # noqa: E402
from pk.maps.terrain import Rules        # noqa: E402

# (kind, map, x, y) of a 4x4 box; grass-sand boxes sit on a grass/sand junction, pond boxes on a
# pond end (water meeting land). Found by scanning vanilla FRLG material grids.
BOXES = [
    ('grass-sand', 'FiveIsland_Meadow_Frlg', 3, 8),
    ('grass-sand', 'FiveIsland_MemorialPillar_Frlg', 10, 7),
    ('grass-sand', 'OneIsland_TreasureBeach_Frlg', 7, 22),
    ('grass-sand', 'OneIsland_TreasureBeach_Frlg', 10, 21),
    ('grass-sand', 'FiveIsland_MemorialPillar_Frlg', 11, 7),
    ('pond', 'PalletTown_Frlg', 5, 15),
    ('pond', 'PalletTown_Frlg', 9, 15),
    ('pond', 'CinnabarIsland_Frlg', 6, 10),
    ('pond', 'FourIsland_Frlg', 13, 7),
    ('pond', 'FuchsiaCity_Frlg', 33, 24),
]
WEIGHTS = (0.0, 0.5, 1.0, 2.0)


def plain_tile(repo, pair):
    """The most common plain ground metatile of the pair (same choice as tests/test_terrain_auto.py
    painting 0x010 on PalletTown), deterministic on frequency ties."""
    rules = Rules(repo, pair, include_pk=False)
    cands = sorted(rules.by_mat.get('ground') or [])
    if not cands:
        return None
    return max(cands, key=lambda m: (rules.freq.get(m, 0), -m))


def run(map_name, x, y, weight):
    tx = Transaction(Repo(ROOT), f'w{weight}')
    repo = Repo(ROOT, tx)
    grid, pair = load(repo, map_name)
    mt = plain_tile(repo, pair)
    if mt is None:
        print(f'INFO {map_name} has no ground metatile; box skipped')
        return None
    edit.command(repo, tx, ['paint', map_name, f'{x},{y},4x4', f'0x{mt:03X}'], {})
    res = terrain.auto(repo, tx, map_name, f'{x},{y},4x4', diag_weight=weight)
    hit = re.search(r'seams:(\d+)->(\d+)', res.summary)
    return int(hit.group(2))


def corners():
    rows, results = [], []
    for kind, name, x, y in BOXES:
        after = [run(name, x, y, w) for w in WEIGHTS]
        results.append(after)
        cells = '  '.join(f'{w}:{a}' for w, a in zip(WEIGHTS, after))
        print(f'{kind:10s} {name} {x},{y}  {cells}', flush=True)
        rows.append((kind, name, x, y, after))
    print()
    for i, w in enumerate(WEIGHTS):
        at_min = sum(1 for after in results if after[i] == min(after))
        strict = sum(1 for after in results if after[i] == min(after) and after.count(min(after)) == 1)
        worse = sum(1 for after in results if after[i] > min(after))
        print(f'weight {w}: strictly best on {strict}/10, at the minimum on {at_min}/10, worse than best on {worse}/10')
    return rows


if __name__ == '__main__':
    {'corners': corners}[sys.argv[1]]()
