"""Vanilla piece catalogue: `pk kit index | find | fit`.

Index contract (.pk/kit/index.json, git-ignored). A JSON list, one object per kept window:
  map       MAP dir name, e.g. "PalletTown_Frlg"
  x, y      window origin in metatile cells
  w, h      window size in metatile cells
  pair      tileset pair, as the repo's pair lookup returns it
  classes   {physical class: share 0..1}, from lint.physical_class only
  edges     {"top"|"bottom"|"left"|"right": dominant class, or "mixed"}
  features  list of names drawn from: shore, pond, ledge, tallgrass, cliff
  curvy     int, direction changes on the surf/walk boundary (shore windows; 0 otherwise)
These field names are the contract between the indexer and the search code. Change them only here.
"""

# --- index ---
# Owned by the indexer agent: build_index, load_index, command_index, command.
import array
import hashlib
import io
import json
import os
import re
from collections import Counter

from PIL import Image, ImageDraw, ImageFont

from ..core.errors import PkError
from ..core.out import Result
from .edit import load
from .layers import Grid, events
from .lint import physical_class, vanilla_outdoor
from .meta import pair_for, render_png
from .tiles import Pair

INDEX_REL = '.pk/kit/index.json'
WINDOW_SIZES = ((2, 2), (3, 2), (2, 3), (3, 3), (4, 3), (3, 4), (4, 4), (5, 4), (4, 5),
                (6, 4), (8, 6), (10, 6), (12, 8), (15, 10))
STRIDE = 2
UNIFORM_PCT = 85         # a window whose largest class is above this share is skipped
EDGE_PCT = 70            # a side has a dominant class only from this share, else "mixed"
TALLGRASS_PCT = 20       # grass_enc share needed for the tallgrass feature
CLIFF_RUN = 4            # wall cells in one straight run needed for the cliff feature
FEATURES = ('shore', 'pond', 'ledge', 'tallgrass', 'cliff')
SKIP_REASONS = ('event', 'door', 'uniform', 'duplicate')


def physical_cells(pair, grid):
    """Row-major physical class of every cell: lint.physical_class on the cell's own behaviour and
    collision (the same call polish makes). Names and tags are never read."""
    memo, out = {}, []
    for mt, coll, _ in grid.cells:
        key = (mt, coll)
        if key not in memo:
            memo[key] = physical_class(pair.behavior_name(mt), coll)
        out.append(memo[key])
    return out


def neighbours(x, y, w, h):
    """The 4-neighbours of (x, y) inside a w x h window."""
    for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
        if 0 <= nx < w and 0 <= ny < h:
            yield nx, ny


def edge_class(seq):
    """Dominant class of one side, or "mixed" when no class reaches EDGE_PCT of it."""
    counts = Counter(seq)
    best = min(counts, key=lambda c: (-counts[c], c))
    return best if counts[best] * 100 >= EDGE_PCT * len(seq) else 'mixed'


def sides(cells, w, h):
    """The four border lines of a row-major window, in the index field order."""
    return {'top': cells[:w], 'bottom': cells[(h - 1) * w:],
            'left': cells[0::w], 'right': cells[w - 1::w]}


def has_shore(cells, w, h):
    return any(cells[y * w + x] == 'surf' and any(cells[ny * w + nx] == 'walk' for nx, ny in neighbours(x, y, w, h))
               for y in range(h) for x in range(w))


def has_pond(cells, w, h):
    """A surf component (4-connected) that does not touch the window edge."""
    seen = set()
    for y in range(h):
        for x in range(w):
            if (x, y) in seen or cells[y * w + x] != 'surf':
                continue
            seen.add((x, y))
            stack, touches_edge = [(x, y)], False
            while stack:
                cx, cy = stack.pop()
                touches_edge = touches_edge or cx in (0, w - 1) or cy in (0, h - 1)
                for nx, ny in neighbours(cx, cy, w, h):
                    if (nx, ny) not in seen and cells[ny * w + nx] == 'surf':
                        seen.add((nx, ny))
                        stack.append((nx, ny))
            if not touches_edge:
                return True
    return False


