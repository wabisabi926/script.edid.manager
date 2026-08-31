import os
import sys

import pytest

_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, '..', 'resources', 'lib'))
sys.path.insert(0, os.environ.get(
    'SIDEDATA_LIB_PATH',
    os.path.join(_HERE, '..', '..', 'script.module.sidedata', 'lib')))

import backend  # noqa: E402
import runtime  # noqa: E402

_VSVDB_HEX = 'eb0146d000480287835c6d8e'
_EDID_HEX = '00ffffffffffff00' + '11' * 120


@pytest.fixture(autouse=True)
def runtime_marker_path(tmp_path, monkeypatch):
    monkeypatch.setenv('EDIDMGR_RUNTIME_MARKER_PATH', str(tmp_path / 'run' / 'active.runtime'))


@pytest.fixture
def calls(monkeypatch):
    recorded = []

    monkeypatch.setattr(backend, 'write_force_vsvdb',
                        lambda value: recorded.append(('force_vsvdb', value)))
    monkeypatch.setattr(backend, 'write_vsvdb_data',
                        lambda data: recorded.append(('vsvdb_data', data.hex())))
    monkeypatch.setattr(backend, 'write_edid',
                        lambda data: recorded.append(('edid', data.hex())))
    monkeypatch.setattr(backend, 'revert_edid',
                        lambda: recorded.append(('edid', 'sentinel')))
    return recorded


@pytest.fixture
def chain_on(monkeypatch):
    monkeypatch.setattr(backend, 'read_hpd_state', lambda: 1)


@pytest.fixture
def chain_off(monkeypatch):
    monkeypatch.setattr(backend, 'read_hpd_state', lambda: 0)


def test_apply_vsvdb_writes_params_then_reparses(calls, chain_on):
    runtime.apply_vsvdb(_VSVDB_HEX)

    assert calls == [
        ('vsvdb_data', _VSVDB_HEX),
        ('force_vsvdb', 255),
        ('edid', 'sentinel'),
    ]


def test_apply_vsvdb_reparses_through_a_forced_edid(calls, chain_on):
    runtime.apply_vsvdb(_VSVDB_HEX, _EDID_HEX)

    assert calls[-1] == ('edid', _EDID_HEX)


def test_apply_vsvdb_is_refused_with_the_chain_off(calls, chain_off):
    with pytest.raises(runtime.ChainOffError):
        runtime.apply_vsvdb(_VSVDB_HEX)

    assert calls == []


def test_write_vsvdb_params_stages_without_a_reparse(calls, chain_off):
    runtime.write_vsvdb_params(_VSVDB_HEX)

    assert calls == [('vsvdb_data', _VSVDB_HEX), ('force_vsvdb', 255)]


def test_apply_edid_is_not_refused_with_the_chain_off(calls, chain_off):
    runtime.apply_edid(_EDID_HEX)

    assert calls == [('edid', _EDID_HEX)]


def test_revert_vsvdb_clears_the_param_before_the_reparse(calls):
    runtime.revert_vsvdb()

    assert calls == [('force_vsvdb', 0), ('edid', 'sentinel')]


def test_revert_all_does_both_layers(calls):
    assert runtime.revert_all() == []
    assert calls == [('force_vsvdb', 0), ('edid', 'sentinel')]


def test_revert_all_keeps_going_after_a_failure(monkeypatch, calls):
    def boom(value):
        raise OSError('no such node')

    monkeypatch.setattr(backend, 'write_force_vsvdb', boom)

    errors = runtime.revert_all()

    assert len(errors) == 1
    assert calls == [('edid', 'sentinel')]


def test_hpd_state_is_none_when_unreadable(monkeypatch):
    monkeypatch.setattr(backend, 'read_hpd_state', lambda: (_ for _ in ()).throw(OSError()))

    assert runtime.hpd_state() is None


def test_no_marker_reads_as_none(calls):
    assert runtime.read_runtime_marker() is None


