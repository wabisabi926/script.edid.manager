# Full-EDID byte work: structural validation and the CTA-861 walk that
# pulls a VSVDB out of an EDID blob. No repair, ever: a structurally broken
# blob is refused, never patched to make it loadable. See
# ~/ce22-docs/vsvdb-manager-design.md section 3.
#
# Block-count resolution mirrors the kernel's own hdmitx_save_edid_file: the
# extension-count byte at 0x7e is authoritative, except when the first
# extension carries an EEODB (CTA extended tag 0x78, tag/length byte 0xe2)
# at its offsets 4 and 5 (0x84/0x85 in the whole blob), in which case the
# count at offset 6 of that block (0x86) is the one that has to agree
# instead (design finding 7).

_HEADER_MAGIC = bytes.fromhex('00ffffffffffff00')
_BLOCK_SIZE = 128
_MAX_LEN = 1024

_EXT_COUNT_OFFSET = 0x7e
_EEODB_TAG_LEN_OFFSET = 0x84
_EEODB_EXTENDED_CODE_OFFSET = 0x85
_EEODB_COUNT_OFFSET = 0x86
_EEODB_TAG_LEN = 0xe2
_EEODB_EXTENDED_CODE = 0x78

_CTA_EXTENSION_TAG = 0x02
_CTA_VSVDB_EXTENDED_TAG = 0x01
_DOLBY_OUI = (0x46, 0xd0, 0x00)

# Known top-level EDID extension tags (VESA E-EDID), everything else is
# refused rather than walked. Only CTA-861 (0x02) is ever walked for a
# VSVDB.
_KNOWN_EXTENSION_TAGS = frozenset({0x02, 0x10, 0x40, 0x50, 0x60, 0xa7, 0xaf, 0xbf, 0xf0, 0xff})

_HEX_DIGITS = frozenset('0123456789abcdefABCDEF')


class EdidValidationError(ValueError):
    pass


def normalize_input(raw):
    """Accept raw binary bytes or hex text (any whitespace) and return
    binary bytes, or None if raw is neither. Does not validate EDID
    structure; call validate_edid on the result for that.
    """
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)

    if isinstance(raw, str):
        stripped = ''.join(raw.split())
        if not stripped or len(stripped) % 2 != 0:
            return None
        if any(c not in _HEX_DIGITS for c in stripped):
            return None
        return bytes.fromhex(stripped)

    return None


def _resolve_extension_count(data):
    base_count = data[_EXT_COUNT_OFFSET]
    if base_count == 0:
        return base_count

    if (len(data) > _EEODB_COUNT_OFFSET and
            data[_EEODB_TAG_LEN_OFFSET] == _EEODB_TAG_LEN and
            data[_EEODB_EXTENDED_CODE_OFFSET] == _EEODB_EXTENDED_CODE):
        return data[_EEODB_COUNT_OFFSET]

    return base_count


def validate_edid(data):
    """Raise EdidValidationError naming the first rule data breaks. Returns
    None on success. Never mutates data.
    """
    if len(data) >= 8 and data[:8] == bytes(8):
        raise EdidValidationError(
            'first 8 bytes are all zero, colliding with the revert sentinel')

    if len(data) == 0:
        raise EdidValidationError('edid is empty')

    if len(data) % _BLOCK_SIZE != 0:
        raise EdidValidationError('length %d is not a multiple of 128 bytes' % len(data))

    if len(data) > _MAX_LEN:
        raise EdidValidationError('length %d exceeds the 1024-byte maximum' % len(data))

    if data[:8] != _HEADER_MAGIC:
        raise EdidValidationError('block 0 header does not match the EDID magic')

    block_count = len(data) // _BLOCK_SIZE
    for block_index in range(block_count):
        block = data[block_index * _BLOCK_SIZE:(block_index + 1) * _BLOCK_SIZE]
        if sum(block) % 256 != 0:
            raise EdidValidationError('block %d checksum is not zero mod 256' % block_index)

    ext_blocks_present = block_count - 1
    resolved_count = _resolve_extension_count(data)
    if resolved_count != ext_blocks_present:
        raise EdidValidationError(
            'declared extension count %d does not match the %d blocks present' %
            (resolved_count, ext_blocks_present))

    for block_index in range(1, block_count):
        tag = data[block_index * _BLOCK_SIZE]
        if tag not in _KNOWN_EXTENSION_TAGS:
            raise EdidValidationError(
                'block %d has an unrecognized extension tag 0x%02x' % (block_index, tag))


