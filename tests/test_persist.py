import os
import subprocess
import sys
import time

import pytest

_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, '..', 'resources', 'lib'))
sys.path.insert(0, os.environ.get(
    'SIDEDATA_LIB_PATH',
    os.path.join(_HERE, '..', '..', 'script.module.sidedata', 'lib')))

import persist  # noqa: E402
import store  # noqa: E402

_VSVDB_HEX = 'eb0146d000480287835c6d8e'
_EDID_HEX = '00ffffffffffff00' + '11' * 120

_BOOT_SCRIPT = os.path.join(_HERE, '..', 'resources', 'bin', 'edidmgr-boot-apply.sh')


@pytest.fixture
def persist_dir(tmp_path, monkeypatch):
    directory = tmp_path / 'edid-manager'
    directory.mkdir()
    monkeypatch.setenv('EDIDMGR_PERSIST_DIR', str(directory))
    return directory


def _vsvdb_profile(profile_id='v1'):
    return {'id': profile_id, 'kind': store.KIND_VSVDB, 'raw': _VSVDB_HEX}


def _edid_profile(profile_id='e1'):
    return {'id': profile_id, 'kind': store.KIND_EDID, 'raw': _EDID_HEX}


def test_paths_follow_the_env_override(persist_dir):
    assert persist.persist_dir() == str(persist_dir)
    assert persist.profile_path() == str(persist_dir / 'active.profile')
    assert persist.state_path() == str(persist_dir / 'active.state')
    assert persist.disarmed_path() == str(persist_dir / 'active.disarmed')
    assert persist.deferred_path() == str(persist_dir / 'active.deferred')


def test_nothing_armed_reads_clean(persist_dir):
    assert persist.current_state() is None
    assert persist.load_persisted() == {}
    assert persist.persisted_profile(store.KIND_VSVDB) is None
    assert persist.disarmed() is False


@pytest.mark.parametrize('contents', ['', '\n', '  \t\r\n'])
def test_an_empty_state_file_is_garbage_not_nothing_armed(persist_dir, contents):
    (persist_dir / 'active.state').write_text(contents)

    assert persist.current_state() == ''
    assert persist.boot_decision(persist.current_state()).disarm is True


def test_persist_profile_writes_block_and_pending(persist_dir):
    persist.persist_profile(_vsvdb_profile())

    assert persist.current_state() == persist.STATE_PENDING
    assert persist.load_persisted() == {
        store.KIND_VSVDB: {'kind': store.KIND_VSVDB, 'id': 'v1', 'raw': _VSVDB_HEX},
    }


def test_persist_profile_keeps_the_other_layer(persist_dir):
    persist.persist_profile(_vsvdb_profile())
    persist.persist_profile(_edid_profile())

    persisted = persist.load_persisted()
    assert set(persisted) == {store.KIND_VSVDB, store.KIND_EDID}
    assert persisted[store.KIND_EDID]['raw'] == _EDID_HEX


def test_persist_profile_replaces_the_same_layer(persist_dir):
    persist.persist_profile(_vsvdb_profile())
    persist.persist_profile(
        {'id': 'v2', 'kind': store.KIND_VSVDB, 'raw': 'EB0146D000480287835C6D8E'})

    persisted = persist.load_persisted()
    assert list(persisted) == [store.KIND_VSVDB]
    assert persisted[store.KIND_VSVDB]['id'] == 'v2'
    assert persisted[store.KIND_VSVDB]['raw'] == _VSVDB_HEX


def test_persist_profile_rejects_an_unknown_kind(persist_dir):
    with pytest.raises(ValueError):
        persist.persist_profile({'id': 'x', 'kind': 'other', 'raw': _VSVDB_HEX})


def test_malformed_profile_file_reads_as_nothing_persisted(persist_dir):
    (persist_dir / 'active.profile').write_text('not a key value file\n')

    assert persist.load_persisted() == {}


def test_set_state_rejects_an_unknown_value(persist_dir):
    with pytest.raises(ValueError):
        persist.set_state('maybe')


