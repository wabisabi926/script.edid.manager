# VSVDB field-patch encoder. Never synthesizes a block: every edit patches
# bytes of an existing block that sidedata.vsvdb.parse_vsvdb already
# accepted, in place, at the same version and declared length. See
# ~/ce22-docs/vsvdb-manager-design.md section 2 for the field tables this
# mirrors and lib/sidedata/vsvdb.py for the decode side each setter below
# inverts, byte for byte.
#
# Every public entry point re-decodes the patched block through
# sidedata.parse_vsvdb before returning, and raises if that fails. That
# makes the shipped decoder the same arbiter on the way out as it is on the
# way in: there is no way to hand back a block the decoder itself would
# reject.

import math

from sidedata import vsvdb

__all__ = [
    'VsvdbEncodeError',
    'MAX_BLOCK_LEN',
    'nits_to_pq_codeword',
    'load_block',
    'editable_fields',
    'patch_field',
    'patch_fields',
]

MAX_BLOCK_LEN = 32  # force_vsvdb kernel array cap (design finding 5)
_HEADER_SIZE = 5  # tag/length + extended tag + 3-byte OUI, see vsvdb.py

_HEX_DIGITS = frozenset('0123456789abcdefABCDEF')
_EXTENDED_TAG_TYPE = 0x07
_VSVDB_EXTENDED_TAG = 0x01
_DOLBY_OUI = (0x46, 0xd0, 0x00)
_HDR10PLUS_OUI = (0x8b, 0x84, 0x90)  # preset 4, design section 3

# ST.2084 inverse EOTF constants, the algebraic inverse of
# sidedata.vsvdb._pq2nits.
_PQ_M1 = 2610.0 / 16384.0
_PQ_M2 = 128.0 * (2523.0 / 4096.0)
_PQ_C1 = 3424.0 / 4096.0
_PQ_C2 = 32.0 * (2413.0 / 4096.0)
_PQ_C3 = 32.0 * (2392.0 / 4096.0)


class VsvdbEncodeError(ValueError):
    pass


def _clamp(value, low, high):
    if value < low:
        return low
    if value > high:
        return high
    return value


def _round_half_up(value):
    return int(math.floor(value + 0.5))


def nits_to_pq_codeword(nits):
    if nits < 0.0:
        nits = 0.0
    y = (nits / 10000.0) ** _PQ_M1
    pq_norm = ((_PQ_C1 + _PQ_C2 * y) / (1.0 + _PQ_C3 * y)) ** _PQ_M2
    return _clamp(_round_half_up(pq_norm * 4095.0), 0, 4095)


def _set_bit(x, idx, bit, enabled):
    mask = 1 << bit
    if enabled:
        x[idx] |= mask
    else:
        x[idx] &= ~mask & 0xff


def _load(hex_str):
    # design section 3: rejections reported specifically, before delegating
    # to parse_vsvdb as the arbiter of everything else (declared length vs.
    # byte count, payload length vs. version).
    if not isinstance(hex_str, str) or len(hex_str) % 2 != 0 or \
            any(c not in _HEX_DIGITS for c in hex_str):
        raise VsvdbEncodeError('input is not an even-length hex string')

    data = bytearray.fromhex(hex_str)
    if len(data) > MAX_BLOCK_LEN:
        raise VsvdbEncodeError('block of %d bytes exceeds the 32-byte force_vsvdb cap' % len(data))

    if len(data) >= 1 and (data[0] >> 5) != _EXTENDED_TAG_TYPE:
        raise VsvdbEncodeError(
            'tag byte 0x%02x is not an extended tag (top 3 bits must be 7)' % data[0])

    if len(data) >= 2 and data[1] != _VSVDB_EXTENDED_TAG:
        raise VsvdbEncodeError(
            'extended tag 0x%02x is not 0x01 (vendor-specific video data block)' % data[1])

    if len(data) >= 5:
        oui = tuple(data[2:5])
        if oui == _HDR10PLUS_OUI:
            raise VsvdbEncodeError('OUI 8b-84-90 is HDR10+, not Dolby (46-d0-00)')
        if oui != _DOLBY_OUI:
            raise VsvdbEncodeError('OUI %02x-%02x-%02x is not Dolby (46-d0-00)' % oui)

    info = vsvdb.parse_vsvdb(hex_str)
    if info is None:
        raise VsvdbEncodeError('not a block parse_vsvdb accepts')

    return data, info


def _layout_of(data, info):
    version = info['version']
    if version == 0:
        return 'v0'
    if version == 2:
        return 'v2'
    payload_len = (data[0] & 0x1f) - (_HEADER_SIZE - 1)
    if payload_len == 7:
        return 'v1_compact'
    return 'v1_full'


