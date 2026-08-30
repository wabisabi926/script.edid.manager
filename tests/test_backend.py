import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'resources', 'lib'))

import backend  # noqa: E402


@pytest.fixture
def tree(tmp_path, monkeypatch):
    force_vsvdb = tmp_path / 'force_vsvdb'
    vsvdb_data = tmp_path / 'vsvdb_data'
    hpd_state = tmp_path / 'hpd_state'
    rawedid = tmp_path / 'rawedid'
    edid = tmp_path / 'edid'

    force_vsvdb.write_text('0\n')
    vsvdb_data.write_text('')
    hpd_state.write_text('1\n')
    rawedid.write_text('')
    edid.write_text('')

    monkeypatch.setenv('EDIDMGR_FORCE_VSVDB_PATH', str(force_vsvdb))
    monkeypatch.setenv('EDIDMGR_VSVDB_DATA_PATH', str(vsvdb_data))
    monkeypatch.setenv('EDIDMGR_HPD_STATE_PATH', str(hpd_state))
    monkeypatch.setenv('EDIDMGR_RAWEDID_PATH', str(rawedid))
    monkeypatch.setenv('EDIDMGR_EDID_PATH', str(edid))

    return {
        'force_vsvdb': force_vsvdb,
        'vsvdb_data': vsvdb_data,
        'hpd_state': hpd_state,
        'rawedid': rawedid,
        'edid': edid,
    }


def test_force_vsvdb_round_trip(tree):
    backend.write_force_vsvdb(3)
    assert tree['force_vsvdb'].read_text() == '3'
    assert backend.read_force_vsvdb() == 3


def test_force_vsvdb_sentinel_255(tree):
    backend.write_force_vsvdb(255)
    assert backend.read_force_vsvdb() == 255


def test_vsvdb_data_write_is_0x_hex(tree):
    backend.write_vsvdb_data(bytes([0xeb, 0x01, 0x46, 0xd0]))
    assert tree['vsvdb_data'].read_text() == '0xeb,0x01,0x46,0xd0'


def test_vsvdb_data_read_is_decimal(tree):
    tree['vsvdb_data'].write_text('235,1,70,208\n')
    assert backend.read_vsvdb_data() == bytes([235, 1, 70, 208])


def test_vsvdb_data_round_trip(tree):
    data = bytes(range(0, 32))
    backend.write_vsvdb_data(data)
    # simulate the kernel's decimal read-back format of what was just written
    tree['vsvdb_data'].write_text(','.join(str(b) for b in data))
    assert backend.read_vsvdb_data() == data


def test_vsvdb_data_empty_read(tree):
    assert backend.read_vsvdb_data() == b''


def test_vsvdb_data_refuses_33_bytes(tree):
    with pytest.raises(ValueError):
        backend.write_vsvdb_data(bytes(33))
    assert tree['vsvdb_data'].read_text() == ''


def test_vsvdb_data_accepts_32_bytes(tree):
    backend.write_vsvdb_data(bytes(32))
    assert tree['vsvdb_data'].read_text() == ','.join(['0x00'] * 32)


def test_hpd_read(tree):
    assert backend.read_hpd_state() == 1
    tree['hpd_state'].write_text('0\n')
    assert backend.read_hpd_state() == 0


def test_rawedid_read_decodes_device_hex_text(tree):
    blob = bytes([0x00, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x00]) + bytes(120)
    tree['rawedid'].write_text(blob.hex() + '\n')
    assert backend.read_rawedid() == blob


def test_rawedid_read_empty_node_is_empty_bytes(tree):
    tree['rawedid'].write_text('')
    assert backend.read_rawedid() == b''


def test_rawedid_read_garbage_node_is_empty_bytes(tree, monkeypatch):
    logged = []
    fake_xbmc = type(sys)('xbmc')
    fake_xbmc.LOGERROR = 3
    fake_xbmc.log = lambda message, level: logged.append((message, level))
    monkeypatch.setitem(sys.modules, 'xbmc', fake_xbmc)

    tree['rawedid'].write_text('not hex at all\n')

    assert backend.read_rawedid() == b''
    assert len(logged) == 1
    assert logged[0][1] == fake_xbmc.LOGERROR


def _checksum(block):
    block = bytearray(block)
    block[127] = 0
    block[127] = (256 - sum(block[:127])) % 256
    return bytes(block)


def _cta_extension_block():
    b = bytearray(128)
    b[0] = 0x02
    b[1] = 0x03
    b[2] = 0x04  # dtd_offset right after the header, no data blocks
    return _checksum(b)


