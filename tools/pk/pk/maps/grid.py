from ..core.io_bin import decode_u16, encode_u16
from ..core.errors import PkError


def decode(raw):
    return [(word & 1023, (word >> 10) & 3, word >> 12) for word in decode_u16(raw)]


def encode(cells):
    words = []
    for meta, coll, elev in cells:
        if not all(isinstance(v, int) for v in (meta, coll, elev)) or not (0 <= meta <= 1023 and 0 <= coll <= 3 and 0 <= elev <= 15):
            raise PkError('BOUNDS', 'block needs metatile 0..1023 collision 0..3 elevation 0..15')
        words.append(meta | coll << 10 | elev << 12)
    return encode_u16(words)
