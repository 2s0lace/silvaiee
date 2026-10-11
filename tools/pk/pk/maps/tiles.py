"""Tileset knowledge: header registration, attributes, behaviors, graphics, names."""
import json
import re
import struct

from ..core.errors import PkError

LIMITS = {  # include/fieldmap.h
    'frlg': dict(tiles=640, metatiles=640, pals=7),
    'emerald': dict(tiles=512, metatiles=512, pals=6),
}
TOTAL_TILES = TOTAL_METATILES = 1024
TOTAL_PALS = 13
NAMES_DIR = 'tools/pk/data/metatiles'


def _registry(repo):
    cache = getattr(repo, '_pk_tileset_registry', None)
    if cache is None:
        headers = repo.read('src/data/tilesets/headers.h').decode('utf-8')
        graphics = repo.read('src/data/tilesets/graphics.h').decode('utf-8')
        metatiles = repo.read('src/data/tilesets/metatiles.h').decode('utf-8')
        cache = {}
        for match in re.finditer(r'const struct Tileset\s+(\w+)\s*=\s*\{(.*?)\};', headers, re.S):
            body = dict(re.findall(r'\.(\w+)\s*=\s*([^,\n]+)', match[2]))
            entry = dict(symbol=match[1], secondary=body.get('isSecondary', 'FALSE').strip() == 'TRUE')
            tiles = re.search(re.escape(body.get('tiles', '?').strip()) + r'\[\]\s*=\s*INCGFX_U32\("([^"]+)"', graphics)
            pal = re.search(re.escape(body.get('palettes', '?').strip()) + r'\[\]\[16\]\s*=\s*\{(.*?)\};', graphics, re.S)
            meta = re.search(re.escape(body.get('metatiles', '?').strip()) + r'\[\]\s*=\s*INCBIN_U16\("([^"]+)"', metatiles)
            attr = re.search(re.escape(body.get('metatileAttributes', '?').strip()) + r'\[\]\s*=\s*INCBIN_U16\("([^"]+)"', metatiles)
            entry['tiles'] = tiles[1] if tiles else None
            entry['palettes'] = re.findall(r'"([^"]+\.pal)"', pal[1]) if pal else []
            entry['metatiles'] = meta[1] if meta else None
            entry['attributes'] = attr[1] if attr else None
            cache[match[1]] = entry
        repo._pk_tileset_registry = cache
    return cache


def tileset(repo, symbol):
    reg = _registry(repo)
    if symbol not in reg:
        raise PkError('NOTFOUND', f'tileset {symbol}; use pk find --kind tileset')
    return reg[symbol]


def behaviors(repo, version):
    """{value: name} for metatile behaviors."""
    key = f'_pk_behaviors_{version}'
    cache = getattr(repo, key, None)
    if cache is not None:
        return cache
    cache = {}
    if version == 'frlg':
        text = repo.read('include/constants/metatile_behaviors_frlg.h').decode('utf-8')
        for name, value in re.findall(r'#define\s+(MB_FRLG_\w+)\s+(0x[0-9A-Fa-f]+|\d+)', text):
            cache.setdefault(int(value, 0), name)
    else:
        text = repo.read('include/constants/metatile_behaviors.h').decode('utf-8')
        body = re.search(r'enum\s*\{(.*?)\}', text, re.S)[1]
        n = 0
        for item in re.findall(r'(MB_\w+)(?:\s*=\s*(\w+))?\s*,', body):
            n = int(item[1], 0) if item[1] else n
            cache.setdefault(n, item[0])
            n += 1
    setattr(repo, key, cache)
    return cache


class Pair:
    """The primary+secondary tileset pair of one layout; metatile ids are global (0..1023)."""
    def __init__(self, repo, layout):
        self.repo = repo
        self.version = repo.version(layout)
        self.limits = LIMITS[self.version]
        self.primary = tileset(repo, layout['primary_tileset'])
        self.secondary = tileset(repo, layout['secondary_tileset'])
        self.attr_size = 4 if self.version == 'frlg' else 2
        self._attrs = {}
        self._names = {}

    def owner(self, metatile):
        return self.primary if metatile < self.limits['metatiles'] else self.secondary

    def local(self, metatile):
        return metatile if metatile < self.limits['metatiles'] else metatile - self.limits['metatiles']

    def count(self, ts):
        counts = self.repo.__dict__.setdefault('_pk_metatile_counts', {})
        if ts['symbol'] not in counts:
            counts[ts['symbol']] = len(self.repo.read(ts['metatiles'])) // 16 if ts['metatiles'] else 0
        return counts[ts['symbol']]

    def valid(self, metatile):
        ts = self.owner(metatile)
        return 0 <= self.local(metatile) < self.count(ts)

    def attributes(self, ts):
        if ts['symbol'] not in self._attrs:
            raw = self.repo.read(ts['attributes']) if ts['attributes'] else b''
            fmt = 'I' if self.attr_size == 4 else 'H'
            self._attrs[ts['symbol']] = struct.unpack(f'<{len(raw)//self.attr_size}{fmt}', raw[:len(raw)//self.attr_size*self.attr_size])
        return self._attrs[ts['symbol']]

    def behavior(self, metatile):
        attrs = self.attributes(self.owner(metatile))
        i = self.local(metatile)
        if i >= len(attrs):
            return 0
        return attrs[i] & (0x1FF if self.version == 'frlg' else 0xFF)

    def layer(self, metatile):
        attrs = self.attributes(self.owner(metatile))
        i = self.local(metatile)
        if i >= len(attrs):
            return 0
        return (attrs[i] >> 29) & 3 if self.version == 'frlg' else (attrs[i] >> 12) & 0xF

    def behavior_name(self, metatile):
        return behaviors(self.repo, self.version).get(self.behavior(metatile), hex(self.behavior(metatile)))

    # --- semantic names (sidecar) -------------------------------------------------
    def names(self, ts):
        sym = ts['symbol']
        if sym not in self._names:
            try:
                self._names[sym] = json.loads(self.repo.read(f'{NAMES_DIR}/{sym}.json'))
            except PkError:
                self._names[sym] = {}
        return self._names[sym]

    def info(self, metatile):
        return self.names(self.owner(metatile)).get(f'{metatile:03X}', {})

    def lookup(self, word):
        """Metatile id from 0x1A3 / 419 / semantic name (unique within the pair)."""
        if re.fullmatch(r'0x[0-9A-Fa-f]+|\d+', word):
            value = int(word, 0)
            if not self.valid(value):
                raise PkError('BOUNDS', f'metatile {word} not in {self.primary["symbol"]}/{self.secondary["symbol"]}')
            return value
        hits = [int(k, 16) for ts in (self.primary, self.secondary) for k, v in self.names(ts).items() if v.get('name') == word]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise PkError('AMBIGUOUS', f'metatile name {word}', [f'0x{h:03X}' for h in hits[:10]])
        raise PkError('NOTFOUND', f'metatile {word}; use pk meta ls or an id like 0x1A3')
