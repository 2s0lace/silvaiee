"""Tile cards: what each metatile IS, computed from vanilla maps and game data (parallel task D).

An agent that sees `0x12C` cannot tell what it is; round 1 guessed names and many are wrong
(`0x045` is `water_151` but a blocked grey block, `0x0D4` is `grass_3` but a rock). A card is
computed, never guessed, so it cannot lie. Where the data is unclear the card says `mixed`.

A card is built per (tileset pair, metatile) from every vanilla *outdoor* layout of that pair
(`lint.vanilla_outdoor`; `Pk*` drafts are never reference data). Like `kit`, it also folds in
"portable" primary metatiles (id < `pair.limits['metatiles']`) seen in other pairs that share the
same primary tileset, counting a neighbour only when that neighbour is itself primary too. A
metatile's own class/collision/elevation come from `lint.metatile_physical` (its own vanilla usage,
with the unused-tile fallback, as the task requires: `0x045` unused in the PalletTown pair is
`walk`). Neighbour classes are the physical class of each neighbour *cell* (`lint.physical_class`
of its behaviour and the collision the map gives it) -- what is really seen there.

Card fields (all derived from the aggregate, none invented):
  class/coll/elev      `lint.metatile_physical`; `uses` is the vanilla use count.
  nbr[dir][class]       neighbour physical classes seen in direction N,E,S,W,NE,SE,SW,NW.
  role                  a phrase from the fixed rules in `classify_role` (never invented).
  needs[dir]            the one metatile that is the neighbour in >= NEED_SHARE of the uses at a
                        cardinal direction, when there are >= NEED_MIN uses; any needs => object.
  fits[dir]             the 3 most common neighbour metatiles of a cardinal direction.
  name/status           the current name and whether its prefix contradicts the card.

Role rules (fixed; `classify_role`): let `own[d]` be the own-class share and `other[d]` the
share of the most common other class in direction d. A metatile used fewer than ROLE_MIN (5)
times is always `mixed`: one observation is not a pattern.
  open c            own[d] >= OPEN_MIN (0.60) for all 8 directions.
  edge: o d         exactly one cardinal d with other[d] >= EDGE_MIN (0.70); the other cardinals
                    keep own >= OPEN_MIN. o is the other class.
  outer corner: o   exactly two adjacent cardinals with other >= EDGE_MIN and the same class o.
  inner corner: o   no cardinal reaches EDGE_MIN and exactly one diagonal reaches DIAG_MIN
                    (0.90): a diagonal is a single cell, so it needs more evidence than a side.
  mixed             anything else; no dominant neighbourhood.
Role families: open / edge / corner (outer+inner) / mixed.

Cache: `.pk/cards/<primary>+<secondary>.json` (git-ignored), raw aggregates only, so changing a
role rule does not require a rebuild.
"""
import json
import os
from collections import Counter

from ..core.errors import PkError
from ..core.out import Result
from .grid import decode
from .lint import physical_class, vanilla_outdoor

CARDS_DIR = '.pk/cards'
CARDS_VERSION = 2
DIRS8 = ('N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW')
CARDINALS = ('N', 'E', 'S', 'W')
DIAGONALS = ('NE', 'SE', 'SW', 'NW')
DELTA = {'N': (0, -1), 'NE': (1, -1), 'E': (1, 0), 'SE': (1, 1),
         'S': (0, 1), 'SW': (-1, 1), 'W': (-1, 0), 'NW': (-1, -1)}
ADJACENT = {('N', 'E'), ('E', 'S'), ('S', 'W'), ('W', 'N')}

OPEN_MIN = 0.60        # own-class share needed in every direction for `open`
EDGE_MIN = 0.70        # other-class share on a cardinal side for `edge`/`outer corner`
DIAG_MIN = 0.90        # other-class share on a diagonal for `inner corner` (higher: diagonals are noisier)
NEED_SHARE = 0.90      # share of one neighbour metatile for a `needs` constraint
NEED_MIN = 5           # minimum uses before a `needs` constraint is trustworthy
NEED_DIRS = CARDINALS   # directions a `needs` constraint may name
ROLE_MIN = 5           # below this many uses a role is not trustworthy: report `mixed`
FIT_TOP = 3            # neighbour metatiles listed per cardinal side
PK_LAYOUT = 'LAYOUT_PK_'
PHYSICAL = {'walk', 'surf', 'wall', 'grass_enc', 'door',
            'ledge_n', 'ledge_e', 'ledge_s', 'ledge_w'}
