"""Reachability diff (SPEC §14.3b): a new map version must not let the player reach a vanilla
cell from an entrance that could not reach it before, in any ability state.

Walk model (deliberately conservative):
- a cell is walkable if collision == 0; water behaviours need Surf, waterfalls need Waterfall;
- ledges (JUMP_*) can only be entered moving in their direction, and the jump lands one cell further;
- cuttable trees / breakable rocks / boulders (object gfx) block unless Cut / Rock Smash / Strength;
- coord triggers with a var condition, all other objects (NPCs) and every literal
  setobjectxy/setobjectxyperm position in the map's scripts are walls;
- entrances: every warp cell and every walkable cell on an edge that has a connection.
"""
import re

from .layers import Grid
from .tiles import Pair

STATES = {'none': set(), 'cut': {'cut'}, 'surf': {'surf'}, 'cut+surf': {'cut', 'surf'},
          'strength+smash': {'strength', 'smash'}, 'all': {'cut', 'surf', 'strength', 'smash', 'waterfall'}}
STEPS = {'up': (0, -1), 'down': (0, 1), 'left': (-1, 0), 'right': (1, 0)}
LEDGE = {'JUMP_SOUTH': 'down', 'JUMP_NORTH': 'up', 'JUMP_EAST': 'right', 'JUMP_WEST': 'left'}
BLOCKERS = (('CUTTABLE_TREE', 'cut'), ('BREAKABLE_ROCK', 'smash'), ('PUSHABLE_BOULDER', 'strength'))


class World:
    def __init__(self, grid, pair, data, scripts=''):
        self.grid, self.pair, self.data = grid, pair, data
        self.blocked = {}
        self.walls = {(e['x'], e['y']) for e in data.get('coord_events') or [] if e.get('var')}
        for e in data.get('object_events') or []:
            gfx = str(e.get('graphics_id', ''))
            ability = next((a for key, a in BLOCKERS if key in gfx), None)
            if ability:
                self.blocked[(e['x'], e['y'])] = ability
            else:
                # NPCs physically block (vanilla road blocks are often a standing NPC). Treating every
                # object as a wall is conservative and identical for old and new versions.
                self.walls.add((e['x'], e['y']))
        # Scripts park NPCs on roads (Viridian's old man: setobjectxyperm LOCALID_TUTORIAL_MAN, 21, 11).
        for m in re.finditer(r'^\s*setobjectxy(?:perm)?\s+\w+\s*,\s*(\d+)\s*,\s*(\d+)', scripts, re.M):
            self.walls.add((int(m[1]), int(m[2])))
        self.kind = {}

    def cell_kind(self, x, y):
        k = self.kind.get((x, y))
        if k is None:
            mt, coll, _ = self.grid.at(x, y)
            name = self.pair.behavior_name(mt)
            ledge = next((d for key, d in LEDGE.items() if key in name), None)
            if ledge:
                k = ('ledge', ledge)
            elif 'WATERFALL' in name:
                k = ('waterfall', None)
            elif re.search(r'WATER|OCEAN|POND|CURRENT', name) and 'SHALLOW' not in name:
                k = ('water', None)
            elif coll:
                k = ('wall', None)
            else:
                k = ('floor', None)
            self.kind[(x, y)] = k
        return k

    def can_enter(self, x, y, direction, abilities):
        if not self.grid.inside(x, y) or (x, y) in self.walls:
            return False
        if (x, y) in self.blocked and self.blocked[(x, y)] not in abilities:
            return False
        kind, ledge_dir = self.cell_kind(x, y)
        if kind == 'floor':
            return True
        if kind == 'water':
            return 'surf' in abilities
        if kind == 'waterfall':
            return 'waterfall' in abilities
        if kind == 'ledge':
            return direction == ledge_dir
        return False

    def entrances(self):
        """{name: set(cells)}: each warp alone, each connected edge as one group."""
        out = {}
        warps = data_warps = [(w['x'], w['y']) for w in self.data.get('warp_events') or []]
        for n, p in enumerate(data_warps):
            if self.grid.inside(*p):
                out[f'warp#{n}'] = {p}
        g = self.grid
        for c in self.data.get('connections') or []:
            d = c.get('direction')
            edge = {'up': {(x, 0) for x in range(g.w)}, 'down': {(x, g.h - 1) for x in range(g.w)},
                    'left': {(0, y) for y in range(g.h)}, 'right': {(g.w - 1, y) for y in range(g.h)}}.get(d, set())
            cells = {p for p in edge if self.cell_kind(*p)[0] != 'wall'}
            if cells:
                out[f'edge {d}'] = cells
        return out

    def reach(self, starts, abilities):
        seen = {p for p in starts if self.cell_kind(*p)[0] != 'wall' or p in starts}
        todo = list(seen)
        while todo:
            x, y = todo.pop()
            here = self.cell_kind(x, y)
            moves = [here[1]] if here[0] == 'ledge' else STEPS
            for d in moves:
                dx, dy = STEPS[d]
                n = (x + dx, y + dy)
                if n not in seen and self.can_enter(*n, d, abilities):
                    seen.add(n)
                    todo.append(n)
        return seen