def _valid_edid(length=128, first_byte=0x00):
    # builds a structurally valid EDID for lengths that are a multiple of
    # 128; other lengths (used to probe the length check itself) are just
    # padded or truncated to the requested size
    ext_count = max(length // 128 - 1, 0)
    header = bytearray(128)
    header[0:8] = bytes([first_byte, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x00])
    header[0x7e] = ext_count
    data = _checksum(header) + _cta_extension_block() * ext_count
    if len(data) < length:
        data += bytes(length - len(data))
    return data[:length]


def test_write_edid_is_load_hex_lowercase_newline(tree):
    edid = _valid_edid()
    backend.write_edid(edid)
    written = tree['edid'].read_text()
    assert written == 'load %s\n' % edid.hex()
    assert written.islower()
    assert written.startswith('load ')
    assert written.endswith('\n')


def test_write_edid_hex_length_matches_kernel_contract(tree):
    for blocks in range(1, 9):
        edid = _valid_edid(length=128 * blocks)
        backend.write_edid(edid)
        written = tree['edid'].read_text()
        hexpart = written[len('load '):]
        assert len(hexpart) == 256 * blocks + 1  # includes trailing newline


def test_write_edid_refuses_zero_prefix(tree):
    zero_prefixed = bytes(8) + bytes(120)
    with pytest.raises(ValueError):
        backend.write_edid(zero_prefixed)
    assert tree['edid'].read_text() == ''


def test_write_edid_refuses_bad_lengths(tree):
    for bad_length in (0, 64, 127, 129, 1025, 1152):
        with pytest.raises(ValueError):
            backend.write_edid(_valid_edid(length=bad_length) if bad_length >= 8 else bytes(bad_length))
    assert tree['edid'].read_text() == ''


def test_write_edid_accepts_max_length(tree):
    backend.write_edid(_valid_edid(length=1024))
    assert tree['edid'].read_text().startswith('load ')


def test_write_edid_refuses_checksum_corrupt(tree):
    # length and header are fine; only the block checksum is broken, so
    # this can only be caught by the full edid.validate_edid pass
    edid = bytearray(_valid_edid())
    edid[126] = (edid[126] + 1) & 0xff
    with pytest.raises(ValueError):
        backend.write_edid(bytes(edid))
    assert tree['edid'].read_text() == ''


def test_revert_edid_writes_exact_sentinel(tree):
    backend.revert_edid()
    assert tree['edid'].read_text() == 'load 0000000000000000\n'


def test_no_fake_plug_anywhere():
    src = os.path.join(os.path.dirname(__file__), '..', 'resources', 'lib', 'backend.py')
    with open(src, 'r', encoding='utf-8') as f:
        assert 'fake_plug' not in f.read()


def test_compose_order_edid_before_params(tree, monkeypatch):
    calls = []
    monkeypatch.setattr(backend, 'write_edid', lambda edid: calls.append(('edid', edid)))
    monkeypatch.setattr(backend, 'write_vsvdb_data', lambda data: calls.append(('vsvdb_data', data)))
    monkeypatch.setattr(backend, 'write_force_vsvdb', lambda value: calls.append(('force_vsvdb', value)))

    backend.compose_apply(edid=b'x', vsvdb_data=b'y', force_vsvdb=1)

    assert [c[0] for c in calls] == ['edid', 'vsvdb_data', 'force_vsvdb']


def test_compose_apply_edid_only(tree):
    edid = _valid_edid()
    backend.compose_apply(edid=edid)
    assert tree['edid'].read_text() == 'load %s\n' % edid.hex()
    assert tree['vsvdb_data'].read_text() == ''
    assert tree['force_vsvdb'].read_text() == '0\n'


def test_compose_apply_vsvdb_only(tree):
    backend.compose_apply(vsvdb_data=bytes([1, 2, 3]), force_vsvdb=2)
    assert tree['vsvdb_data'].read_text() == '0x01,0x02,0x03'
    assert tree['force_vsvdb'].read_text() == '2'
    assert tree['edid'].read_text() == ''


def test_read_effective_vsvdb_hex_without_sidedata_installed():
    # sidedata is not a test dependency; the wiring must degrade to None
    # rather than raise when the import fails.
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == 'sidedata':
            raise ImportError('no sidedata in this environment')
        return real_import(name, *args, **kwargs)

    orig = builtins.__import__
    builtins.__import__ = fake_import
    try:
        assert backend.read_effective_vsvdb_hex() is None
    finally:
        builtins.__import__ = orig


def test_read_effective_vsvdb_hex_delegates_to_sidedata(monkeypatch):
    fake_sidedata = type(sys)('sidedata')
    fake_sidedata.read_vsvdb_raw = lambda: 'e5013fd046000102'
    monkeypatch.setitem(sys.modules, 'sidedata', fake_sidedata)
    assert backend.read_effective_vsvdb_hex() == 'e5013fd046000102'
