import math
import os
import sys

import pytest

_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, '..', 'resources', 'lib'))
sys.path.insert(0, os.environ.get(
    'SIDEDATA_LIB_PATH',
    os.path.join(_HERE, '..', '..', 'script.module.sidedata', 'lib')))

import encode  # noqa: E402
from sidedata import vsvdb  # noqa: E402

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


def _values_equal(a, b):
    if isinstance(a, float) or isinstance(b, float):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_values_equal(a[k], b[k]) for k in a)
    return a == b


# keys that legitimately move together with a field edit: max_nits/min_nits
# also carry a derived codeword/code_raw view of the same bits
def _companion_keys(field):
    if field == 'max_nits':
        return {'max_nits', 'max_pq_codeword', 'max_code_raw'}
    if field == 'min_nits':
        return {'min_nits', 'min_pq_codeword', 'min_code_raw'}
    if field == 'backlight_min_luma':
        return {'backlight_min_luma_nits'}
    return {field}


def _assert_isolated(before, after, field):
    companions = _companion_keys(field)
    for key in before:
        if key in companions:
            continue
        assert _values_equal(after.get(key), before.get(key)), (
            'field %r edit changed unrelated key %r: %r -> %r' %
            (field, key, before.get(key), after.get(key)))


def _sample_edit(field, before):
    if field == 'max_nits':
        return 500.0
    if field == 'min_nits':
        return 0.3
    if field == 'dm_version_major':
        return 5
    if field == 'dm_version_minor':
        return 7
    if field == 'backlight_min_luma':
        return 2 if before.get('backlight_min_luma_nits') != 75 else 1
    if field == 'interface_bits':
        return 1 if before.get('interface_bits') != 1 else 2
    if field == 'sup_10b_12b_444':
        return 1 if before.get('sup_10b_12b_444') != 1 else 2
    if field == 'primaries':
        return dict((k, min(max(v + 0.01, 0.0), 0.99)) for k, v in before['primaries'].items())
    # boolean flags
    return not before.get(field, False)


# --- independent oracle -----------------------------------------------------
# Reimplements the design's field quantization from scratch, never by calling
# encode.py's setters, so tests can check a setter landed the requested value
# on the wire and not just that it left everything else alone.

def _oracle_round_half_up(value):
    return int(math.floor(value + 0.5))


def _oracle_clamp(value, low, high):
    return max(low, min(high, value))


def _oracle_pq_codeword(nits):
    # ST.2084 inverse EOTF, typed independently of encode.nits_to_pq_codeword
    if nits < 0.0:
        nits = 0.0
    m1 = 2610.0 / 16384.0
    m2 = 128.0 * (2523.0 / 4096.0)
    c1 = 3424.0 / 4096.0
    c2 = 32.0 * (2413.0 / 4096.0)
    c3 = 32.0 * (2392.0 / 4096.0)
    y = (nits / 10000.0) ** m1
    pq_norm = ((c1 + c2 * y) / (1.0 + c3 * y)) ** m2
    return _oracle_clamp(_oracle_round_half_up(pq_norm * 4095.0), 0, 4095)


def _oracle_layout(hex_str, info):
    version = info['version']
    if version == 0:
        return 'v0'
    if version == 2:
        return 'v2'
    payload_len = (bytearray.fromhex(hex_str)[0] & 0x1f) - 4
    return 'v1_compact' if payload_len == 7 else 'v1_full'


_V1C_RX_MIN, _V1C_RX_STEP = 0.625, (0.74609375 - 0.625) / 31.0
_V1C_RY_MIN, _V1C_RY_STEP = 0.25, (0.37109375 - 0.25) / 31.0
_V1C_GX_STEP = 0.49609375 / 127.0
_V1C_GY_MIN, _V1C_GY_STEP = 0.5, (0.99609375 - 0.5) / 127.0
_V1C_BX_MIN, _V1C_BX_STEP = 0.125, (0.15234375 - 0.125) / 7.0
_V1C_BY_MIN, _V1C_BY_STEP = 0.03125, (0.05859375 - 0.03125) / 7.0


