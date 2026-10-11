import struct
from .errors import PkError


def decode_u16(raw):
    if len(raw) % 2:
        raise PkError('PARSE', 'odd byte count; expected little-endian u16 blocks')
    return list(struct.unpack(f'<{len(raw)//2}H', raw))


def encode_u16(values):
    try:
        return struct.pack(f'<{len(values)}H', *values)
    except (struct.error, TypeError):
        raise PkError('BOUNDS', 'block words must be integers 0..65535') from None