@pytest.mark.parametrize('state_value,expected', [
    (None, (False, None, False)),
    (persist.STATE_PENDING, (True, persist.STATE_TRIAL, False)),
    (persist.STATE_TRIAL, (False, None, True)),
    (persist.STATE_TRIAL_ACKED, (True, persist.STATE_TRIAL_ACKED, False)),
    (persist.STATE_CONFIRMED, (True, persist.STATE_CONFIRMED, False)),
    ('', (False, None, True)),
    ('nonsense', (False, None, True)),
])
def test_boot_decision_matrix(state_value, expected):
    assert tuple(persist.boot_decision(state_value)) == expected


def test_dialog_shown_promotes_trial(persist_dir):
    persist.persist_profile(_vsvdb_profile())
    persist.set_state(persist.STATE_TRIAL)

    assert persist.dialog_shown() is True
    assert persist.current_state() == persist.STATE_TRIAL_ACKED


@pytest.mark.parametrize('state_value', [
    persist.STATE_PENDING, persist.STATE_TRIAL_ACKED, persist.STATE_CONFIRMED])
def test_dialog_shown_touches_nothing_else(persist_dir, state_value):
    persist.persist_profile(_vsvdb_profile())
    persist.set_state(state_value)

    assert persist.dialog_shown() is False
    assert persist.current_state() == state_value


def test_confirm(persist_dir):
    persist.persist_profile(_vsvdb_profile())
    persist.confirm()

    assert persist.current_state() == persist.STATE_CONFIRMED


def test_disarm_keeps_the_profile_and_marks_why(persist_dir):
    persist.persist_profile(_vsvdb_profile())
    persist.disarm()

    assert persist.current_state() is None
    assert persist.disarmed() is True
    assert persist.load_persisted() != {}


def test_rearm_after_a_disarm(persist_dir):
    persist.persist_profile(_vsvdb_profile())
    persist.disarm()

    assert persist.rearm() is True
    assert persist.current_state() == persist.STATE_PENDING
    assert persist.disarmed() is False


def test_rearm_without_a_profile_clears_the_marker(persist_dir):
    persist.disarm()

    assert persist.rearm() is False
    assert persist.disarmed() is False


def test_clear_disarmed(persist_dir):
    persist.disarm()
    persist.clear_disarmed()

    assert persist.disarmed() is False


def test_the_deferred_marker_reads_and_clears(persist_dir):
    assert persist.deferred() is False

    (persist_dir / 'active.deferred').write_text('edid-deferred\n')

    assert persist.deferred() is True

    persist.clear_deferred()

    assert persist.deferred() is False


def test_unpersist_takes_the_deferred_marker_with_it(persist_dir):
    persist.persist_profile(_edid_profile())
    (persist_dir / 'active.deferred').write_text('edid-deferred\n')

    persist.unpersist()

    assert persist.deferred() is False
    assert os.listdir(str(persist_dir)) == []


def test_unpersist_one_layer_leaves_the_other_armed(persist_dir):
    persist.persist_profile(_vsvdb_profile())
    persist.persist_profile(_edid_profile())
    persist.set_state(persist.STATE_CONFIRMED)

    remaining = persist.unpersist(store.KIND_EDID)

    assert list(remaining) == [store.KIND_VSVDB]
    assert persist.current_state() == persist.STATE_CONFIRMED


def test_unpersist_the_last_layer_disarms_everything(persist_dir):
    persist.persist_profile(_vsvdb_profile())
    persist.disarm()

    assert persist.unpersist(store.KIND_VSVDB) == {}
    assert persist.load_persisted() == {}
    assert persist.current_state() is None
    assert persist.disarmed() is False


def test_unpersist_everything(persist_dir):
    persist.persist_profile(_vsvdb_profile())
    persist.persist_profile(_edid_profile())

    assert persist.unpersist() == {}
    assert os.listdir(str(persist_dir)) == []