def _oracle_primary_coord(layout, coord, value):
    if layout == 'v0':
        code = _oracle_clamp(_oracle_round_half_up(value * 4096.0), 0, 4095)
        return code / 4096.0
    if layout == 'v1_full':
        code = _oracle_clamp(_oracle_round_half_up(value * 256.0), 0, 255)
        return code / 256.0
    if layout == 'v1_compact':
        low, step, max_code = {
            'rx': (_V1C_RX_MIN, _V1C_RX_STEP, 31),
            'ry': (_V1C_RY_MIN, _V1C_RY_STEP, 31),
            'gx': (0.0, _V1C_GX_STEP, 127),
            'gy': (_V1C_GY_MIN, _V1C_GY_STEP, 127),
            'bx': (_V1C_BX_MIN, _V1C_BX_STEP, 7),
            'by': (_V1C_BY_MIN, _V1C_BY_STEP, 7),
        }[coord]
        code = _oracle_clamp(_oracle_round_half_up((value - low) / step), 0, max_code)
        return low + step * code
    if layout == 'v2':
        offset, max_code = {
            'gx': (0.0, 127), 'gy': (0.5, 127),
            'rx': (0.625, 31), 'ry': (0.25, 31),
            'bx': (0.125, 7), 'by': (0.03125, 7),
        }[coord]
        code = _oracle_clamp(_oracle_round_half_up((value - offset) * 256.0), 0, max_code)
        return offset + code / 256.0
    raise ValueError('unknown layout %r' % layout)


_BOOLEAN_FIELDS = frozenset({
    'yuv422_12bit', 'sup_2160p60', 'global_dimming', 'backlight_control',
    'colorimetry_p3d65', 'low_latency',
})


def _oracle_scalar_value(layout, field, value):
    if field in _BOOLEAN_FIELDS:
        return bool(value)

    if field == 'dm_version_major':
        if layout == 'v0':
            return _oracle_clamp(int(value), 0, 15)
        return _oracle_clamp(int(value), 2, 9)

    if field == 'dm_version_minor':
        return _oracle_clamp(int(value), 0, 15)

    if field in ('interface_bits', 'sup_10b_12b_444'):
        return _oracle_clamp(int(value), 0, 3)

    if field == 'backlight_min_luma':
        code = _oracle_clamp(int(value), 0, 3)
        return 25 + 25 * code

    if field == 'max_nits':
        if layout == 'v0':
            code = _oracle_pq_codeword(value)
            return vsvdb._pq2nits(code / 4095.0)
        if layout in ('v1_compact', 'v1_full'):
            code = _oracle_clamp(_oracle_round_half_up((value - 100.0) / 50.0), 0, 127)
            return 100.0 + code * 50.0
        if layout == 'v2':
            pq = _oracle_pq_codeword(value)
            code = _oracle_clamp(_oracle_round_half_up((pq - 2055) / 65.0), 0, 31)
            return vsvdb._pq2nits((2055 + 65 * code) / 4095.0)

    if field == 'min_nits':
        if layout == 'v0':
            code = _oracle_pq_codeword(value)
            return vsvdb._pq2nits(code / 4095.0)
        if layout in ('v1_compact', 'v1_full'):
            nits = value if value > 0.0 else 0.0
            code = _oracle_clamp(_oracle_round_half_up(127.0 * math.sqrt(nits)), 0, 127)
            lm = code / 127.0
            return lm * lm
        if layout == 'v2':
            pq = _oracle_pq_codeword(value)
            code = _oracle_clamp(_oracle_round_half_up(pq / 20.0), 0, 31)
            return vsvdb._pq2nits((20 * code) / 4095.0)

    raise ValueError('no oracle for field %r on layout %r' % (field, layout))


