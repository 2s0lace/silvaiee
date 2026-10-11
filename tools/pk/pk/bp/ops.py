"""pk bp: plan / check / apply / diff / render / export / world / show."""
import re

from ..core.errors import PkError
from ..core.out import Result, page
from ..core.journal import atomic_write
from ..maps.layers import Grid, parse_rect
from ..maps.tiles import Pair
from . import apply as bp_apply

WORLD = 'blueprints/world.bp'


def resolve(repo, word):
    """A blueprint path, or a map name -> blueprints/**/<Map>.bp."""
    if word.endswith('.bp'):
        repo.read(word)
        return word
    hits = sorted(p.relative_to(repo.root).as_posix() for p in repo.root.glob(f'blueprints/**/{word}.bp'))
    if len(hits) != 1:
        raise PkError('NOTFOUND' if not hits else 'AMBIGUOUS', f'blueprint for {word}', hits)
    return hits[0]


def export(repo, tx, map_word, opts):
    from ..maps.reach import BLOCKERS
    name, data, layout = repo.map(map_word)
    region = opts.get('region', 'Kanto' if name.endswith('_Frlg') else 'Hoenn')
    out = opts.get('out') or f'blueprints/{region}/{name}.bp'
    if repo.root.joinpath(out).exists() and not opts.get('overwrite'):
        raise PkError('CONFLICT', f'{out} exists; add --overwrite')
    lines = [f'blueprint {name} v1', f'base {name}', f"size {layout['width']}x{layout['height']}",
             f"tilesets {layout['primary_tileset']} {layout['secondary_tileset']}",
             f"header music={data['music']} weather={data['weather']} type={data['map_type']}",
             'intent "TODO: one line of design intent"', '', 'terrain',
             f"  keep 0,0 {layout['width']}x{layout['height']}", '', 'gates']
    for e in data.get('object_events') or []:
        for key, ability in BLOCKERS:
            if key in str(e.get('graphics_id')):
                lines.append(f"  {ability} {e['x']},{e['y']}")
    for e in data.get('coord_events') or []:
        lines.append(f"  trigger {e['x']},{e['y']} {e.get('var')}={e.get('var_value')}")
    lines += ['', 'vanilla', '  keep all', '', 'notes', f'  exported by pk bp export from {name}']
    tx.stage(out, ('\n'.join(lines) + '\n').encode('utf-8'))
    return Result(f'bp export {name} -> {out}', ['edit size/terrain/npcs, then pk bp check ' + out])


def command(repo, tx, args, opts, pagination):
    verb, rest = args[0], args[1:]
    if verb == 'world':
        rows = []
        for line in repo.read(WORLD).decode('utf-8').splitlines():
            if not line or line.startswith(('#', '##')):
                continue
            if opts.get('status') and f"status={opts['status']}" not in line:
                continue
            if rest and rest[0].casefold() not in line.casefold():
                continue
            rows.append(line)
        return page(f'bp world {len(rows)}', rows, *pagination(opts))
    if verb == 'export' and len(rest) == 1:
        return export(repo, tx, rest[0], opts)
    if verb == 'show' and len(rest) == 1:
        path = resolve(repo, rest[0])
        lines = repo.read(path).decode('utf-8').splitlines()
        if opts.get('section'):
            out, inside = [], False
            for line in lines:
                if not line.startswith(' ') and line.strip():
                    inside = line.split()[0] == opts['section']
                if inside:
                    out.append(line)
            lines = out
        return page(f'bp {path} {len(lines)} lines', lines, *pagination(opts, 40), maximum=80)
    if verb in ('plan', 'check', 'apply', 'diff', 'render') and len(rest) == 1:
        path = resolve(repo, rest[0])
        mode = {'diff': 'plan', 'render': 'plan'}.get(verb, verb)
        result = bp_apply.run(repo, tx, path, mode, opts)
        if verb in ('plan', 'check', 'diff', 'render'):
            # Never commit: keep only cache writes made while planning.
            name = re.search(r'bp \w+ (\w+)', result.summary)[1]
            if verb == 'render':
                from ..maps.meta import render_png
                _, data, layout = repo.map(name)
                grid = Grid(repo, name, data, layout)
                png = render_png(repo, grid, Pair(repo, layout), parse_rect(opts.get('rect'), grid.w, grid.h),
                                 int(opts.get('scale', 1)))
                out = repo.root / '.pk/render' / f'{name}.bp.png'
                atomic_write(out, png)
                result.data.insert(0, f'render -> .pk/render/{name}.bp.png (blueprint applied in memory only)')
            for key in list(tx.writes):
                if not key.startswith('.pk/'):
                    del tx.writes[key]
            if verb == 'diff':
                result.summary = result.summary.replace('bp plan', 'bp diff')
        return result
    raise PkError('PARSE', 'usage: pk bp world [filter] [--status s] | show|plan|check|apply|diff|render BP_OR_MAP | export MAP')