def has_cliff(cells, w, h):
    """A straight run (along a row or a column) of at least CLIFF_RUN wall cells with walk beside it."""
    lines = [[(x, y) for x in range(w)] for y in range(h)] + [[(x, y) for y in range(h)] for x in range(w)]
    for line in lines:
        run = []
        for pos in line + [None]:
            if pos is not None and cells[pos[1] * w + pos[0]] == 'wall':
                run.append(pos)
                continue
            if len(run) >= CLIFF_RUN and any(cells[ny * w + nx] == 'walk'
                                             for x, y in run for nx, ny in neighbours(x, y, w, h)):
                return True
            run = []
    return False


def direction_changes(values):
    """Number of sign flips in the non-zero steps of a sequence (a straight slope scores 0)."""
    steps = [1 if b > a else -1 for a, b in zip(values, values[1:]) if b != a]
    return sum(1 for s, t in zip(steps, steps[1:]) if s != t)


def shore_curvy(cells, w, h):
    """Direction changes along the surf/walk boundary. The boundary is read as one height per column
    (horizontal shore) or per row (vertical shore), whichever has more surf/walk edges, taking the
    first transition of each column or row; columns or rows without a transition are skipped."""
    def edge(a, b):
        return {a, b} == {'surf', 'walk'}
    across_rows = sum(edge(cells[y * w + x], cells[(y + 1) * w + x]) for y in range(h - 1) for x in range(w))
    across_cols = sum(edge(cells[y * w + x], cells[y * w + x + 1]) for y in range(h) for x in range(w - 1))
    profile = []
    if across_rows >= across_cols:
        for x in range(w):
            hit = [y for y in range(h - 1) if edge(cells[y * w + x], cells[(y + 1) * w + x])]
            if hit:
                profile.append(hit[0])
    else:
        for y in range(h):
            hit = [x for x in range(w - 1) if edge(cells[y * w + x], cells[y * w + x + 1])]
            if hit:
                profile.append(hit[0])
    return direction_changes(profile)


def describe(cells, w, h, has_event=False):
    """Index fields of one window from its row-major physical classes.
    Returns (fields, None) when kept, or (None, reason) when skipped. Checks run in this order:
    event, door, uniform."""
    if has_event:
        return None, 'event'
    if 'door' in cells:
        return None, 'door'
    total = w * h
    counts = Counter(cells)
    if max(counts.values()) * 100 > UNIFORM_PCT * total:
        return None, 'uniform'
    features = []
    if counts['surf'] and counts['walk'] and has_shore(cells, w, h):
        features.append('shore')
    if counts['surf'] and has_pond(cells, w, h):
        features.append('pond')
    if any(c.startswith('ledge_') for c in counts):
        features.append('ledge')
    if counts['grass_enc'] * 100 >= TALLGRASS_PCT * total:
        features.append('tallgrass')
    if counts['wall'] >= CLIFF_RUN and has_cliff(cells, w, h):
        features.append('cliff')
    fields = dict(classes={c: round(n / total, 4) for c, n in sorted(counts.items())},
                  edges={side: edge_class(seq) for side, seq in sides(cells, w, h).items()},
                  features=[f for f in FEATURES if f in features],
                  curvy=shore_curvy(cells, w, h) if 'shore' in features else 0)
    return fields, None


def window_digest(pair_key, w, h, ids):
    """Identity of a window's metatile content (dedupe key)."""
    return hashlib.sha1(f'{pair_key}|{w}x{h}|'.encode() + array.array('H', ids).tobytes()).hexdigest()


def scan_map(name, pair_key, map_w, map_h, classes, ids, event_cells, seen, stats, out, sizes=WINDOW_SIZES,
             primary_limit=0x280):
    """Append the kept windows of one map to `out`. `classes` and `ids` are row-major per cell (physical
    class, metatile id); `seen` holds digests of kept windows (the first one wins); `stats` collects
    scanned / kept / skipped-per-reason counts."""
    for w, h in sizes:
        for y in range(0, map_h - h + 1, STRIDE):
            for x in range(0, map_w - w + 1, STRIDE):
                stats['scanned'] += 1
                cells = [classes[(y + j) * map_w + x + i] for j in range(h) for i in range(w)]
                has_event = any(x <= ex < x + w and y <= ey < y + h for ex, ey in event_cells)
                fields, reason = describe(cells, w, h, has_event)
                if reason:
                    stats['skipped'][reason] += 1
                    continue
                window_ids = [ids[(y + j) * map_w + x + i] for j in range(h) for i in range(w)]
                digest = window_digest(pair_key, w, h, window_ids)
                if digest in seen:
                    stats['skipped']['duplicate'] += 1
                    continue
                seen.add(digest)
                out.append(dict(map=name, x=x, y=y, w=w, h=h, pair=pair_key,
                                portable=all(mt < primary_limit for mt in window_ids), **fields))
                stats['kept'] += 1