def _assert_achieved(hex_str, field, layout, requested, achieved, mismatches):
    if field == 'primaries':
        for coord, target in requested.items():
            expected = _oracle_primary_coord(layout, coord, target)
            if not _values_equal(achieved[coord], expected):
                mismatches.append(
                    '%s field=primaries coord=%s: requested %r expected %r got %r' %
                    (hex_str, coord, target, expected, achieved[coord]))
        return

    expected = _oracle_scalar_value(layout, field, requested)
    if not _values_equal(achieved, expected):
        mismatches.append(
            '%s field=%s: requested %r expected %r got %r' %
            (hex_str, field, requested, expected, achieved))


class TestCorpusIdentityRoundTrip:
    # the centerpiece gate: every corpus block, decoded and re-encoded with
    # no edits, must come back byte-identical (design section 6, item 1)

    def test_corpus_has_333_blocks(self):
        assert len(_CORPUS) == 333

    def test_every_block_round_trips_byte_identical(self):
        mismatches = []
        for hex_str in _CORPUS:
            got = encode.load_block(hex_str)
            if got != hex_str:
                mismatches.append((hex_str, got))

        assert mismatches == [], (
            '%d/%d blocks failed to round-trip byte-identical:\n%s' %
            (len(mismatches), len(_CORPUS), mismatches[:10]))


class TestSingleFieldIsolation:
    # for every corpus block and every editable field of its version, one
    # edit changes only that field's bits (design section 6, item 2)

    def test_every_field_isolated_across_corpus(self):
        mismatches = []
        for hex_str in _CORPUS:
            before = vsvdb.parse_vsvdb(hex_str)
            layout = _oracle_layout(hex_str, before)
            fields = encode.editable_fields(hex_str)
            for field in fields:
                value = _sample_edit(field, before)
                try:
                    patched, achieved = encode.patch_field(hex_str, field, value)
                except encode.VsvdbEncodeError as exc:
                    mismatches.append('%s field=%s raised %s' % (hex_str, field, exc))
                    continue

                after = vsvdb.parse_vsvdb(patched)
                if after is None:
                    mismatches.append('%s field=%s: patched block failed to re-decode' %
                                       (hex_str, field))
                    continue

                try:
                    _assert_isolated(before, after, field)
                except AssertionError as exc:
                    mismatches.append('%s field=%s: %s' % (hex_str, field, exc))

                _assert_achieved(hex_str, field, layout, value, achieved, mismatches)

        assert mismatches == [], '%d isolation/value failures:\n%s' % (
            len(mismatches), '\n'.join(mismatches[:20]))


class TestPrimariesCoordinateIsolation:
    # the shared-byte packing the design calls out for v1-compact and v2:
    # editing one coordinate must not disturb its neighbours

    _V0 = 'f90146d0000034ad4ee641a80b270c145054cc07b52800000000'
    _V1_COMPACT = 'eb0146d00022082b515999aa'
    _V1_FULL = 'ee0146d000200fa300a6554e9d270c'
    _V2 = 'eb0146d000480287835c6d8e'  # the LG C4 block

    _LAYOUT_OF = {_V0: 'v0', _V1_COMPACT: 'v1_compact', _V1_FULL: 'v1_full', _V2: 'v2'}

    @pytest.mark.parametrize('hex_str', [_V0, _V1_COMPACT, _V1_FULL, _V2])
    def test_editing_one_coordinate_leaves_the_others_alone(self, hex_str):
        layout = self._LAYOUT_OF[hex_str]
        before = vsvdb.parse_vsvdb(hex_str)
        for coord in before['primaries']:
            target = min(max(before['primaries'][coord] + 0.02, 0.0), 0.99)
            patched, achieved = encode.patch_field(hex_str, 'primaries', {coord: target})
            after = vsvdb.parse_vsvdb(patched)

            for other in before['primaries']:
                if other == coord:
                    continue
                assert _values_equal(after['primaries'][other], before['primaries'][other]), (
                    'editing %s moved %s' % (coord, other))

            _assert_isolated(before, after, 'primaries')

            expected = _oracle_primary_coord(layout, coord, target)
            assert after['primaries'][coord] == pytest.approx(expected, abs=1e-9), (
                'editing %s: requested %r expected %r got %r' %
                (coord, target, expected, after['primaries'][coord]))