def test_apply_edid_writes_the_runtime_marker(calls):
    runtime.apply_edid(_EDID_HEX)

    assert runtime.read_runtime_marker() == _EDID_HEX.lower()


def test_a_failed_write_edid_leaves_no_marker_behind(monkeypatch, calls):
    monkeypatch.setattr(backend, 'write_edid', lambda data: (_ for _ in ()).throw(OSError()))

    with pytest.raises(OSError):
        runtime.apply_edid(_EDID_HEX)

    assert runtime.read_runtime_marker() is None


def test_revert_edid_clears_the_runtime_marker(calls):
    runtime.apply_edid(_EDID_HEX)

    runtime.revert_edid()

    assert runtime.read_runtime_marker() is None


def test_revert_all_clears_the_runtime_marker(calls):
    runtime.apply_edid(_EDID_HEX)

    runtime.revert_all()

    assert runtime.read_runtime_marker() is None


def test_revert_all_clears_the_marker_even_when_a_step_fails(monkeypatch, calls):
    runtime.apply_edid(_EDID_HEX)
    monkeypatch.setattr(backend, 'write_force_vsvdb',
                        lambda value: (_ for _ in ()).throw(OSError('no such node')))

    runtime.revert_all()

    assert runtime.read_runtime_marker() is None


def test_reparse_without_a_forced_edid_clears_the_runtime_marker(calls):
    # D1: this branch actually un-forces the EDID (the sentinel drops back
    # to a DDC read), so a marker an earlier apply_edid left behind would
    # otherwise keep asserting an EDID that is no longer forced at all
    runtime.apply_edid(_EDID_HEX)

    runtime.reparse()

    assert calls[-1] == ('edid', 'sentinel')
    assert runtime.read_runtime_marker() is None


def test_reparse_with_a_forced_edid_leaves_the_marker_alone(calls):
    # this branch re-loads a persisted EDID's own bytes to trigger the
    # VSVDB layer's re-parse; classify_edid never consults the marker once
    # a persisted match already decided the layer, so there is nothing here
    # for reparse to update
    runtime.apply_edid(_EDID_HEX)

    runtime.reparse(_EDID_HEX)

    assert calls[-1] == ('edid', _EDID_HEX)
    assert runtime.read_runtime_marker() == _EDID_HEX.lower()


def test_apply_edid_succeeds_even_when_the_marker_write_fails(monkeypatch, calls, tmp_path):
    # D3: a directory in place of the marker file makes the write raise;
    # that must never propagate out of apply_edid
    blocked = tmp_path / 'blocked'
    blocked.mkdir()
    monkeypatch.setenv('EDIDMGR_RUNTIME_MARKER_PATH', str(blocked))

    runtime.apply_edid(_EDID_HEX)

    assert calls == [('edid', _EDID_HEX)]
    assert runtime.read_runtime_marker() is None


def test_a_failed_marker_write_is_logged(monkeypatch, calls, tmp_path):
    logged = []
    fake_xbmc = type(sys)('xbmc')
    fake_xbmc.LOGWARNING = 2
    fake_xbmc.log = lambda message, level: logged.append((message, level))
    monkeypatch.setitem(sys.modules, 'xbmc', fake_xbmc)
    blocked = tmp_path / 'blocked'
    blocked.mkdir()
    monkeypatch.setenv('EDIDMGR_RUNTIME_MARKER_PATH', str(blocked))

    runtime.apply_edid(_EDID_HEX)

    assert len(logged) == 1
    assert logged[0][1] == fake_xbmc.LOGWARNING


def test_a_dirname_less_override_does_not_crash_the_write(monkeypatch, calls, tmp_path):
    # os.path.dirname('active.runtime') is '', which os.makedirs('') raises
    # on; the write still has to degrade to a logged no-op, not a crash
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('EDIDMGR_RUNTIME_MARKER_PATH', 'active.runtime')

    runtime.apply_edid(_EDID_HEX)

    assert runtime.read_runtime_marker() == _EDID_HEX.lower()