# name prefix -> physical classes it agrees with (walkable grass and sand are legitimate).
NAME_IMPLIES = {'water': {'surf'}, 'grass': {'grass_enc', 'walk'}, 'sand': {'walk'},
                'solid': {'wall'}, 'ledge': {'ledge_n', 'ledge_e', 'ledge_s', 'ledge_w'}}
NAME_REQUIRES_ROLE = {'grass', 'sand'}     # these also need a resolved (non-mixed) role


# --- aggregate ------------------------------------------------------------------------

def _blank():
    return dict(uses=0, coll=Counter(), elev=Counter(),
                ncls={d: Counter() for d in DIRS8}, nmt={d: Counter() for d in DIRS8})


def _read_raw(repo, relative):
    if repo.transaction is None:
        return repo.read(relative)
    return repo.path(relative).read_bytes()


def _layouts(repo, pair):
    """[(layout_id, layout)] for every vanilla outdoor layout sharing the pair's primary."""
    maps, layouts = repo.maps(), repo.layouts()
    prim = pair.primary['symbol']
    seen, out = set(), []
    for name, _stratum in vanilla_outdoor(repo):
        lid = maps[name]['layout']
        if lid in seen:
            continue
        seen.add(lid)
        layout = layouts.get(lid)
        if layout is None or str(lid).startswith(PK_LAYOUT):
            continue
        if layout.get('primary_tileset') == prim:
            out.append((lid, layout))
    return out


def aggregate(repo, pair, exclude_layouts=()):
    """Raw per-metatile neighbour statistics (dict mt -> {uses, coll, elev, nmt})."""
    prim, sec = pair.primary['symbol'], pair.secondary['symbol']
    limit = pair.limits['metatiles']
    exclude = set(exclude_layouts)
    data = {}
    for lid, layout in _layouts(repo, pair):
        if lid in exclude:
            continue
        own = layout.get('secondary_tileset') == sec
        w, h = layout['width'], layout['height']
        try:
            cells = decode(_read_raw(repo, layout['blockdata_filepath'])[:w * h * 2])
        except (PkError, OSError):
            continue
        if len(cells) != w * h:
            continue
        for y in range(h):
            for x in range(w):
                mt, coll, elev = cells[y * w + x]
                if not own and mt >= limit:
                    continue
                rec = data.setdefault(mt, _blank())
                rec['uses'] += 1
                rec['coll'][coll] += 1
                rec['elev'][elev] += 1
                for d, (dx, dy) in DELTA.items():
                    nx, ny = x + dx, y + dy
                    if not (0 <= nx < w and 0 <= ny < h):
                        continue
                    nmt, ncoll, _ = cells[ny * w + nx]
                    if not own and nmt >= limit:
                        continue
                    rec['ncls'][d][physical_class(pair.behavior_name(nmt), ncoll)] += 1
                    rec['nmt'][d][nmt] += 1
    return data


def finalize(data, pair):
    """Raw aggregates -> {metatile: card}. The only place a class/role is decided.

    The metatile's own class/collision/elevation come from `lint.metatile_physical` (its own
    vanilla usage, with the unused-tile fallback). Neighbour classes are the physical class of
    each neighbour *cell* (`physical_class` of its behaviour and the collision the map gives it),
    i.e. what is really seen there. Only the neighbour counts are portable-aware (`aggregate`)."""
    from .lint import metatile_physical
    repo = pair.repo
    cards = {}
    for mt, rec in data.items():
        cls, coll, elev = metatile_physical(repo, pair, mt)
        nbr = {d: dict(rec['ncls'][d]) for d in DIRS8}
        role, family, predict = classify_role(cls, rec['ncls'])
        if rec['uses'] < ROLE_MIN:
            role, family, predict = 'mixed', 'mixed', {}
        needs = {}
        for d in NEED_DIRS:
            total = sum(rec['nmt'][d].values())
            if total >= NEED_MIN:
                top, n = max(rec['nmt'][d].items(), key=lambda kv: (kv[1], -kv[0]))
                if n >= NEED_SHARE * total:
                    needs[d] = top
        fits = {d: [(mt2, n, sum(rec['nmt'][d].values())) for mt2, n in rec['nmt'][d].most_common(FIT_TOP)]
                for d in CARDINALS}
        name = pair.info(mt).get('name')
        cards[mt] = dict(mt=mt, cls=cls, coll=coll, elev=elev,
                         uses=rec['uses'], nbr=nbr,
                         role=role, family=family, predict=predict,
                         object=bool(needs), needs=needs, fits=fits,
                         name=name, status=name_status(name, cls, role, family))
    return cards


# --- role rules -----------------------------------------------------------------------