class TestPqInverse:
    def test_full_codeword_range_round_trips_exactly(self):
        mismatches = []
        for pq in range(4096):
            nits = vsvdb._pq2nits(pq / 4095.0)
            back = encode.nits_to_pq_codeword(nits)
            if back != pq:
                mismatches.append((pq, nits, back))
        assert mismatches == [], '%d/4096 codewords failed to round-trip:\n%s' % (
            len(mismatches), mismatches[:10])

    def test_v2_max_grid_adjacency(self):
        # design section 2's own worked example
        for target, expected_code in ((896.5, 15), (1037.1, 16), (1199.6, 17)):
            patched, achieved = encode.patch_field(
                'eb0146d000480287835c6d8e', 'max_nits', target)
            info = vsvdb.parse_vsvdb(patched)
            assert info['max_code_raw'] == expected_code

    def test_v2_max_grid_full_sweep(self):
        # every one of the 32 reachable codes, each nits target computed
        # independently from the code via the decoder's own pq2nits, never
        # via encode.py
        hex_str = 'eb0146d000480287835c6d8e'
        mismatches = []
        for code in range(32):
            nits = vsvdb._pq2nits((2055 + 65 * code) / 4095.0)
            patched, achieved = encode.patch_field(hex_str, 'max_nits', nits)
            info = vsvdb.parse_vsvdb(patched)
            if info['max_code_raw'] != code:
                mismatches.append((code, nits, info['max_code_raw']))
        assert mismatches == [], '%d/32 max grid points missed their code:\n%s' % (
            len(mismatches), mismatches)

    def test_v2_min_grid_full_sweep(self):
        hex_str = 'eb0146d000480287835c6d8e'
        mismatches = []
        for code in range(32):
            nits = vsvdb._pq2nits((20 * code) / 4095.0)
            patched, achieved = encode.patch_field(hex_str, 'min_nits', nits)
            info = vsvdb.parse_vsvdb(patched)
            if info['min_code_raw'] != code:
                mismatches.append((code, nits, info['min_code_raw']))
        assert mismatches == [], '%d/32 min grid points missed their code:\n%s' % (
            len(mismatches), mismatches)

    def test_v2_max_clamp_bounds(self):
        hex_str = 'eb0146d000480287835c6d8e'
        _, low = encode.patch_field(hex_str, 'max_nits', 0.0)
        assert low == pytest.approx(93.95298193072989, abs=0.01)
        _, high = encode.patch_field(hex_str, 'max_nits', 50000.0)
        assert high == pytest.approx(9434.033721202319, abs=0.01)


