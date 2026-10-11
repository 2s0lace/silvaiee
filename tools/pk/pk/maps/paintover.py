"""pk map paintover IMAGE: turn a painted/drawn map image back into real metatiles.

The image must be the map's pixel size at scale 1 or 2 (W*16 x H*16). Every metatile of the
map's tileset pair is rendered once to 16x16 RGB (reusing the map render code); each cell is
matched against them by colour distance. Cells that already match the current metatile are
frozen so untouched vanilla areas survive byte-exactly; everything else is solved with the
same greedy+ICM scheme as terrain.solve: unary -k*colour-distance + learned vanilla adjacency
(rules.h/rules.v) + the small frequency prior. One journaled transaction.
"""
import io
import json
import operator
import re
from pathlib import Path

from PIL import Image

from ..core.errors import PkError
from ..core.out import Result
from . import terrain
from .edit import load, save
from .meta import all_ids, draw
from .grid import decode

FREEZE_MEAN = 6.0   # mean |RGB| distance per pixel below which a cell stays untouched
K = 0.05            # unary weight applied to the mean colour distance (0..255)
TOP = 12            # candidate metatiles kept per cell
GREY_STAGE = 20     # metatiles entering the exact-colour pass


def _sub_sum(a, b):
    return sum(map(abs, map(operator.sub, a, b)))


def _flat(img):
    return tuple(v for p in img.get_flattened_data() for v in p)


def metatile_images(repo, pair, ids):
    """{metatile: 16x16 RGB PIL image} using the same renderer as pk map render."""
    out = {}
    for mt in ids:
        img = Image.new('RGB', (16, 16), (0, 0, 0))
        img.putdata([p or (0, 0, 0) for row in draw(repo, pair, mt) for p in row])
        out[mt] = img
    return out


def load_image(repo, image, grid):
    """RGB image of W*16 x H*16; warns (and resizes with Image.BOX) on any other size."""
    path = Path(image)
    target = path if path.is_absolute() else repo.root / path
    try:
        data = target.read_bytes()
    except OSError:
        raise PkError('NOTFOUND', f'image {image}; pass a readable PNG path') from None
    img = Image.open(io.BytesIO(data)).convert('RGB')
    need = (grid.w * 16, grid.h * 16)
    if img.size != need and img.size != (2 * need[0], 2 * need[1]):
        return img.resize(need, Image.BOX), (f'WARN image {img.width}x{img.height} is not the map size at '
                                             f'scale 1 or 2; resized to {need[0]}x{need[1]}')
    return img.resize(need, Image.BOX), None


def match_cells(repo, pair, grid, image, cells, meta):
    """(candidates {cell: [(mt, mean_dist)...]}, {cell: dist of the current metatile}).

    Two stages per cell: cheap grey 4x4 scan over every metatile, then exact 16x16 RGB match
    on the GREY_STAGE best; the top TOP of those become candidates."""
    ids = list(meta)
    grey = {m: tuple(meta[m].resize((4, 4), Image.BOX).convert('L').get_flattened_data()) for m in ids}
    fine = {m: _flat(meta[m]) for m in ids}
    tiles = {p: image.crop((p[0] * 16, p[1] * 16, p[0] * 16 + 16, p[1] * 16 + 16)) for p in cells}
    tile_grey = {p: tuple(t.resize((4, 4), Image.BOX).convert('L').get_flattened_data()) for p, t in tiles.items()}
    tile_fine = {p: _flat(t) for p, t in tiles.items()}
    self_dist = {}
    for p in cells:
        mt = grid.at(*p)[0]
        self_dist[p] = _sub_sum(tile_fine[p], fine[mt]) / 768.0 if mt in fine else 255.0
    cand = {}
    for p in cells:
        pool = sorted(ids, key=lambda m: (_sub_sum(tile_grey[p], grey[m]), m))[:GREY_STAGE]
        scored = sorted((_sub_sum(tile_fine[p], fine[m]) / 768.0, m) for m in pool)
        cand[p] = [(m, d) for d, m in scored[:TOP]]
    return cand, self_dist, tile_fine, fine