def _shares(cls, nbr):
    """Per direction (own-class share, other-class share, other class)."""
    out = {}
    for d in DIRS8:
        counts = nbr[d]
        total = sum(counts.values())
        if not total:
            out[d] = (0.0, 0.0, None)
            continue
        own = counts.get(cls, 0) / total
        others = {c: n for c, n in counts.items() if c != cls}
        if others:
            oc = max(others, key=lambda c: (others[c], c))
            out[d] = (own, others[oc] / total, oc)
        else:
            out[d] = (own, 0.0, None)
    return out


def classify_role(cls, nbr):
    """Fixed role rules (see the module docstring). Returns (role, family, predict)."""
    sh = _shares(cls, nbr)
    if all(sh[d][0] >= OPEN_MIN for d in DIRS8):
        return f'open {cls}', 'open', {d: cls for d in DIRS8}
    dev_card = [d for d in CARDINALS if sh[d][1] >= EDGE_MIN]
    dev_diag = [d for d in DIAGONALS if sh[d][1] >= DIAG_MIN]
    if len(dev_card) == 1:
        d = dev_card[0]
        o = sh[d][2]
        if all(sh[x][0] >= OPEN_MIN for x in CARDINALS if x != d):
            return f'edge: {o} {d}', 'edge', {d: o}
    if len(dev_card) == 2 and tuple(sorted(dev_card)) in ADJACENT:
        d1, d2 = dev_card
        if sh[d1][2] == sh[d2][2]:
            o = sh[d1][2]
            return f'outer corner: {o} {d1}+{d2}', 'corner', {d1: o, d2: o}
    if not dev_card and len(dev_diag) == 1:
        d = dev_diag[0]
        return f'inner corner: {sh[d][2]} {d}', 'corner', {d: sh[d][2]}
    return 'mixed', 'mixed', {}


def role_family(role):
    if role.startswith('open'):
        return 'open'
    if role.startswith('edge'):
        return 'edge'
    if role.startswith(('outer corner', 'inner corner')):
        return 'corner'
    return 'mixed'


def name_status(name, cls, role, family):
    """`ok`, or NAME CONTRADICTS when the prefix disagrees with the physical class or role.

    `grass_`/`sand_` describe walkable terrain, so they also need a resolved (non-mixed) role: a
    tile that is walkable but sits in a rock/cliff neighbourhood (a mixed role) is not grass."""
    if not name:
        return None
    prefix = name.split('_', 1)[0]
    implied = NAME_IMPLIES.get(prefix)
    if implied is None:
        return 'ok'
    if cls in implied:
        if prefix in NAME_REQUIRES_ROLE and cls == 'walk' and family not in ('open', 'edge', 'corner'):
            return 'NAME CONTRADICTS'
        return 'ok'
    role_classes = {tok for tok in role.replace(':', ' ').replace('+', ' ').split() if tok in PHYSICAL}
    if role_classes & implied:
        return 'ok'
    return 'NAME CONTRADICTS'


# --- rendering ------------------------------------------------------------------------

def _pct(n, total):
    return round(100 * n / total) if total else 0


def _fits_text(fits):
    return ' '.join(f'0x{mt:03X} {_pct(n, total)}%' for mt, n, total in fits)


def row_text(card):
    """One line for `pk meta cards`: `0xABC class role`."""
    return f"0x{card['mt']:03X} {card['cls']} {card['role']}"


def card_lines(card):
    """At most 8 lines describing one card."""
    nbr = {d: sum(card['nbr'][d].values()) for d in DIRS8}
    neigh = ' '.join(f'{d}:{_top_class(card["nbr"][d])}{_pct(card["nbr"][d].get(_top_class(card["nbr"][d]), 0), nbr[d])}%'
                     for d in DIRS8)
    fits_ns = _fits_text(card['fits']['N']) + ' | S: ' + _fits_text(card['fits']['S'])
    fits_ew = _fits_text(card['fits']['E']) + ' | W: ' + _fits_text(card['fits']['W'])
    if card['needs']:
        obj = 'object: needs ' + ', '.join(f'0x{mt:03X} at {d}' for d, mt in sorted(card['needs'].items()))
    else:
        obj = 'object: no'
    if card['name'] is None:
        name = 'name: - (unnamed)'
    else:
        name = f"name: {card['name']} ({card['status'].lower()})"
    return [f"0x{card['mt']:03X} {card['cls']} elev {card['elev']} used {card['uses']}x",
            f'neigh: {neigh}',
            f"role: {card['role']}",
            f'fits N: {fits_ns}',
            f'fits E: {fits_ew}',
            obj,
            name]