def test_every_artifact_matches_the_documented_recovery_glob(persist_dir):
    persist.persist_profile(_vsvdb_profile())
    persist.disarm()

    for name in os.listdir(str(persist_dir)):
        assert name.startswith('active.')


def test_modprobe_leftovers(tmp_path, monkeypatch):
    directory = tmp_path / 'modprobe.d'
    directory.mkdir()
    (directory / 'vsvdb.conf').write_text('options aml_media force_vsvdb=2\n')
    (directory / 'commented.conf').write_text('# options aml_media force_vsvdb=2\n')
    (directory / 'other.conf').write_text('options snd_hda_intel model=auto\n')
    (directory / 'notaconf.txt').write_text('options aml_media force_vsvdb=2\n')
    monkeypatch.setenv('EDIDMGR_MODPROBE_D', str(directory))

    assert persist.modprobe_leftovers() == [str(directory / 'vsvdb.conf')]


@pytest.fixture
def modprobe_dir(tmp_path, monkeypatch):
    directory = tmp_path / 'modprobe.d'
    directory.mkdir()
    monkeypatch.setenv('EDIDMGR_MODPROBE_D', str(directory))
    return directory


def test_leftover_lines_are_the_directives_the_ui_shows(modprobe_dir):
    leftover = modprobe_dir / 'vsvdb.conf'
    leftover.write_text('# written by the gist\n\noptions aml_media force_vsvdb=2\n')

    assert persist.modprobe_leftover_lines(str(leftover)) == [
        'options aml_media force_vsvdb=2']


def test_leftover_lines_refuse_a_file_that_configures_anything_else(modprobe_dir):
    leftover = modprobe_dir / 'mixed.conf'
    leftover.write_text('options aml_media force_vsvdb=2\noptions snd_hda_intel model=auto\n')

    assert persist.modprobe_leftover_lines(str(leftover)) is None


def test_delete_removes_an_aml_media_only_file(modprobe_dir):
    leftover = modprobe_dir / 'vsvdb.conf'
    leftover.write_text('options aml_media force_vsvdb=2\n')

    assert persist.delete_modprobe_leftover(str(leftover))[0] is True
    assert not leftover.exists()


def test_delete_refuses_a_file_that_configures_anything_else(modprobe_dir):
    leftover = modprobe_dir / 'mixed.conf'
    leftover.write_text('options aml_media force_vsvdb=2\noptions snd_hda_intel model=auto\n')

    ok, _ = persist.delete_modprobe_leftover(str(leftover))

    assert ok is False
    assert leftover.exists()


def test_modprobe_leftovers_never_deletes(tmp_path, monkeypatch):
    directory = tmp_path / 'modprobe.d'
    directory.mkdir()
    leftover = directory / 'vsvdb.conf'
    leftover.write_text('options aml_media force_vsvdb=2\n')
    monkeypatch.setenv('EDIDMGR_MODPROBE_D', str(directory))

    persist.modprobe_leftovers()

    assert leftover.exists()


def test_modprobe_leftovers_without_the_directory(tmp_path, monkeypatch):
    monkeypatch.setenv('EDIDMGR_MODPROBE_D', str(tmp_path / 'absent'))

    assert persist.modprobe_leftovers() == []


@pytest.fixture
def unit_tree(tmp_path, monkeypatch):
    addon = tmp_path / 'addon'
    (addon / 'system.d').mkdir(parents=True)
    unit = addon / 'system.d' / persist.UNIT_NAME
    unit.write_text('[Unit]\n')

    system_d = tmp_path / 'system.d'
    system_d.mkdir()

    calls = tmp_path / 'systemctl.log'
    stub = tmp_path / 'systemctl'
    stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$EDIDMGR_TEST_LOG"\nexit 0\n')
    stub.chmod(0o755)

    monkeypatch.setenv('EDIDMGR_ADDON_DIR', str(addon))
    monkeypatch.setenv('EDIDMGR_SYSTEM_D', str(system_d))
    monkeypatch.setenv('EDIDMGR_SYSTEMCTL', str(stub))
    monkeypatch.setenv('EDIDMGR_TEST_LOG', str(calls))

    return {'unit': unit, 'system_d': system_d, 'calls': calls}


