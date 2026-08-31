import copy
import os
import sys

import pytest

_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, '..', 'resources', 'lib'))

import edid  # noqa: E402

_CORPUS_TSV = os.environ.get(
    'VSVDB_CORPUS_TSV',
    os.path.expanduser('~/ce22-docs/vsvdb-corpus-blocks.tsv'))


def _load_corpus():
    with open(_CORPUS_TSV, encoding='utf-8') as f:
        lines = [line.rstrip('\n') for line in f]
    header = lines[0].split('\t')
    hex_idx = header.index('hex')
    return [line.split('\t')[hex_idx] for line in lines[1:] if line]


_CORPUS = _load_corpus()

_HEADER_MAGIC = bytes.fromhex('00ffffffffffff00')
_DOLBY_VSVDB = bytes.fromhex('eb0146d000480287835c6d8e')  # the LG C4 block
_HDR10PLUS_BLOCK = bytes.fromhex('e5018b849001')  # preset 4, non-Dolby OUI


def _checksum(block):
    block = bytearray(block)
    block[127] = 0
    block[127] = (256 - sum(block[:127])) % 256
    return bytes(block)


def _base_block(ext_count=0, first_byte=0x00):
    b = bytearray(128)
    b[0:8] = bytes([first_byte, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x00])
    b[0x7e] = ext_count
    return _checksum(b)


def _cta_extension(data_blocks=b'', revision=3):
    b = bytearray(128)
    b[0] = 0x02
    b[1] = revision
    dtd_offset = 4 + len(data_blocks)
    b[2] = dtd_offset if dtd_offset <= 127 else 0
    b[3] = 0x00
    b[4:4 + len(data_blocks)] = data_blocks
    return _checksum(b)


def _unknown_extension(tag=0x99):
    b = bytearray(128)
    b[0] = tag
    return _checksum(b)


def _eeodb_data_blocks(real_count):
    return bytes([0xe2, 0x78, real_count])


def _edid_with_vsvdb(vsvdb_bytes=_DOLBY_VSVDB, padding_before=b''):
    ext = _cta_extension(padding_before + vsvdb_bytes)
    return _base_block(ext_count=1) + ext


class TestNormalizeInput:
    def test_bytes_pass_through(self):
        assert edid.normalize_input(b'\x00\xff') == b'\x00\xff'

    def test_bytearray_pass_through(self):
        assert edid.normalize_input(bytearray(b'\x00\xff')) == b'\x00\xff'

    def test_plain_hex(self):
        assert edid.normalize_input('00ffab') == b'\x00\xff\xab'

    def test_uppercase_hex(self):
        assert edid.normalize_input('00FFAB') == b'\x00\xff\xab'

    def test_whitespace_tolerant_hex(self):
        assert edid.normalize_input('00 ff\nab\tcd\r\n') == b'\x00\xff\xab\xcd'

    def test_rejects_odd_length_hex(self):
        assert edid.normalize_input('00f') is None

    def test_rejects_non_hex_chars(self):
        assert edid.normalize_input('00ffzz') is None

    def test_rejects_empty_string(self):
        assert edid.normalize_input('') is None

    def test_rejects_other_types(self):
        assert edid.normalize_input(1234) is None
        assert edid.normalize_input(None) is None


class TestParsePnpId:
    def test_decodes_manufacturer_and_product(self):
        b = bytearray(_base_block())
        b[8] = 0x1e  # ACM (5-bit letters clamp within range)
        b[9] = 0xa5
        b[10] = 0x34
        b[11] = 0x12
        manufacturer, product = edid.parse_pnp_id(bytes(b))
        assert manufacturer is not None
        assert product == 0x1234

    def test_too_short_returns_none_none(self):
        assert edid.parse_pnp_id(bytes(11)) == (None, None)

    def test_never_raises_on_empty(self):
        assert edid.parse_pnp_id(b'') == (None, None)


def _with_descriptor(slot, tag, text):
    b = bytearray(_base_block())
    offset = 54 + slot * 18
    b[offset:offset + 3] = b'\x00\x00\x00'
    b[offset + 3] = tag
    b[offset + 4] = 0x00
    b[offset + 5:offset + 18] = text.ljust(13, b'\x00')[:13]
    return bytes(b)