def load_block(hex_str):
    data, _ = _load(hex_str)
    return data.hex()


def editable_fields(hex_str):
    _, info = _load(hex_str)
    data = bytearray.fromhex(hex_str)
    return frozenset(_LAYOUT_FIELDS[_layout_of(data, info)])


# --- v0: 12-bit primaries and PQ codewords packed pairwise across shared
# nibbles, see vsvdb.py's _decode_v0 ---

def _v0_set_yuv422_12bit(x, info, value):
    _set_bit(x, 0, 0, bool(value))


def _v0_set_sup_2160p60(x, info, value):
    _set_bit(x, 0, 1, bool(value))


def _v0_set_global_dimming(x, info, value):
    _set_bit(x, 0, 2, bool(value))


def _v0_set_dm_version_major(x, info, value):
    major = _clamp(int(value), 0, 15)
    x[16] = (major << 4) | (x[16] & 0x0f)


def _v0_set_dm_version_minor(x, info, value):
    minor = _clamp(int(value), 0, 15)
    x[16] = (x[16] & 0xf0) | minor


def _v0_set_min_nits(x, info, value):
    code = nits_to_pq_codeword(value)
    x[13] = (x[13] & 0x0f) | ((code & 0x0f) << 4)
    x[14] = (code >> 4) & 0xff


def _v0_set_max_nits(x, info, value):
    code = nits_to_pq_codeword(value)
    x[13] = (x[13] & 0xf0) | (code & 0x0f)
    x[15] = (code >> 4) & 0xff


def _v0_set_primaries(x, info, value):
    merged = dict(info['primaries'])
    merged.update(value)

    def code12(v):
        return _clamp(_round_half_up(v * 4096.0), 0, 4095)

    rx, ry = code12(merged['rx']), code12(merged['ry'])
    gx, gy = code12(merged['gx']), code12(merged['gy'])
    bx, by = code12(merged['bx']), code12(merged['by'])
    wx, wy = code12(merged['wx']), code12(merged['wy'])

    x[1] = ((rx & 0x0f) << 4) | (ry & 0x0f)
    x[2] = (rx >> 4) & 0xff
    x[3] = (ry >> 4) & 0xff
    x[4] = ((gx & 0x0f) << 4) | (gy & 0x0f)
    x[5] = (gx >> 4) & 0xff
    x[6] = (gy >> 4) & 0xff
    x[7] = ((bx & 0x0f) << 4) | (by & 0x0f)
    x[8] = (bx >> 4) & 0xff
    x[9] = (by >> 4) & 0xff
    x[10] = ((wx & 0x0f) << 4) | (wy & 0x0f)
    x[11] = (wx >> 4) & 0xff
    x[12] = (wy >> 4) & 0xff


# --- v1, compact and full share x[0..2] and diverge on primaries (and
# low_latency, compact only), see vsvdb.py's _decode_v1 ---

def _v1_set_yuv422_12bit(x, info, value):
    _set_bit(x, 0, 0, bool(value))


def _v1_set_sup_2160p60(x, info, value):
    _set_bit(x, 0, 1, bool(value))


def _v1_set_dm_version_major(x, info, value):
    selector = _clamp(int(value), 2, 9) - 2
    x[0] = (x[0] & 0xe3) | (selector << 2)


def _v1_set_global_dimming(x, info, value):
    _set_bit(x, 1, 0, bool(value))


def _v1_set_max_nits(x, info, value):
    code = _clamp(_round_half_up((value - 100.0) / 50.0), 0, 127)
    x[1] = (x[1] & 0x01) | (code << 1)


def _v1_set_colorimetry_p3d65(x, info, value):
    _set_bit(x, 2, 0, bool(value))


def _v1_set_min_nits(x, info, value):
    nits = value if value > 0.0 else 0.0
    code = _clamp(_round_half_up(127.0 * math.sqrt(nits)), 0, 127)
    x[2] = (x[2] & 0x01) | (code << 1)


def _v1_compact_set_low_latency(x, info, value):
    _set_bit(x, 3, 0, bool(value))


_V1C_RX_MIN, _V1C_RX_STEP = 0.625, (0.74609375 - 0.625) / 31.0
_V1C_RY_MIN, _V1C_RY_STEP = 0.25, (0.37109375 - 0.25) / 31.0
_V1C_GX_STEP = 0.49609375 / 127.0
_V1C_GY_MIN, _V1C_GY_STEP = 0.5, (0.99609375 - 0.5) / 127.0
_V1C_BX_MIN, _V1C_BX_STEP = 0.125, (0.15234375 - 0.125) / 7.0
_V1C_BY_MIN, _V1C_BY_STEP = 0.03125, (0.05859375 - 0.03125) / 7.0