def copy_map(repo, tx, query, new_name):
    """NEW_MAP = copy of MAP: new map.json (events kept, connections cleared), layout +
    blockdata/border copies, scripts.inc, group entry, event_scripts.s include."""
    from .create import snake, stage_map_include
    from ..core.repo import JsonDocument
    if not re.fullmatch(r'[A-Z][A-Za-z0-9_]*', new_name):
        raise PkError('PARSE', 'map name is CamelCase like PkPalletDraft2_Frlg')
    if repo.root.joinpath('data/maps', new_name).exists():
        raise PkError('CONFLICT', f'map {new_name} exists')
    name, data, layout = repo.map(query)
    version = repo.version(layout)
    map_id, layout_id = 'MAP_' + snake(new_name), 'LAYOUT_' + snake(new_name) + ('_FRLG' if version == 'frlg' else '')
    new_layout = dict(layout, id=layout_id, name=f'{new_name}_Layout',
                      border_filepath=f'data/layouts/{new_name}/border.bin',
                      blockdata_filepath=f'data/layouts/{new_name}/map.bin')
    tx.stage(new_layout['blockdata_filepath'], repo.read(layout['blockdata_filepath']))
    tx.stage(new_layout['border_filepath'], repo.read(layout['border_filepath']))
    layouts = JsonDocument(repo.read('data/layouts/layouts.json'))
    if any(x['id'] == layout_id for x in layouts.value['layouts']):
        raise PkError('CONFLICT', f'{layout_id} exists')
    layouts.value['layouts'].append(new_layout)
    tx.stage('data/layouts/layouts.json', layouts.emit())
    new_data = dict(data, id=map_id, name=new_name, layout=layout_id, connections=None)
    tx.stage(f'data/maps/{new_name}/map.json', (json.dumps(new_data, indent=2, ensure_ascii=False) + '\n').encode('utf-8'))
    try:
        tx.stage(f'data/maps/{new_name}/scripts.inc', repo.read(f'data/maps/{name}/scripts.inc'))
    except PkError:
        pass
    groups = JsonDocument(repo.read('data/maps/map_groups.json'))
    group = next((g for g in groups.value['group_order'] if name in groups.value[g]), 'gMapGroup_PkNew')
    groups.value[group].append(new_name)
    tx.stage('data/maps/map_groups.json', groups.emit())
    stage_map_include(repo, tx, new_name)
    return new_name, new_data, new_layout


def paint_image(repo, grid, pair, rules, img, targets):
    """Match, freeze, solve; returns (solved {cell: mt}, match {cell: mean_dist}, warn rows)."""
    meta = metatile_images(repo, pair, all_ids(pair))
    cand_by_cell, self_dist, tile_fine, fine = match_cells(repo, pair, grid, img, targets, meta)
    frozen = {p for p in targets if self_dist[p] <= FREEZE_MEAN}
    solved_cells = targets - frozen
    solved, match = {}, {}
    if solved_cells:
        cand = {p: [m for m, _ in cand_by_cell[p]] for p in solved_cells}
        dist = {(p, m): d for p in solved_cells for m, d in cand_by_cell[p]}
        solved = terrain.solve(grid, rules, {p: rules.material(grid.at(*p)) for p in solved_cells}, margin=0,
                               frozen=frozen, choices=cand, unary=lambda m, p: -K * dist[(p, m)])
        match = {p: _sub_sum(tile_fine[p], fine[solved[p]]) / 768.0 for p in solved_cells}
    return solved, match, frozen


def command(repo, tx, args, opts):
    verb, rest = args[0], args[1:]
    if verb != 'paintover' or len(rest) != 2:
        raise PkError('PARSE', 'usage: pk map paintover MAP IMAGE [--into NEW_MAP]')
    map_query, image = rest
    grid = None
    if opts.get('into'):
        name, data, layout = copy_map(repo, tx, map_query, opts['into'])
        from .layers import Grid
        from .tiles import Pair
        grid, pair = Grid(repo, name, data, layout), Pair(repo, layout)
    if grid is None:
        grid, pair = load(repo, map_query)
    rules = terrain.Rules(repo, pair)
    img, warn = load_image(repo, image, grid)
    targets = {(x, y) for y in range(grid.h) for x in range(grid.w)}
    solved, match, frozen = paint_image(repo, grid, pair, rules, img, targets)
    bad = terrain.unseen(grid, rules, solved)
    changed = 0
    for p, mt in solved.items():
        old = grid.at(*p)
        if old[0] != mt:
            grid.set(*p, terrain.bits_for(repo, pair, rules, mt, rules.material(old), old))
            changed += 1
    if changed:
        save(tx, grid)
    mean = sum(match.values()) / len(match) if match else 0.0
    worst = sorted(match.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    label = f'{opts["into"]} <- {image}' if opts.get('into') else image
    rows = ([warn] if warn else []) + [
        f'cells {len(targets)} frozen {len(frozen)} changed {changed}',
        f'mean {mean:.2f} worst ' + ' '.join(f'{x},{y} {d:.2f}' for (x, y), d in worst),
    ] + ([f'WARN {len(bad)} unseen seams; e.g. ' + ' '.join(f'{x},{y}{d}' for x, y, d in bad[:5])
          if bad else ''][:1 if bad else 0])
    return Result(f'paintover {grid.name} {label} {len(targets)} cells: {changed} changed, '
                  f'{len(frozen)} frozen, {len(bad)} seams, mean {mean:.2f}', rows)