def _traps(self, abilities):
    """Cells reachable from some entrance from which no entrance can be reached again (softlock)."""
    entries = self.entrances()
    exits = set().union(*entries.values()) if entries else set()
    reached = set()
    for cells in entries.values():
        reached |= self.reach(cells, abilities)
    back = {p for p in exits if p in reached}
    todo = list(back)
    while todo:                               # reverse walk: who can step onto a cell that gets out?
        nx, ny = todo.pop()
        for d, (sx, sy) in STEPS.items():
            p = (nx - sx, ny - sy)
            if p in back or p not in reached:
                continue
            kind = self.cell_kind(*p)
            if kind[0] == 'ledge' and kind[1] != d:
                continue
            if (nx, ny) in exits or self.can_enter(nx, ny, d, abilities):   # doors are solid but warp
                back.add(p)
                todo.append(p)
    return reached - back


World.traps = _traps


def check_write(repo, pair, before_cells, grid):
    """After a map write, compare walkable cells reachable before vs after.
    Returns 'reach: ok', 'WARN reach: N cells cut off, e.g. x,y x,y', or
    'INFO reach: not checked (reason)'."""
    try:
        scripts = repo.read(f'data/maps/{grid.name}/scripts.inc').decode('utf-8')
    except Exception:
        scripts = ''
    old_grid = Grid.__new__(Grid)
    old_grid.repo, old_grid.name, old_grid.data, old_grid.layout = grid.repo, grid.name, grid.data, grid.layout
    old_grid.w, old_grid.h, old_grid.cells = grid.w, grid.h, list(before_cells)
    old_world = World(old_grid, pair, grid.data, scripts)
    new_world = World(grid, pair, grid.data, scripts)
    old_entries = old_world.entrances()
    if not old_entries:
        return 'INFO reach: not checked (no entrance)'
    new_entries = new_world.entrances()
    warps = [k for k in old_entries if k.startswith('warp#')]

    def walkable(world):
        return {(x, y) for y in range(grid.h) for x in range(grid.w)
                if (x, y) not in world.walls and (x, y) not in world.blocked
                and world.cell_kind(x, y)[0] in ('floor', 'ledge')}

    walk_before = walkable(old_world)
    walk_after = walkable(new_world)
    if warps:
        old_starts = set().union(*(old_entries[k] for k in warps))
        new_starts = set().union(*(new_entries.get(k, set()) for k in warps))
        before = old_world.reach(old_starts, set()) & walk_before
        after = new_world.reach(new_starts, set()) if new_starts else set()
        cut = (before & walk_after) - after
    else:
        cuts = []
        for k in sorted(old_entries):
            before = old_world.reach(old_entries[k], set()) & walk_before
            new_starts = new_entries.get(k, set())
            after = new_world.reach(new_starts, set()) if new_starts else set()
            c = (before & walk_after) - after
            if c:
                cuts.append(c)
        cut = min(cuts, key=len) if cuts else set()
    if not cut:
        return 'reach: ok'
    ordered = sorted(cut, key=lambda p: (p[1], p[0]))
    sample = ' '.join(f'{x},{y}' for x, y in ordered[:5])
    return f'WARN reach: {len(ordered)} cells cut off, e.g. {sample}'


def diff(repo, name, old_state, new_state, shift=(0, 0)):
    """old_state/new_state: (data, layout, repo_for_reading). Returns list of bypass descriptions."""
    def world(state):
        data, layout, r = state
        try:
            scripts = r.read(f'data/maps/{name}/scripts.inc').decode('utf-8')
        except Exception:
            scripts = ''
        return World(Grid(r, name, data, layout), Pair(r, layout), data, scripts)
    old, new = world(old_state), world(new_state)
    dx, dy = shift
    problems = []
    old_entries, new_entries = old.entrances(), new.entrances()
    for state, abilities in STATES.items():
        for key, cells in sorted(old_entries.items()):
            new_cells = new_entries.get(key) if key.startswith('edge') else {(x + dx, y + dy) for x, y in cells}
            if not new_cells:
                continue
            before = old.reach(cells, abilities)
            after = new.reach(new_cells, abilities)
            extra = [(x - dx, y - dy) for x, y in after
                     if old.grid.inside(x - dx, y - dy) and (x - dx, y - dy) not in before
                     and old.cell_kind(x - dx, y - dy)[0] != 'wall']
            if extra:
                x, y = min(extra, key=lambda p: (p[1], p[0]))
                problems.append(f'GATE state {state}: from {key} the new map reaches vanilla {x},{y} '
                                f'(+{len(extra)} cells) that vanilla could not')
        if problems:
            break
    for state in ('none', 'all'):
        old_traps = {(x + dx, y + dy) for x, y in old.traps(STATES[state])}
        fresh = sorted(new.traps(STATES[state]) - old_traps, key=lambda p: (p[1], p[0]))
        if fresh:
            x, y = fresh[0]
            problems.append(f'TRAP state {state}: {x},{y} (+{len(fresh) - 1} cells) can be reached but has no way '
                            f'back to any warp or map edge (one-way ledge without stairs?)')
            break
    return problems