def _v1_compact_set_primaries(x, info, value):
    merged = dict(info['primaries'])
    merged.update(value)

    rx = _clamp(_round_half_up((merged['rx'] - _V1C_RX_MIN) / _V1C_RX_STEP), 0, 31)
    ry = _clamp(_round_half_up((merged['ry'] - _V1C_RY_MIN) / _V1C_RY_STEP), 0, 31)
    gx = _clamp(_round_half_up(merged['gx'] / _V1C_GX_STEP), 0, 127)
    gy = _clamp(_round_half_up((merged['gy'] - _V1C_GY_MIN) / _V1C_GY_STEP), 0, 127)
    bx = _clamp(_round_half_up((merged['bx'] - _V1C_BX_MIN) / _V1C_BX_STEP), 0, 7)
    by = _clamp(_round_half_up((merged['by'] - _V1C_BY_MIN) / _V1C_BY_STEP), 0, 7)

    # x[3] bit 0 is low_latency, kept as-is; bits 2-4 and 5-7 are by/bx.
    x[3] = (x[3] & 0x03) | ((bx & 0x07) << 5) | ((by & 0x07) << 2)
    # ry's 5-bit code splits across x[4] bit0, x[5] bit0 and x[6] bits0-2.
    x[4] = ((gx & 0x7f) << 1) | (ry & 0x01)
    x[5] = ((gy & 0x7f) << 1) | ((ry >> 1) & 0x01)
    x[6] = ((rx & 0x1f) << 3) | ((ry >> 2) & 0x07)


def _v1_full_set_primaries(x, info, value):
    merged = dict(info['primaries'])
    merged.update(value)

    def code8(v):
        return _clamp(_round_half_up(v * 256.0), 0, 255)

    x[4] = code8(merged['rx'])
    x[5] = code8(merged['ry'])
    x[6] = code8(merged['gx'])
    x[7] = code8(merged['gy'])
    x[8] = code8(merged['bx'])
    x[9] = code8(merged['by'])


# --- v2, see vsvdb.py's _decode_v2 ---

def _v2_set_yuv422_12bit(x, info, value):
    _set_bit(x, 0, 0, bool(value))


def _v2_set_backlight_control(x, info, value):
    _set_bit(x, 0, 1, bool(value))


def _v2_set_dm_version_major(x, info, value):
    selector = _clamp(int(value), 2, 9) - 2
    x[0] = (x[0] & 0xe3) | (selector << 2)


def _v2_set_backlight_min_luma(x, info, value):
    code = _clamp(int(value), 0, 3)
    x[1] = (x[1] & 0xfc) | code


def _v2_set_global_dimming(x, info, value):
    _set_bit(x, 1, 2, bool(value))


def _v2_set_min_nits(x, info, value):
    pq = nits_to_pq_codeword(value)
    code = _clamp(_round_half_up(pq / 20.0), 0, 31)
    x[1] = (x[1] & 0x07) | (code << 3)


def _v2_set_interface_bits(x, info, value):
    code = _clamp(int(value), 0, 3)
    x[2] = (x[2] & 0xfc) | code


def _v2_set_max_nits(x, info, value):
    pq = nits_to_pq_codeword(value)
    code = _clamp(_round_half_up((pq - 2055) / 65.0), 0, 31)
    x[2] = (x[2] & 0x07) | (code << 3)


def _v2_set_sup_10b_12b_444(x, info, value):
    code = _clamp(int(value), 0, 3)
    _set_bit(x, 3, 0, bool(code & 0x02))
    _set_bit(x, 4, 0, bool(code & 0x01))


def _v2_set_primaries(x, info, value):
    merged = dict(info['primaries'])
    merged.update(value)

    gx = _clamp(_round_half_up(merged['gx'] * 256.0), 0, 127)
    gy = _clamp(_round_half_up((merged['gy'] - 0.5) * 256.0), 0, 127)
    rx = _clamp(_round_half_up((merged['rx'] - 0.625) * 256.0), 0, 31)
    bx = _clamp(_round_half_up((merged['bx'] - 0.125) * 256.0), 0, 7)
    ry = _clamp(_round_half_up((merged['ry'] - 0.25) * 256.0), 0, 31)
    by = _clamp(_round_half_up((merged['by'] - 0.03125) * 256.0), 0, 7)

    # x[3]/x[4] bit 0 carry sup_10b_12b_444, kept as-is.
    x[3] = (x[3] & 0x01) | ((gx & 0x7f) << 1)
    x[4] = (x[4] & 0x01) | ((gy & 0x7f) << 1)
    x[5] = ((rx & 0x1f) << 3) | (bx & 0x07)
    x[6] = ((ry & 0x1f) << 3) | (by & 0x07)