class TestClampAndBoundary:
    _V0 = 'f90146d0000034ad4ee641a80b270c145054cc07b52800000000'
    _V1_COMPACT = 'eb0146d00022082b515999aa'
    _V2 = 'eb0146d000480287835c6d8e'

    def test_v1_max_nits_grid_is_exact_50_step(self):
        patched, achieved = encode.patch_field(self._V1_COMPACT, 'max_nits', 350.0)
        assert achieved == pytest.approx(350.0)

    def test_v1_max_nits_clamps_low_and_high(self):
        _, low = encode.patch_field(self._V1_COMPACT, 'max_nits', -500.0)
        assert low == pytest.approx(100.0)
        _, high = encode.patch_field(self._V1_COMPACT, 'max_nits', 100000.0)
        assert high == pytest.approx(6450.0)

    def test_v1_min_nits_clamps_low_and_high(self):
        _, low = encode.patch_field(self._V1_COMPACT, 'min_nits', -1.0)
        assert low == pytest.approx(0.0)
        _, high = encode.patch_field(self._V1_COMPACT, 'min_nits', 100.0)
        assert high == pytest.approx(1.0)

    def test_v2_min_nits_clamps_low_and_high(self):
        _, low = encode.patch_field(self._V2, 'min_nits', -1.0)
        assert low == pytest.approx(0.0, abs=0.01)
        patched, high = encode.patch_field(self._V2, 'min_nits', 100.0)
        info = vsvdb.parse_vsvdb(patched)
        assert info['min_code_raw'] == 31

    def test_v2_backlight_min_luma_four_values(self):
        for code, nits in ((0, 25), (1, 50), (2, 75), (3, 100)):
            patched, achieved = encode.patch_field(self._V2, 'backlight_min_luma', code)
            assert achieved == nits

    def test_v2_backlight_min_luma_clamps(self):
        patched, achieved = encode.patch_field(self._V2, 'backlight_min_luma', 99)
        assert achieved == 100

    def test_dm_version_major_clamps_v1_v2(self):
        _, achieved = encode.patch_field(self._V1_COMPACT, 'dm_version_major', 1)
        assert achieved == 2
        _, achieved = encode.patch_field(self._V1_COMPACT, 'dm_version_major', 99)
        assert achieved == 9

    def test_dm_version_major_minor_v0_full_nibble_range(self):
        _, achieved = encode.patch_field(self._V0, 'dm_version_major', -5)
        assert achieved == 0
        _, achieved = encode.patch_field(self._V0, 'dm_version_major', 99)
        assert achieved == 15
        _, achieved = encode.patch_field(self._V0, 'dm_version_minor', 99)
        assert achieved == 15

    def test_dm_version_minor_clamps_low(self):
        _, achieved = encode.patch_field(self._V0, 'dm_version_minor', -5)
        assert achieved == 0

    def test_interface_bits_clamp(self):
        _, achieved = encode.patch_field(self._V2, 'interface_bits', 99)
        assert achieved == 3

    def test_interface_bits_clamps_low(self):
        _, achieved = encode.patch_field(self._V2, 'interface_bits', -1)
        assert achieved == 0

    def test_sup_10b_12b_444_clamp(self):
        _, achieved = encode.patch_field(self._V2, 'sup_10b_12b_444', 99)
        assert achieved == 3
        _, achieved = encode.patch_field(self._V2, 'sup_10b_12b_444', -1)
        assert achieved == 0

    def test_v0_min_max_nits_codeword_bounds(self):
        _, low = encode.patch_field(self._V0, 'min_nits', -5.0)
        assert low == pytest.approx(0.0, abs=0.01)
        patched, high = encode.patch_field(self._V0, 'max_nits', 50000.0)
        info = vsvdb.parse_vsvdb(patched)
        assert info['max_pq_codeword'] == 4095