def build_index(repo):
    """Scan every vanilla outdoor map (lint.vanilla_outdoor). Returns (entries, stats, notes); writes nothing.
    Maps that cannot be read are skipped with an INFO note."""
    maps, layouts = repo.maps(), repo.layouts()
    stats = dict(scanned=0, kept=0, maps=0, skipped=dict.fromkeys(SKIP_REASONS, 0))
    notes, entries, seen = [], [], set()
    for name, _stratum in vanilla_outdoor(repo):
        try:
            grid = Grid(repo, name, maps[name], layouts[maps[name]['layout']])
            pair = Pair(repo, grid.layout)
            classes = physical_cells(pair, grid)
        except PkError as exc:
            notes.append(f'INFO map {name} skipped: {exc}')
            continue
        pair_key = f"{pair.primary['symbol']}+{pair.secondary['symbol']}"
        ids = [cell[0] for cell in grid.cells]
        stats['maps'] += 1
        scan_map(name, pair_key, grid.w, grid.h, classes, ids, list(events(grid.data)), seen, stats, entries,
                 primary_limit=pair.limits['metatiles'])
    return entries, stats, notes


def write_index(repo, entries):
    path = repo.path(INDEX_REL)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(entries, separators=(',', ':')) + '\n', encoding='utf-8')
    os.replace(tmp, path)
    return path


def load_index(repo, notes=None):
    """The index entries (a list of window dicts). Rebuilds and writes .pk/kit/index.json when it is
    missing; the rebuild's INFO notes are appended to `notes` when a list is given."""
    path = repo.path(INDEX_REL)
    if path.is_file():
        try:
            entries = json.loads(path.read_text(encoding='utf-8'))
            if all('portable' in entry for entry in entries):
                return entries
            if notes is not None:
                notes.append('INFO old kit index lacks portability; rebuilding')
        except (ValueError, UnicodeError) as exc:
            raise PkError('PARSE', f'{INDEX_REL}: {exc}; run pk kit index') from None
    entries, stats, built_notes = build_index(repo)
    write_index(repo, entries)
    if notes is not None:
        notes.extend(built_notes)
        notes.append(f'INFO index rebuilt: {len(entries)} windows')
    return entries


def command_index(repo, args, opts):
    if args != ['index']:
        raise PkError('PARSE', 'usage: pk kit index')
    entries, stats, notes = build_index(repo)
    write_index(repo, entries)
    lines = [f"scanned {stats['scanned']} kept {stats['kept']} "
             f"skipped {stats['scanned'] - stats['kept']}"]
    lines += [f"INFO skipped {reason}: {stats['skipped'][reason]}" for reason in SKIP_REASONS]
    lines += notes
    lines.append(f'wrote {INDEX_REL}')
    return Result(f"index {stats['kept']} windows from {stats['maps']} maps", lines)


def command(repo, args, opts):
    """Dispatch `pk kit ...` on args[0]: index | find | fit."""
    verb = args[0] if args else ''
    if verb == 'index' and len(args) == 1:
        return command_index(repo, args, opts)
    if verb == 'find':
        return command_find(repo, args, opts)
    if verb == 'fit':
        return command_fit(repo, args, opts)
    raise PkError('PARSE', 'usage: pk kit index | find [...] | fit MAP x,y,WxH; see pk help kit')

# --- search ---
# Owned by the search agent: find, fit, sheet, command_find, command_fit.
# Both commands get the dispatcher's args, so args[0] is the verb and args[1:] the positional words.
SIZE_NEAR = 2                  # --size: exact tier first, then within this many cells per dimension
DEFAULT_LIMIT, MAX_LIMIT = 10, 200
SIDES = ('top', 'bottom', 'left', 'right')
EDGE_VALUES = {'walk', 'surf', 'wall', 'grass_enc', 'door', 'mixed'} | {f'ledge_{d}' for d in 'nesw'}
TIER_ORDER = {'exact': 0, 'any': 0, 'near': 1}
SHEET_REL = '.pk/kit/sheet.png'
SHEET_COLUMNS, SHEET_GAP, SHEET_LABEL_H = 5, 8, 22
FIND_USAGE = ('usage: pk kit find [--size WxH] [--top C] [--bottom C] [--left C] [--right C] '
              '[--feature F] [--pair P] [--limit 10] [--sheet]')