def test_unit_absent_is_ours_to_create(unit_tree):
    assert persist.unit_is_ours() is True
    assert persist.unit_enabled() is False


def test_unit_symlink_into_our_addon_is_ours(unit_tree):
    os.symlink(str(unit_tree['unit']), str(unit_tree['system_d'] / persist.UNIT_NAME))

    assert persist.unit_is_ours() is True
    assert persist.unit_enabled() is True


def test_a_plain_file_in_system_d_is_not_ours(unit_tree):
    (unit_tree['system_d'] / persist.UNIT_NAME).write_text('[Unit]\n')

    assert persist.unit_is_ours() is False


def test_a_symlink_elsewhere_is_not_ours(unit_tree, tmp_path):
    other = tmp_path / 'somebody-elses.service'
    other.write_text('[Unit]\n')
    os.symlink(str(other), str(unit_tree['system_d'] / persist.UNIT_NAME))

    assert persist.unit_is_ours() is False


def test_enable_unit_calls_systemctl_with_the_shipped_path(unit_tree):
    ok, _ = persist.enable_unit()

    assert ok is True
    assert unit_tree['calls'].read_text().strip() == 'enable %s' % unit_tree['unit']


def test_enable_unit_refuses_a_foreign_entry(unit_tree):
    (unit_tree['system_d'] / persist.UNIT_NAME).write_text('[Unit]\n')

    ok, detail = persist.enable_unit()

    assert ok is False
    assert detail == persist.unit_link_path()
    assert not unit_tree['calls'].exists()


def test_disable_unit_calls_systemctl_with_the_unit_name(unit_tree):
    ok, _ = persist.disable_unit()

    assert ok is True
    assert unit_tree['calls'].read_text().strip() == 'disable %s' % persist.UNIT_NAME


@pytest.fixture
def boot_tree(tmp_path, monkeypatch):
    persist_dir = tmp_path / 'edid-manager'
    persist_dir.mkdir()
    params = tmp_path / 'parameters'
    params.mkdir()
    hdmi = tmp_path / 'amhdmitx0'
    hdmi.mkdir()

    (params / 'force_vsvdb').write_text('0')
    (params / 'vsvdb_data').write_text('')
    (hdmi / 'edid').write_text('')
    (hdmi / 'hpd_state').write_text('1\n')

    monkeypatch.setenv('EDIDMGR_PERSIST_DIR', str(persist_dir))

    return {
        'dir': persist_dir,
        'env': {
            # the script only honours these with EDIDMGR_TEST=1; the shipped
            # unit's environment stays clean
            'EDIDMGR_TEST': '1',
            'EDIDMGR_PERSIST_DIR': str(persist_dir),
            'EDIDMGR_PARAMS_DIR': str(params),
            'EDIDMGR_HDMI_DIR': str(hdmi),
            'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
        },
        'force_vsvdb': params / 'force_vsvdb',
        'vsvdb_data': params / 'vsvdb_data',
        'edid': hdmi / 'edid',
        'hpd_state': hdmi / 'hpd_state',
    }


def _run_boot_script(boot_tree):
    result = subprocess.run(['/bin/sh', _BOOT_SCRIPT], env=boot_tree['env'])
    assert result.returncode == 0


def _arm(boot_tree, state_value, vsvdb=True, edid=True):
    lines = []
    if edid:
        lines.append('edid_id=e1')
        lines.append('edid_raw=%s' % _EDID_HEX)
    if vsvdb:
        lines.append('vsvdb_id=v1')
        lines.append('vsvdb_raw=%s' % _VSVDB_HEX)
    (boot_tree['dir'] / 'active.profile').write_text(''.join(l + '\n' for l in lines))
    if state_value is not None:
        (boot_tree['dir'] / 'active.state').write_text(state_value + '\n')


