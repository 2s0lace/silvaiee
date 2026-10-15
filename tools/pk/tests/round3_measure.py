"""Reproducible Round 3 measurements. Never writes maps or the frozen snapshot."""
import sys
from collections import defaultdict
from pathlib import Path

HOME = Path(__file__).resolve().parents[1]
ROOT = HOME.parents[1]
sys.path.insert(0, str(HOME))
from pk.core.repo import Repo
from pk.core.errors import PkError
from pk.maps import edit, lint
from pk.maps.tiles import Pair


def audit_rules(name, cls, collision, behaviour):
    """Exactly the owner's four semantic contradictions; no visual grass inference."""
    rules = []
    if name.startswith('water_') and cls != 'surf':
        rules.append('water_not_surf')
    if name.startswith(('grass_', 'sand_', 'ground_')) and collision == 1:
        rules.append('terrain_collision_1')
    if name.startswith(('solid_', 'wall_')) and collision == 0:
        rules.append('solid_collision_0')
    if name.startswith('ledge_') and not any(key in behaviour for key in lint.LEDGE_DIR):
        rules.append('ledge_without_behaviour')
    return rules


def audit():
    repo = Repo(ROOT)
    named_sets = {p.stem for p in (HOME / 'data/metatiles').glob('*.json')}
    rules = ('water_not_surf', 'terrain_collision_1', 'solid_collision_0', 'ledge_without_behaviour')
    found, seen_pairs, covered = defaultdict(set), set(), set()
    for layout in repo.layouts().values():
        if layout['id'].startswith('LAYOUT_PK_'):
            continue
        symbols = layout['primary_tileset'], layout['secondary_tileset']
        if symbols in seen_pairs or not named_sets.intersection(symbols):
            continue
        seen_pairs.add(symbols)
        try:
            pair = Pair(repo, layout)
        except PkError as exc:
            print(f'INFO audit skipped pair {symbols}: {exc}')
            continue
        for ts in (pair.primary, pair.secondary):
            for text_id, info in pair.names(ts).items():
                mt, name = int(text_id, 16), info.get('name', '')
                covered.add((ts['symbol'], mt))
                cls, coll, elev = lint.metatile_physical(repo, pair, mt)
                behaviour = pair.behavior_name(mt)
                for rule in audit_rules(name, cls, coll, behaviour):
                    replacement = {'walk': 'ground', 'surf': 'water', 'wall': 'solid',
                                   'grass_enc': 'grass', 'door': 'door'}.get(cls, cls)
                    found[(rule, ts['symbol'], mt, name, cls, coll, behaviour, f'{replacement}_{mt:03x}')].add('+'.join(symbols))
    counts = {rule: sum(key[0] == rule for key in found) for rule in rules}
    rows = ['# Name audit', '', 'Command: `C:/Python314/python.exe -B tools/pk/tests/round3_measure.py audit`', '',
            'Owner-approved rules only:', '',
            '- water_* whose physical class is not surf.',
            '- grass_*, sand_* or ground_* with dominant collision exactly 1 (wall). Walkable grass is allowed.',
            '- solid_* or wall_* with dominant collision exactly 0.',
            '- ledge_* without any JUMP_NORTH/SOUTH/EAST/WEST behaviour.', '',
            'Classes and collision use `lint.metatile_physical`, including its unused-tile fallback (collision 0).',
            'Rows group identical findings across vanilla pair contexts. Suggested names require owner review; no names changed.', '',
            f'Named metatiles inspected: {len(covered)}. Contradictory rows: {len(found)}.', '',
            '| Rule | Count |', '|---|---|']
    rows += [f'| {rule} | {counts[rule]} |' for rule in rules]
    rows += ['', '| Rule | Tileset | ID | Name | Physical class | Collision | Behaviour | Suggested name | Pair contexts |',
             '|---|---|---|---|---|---|---|---|---|']
    for (rule, symbol, mt, name, cls, coll, behaviour, fix), pairs in sorted(found.items()):
        rows.append(f'| {rule} | {symbol} | 0x{mt:03X} | {name} | {cls} | {coll} | {behaviour} | {fix} | {", ".join(sorted(pairs))} |')
    missing = named_sets - {s for s, _ in covered}
    for symbol in sorted(missing):
        print(f'INFO named tileset has no vanilla context: {symbol}')
    (HOME / 'tasks/name_audit.md').write_text('\n'.join(rows) + '\n', encoding='utf-8')
    print(f'AUDIT named_metatiles={len(covered)} contradictions={len(found)} missing_sets={len(missing)}')
    for rule in rules:
        print(f'AUDIT_RULE {rule}={counts[rule]}')


def shapes():
    repo = Repo(ROOT)
    snap = lint.load_snapshot(repo)
    total, cells, maps, nonzero = 0, 0, 0, 0
    for name, _ in lint.vanilla_outdoor(repo):
        grid, pair = edit.load(repo, name)
        rows = lint.shapes(grid, pair, snap)
        if any('unavailable' in row for row in rows):
            raise SystemExit('STOP: SHAPE unavailable on ' + name)
        n = len(rows)
        maps += 1
        total += n
        cells += grid.w * grid.h
        nonzero += bool(n)
        print(f'VANILLA {name} shapes={n}', flush=True)
    print(f'VANILLA_TOTAL maps={maps} shapes={total} cells={cells} nonzero_maps={nonzero} leave_one_out=false', flush=True)
    if total:
        raise SystemExit('STOP: vanilla no-LOO SHAPE target zero missed; no threshold relaxed')


if __name__ == '__main__':
    {'audit': audit, 'shapes': shapes}[sys.argv[1]]()