FIT_USAGE = 'usage: pk kit fit MAP x,y,WxH [--limit 10] [--sheet]'


def parse_size(text):
    m = re.fullmatch(r'(\d+)x(\d+)', text or '')
    if not m or not int(m[1]) or not int(m[2]):
        raise PkError('PARSE', f'--size {text!r} must be WxH, e.g. 10x6')
    return int(m[1]), int(m[2])


def parse_box(text):
    m = re.fullmatch(r'(\d+),(\d+),(\d+)x(\d+)', text or '')
    if not m or not int(m[3]) or not int(m[4]):
        raise PkError('PARSE', f'box {text!r} must be x,y,WxH, e.g. 4,8,10x6')
    return tuple(int(v) for v in m.groups())


def parse_limit(opts):
    try:
        limit = int(opts.get('limit', DEFAULT_LIMIT))
    except ValueError:
        raise PkError('PARSE', '--limit must be an integer') from None
    if not 1 <= limit <= MAX_LIMIT:
        raise PkError('BOUNDS', f'--limit 1..{MAX_LIMIT}')
    return limit


def pair_name(pair):
    """Tileset pair as build_index stores it: "gTileset_Primary+gTileset_Secondary"."""
    return f"{pair.primary['symbol']}+{pair.secondary['symbol']}"


def parse_filters(repo, opts):
    """`pk kit find` options -> keyword arguments of find(). Every value is checked here."""
    filters = dict(size=None, top=None, bottom=None, left=None, right=None,
                   feature=None, pair=None, limit=parse_limit(opts))
    if 'size' in opts:
        filters['size'] = parse_size(opts['size'])
    for side in SIDES:
        if side in opts:
            if opts[side] not in EDGE_VALUES:
                raise PkError('PARSE', f"--{side} {opts[side]}; use one of {' '.join(sorted(EDGE_VALUES))}")
            filters[side] = opts[side]
    if 'feature' in opts:
        if opts['feature'] not in FEATURES:
            raise PkError('PARSE', f"--feature {opts['feature']}; use one of {' '.join(FEATURES)}")
        filters['feature'] = opts['feature']
    if 'pair' in opts:
        # Accepts "A+B", "A,B" or any map name, and resolves it the same way for every map.
        filters['pair'] = pair_name(pair_for(repo, opts['pair'].replace('+', ',')))
    return filters


def find(entries, size=None, top=None, bottom=None, left=None, right=None,
         feature=None, pair=None, limit=DEFAULT_LIMIT, exact_only=False):
    """Index windows that match, best first. Returns (hits, counts).

    hits: at most `limit` (tier, entry) pairs; tier is 'exact' or 'near' when size is given, else 'any'.
    counts: {'matched', 'exact', 'near'} over every match, not only the hits shown.
    Order: exact tier, near tier; for shore queries (feature shore, or both a surf and a walk edge) more
    curvy first; then fewer 'mixed' edges; then map name and position."""
    want = dict(top=top, bottom=bottom, left=left, right=right)
    requested = {v for v in want.values() if v is not None}
    shore = feature == 'shore' or {'surf', 'walk'} <= requested
    matches = []
    for entry in entries:
        if pair is not None and entry['pair'] != pair and not (
                entry.get('portable', False) and entry['pair'].split('+')[0] == pair.split('+')[0]):
            continue
        if feature is not None and feature not in entry['features']:
            continue
        if any(v is not None and entry['edges'][side] != v for side, v in want.items()):
            continue
        if size is None:
            tier = 'any'
        elif (entry['w'], entry['h']) == size:
            tier = 'exact'
        elif not exact_only and abs(entry['w'] - size[0]) <= SIZE_NEAR and abs(entry['h'] - size[1]) <= SIZE_NEAR:
            tier = 'near'
        else:
            continue
        matches.append((tier, entry))

    def rank(item):
        tier, entry = item
        mixed = sum(entry['edges'][side] == 'mixed' for side in SIDES)
        curvy = entry.get('curvy', 0) if shore else 0
        return (TIER_ORDER[tier], -curvy, mixed, entry['map'], entry['y'], entry['x'], entry['w'], entry['h'])

    matches.sort(key=rank)
    counts = dict(matched=len(matches), exact=sum(t == 'exact' for t, _ in matches),
                  near=sum(t == 'near' for t, _ in matches))
    return matches[:limit], counts