def _applied(boot_tree):
    return {
        'edid': boot_tree['edid'].read_text(),
        'vsvdb_data': boot_tree['vsvdb_data'].read_text(),
        'force_vsvdb': boot_tree['force_vsvdb'].read_text(),
    }


def test_boot_script_applies_and_starts_the_trial_on_pending(boot_tree):
    _arm(boot_tree, persist.STATE_PENDING)

    _run_boot_script(boot_tree)

    applied = _applied(boot_tree)
    assert applied['edid'] == 'load %s\n' % _EDID_HEX
    assert applied['vsvdb_data'] == '0xeb,0x01,0x46,0xd0,0x00,0x48,0x02,0x87,0x83,0x5c,0x6d,0x8e'
    assert applied['force_vsvdb'] == '255'
    assert persist.current_state() == persist.STATE_TRIAL


def test_boot_script_applies_on_confirmed(boot_tree):
    _arm(boot_tree, persist.STATE_CONFIRMED)

    _run_boot_script(boot_tree)

    assert _applied(boot_tree)['force_vsvdb'] == '255'
    assert persist.current_state() == persist.STATE_CONFIRMED


def test_boot_script_reapplies_and_keeps_trial_acked(boot_tree):
    _arm(boot_tree, persist.STATE_TRIAL_ACKED)

    _run_boot_script(boot_tree)

    assert _applied(boot_tree)['force_vsvdb'] == '255'
    assert persist.current_state() == persist.STATE_TRIAL_ACKED


def test_boot_script_disarms_on_plain_trial(boot_tree):
    _arm(boot_tree, persist.STATE_TRIAL)

    _run_boot_script(boot_tree)

    assert _applied(boot_tree) == {'edid': '', 'vsvdb_data': '', 'force_vsvdb': '0'}
    assert persist.current_state() is None
    assert persist.disarmed() is True
    assert persist.load_persisted() != {}


def test_boot_script_disarms_on_an_unrecognized_state(boot_tree):
    _arm(boot_tree, 'nonsense')

    _run_boot_script(boot_tree)

    assert _applied(boot_tree)['force_vsvdb'] == '0'
    assert persist.current_state() is None
    assert persist.disarmed() is True


def test_boot_script_does_nothing_without_a_state_file(boot_tree):
    _arm(boot_tree, None)

    _run_boot_script(boot_tree)

    assert _applied(boot_tree) == {'edid': '', 'vsvdb_data': '', 'force_vsvdb': '0'}
    assert persist.disarmed() is False


def test_boot_script_disarms_on_an_empty_state_file(boot_tree):
    _arm(boot_tree, None)
    (boot_tree['dir'] / 'active.state').write_text('\n')

    _run_boot_script(boot_tree)

    assert _applied(boot_tree)['force_vsvdb'] == '0'
    assert persist.current_state() is None
    assert persist.disarmed() is True


def test_boot_script_applies_one_layer_alone(boot_tree):
    _arm(boot_tree, persist.STATE_CONFIRMED, vsvdb=False)

    _run_boot_script(boot_tree)

    applied = _applied(boot_tree)
    assert applied['edid'] == 'load %s\n' % _EDID_HEX
    assert applied['vsvdb_data'] == ''
    assert applied['force_vsvdb'] == '0'


def test_boot_script_reparses_a_vsvdb_only_profile_with_the_chain_on(boot_tree):
    _arm(boot_tree, persist.STATE_CONFIRMED, edid=False)

    _run_boot_script(boot_tree)

    assert _applied(boot_tree)['force_vsvdb'] == '255'
    assert boot_tree['edid'].read_text() == 'load 0000000000000000\n'


def test_boot_script_writes_no_sentinel_with_the_chain_off(boot_tree):
    boot_tree['hpd_state'].write_text('0\n')
    _arm(boot_tree, persist.STATE_CONFIRMED, edid=False)

    _run_boot_script(boot_tree)

    assert _applied(boot_tree)['force_vsvdb'] == '255'
    assert boot_tree['edid'].read_text() == ''