class TestParseMonitorName:
    def test_decodes_a_terminated_padded_name(self):
        data = _with_descriptor(0, 0xfc, b'TX-RZ50\n     ')

        assert edid.parse_monitor_name(data) == 'TX-RZ50'

    def test_padding_after_the_terminator_is_not_part_of_the_name(self):
        data = _with_descriptor(1, 0xfc, b'ONK\n         ')

        assert edid.parse_monitor_name(data) == 'ONK'

    def test_no_name_descriptor_in_any_slot_is_none(self):
        assert edid.parse_monitor_name(_base_block()) is None

    def test_non_ascii_text_is_none_not_an_exception(self):
        data = _with_descriptor(0, 0xfc, bytes([0xff]) + b'\n' + b' ' * 11)

        assert edid.parse_monitor_name(data) is None

    def test_too_short_to_hold_descriptors_is_none(self):
        assert edid.parse_monitor_name(bytes(125)) is None

    def test_never_raises_on_empty(self):
        assert edid.parse_monitor_name(b'') is None

    def test_a_name_descriptor_is_found_outside_the_first_slot(self):
        data = _with_descriptor(2, 0xfc, b'ONKYO\n       ')

        assert edid.parse_monitor_name(data) == 'ONKYO'


class TestValidateEdidAccepts:
    def test_single_block_no_extensions(self):
        data = _base_block(ext_count=0)
        edid.validate_edid(data)
        assert edid.is_valid_edid(data)

    def test_with_one_cta_extension_and_vsvdb(self):
        data = _edid_with_vsvdb()
        edid.validate_edid(data)

    def test_with_cta_extension_no_vsvdb(self):
        # a structurally valid EDID with no VSVDB anywhere still imports
        # fine as a full EDID profile (design section 3)
        ext = _cta_extension(data_blocks=b'')
        data = _base_block(ext_count=1) + ext
        edid.validate_edid(data)
        assert edid.find_vsvdb(data) is None

    def test_with_unknown_but_ignored_extension_absent(self):
        data = _base_block(ext_count=2) + _cta_extension() + _cta_extension()
        edid.validate_edid(data)

    def test_eeodb_well_formed(self):
        # base 0x7e says 1, but EEODB overrides to the real count of 3
        first_ext = _cta_extension(data_blocks=_eeodb_data_blocks(3))
        data = _base_block(ext_count=1) + first_ext + _cta_extension() + _cta_extension()
        edid.validate_edid(data)

    def test_max_length_1024(self):
        blocks = [_base_block(ext_count=7)] + [_cta_extension() for _ in range(7)]
        data = b''.join(blocks)
        assert len(data) == 1024
        edid.validate_edid(data)

    def test_validator_never_mutates_input(self):
        data = _edid_with_vsvdb()
        before = copy.deepcopy(data)
        edid.validate_edid(data)
        assert data == before

    def test_corpus_blocks_embed_into_valid_edids(self):
        failures = []
        for hex_str in _CORPUS:
            vsvdb_bytes = bytes.fromhex(hex_str)
            data = _edid_with_vsvdb(vsvdb_bytes)
            try:
                edid.validate_edid(data)
            except edid.EdidValidationError as exc:
                failures.append('%s: %s' % (hex_str, exc))
        assert failures == [], '%d/%d corpus embeds failed validation:\n%s' % (
            len(failures), len(_CORPUS), failures[:10])


class TestValidateEdidRejects:
    def test_zero_length(self):
        with pytest.raises(edid.EdidValidationError):
            edid.validate_edid(b'')

    def test_length_not_128_multiple(self):
        with pytest.raises(edid.EdidValidationError):
            edid.validate_edid(_base_block()[:100])

    def test_length_over_1024(self):
        blocks = [_base_block(ext_count=8)] + [_cta_extension() for _ in range(8)]
        data = b''.join(blocks)
        assert len(data) == 1152
        with pytest.raises(edid.EdidValidationError):
            edid.validate_edid(data)

    @pytest.mark.parametrize('byte_index', range(8))
    def test_each_header_byte_wrong(self, byte_index):
        data = bytearray(_base_block())
        data[byte_index] ^= 0xff
        with pytest.raises(edid.EdidValidationError):
            edid.validate_edid(bytes(data))

    def test_block0_checksum_off_by_one(self):
        data = bytearray(_base_block())
        data[126] = (data[126] + 1) & 0xff
        with pytest.raises(edid.EdidValidationError):
            edid.validate_edid(bytes(data))

    def test_extension_block_checksum_off_by_one(self):
        base = _base_block(ext_count=1)
        ext = bytearray(_cta_extension())
        ext[126] = (ext[126] + 1) & 0xff
        with pytest.raises(edid.EdidValidationError):
            edid.validate_edid(base + bytes(ext))

    def test_ext_count_higher_than_present(self):
        data = _base_block(ext_count=2) + _cta_extension()
        with pytest.raises(edid.EdidValidationError):
            edid.validate_edid(data)

    def test_ext_count_lower_than_present(self):
        data = _base_block(ext_count=1) + _cta_extension() + _cta_extension()
        with pytest.raises(edid.EdidValidationError):
            edid.validate_edid(data)

    def test_eeodb_count_disagreeing(self):
        first_ext = _cta_extension(data_blocks=_eeodb_data_blocks(5))
        data = _base_block(ext_count=1) + first_ext + _cta_extension()
        with pytest.raises(edid.EdidValidationError):
            edid.validate_edid(data)

    def test_unknown_extension_tag(self):
        data = _base_block(ext_count=1) + _unknown_extension(tag=0x33)
        with pytest.raises(edid.EdidValidationError):
            edid.validate_edid(data)

    def test_zero_prefix_sentinel_collision(self):
        data = bytes(8) + bytes(120)
        with pytest.raises(edid.EdidValidationError):
            edid.validate_edid(data)

    def test_zero_prefix_takes_priority_over_length(self):
        with pytest.raises(edid.EdidValidationError):
            edid.validate_edid(bytes(8))

    def test_is_valid_edid_false_on_any_rejection(self):
        assert edid.is_valid_edid(bytes(8) + bytes(120)) is False