def hit_lines(hit, target=None, at=None):
    """Two output lines per hit: the window, then the ready-to-run copy command.
    Without target and at (find), the command keeps the TARGET and TX,TY placeholders."""
    _, entry = hit
    window = f"{entry['x']},{entry['y']},{entry['w']}x{entry['h']}"
    features = ','.join(entry['features']) or '-'
    dest = f'{at[0]},{at[1]}' if at else 'TX,TY'
    return [f"{entry['map']} {window} {entry['pair']} {features} curvy:{entry.get('curvy', 0)} portable:{str(entry.get('portable', False)).lower()}",
            f"pk map copy {target or 'TARGET'} {window} {dest} --from {entry['map']} --repair"]


def ring_edges(grid, pair, box):
    """Edge of each side of box, as (edges, notes), read the way the index reads a window side: the dominant
    physical class of the cells just outside it, or "mixed" when none reaches EDGE_PCT. A side that lies off
    the map is None (not constrained). Physical classes come from lint.physical_class via physical_cells only."""
    x0, y0, w, h = box
    ring = dict(top=[(x, y0 - 1) for x in range(x0, x0 + w)],
                bottom=[(x, y0 + h) for x in range(x0, x0 + w)],
                left=[(x0 - 1, y) for y in range(y0, y0 + h)],
                right=[(x0 + w, y) for y in range(y0, y0 + h)])
    classes = physical_cells(pair, grid)
    edges, notes = {}, []
    for side in SIDES:
        cells = [(x, y) for x, y in ring[side] if grid.inside(x, y)]
        if not cells:
            edges[side] = None
            notes.append(f'INFO {side}: the box touches the map edge, so this side is not constrained')
            continue
        counts = Counter(classes[y * grid.w + x] for x, y in cells)
        best = min(counts, key=lambda c: (-counts[c], c))
        if counts[best] * 100 < EDGE_PCT * len(cells):
            edges[side] = 'mixed'
            notes.append(f'INFO {side}: {best} is only {counts[best] * 100 // len(cells)}% of the ring, '
                         'so the side is matched as mixed (as the index stores it)')
        else:
            edges[side] = best
    return edges, notes


def fit(repo, query, box, entries, limit=DEFAULT_LIMIT):
    """Pieces whose stored edges match the ring around box on the target map, with the box size and the
    target's tileset pair. Returns (target name, edges, notes, hits, counts). Writes nothing."""
    grid, pair = load(repo, query)
    x0, y0, w, h = box
    if not (grid.inside(x0, y0) and grid.inside(x0 + w - 1, y0 + h - 1)):
        raise PkError('BOUNDS', f'box {x0},{y0},{w}x{h} outside {grid.name} {grid.w}x{grid.h}')
    edges, notes = ring_edges(grid, pair, box)
    hits, counts = find(entries, size=(w, h), pair=pair_name(pair), limit=limit, exact_only=True, **edges)
    return grid.name, edges, notes, hits, counts