class TestRejection:
    _V0 = 'f90146d0000034ad4ee641a80b270c145054cc07b52800000000'
    _V1_COMPACT = 'eb0146d00022082b515999aa'
    _V1_FULL = 'ee0146d000200fa300a6554e9d270c'

    def test_rejects_malformed_hex(self):
        with pytest.raises(encode.VsvdbEncodeError, match='hex'):
            encode.load_block('zz0146d0002618039182697c')

    def test_rejects_odd_length_hex(self):
        # even count of hex digits but one byte short of a real block: a
        # truncation, not literally odd-length input, so no dedicated
        # message applies here - see test_rejects_odd_hex_digit_count below
        with pytest.raises(encode.VsvdbEncodeError):
            encode.load_block('eb0146d000261803918269')

    def test_rejects_odd_hex_digit_count(self):
        with pytest.raises(encode.VsvdbEncodeError, match='hex'):
            encode.load_block('eb0146d0002618039182697')

    def test_rejects_wrong_tag_top_bits(self):
        # tag byte 0x1b: top 3 bits are 0, not the extended-tag marker 7
        with pytest.raises(encode.VsvdbEncodeError, match='extended tag') as exc:
            encode.load_block('1b0146d0002618039182697c')
        assert 'top 3 bits' in str(exc.value)

    def test_rejects_wrong_extended_tag(self):
        # tag byte is fine, but byte 1 is 0x02, not the VSVDB extended tag 0x01
        with pytest.raises(encode.VsvdbEncodeError) as exc:
            encode.load_block('eb0246d0002618039182697c')
        assert '0x01' in str(exc.value)

    def test_rejects_wrong_oui(self):
        with pytest.raises(encode.VsvdbEncodeError, match='OUI') as exc:
            encode.load_block('eb0147d0002618039182697c')
        assert 'HDR10' not in str(exc.value)

    def test_rejects_hdr10plus_preset(self):
        # preset 4, OUI 8b-84-90 (HDR10+), not Dolby: parse_vsvdb correctly
        # returns None for it (design finding 6)
        with pytest.raises(encode.VsvdbEncodeError, match='HDR10'):
            encode.load_block('e5018b849001')

    def test_rejects_truncated_block(self):
        with pytest.raises(encode.VsvdbEncodeError):
            encode.load_block('eb0146d0002618')

    def test_rejects_declared_length_mismatch(self):
        # declared length 0x09 matches neither v1 layout
        with pytest.raises(encode.VsvdbEncodeError):
            encode.load_block('e90146d0002618039182697c')

    def test_rejects_over_32_bytes(self):
        # 33 bytes: a structurally valid v0 block padded past the cap
        oversized = self._V0 + '00' * (33 - len(bytes.fromhex(self._V0)))
        with pytest.raises(encode.VsvdbEncodeError, match='32-byte'):
            encode.load_block(oversized)

    def test_accepts_exactly_32_bytes(self):
        padded = self._V0 + '00' * (32 - len(bytes.fromhex(self._V0)))
        assert encode.load_block(padded) == padded

    def test_rejects_field_not_editable_on_version(self):
        with pytest.raises(encode.VsvdbEncodeError):
            encode.patch_field(self._V1_FULL, 'low_latency', True)

    def test_rejects_v2_only_field_on_v1(self):
        with pytest.raises(encode.VsvdbEncodeError):
            encode.patch_field(self._V1_COMPACT, 'backlight_min_luma', 1)

    def test_rejects_v0_only_field_on_v2(self):
        with pytest.raises(encode.VsvdbEncodeError):
            encode.patch_field('eb0146d000480287835c6d8e', 'dm_version_minor', 1)


class TestAchievedValueCorrectness:
    # requesting parse_vsvdb(patched)[key] here would just redecode the same
    # patched bytes achieved was already read from - it can never catch a
    # wrong quantization, only a mismatched dict key. The independent oracle
    # computes the expected on-wire value from the request itself.

    def test_achieved_value_matches_redecode(self):
        hex_str = 'eb0146d000480287835c6d8e'
        patched, achieved = encode.patch_field(hex_str, 'max_nits', 800.0)
        expected = _oracle_scalar_value('v2', 'max_nits', 800.0)
        assert achieved == pytest.approx(expected)

    def test_patch_fields_achieved_dict_matches_redecode(self):
        hex_str = 'eb0146d000480287835c6d8e'
        patched, achieved = encode.patch_fields(
            hex_str, {'max_nits': 800.0, 'global_dimming': True})
        expected_max = _oracle_scalar_value('v2', 'max_nits', 800.0)
        assert achieved['max_nits'] == pytest.approx(expected_max)
        assert achieved['global_dimming'] is True

    def test_editable_fields_matches_design_table(self):
        assert encode.editable_fields('eb0146d000480287835c6d8e') == {
            'yuv422_12bit', 'backlight_control', 'dm_version_major',
            'backlight_min_luma', 'global_dimming', 'min_nits',
            'interface_bits', 'max_nits', 'sup_10b_12b_444', 'primaries',
        }