_LAYOUT_FIELDS = {
    'v0': {
        'yuv422_12bit', 'sup_2160p60', 'global_dimming',
        'min_nits', 'max_nits', 'dm_version_major', 'dm_version_minor',
        'primaries',
    },
    'v1_compact': {
        'yuv422_12bit', 'sup_2160p60', 'dm_version_major', 'global_dimming',
        'max_nits', 'colorimetry_p3d65', 'min_nits', 'low_latency',
        'primaries',
    },
    'v1_full': {
        'yuv422_12bit', 'sup_2160p60', 'dm_version_major', 'global_dimming',
        'max_nits', 'colorimetry_p3d65', 'min_nits', 'primaries',
    },
    'v2': {
        'yuv422_12bit', 'backlight_control', 'dm_version_major',
        'backlight_min_luma', 'global_dimming', 'min_nits',
        'interface_bits', 'max_nits', 'sup_10b_12b_444', 'primaries',
    },
}

_SETTERS = {
    'v0': {
        'yuv422_12bit': _v0_set_yuv422_12bit,
        'sup_2160p60': _v0_set_sup_2160p60,
        'global_dimming': _v0_set_global_dimming,
        'min_nits': _v0_set_min_nits,
        'max_nits': _v0_set_max_nits,
        'dm_version_major': _v0_set_dm_version_major,
        'dm_version_minor': _v0_set_dm_version_minor,
        'primaries': _v0_set_primaries,
    },
    'v1_compact': {
        'yuv422_12bit': _v1_set_yuv422_12bit,
        'sup_2160p60': _v1_set_sup_2160p60,
        'dm_version_major': _v1_set_dm_version_major,
        'global_dimming': _v1_set_global_dimming,
        'max_nits': _v1_set_max_nits,
        'colorimetry_p3d65': _v1_set_colorimetry_p3d65,
        'min_nits': _v1_set_min_nits,
        'low_latency': _v1_compact_set_low_latency,
        'primaries': _v1_compact_set_primaries,
    },
    'v1_full': {
        'yuv422_12bit': _v1_set_yuv422_12bit,
        'sup_2160p60': _v1_set_sup_2160p60,
        'dm_version_major': _v1_set_dm_version_major,
        'global_dimming': _v1_set_global_dimming,
        'max_nits': _v1_set_max_nits,
        'colorimetry_p3d65': _v1_set_colorimetry_p3d65,
        'min_nits': _v1_set_min_nits,
        'primaries': _v1_full_set_primaries,
    },
    'v2': {
        'yuv422_12bit': _v2_set_yuv422_12bit,
        'backlight_control': _v2_set_backlight_control,
        'dm_version_major': _v2_set_dm_version_major,
        'backlight_min_luma': _v2_set_backlight_min_luma,
        'global_dimming': _v2_set_global_dimming,
        'min_nits': _v2_set_min_nits,
        'interface_bits': _v2_set_interface_bits,
        'max_nits': _v2_set_max_nits,
        'sup_10b_12b_444': _v2_set_sup_10b_12b_444,
        'primaries': _v2_set_primaries,
    },
}

_ACHIEVED_KEY = {
    'yuv422_12bit': 'yuv422_12bit',
    'sup_2160p60': 'sup_2160p60',
    'global_dimming': 'global_dimming',
    'backlight_control': 'backlight_control',
    'colorimetry_p3d65': 'colorimetry_p3d65',
    'low_latency': 'low_latency',
    'dm_version_major': 'dm_version_major',
    'dm_version_minor': 'dm_version_minor',
    'interface_bits': 'interface_bits',
    'sup_10b_12b_444': 'sup_10b_12b_444',
    'backlight_min_luma': 'backlight_min_luma_nits',
    'min_nits': 'min_nits',
    'max_nits': 'max_nits',
    'primaries': 'primaries',
}


def patch_fields(hex_str, edits):
    data, info = _load(hex_str)
    layout = _layout_of(data, info)
    fields = _LAYOUT_FIELDS[layout]
    setters = _SETTERS[layout]

    for field in edits:
        if field not in fields:
            raise VsvdbEncodeError(
                'field %r is not editable on a version %d block' % (field, info['version']))

    x = bytearray(data[_HEADER_SIZE:])
    for field, value in edits.items():
        setters[field](x, info, value)
    data[_HEADER_SIZE:] = x

    patched_hex = data.hex()
    patched_info = vsvdb.parse_vsvdb(patched_hex)
    if patched_info is None:
        raise VsvdbEncodeError('patched block failed to re-decode')

    achieved = dict((field, patched_info[_ACHIEVED_KEY[field]]) for field in edits)
    return patched_hex, achieved


def patch_field(hex_str, field, value):
    patched_hex, achieved = patch_fields(hex_str, {field: value})
    return patched_hex, achieved[field]
