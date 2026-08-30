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