def sheet(repo, hits, scale=2):
    """PNG of the hits, SHEET_COLUMNS per row, each piece labelled with its rank (1 = first printed result).
    Returns (png bytes, or None when nothing could be drawn; INFO notes for pieces that were skipped)."""
    notes, pieces, maps = [], [], {}
    for rank, (_, entry) in enumerate(hits, 1):
        name, x, y, w, h = entry['map'], entry['x'], entry['y'], entry['w'], entry['h']
        try:
            if name not in maps:
                maps[name] = load(repo, name)
            grid, pair = maps[name]
            if not (grid.inside(x, y) and grid.inside(x + w - 1, y + h - 1)):
                raise PkError('BOUNDS', f'window outside {grid.w}x{grid.h}; the index is stale, run pk kit index')
            png = render_png(repo, grid, pair, (x, y, w, h), scale)
        except PkError as exc:
            notes.append(f'INFO rank {rank} not drawn ({name} {x},{y} {w}x{h}): {exc.code} {exc}')
            continue
        pieces.append((rank, entry, Image.open(io.BytesIO(png)).convert('RGB')))
    if not pieces:
        return None, notes
    try:
        font = ImageFont.load_default(size=16)
    except TypeError:    # Pillow older than 10.1: the default font has no size
        font = ImageFont.load_default()
    probe = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    labels = [f"{rank}  {e['map']} {e['x']},{e['y']} {e['w']}x{e['h']}" for rank, e, _ in pieces]
    cell_w = max(max(img.width for _, _, img in pieces), max(int(probe.textlength(t, font=font)) for t in labels))
    cell_h = SHEET_LABEL_H + max(img.height for _, _, img in pieces)
    cols = min(SHEET_COLUMNS, len(pieces))
    rows = -(-len(pieces) // cols)
    canvas = Image.new('RGB', (cols * cell_w + (cols + 1) * SHEET_GAP, rows * cell_h + (rows + 1) * SHEET_GAP),
                       (24, 24, 24))
    draw = ImageDraw.Draw(canvas)
    for i, ((_, _, img), label) in enumerate(zip(pieces, labels)):
        x = SHEET_GAP + (i % cols) * (cell_w + SHEET_GAP)
        y = SHEET_GAP + (i // cols) * (cell_h + SHEET_GAP)
        draw.text((x, y + 3), label, fill=(255, 255, 255), font=font)
        canvas.paste(img, (x, y + SHEET_LABEL_H))
    buf = io.BytesIO()
    canvas.save(buf, 'PNG')
    return buf.getvalue(), notes


def write_sheet(repo, hits):
    """Writes .pk/kit/sheet.png (git-ignored cache). Returns (INFO lines, relative path or None)."""
    if not hits:
        return ['INFO sheet not written: no results'], None
    png, notes = sheet(repo, hits)
    if png is None:
        return notes + ['INFO sheet not written: nothing could be drawn'], None
    path = repo.path(SHEET_REL)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)
    return notes, SHEET_REL


def with_sheet(repo, opts, hits, result):
    if opts.get('sheet'):
        notes, rel = write_sheet(repo, hits)
        result.data += notes
        if rel:
            result.data.append(f'sheet {rel}')
    return result


def command_find(repo, args, opts):
    """pk kit find. args = ['find'] (the dispatcher's list); positional words are refused."""
    if args[1:]:
        raise PkError('PARSE', FIND_USAGE)
    filters = parse_filters(repo, opts)
    notes = []
    entries = load_index(repo, notes)
    hits, counts = find(entries, **filters)
    summary = f"find {len(hits)} of {counts['matched']} matches"
    if filters['size']:
        summary += f" (exact {counts['exact']}, near {counts['near']})"
    lines = notes + [line for hit in hits for line in hit_lines(hit)]
    if not entries:
        lines.append('INFO the index has no windows; check the vanilla maps, then run pk kit index')
    elif not hits:
        lines.append('INFO no window matches; drop an edge or --size, or change --feature')
    return with_sheet(repo, opts, hits, Result(summary, lines))


def command_fit(repo, args, opts):
    """pk kit fit MAP x,y,WxH. args = ['fit', MAP, box]. Only --limit and --sheet apply."""
    words = args[1:]
    if len(words) != 2:
        raise PkError('PARSE', FIT_USAGE)
    ignored = [key for key in ('size', 'feature', 'pair', *SIDES) if key in opts]
    if ignored:
        raise PkError('PARSE', f'fit takes MAP x,y,WxH only; drop --{ignored[0]}')
    limit = parse_limit(opts)
    box = parse_box(words[1])
    notes = []
    entries = load_index(repo, notes)
    name, edges, fit_notes, hits, counts = fit(repo, words[0], box, entries, limit)
    x, y, w, h = box
    edge_text = ' '.join(f"{side}={edges[side] or 'free'}" for side in SIDES)
    lines = notes + fit_notes + [line for hit in hits for line in hit_lines(hit, target=name, at=(x, y))]
    if not entries:
        lines.append('INFO the index has no windows; check the vanilla maps, then run pk kit index')
    elif not hits:
        lines.append('INFO no piece matches these edges and size; try pk kit find with fewer edges')
    summary = f"fit {name} {x},{y},{w}x{h} edges {edge_text} matches {counts['matched']}"
    return with_sheet(repo, opts, hits, Result(summary, lines))