def _top_class(counts):
    return max(counts, key=lambda c: (counts[c], c)) if counts else '-'


def role_symbol(card):
    """Distinguishes outer (+) from inner (x) corners for the map view."""
    if card['cls'] == 'wall' or card['object']:
        return '#'
    if card['family'] == 'open':
        return '~' if card['cls'] == 'surf' else '.'
    if card['family'] == 'edge':
        return '-'
    if card['family'] == 'corner':
        return '+' if card['role'].startswith('outer') else 'x'
    return '?'


# --- cache ----------------------------------------------------------------------------

def _cache_path(repo, pair):
    key = f"{pair.primary['symbol']}+{pair.secondary['symbol']}"
    return repo.path(f'{CARDS_DIR}/{key}.json')


def _dump(data):
    return {str(mt): dict(uses=rec['uses'], coll={str(k): v for k, v in rec['coll'].items()},
                          elev={str(k): v for k, v in rec['elev'].items()},
                          ncls={d: {c: n for c, n in rec['ncls'][d].items()} for d in DIRS8},
                          nmt={d: {str(m): n for m, n in rec['nmt'][d].items()} for d in DIRS8})
            for mt, rec in data.items()}


def _load(doc):
    data = {}
    for mt, rec in doc.items():
        data[int(mt)] = dict(uses=rec['uses'], coll=Counter({int(k): v for k, v in rec['coll'].items()}),
                             elev=Counter({int(k): v for k, v in rec['elev'].items()}),
                             ncls={d: Counter(rec['ncls'][d]) for d in DIRS8},
                             nmt={d: Counter({int(m): n for m, n in rec['nmt'][d].items()}) for d in DIRS8})
    return data


def load_cards(repo, pair, exclude_layouts=()):
    """Cards of a pair, from the `.pk/cards/` cache when the key is untouched."""
    key = f"{pair.primary['symbol']}+{pair.secondary['symbol']}"
    if not exclude_layouts:
        try:
            path = _cache_path(repo, pair)
            if path.is_file():
                doc = json.loads(path.read_text('utf-8'))
                if doc.get('version') == CARDS_VERSION and doc.get('key') == key:
                    return finalize(_load(doc['mt']), pair)
        except (PkError, OSError, ValueError, UnicodeError, KeyError, TypeError):
            pass
    data = aggregate(repo, pair, exclude_layouts)
    if not exclude_layouts:
        try:
            path = _cache_path(repo, pair)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix('.json.tmp')
            doc = dict(version=CARDS_VERSION, key=key, mt=_dump(data))
            tmp.write_text(json.dumps(doc, separators=(',', ':')) + '\n', encoding='utf-8')
            os.replace(tmp, path)
        except (OSError, PkError):
            pass
    return finalize(data, pair)


# --- commands -------------------------------------------------------------------------

def _pair_for_map(repo, query):
    from .meta import pair_for
    return pair_for(repo, query)


def command(repo, tx, args, opts):
    """`pk meta card MAP METATILE` and `pk meta cards MAP [filters] [--sheet]`."""
    verb = args[0]
    rest = args[1:]
    if verb == 'card' and len(rest) == 2:
        pair = _pair_for_map(repo, rest[0])
        mt = pair.lookup(rest[1])
        cards = load_cards(repo, pair)
        card = cards.get(mt)
        if card is None:
            return Result(f"card {rest[0]} 0x{mt:03X}", ['unused in vanilla outdoor maps of this pair'])
        return Result(f"card 0x{mt:03X} {pair.primary['symbol']}+{pair.secondary['symbol']}", card_lines(card))
    if verb == 'cards' and len(rest) == 1:
        pair = _pair_for_map(repo, rest[0])
        cards = load_cards(repo, pair)
        rows, notes = cards_rows(cards, opts)
        result = Result(f"cards {pair.primary['symbol']}+{pair.secondary['symbol']} {len(rows)}", notes + rows)
        if opts.get('sheet'):
            note, rel = _sheet(repo, pair, cards)
            result.data += note
            if rel:
                result.data.append(f'sheet {rel}')
        return result
    raise PkError('PARSE', 'usage: pk meta card MAP METATILE | pk meta cards MAP [--role R --class C --limit N --sheet]')


