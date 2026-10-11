"""pk check: static validation without compiling (SPEC §9)."""
import io
import json

from PIL import Image

from ..core.errors import PkError
from ..core.out import page
from ..index.builder import ensure
from .grid import decode
from .tiles import Pair, LIMITS, TOTAL_TILES, TOTAL_METATILES

EVENT_KEYS = ('object_events', 'warp_events', 'coord_events', 'bg_events')
BASELINE = 'tools/pk/data/check_baseline.json'
NO_SCRIPT = ('0x0', '0', '', 'NULL')


def run(repo, tx, target, opts, pagination):
    maps_count, layouts_count = repo.structural_check()
    maps = repo.maps()
    by_id = {d['id']: n for n, d in maps.items()}
    layouts = repo.layouts()
    if target and target != 'all':
        name, _, _ = repo.map(target)
        selected = [name]
    else:
        selected = sorted(maps)
    db = ensure(repo, tx)
    try:
        labels = {n for (n,) in db.execute('SELECT name FROM scripts UNION SELECT name FROM texts')}
    finally:
        db.close()
    # Labels written earlier in this same transaction (e.g. by pk bp) are not in the index yet.
    import re as _re
    for key, raw in tx.writes.items():
        if raw and key.endswith('.inc'):
            labels |= set(_re.findall(r'^(\w+)::?\s*$', raw.decode('utf-8', 'replace'), _re.M))
    errors, warnings = [], []
    pairs_checked = set()
    for name in selected:
        data = maps[name]
        layout = layouts[data['layout']]
        w, h = layout['width'], layout['height']
        pair = Pair(repo, layout)
        cells = decode(repo.read(layout['blockdata_filepath'])[:w * h * 2])
        bad = sorted({mt for mt, _, _ in cells if not pair.valid(mt)})
        if bad:
            errors.append(f'{name} metatiles outside tileset pair: ' + ' '.join(f'0x{m:03X}' for m in bad[:6]))
        for key in EVENT_KEYS:
            for n, e in enumerate(data.get(key) or []):
                if not (0 <= e.get('x', 0) < w and 0 <= e.get('y', 0) < h):
                    # Vanilla parks some objects/warps off-map on purpose (scripted entrances).
                    warnings.append(f"{name} {key[:-7]}#{n} @{e.get('x')},{e.get('y')} outside {w}x{h}")
                script = str(e.get('script', '0x0'))
                if script not in NO_SCRIPT and script not in labels:
                    errors.append(f'{name} {key[:-7]}#{n} script {script} not defined')
        for n, warp in enumerate(data.get('warp_events') or []):
            dest = by_id.get(warp.get('dest_map'))
            target_id = str(warp.get('dest_warp_id'))
            if warp.get('dest_map') == 'MAP_DYNAMIC' or not target_id.isdigit():
                continue
            if dest is None:
                errors.append(f"{name} warp#{n} -> unknown map {warp.get('dest_map')}")
                continue
            dest_warps = maps[dest].get('warp_events') or []
            if int(target_id) >= len(dest_warps):
                errors.append(f'{name} warp#{n} -> {dest}#{target_id} does not exist')
            elif by_id.get(dest_warps[int(target_id)].get('dest_map')) != name:
                warnings.append(f'{name} warp#{n} -> {dest}#{target_id} is one-way')
        for c in data.get('connections') or []:
            other = by_id.get(c.get('map'))
            if other is None:
                errors.append(f"{name} connection {c.get('direction')} -> unknown {c.get('map')}")
                continue
            back = {'up': 'down', 'down': 'up', 'left': 'right', 'right': 'left'}.get(c['direction'])
            mirrors = [x for x in maps[other].get('connections') or [] if x.get('map') == data['id'] and x.get('direction') == back]
            if back and not mirrors:
                warnings.append(f"{name} connection {c['direction']} -> {other} has no reciprocal")
            elif back and all(int(x['offset']) != -int(c['offset']) for x in mirrors):
                errors.append(f"{name} connection {c['direction']} -> {other} offset {c['offset']} mismatches reciprocal")
        key = (layout['primary_tileset'], layout['secondary_tileset'])
        if key not in pairs_checked:
            pairs_checked.add(key)
            errors += budget(repo, pair)
    if not target or target == 'all':
        from ..text.ops import Text
        from ..text.strings import blocks, overflows
        t = Text(repo)
        for path in t.files():
            for block in blocks(repo.read(path).decode('utf-8')):
                if t.font(block.source) == 'FONT_NORMAL':
                    for b, n, wpx, _ in overflows(block.source, t.measure, t.limit, estimates=False):
                        warnings.append(f'{block.label} box{b} line{n} {wpx}px > {t.limit}px')
    # Vanilla already has a few issues; they live in a baseline so only NEW problems fail.
    if opts.get('baseline'):
        if target and target != 'all':
            raise PkError('PARSE', 'check --baseline needs the whole project (pk check all --baseline)')
        tx.stage(BASELINE, (json.dumps(sorted(set(errors)), indent=1) + '\n').encode('utf-8'))
        return page(f'check baseline recorded {len(set(errors))} known errors', [])
    try:
        known = set(json.loads(repo.read(BASELINE)))
    except PkError:
        known = set()
    new = [e for e in errors if e not in known]
    old = [e for e in errors if e in known]
    rows = [f'ERR {e}' for e in new] + ([f'KNOWN {e}' for e in old] if opts.get('known') else []) \
        + ([f'WARN {w}' for w in warnings] if opts.get('warnings') else [])
    summary = (f'check maps:{len(selected)} errors:{len(new)} known:{len(old)} warnings:{len(warnings)}'
               + ('' if opts.get('warnings') else ' (--warnings/--known list them)'))
    result = page(summary, rows, *pagination(opts))
    if new:
        result.ok, result.code = False, 'PARSE'
    return result


def budget(repo, pair):
    out = []
    lim = pair.limits
    for ts, is_primary in ((pair.primary, True), (pair.secondary, False)):
        sym = ts['symbol']
        count = pair.count(ts)
        max_meta = lim['metatiles'] if is_primary else TOTAL_METATILES - lim['metatiles']
        if count > max_meta:
            out.append(f'{sym} metatiles {count}/{max_meta}')
        if ts['tiles']:
            try:
                img = Image.open(io.BytesIO(repo.read(ts['tiles'])))
                tiles = (img.width // 8) * (img.height // 8)
                max_tiles = lim['tiles'] if is_primary else TOTAL_TILES - lim['tiles']
                if tiles > max_tiles:
                    # The engine loads at most max_tiles; vanilla sheets are sometimes padded.
                    out.append(f'{sym} tiles.png has {tiles} tiles, only {max_tiles} load')
            except (OSError, PkError) as exc:
                out.append(f'{sym} tiles.png unreadable: {exc}')
        for p in ts['palettes']:
            try:
                repo.read(p)
            except PkError:
                out.append(f'{sym} palette missing {p}')
    return out