def is_valid_edid(data):
    try:
        validate_edid(data)
        return True
    except EdidValidationError:
        return False


def parse_pnp_id(data):
    """Decode the manufacturer (3-letter PnP ID) and product code packed at
    EDID block 0 offsets 8-11. Returns (manufacturer, product), both None if
    data is too short to hold them. Never raises.
    """
    if len(data) < 12:
        return None, None

    manu_code = (data[8] << 8) | data[9]
    manufacturer = ''.join(
        chr(((manu_code >> shift) & 0x1f) + ord('A') - 1)
        for shift in (10, 5, 0))
    product = data[10] | (data[11] << 8)
    return manufacturer, product


_DESCRIPTOR_OFFSETS = (54, 72, 90, 108)
_DESCRIPTOR_SIZE = 18
_DISPLAY_DESCRIPTOR_TAG = 0xfc
_DESCRIPTOR_TEXT_TERMINATOR = 0x0a


def parse_monitor_name(data):
    """Decode the display product name descriptor (tag 0xfc) out of block
    0's four 18-byte descriptor slots at offsets 54-125. Returns the ASCII
    name, or None if no such descriptor is present or its text is not clean
    ASCII. Never raises.
    """
    if len(data) < 126:
        return None

    for offset in _DESCRIPTOR_OFFSETS:
        descriptor = data[offset:offset + _DESCRIPTOR_SIZE]
        if descriptor[0:3] != b'\x00\x00\x00' or descriptor[3] != _DISPLAY_DESCRIPTOR_TAG:
            continue

        text = descriptor[5:18]
        terminator = text.find(_DESCRIPTOR_TEXT_TERMINATOR)
        if terminator >= 0:
            text = text[:terminator]
        try:
            name = text.decode('ascii').strip()
        except UnicodeDecodeError:
            return None
        return name or None

    return None


def find_vsvdb(data):
    """Walk the CTA-861 extension blocks for a vendor-specific data block
    carrying the Dolby OUI and return its raw bytes, the same shape
    parse_vsvdb expects (tag byte onward). Returns None if there is none.
    Never raises: a truncated or corrupt data block collection is a clean
    None, same as absence.
    """
    try:
        return _find_vsvdb_unsafe(data)
    except Exception:
        return None


def _find_vsvdb_unsafe(data):
    block_count = len(data) // _BLOCK_SIZE
    for block_index in range(1, block_count):
        offset = block_index * _BLOCK_SIZE
        block = data[offset:offset + _BLOCK_SIZE]
        if block[0] != _CTA_EXTENSION_TAG:
            continue

        dtd_offset = block[2]
        end = dtd_offset if 4 <= dtd_offset <= 127 else 127

        pos = 4
        while pos < end:
            tag_len = block[pos]
            block_type = tag_len >> 5
            declared_len = tag_len & 0x1f
            payload_start = pos + 1
            payload_end = payload_start + declared_len
            if payload_end > end:
                break

            if block_type == 0x07 and declared_len >= 4:
                extended_tag = block[payload_start]
                if extended_tag == _CTA_VSVDB_EXTENDED_TAG:
                    oui = tuple(block[payload_start + 1:payload_start + 4])
                    if oui == _DOLBY_OUI:
                        return bytes(block[pos:payload_end])

            pos = payload_end

    return None