def test_boot_script_never_sentinels_over_its_own_forced_edid(boot_tree):
    _arm(boot_tree, persist.STATE_CONFIRMED)

    _run_boot_script(boot_tree)

    assert boot_tree['edid'].read_text() == 'load %s\n' % _EDID_HEX


def test_boot_script_defers_the_edid_layer_with_the_chain_off(boot_tree):
    boot_tree['hpd_state'].write_text('0\n')
    _arm(boot_tree, persist.STATE_PENDING)

    _run_boot_script(boot_tree)

    applied = _applied(boot_tree)
    assert applied['edid'] == ''
    assert applied['vsvdb_data'] == '0xeb,0x01,0x46,0xd0,0x00,0x48,0x02,0x87,0x83,0x5c,0x6d,0x8e'
    assert applied['force_vsvdb'] == '255'
    assert (boot_tree['dir'] / 'active.deferred').read_text() == 'edid-deferred\n'
    assert persist.current_state() == persist.STATE_TRIAL
    for name in os.listdir(str(boot_tree['dir'])):
        assert name.startswith('active.')


def test_boot_script_defers_an_edid_only_profile_with_the_chain_off(boot_tree):
    boot_tree['hpd_state'].write_text('0\n')
    _arm(boot_tree, persist.STATE_CONFIRMED, vsvdb=False)

    _run_boot_script(boot_tree)

    assert _applied(boot_tree) == {'edid': '', 'vsvdb_data': '', 'force_vsvdb': '0'}
    assert persist.deferred() is True
    assert persist.current_state() == persist.STATE_CONFIRMED


def test_boot_script_defers_nothing_with_the_chain_on(boot_tree):
    _arm(boot_tree, persist.STATE_CONFIRMED)

    _run_boot_script(boot_tree)

    assert boot_tree['edid'].read_text() == 'load %s\n' % _EDID_HEX
    assert persist.deferred() is False


def test_boot_script_clears_a_deferred_marker_it_did_not_write(boot_tree):
    # the marker describes one boot: a service that never ran must not leave
    # the next one applying an EDID layer nobody deferred
    (boot_tree['dir'] / 'active.deferred').write_text('edid-deferred\n')
    _arm(boot_tree, persist.STATE_TRIAL)

    _run_boot_script(boot_tree)

    assert persist.deferred() is False
    assert persist.disarmed() is True


def test_boot_script_writes_trial_before_applying(boot_tree):
    # a real crash mid-apply: the edid node is a fifo nobody reads, so the
    # apply blocks there and the process is killed in it. The state has to
    # already read trial, never the pending that would re-apply forever.
    boot_tree['edid'].unlink()
    os.mkfifo(str(boot_tree['edid']))
    _arm(boot_tree, persist.STATE_PENDING)

    process = subprocess.Popen(['/bin/sh', _BOOT_SCRIPT], env=boot_tree['env'])
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and process.poll() is None:
            if persist.current_state() == persist.STATE_TRIAL:
                break
            time.sleep(0.02)
    finally:
        process.kill()
        process.wait()

    assert persist.current_state() == persist.STATE_TRIAL


def test_boot_script_ignores_the_env_overrides_without_the_test_flag(boot_tree):
    _arm(boot_tree, persist.STATE_PENDING)
    del boot_tree['env']['EDIDMGR_TEST']

    _run_boot_script(boot_tree)

    assert _applied(boot_tree) == {'edid': '', 'vsvdb_data': '', 'force_vsvdb': '0'}
    assert persist.current_state() == persist.STATE_PENDING


def test_boot_script_survives_missing_sysfs(boot_tree, tmp_path):
    boot_tree['env']['EDIDMGR_PARAMS_DIR'] = str(tmp_path / 'absent')
    boot_tree['env']['EDIDMGR_HDMI_DIR'] = str(tmp_path / 'absent')
    _arm(boot_tree, persist.STATE_PENDING)

    _run_boot_script(boot_tree)

    assert persist.current_state() == persist.STATE_TRIAL