class TestCtaWalk:
    def test_finds_vsvdb_at_offset_zero(self):
        data = _edid_with_vsvdb(padding_before=b'')
        assert edid.find_vsvdb(data) == _DOLBY_VSVDB

    def test_finds_vsvdb_after_other_data_blocks(self):
        padding = bytes([0x20])  # a harmless zero-length audio data block
        data = _edid_with_vsvdb(padding_before=padding)
        assert edid.find_vsvdb(data) == _DOLBY_VSVDB

    def test_finds_vsvdb_among_multiple_vendor_blocks(self):
        data = _edid_with_vsvdb(padding_before=_HDR10PLUS_BLOCK)
        assert edid.find_vsvdb(data) == _DOLBY_VSVDB

    def test_finds_vsvdb_in_second_cta_extension(self):
        first = _cta_extension(data_blocks=_HDR10PLUS_BLOCK)
        second = _cta_extension(data_blocks=_DOLBY_VSVDB)
        data = _base_block(ext_count=2) + first + second
        assert edid.find_vsvdb(data) == _DOLBY_VSVDB

    def test_no_vsvdb_anywhere_returns_none(self):
        data = _base_block(ext_count=1) + _cta_extension(data_blocks=_HDR10PLUS_BLOCK)
        assert edid.find_vsvdb(data) is None

    def test_no_extensions_returns_none(self):
        assert edid.find_vsvdb(_base_block(ext_count=0)) is None

    def test_non_cta_extension_returns_none(self):
        data = _base_block(ext_count=1) + _unknown_extension(tag=0x10)
        assert edid.find_vsvdb(data) is None

    def test_truncated_data_block_returns_none_not_raise(self):
        # a tag/length byte declaring 5 more bytes that are never there
        b = bytearray(128)
        b[0] = 0x02
        b[1] = 0x03
        b[2] = 6
        b[3] = 0x00
        b[4] = 0xe5  # extended tag, declared length 5, only 0 bytes follow
        b[127] = (256 - sum(b[:127])) % 256
        data = _base_block(ext_count=1) + bytes(b)
        assert edid.find_vsvdb(data) is None

    def test_bogus_dtd_offset_does_not_crash(self):
        b = bytearray(128)
        b[0] = 0x02
        b[1] = 0x03
        b[2] = 250  # out of range, beyond the block
        b[3] = 0x00
        b[127] = (256 - sum(b[:127])) % 256
        data = _base_block(ext_count=1) + bytes(b)
        assert edid.find_vsvdb(data) is None

    def test_short_data_never_raises(self):
        assert edid.find_vsvdb(b'') is None
        assert edid.find_vsvdb(bytes(50)) is None

    def test_hex_text_and_binary_forms_agree(self):
        data = _edid_with_vsvdb()
        hex_form = edid.normalize_input(data.hex())
        assert hex_form == data
        assert edid.find_vsvdb(hex_form) == edid.find_vsvdb(data) == _DOLBY_VSVDB

    def test_corpus_blocks_round_trip_through_cta_walk(self):
        failures = []
        for hex_str in _CORPUS:
            vsvdb_bytes = bytes.fromhex(hex_str)
            data = _edid_with_vsvdb(vsvdb_bytes)
            found = edid.find_vsvdb(data)
            if found != vsvdb_bytes:
                failures.append((hex_str, found.hex() if found else None))
        assert failures == [], '%d/%d corpus blocks failed the CTA walk:\n%s' % (
            len(failures), len(_CORPUS), failures[:10])

    def test_corpus_blocks_round_trip_as_hex_text_edid(self):
        failures = []
        for hex_str in _CORPUS[:20]:
            vsvdb_bytes = bytes.fromhex(hex_str)
            data = _edid_with_vsvdb(vsvdb_bytes)
            reparsed = edid.normalize_input(data.hex())
            found = edid.find_vsvdb(reparsed)
            if found != vsvdb_bytes:
                failures.append(hex_str)
        assert failures == []