def cards_rows(cards, opts):
    """Filtered `0xABC class role` lines. `--role object` selects tiles that are part of an object."""
    want_role = opts.get('role')
    want_class = opts.get('class')
    if want_role and want_role not in ('open', 'edge', 'corner', 'mixed', 'object'):
        raise PkError('PARSE', f"--role {want_role}; use open edge corner mixed object")
    if want_class and want_class not in PHYSICAL:
        raise PkError('PARSE', f"--class {want_class}; use one of {' '.join(sorted(PHYSICAL))}")
    try:
        limit = int(opts.get('limit', 20))
    except ValueError:
        raise PkError('PARSE', '--limit must be an integer') from None
    if limit < 1:
        raise PkError('BOUNDS', '--limit must be >= 1')
    sel = []
    for card in cards.values():
        if want_role == 'object' and not card['object']:
            continue
        if want_role and want_role != 'object' and card['family'] != want_role:
            continue
        if want_class and card['cls'] != want_class:
            continue
        sel.append(card)
    sel.sort(key=lambda c: (-c['uses'], c['mt']))
    return [row_text(c) for c in sel[:limit]], []


def show_roles(repo, query, opts):
    """`pk map show MAP --roles [--rect x,y,WxH]`: one symbol per role family plus a legend."""
    from .edit import load
    from .layers import parse_rect
    grid, pair = load(repo, query)
    rect = parse_rect(opts.get('rect'), grid.w, grid.h)
    cards = load_cards(repo, pair)
    unknown = set()
    x0, y0, w, h = rect
    body = []
    for y in range(y0, y0 + h):
        line = ''
        for x in range(x0, x0 + w):
            card = cards.get(grid.at(x, y)[0])
            if card is None:
                unknown.add(grid.at(x, y)[0])
                line += '?'
            else:
                line += role_symbol(card)
        body.append(f'{y:02d}|{line}')
    legend = ['legend ~=open surf .=open walk #=wall/object -=edge +=outer corner x=inner corner ?=mixed']
    if unknown:
        legend.append('INFO ' + ' '.join(f'0x{mt:03X}' for mt in sorted(unknown)[:10]) + ' unknown (not in this pair)')
    return Result(f'{grid.name} {grid.w}x{grid.h} rect {x0},{y0},{w}x{h} roles', legend + body)


# --- sheet ----------------------------------------------------------------------------

def _tile_image(repo, pair, mt, scale):
    from PIL import Image
    from .meta import draw
    img = Image.new('RGBA', (16, 16), (0, 0, 0, 0))
    if pair.valid(mt):
        for j, row in enumerate(draw(repo, pair, mt)):
            for i, p in enumerate(row):
                if p:
                    img.putpixel((i, j), (*p, 255))
    return img.resize((16 * scale, 16 * scale), Image.NEAREST)


def _sheet(repo, pair, cards, out=None, scale=3, per_family=10):
    """`.pk/render/cards_<pair>.png`: `per_family` most-used tiles of each role family, role below."""
    from PIL import Image, ImageDraw, ImageFont
    families = (('open', 'open'), ('edge', 'edge'), ('corner', 'corner'))
    chosen = []
    for fam, label in families:
        pool = [c for c in cards.values() if c['family'] == fam]
        pool.sort(key=lambda c: (-c['uses'], c['mt']))
        chosen.append((label, pool[:per_family]))
    if not any(pool for _, pool in chosen):
        return ['INFO sheet not written: no cards'], None
    try:
        font = ImageFont.load_default(size=13)
    except TypeError:
        font = ImageFont.load_default()
    probe = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    cell = 16 * scale
    labels = [f"{label}: {c['role']}" for label, pool in chosen for c in pool]
    cw = max([cell] + [int(probe.textlength(t, font=font)) for t in labels])
    ch = cell + 16
    gap = 6
    cols = per_family
    rows = sum(1 for _, pool in chosen if pool)
    width = gap + cols * (cw + gap)
    height = gap + rows * (ch + gap + 18)
    canvas = Image.new('RGB', (width, height), (24, 24, 24))
    pen = ImageDraw.Draw(canvas)
    y = gap
    for label, pool in chosen:
        if not pool:
            continue
        pen.text((gap, y), label, fill=(255, 255, 255), font=font)
        for i, card in enumerate(pool):
            x = gap + i * (cw + gap)
            image = _tile_image(repo, pair, card['mt'], scale)
            canvas.paste(image, (x, y + 16), image)
            pen.text((x, y + 16 + cell + 1), f"0x{card['mt']:03X} {card['role']}",
                     fill=(200, 200, 200), font=font)
        y += ch + gap + 18
    path = repo.path(out or f'.pk/render/cards_{pair.primary["symbol"]}+{pair.secondary["symbol"]}.png')
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path, 'PNG')
    return [], path.relative_to(repo.root).as_posix()
