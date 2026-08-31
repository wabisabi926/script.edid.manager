"""Flow-level coverage of the two Kodi entry points, default.py and
service.py, with the four xbmc modules stubbed in-test. Everything below is
about orchestration: which dialog is asked, in what order, and what reaches
sysfs as a result. The byte work has its own tests.
"""

import os
import re
import sys
import threading
import types
import xml.etree.ElementTree

import pytest

_HERE = os.path.dirname(__file__)
_ROOT = os.path.join(_HERE, '..')
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, 'resources', 'lib'))
sys.path.insert(0, os.environ.get(
    'SIDEDATA_LIB_PATH',
    os.path.join(_HERE, '..', '..', 'script.module.sidedata', 'lib')))

_PO_PATH = os.path.join(
    _ROOT, 'resources', 'language', 'resource.language.en_gb', 'strings.po')

with open(_PO_PATH, 'r', encoding='utf-8') as _handle:
    _STRINGS = dict(
        (int(sid), msgid.replace('\\n', '\n').replace('\\"', '"'))
        for sid, msgid in re.findall(r'msgctxt "#(\d+)"\nmsgid "(.*)"\n', _handle.read()))

_SETTINGS = {
    'safe_mode_timeout': 30,
    'default_apply_mode': 'runtime',
    'warn_on_avr_capture': False,
    'debug_logging': False,
    'boot_trial_wait': 120,
    'startup_delay': 5,
}


# only a hung countdown thread ever reaches it, and then the assertion is the
# point; no test waits anywhere near this long
_CLOSE_TIMEOUT = 5.0


class _Dialog(object):
    """Every xbmcgui dialog the addon opens, recorded and scripted. Answers
    are queues so a flow that asks twice is told apart from one that asks
    once; an exhausted queue answers the least destructive way.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.calls = []
        self.select_answers = []
        self.yesno_answers = []
        self.yesnocustom_answers = []
        self.input_answers = []
        self.on_yesnocustom = None
        self.builtins = []
        self.closed = threading.Event()
        self.wait_for_close = False

    def _answer(self, queue, fallback):
        return queue.pop(0) if queue else fallback

    def notification(self, heading, message):
        self.calls.append(('notification', message))

    def ok(self, heading, message):
        self.calls.append(('ok', message))
        return True

    def yesno(self, heading, message, nolabel=None, yeslabel=None, autoclose=0):
        self.calls.append(('yesno', message))
        return self._answer(self.yesno_answers, False)

    def yesnocustom(self, heading, message, customlabel=None, nolabel=None,
                    yeslabel=None, autoclose=0, defaultbutton=None):
        self.calls.append(('yesnocustom', message, customlabel, nolabel, yeslabel, autoclose))
        if self.on_yesnocustom is not None:
            self.on_yesnocustom()
        # the real dialog only returns once something closes it, which is what
        # makes the countdown's Dialog.Close ordering deterministic here
        if self.wait_for_close:
            assert self.closed.wait(_CLOSE_TIMEOUT), 'the countdown never closed the dialog'
        return self._answer(self.yesnocustom_answers, -1)

    def executebuiltin(self, command):
        self.builtins.append(command)
        if command == 'Dialog.Close(yesnodialog)':
            self.closed.set()

    def select(self, heading, labels, preselect=-1):
        self.calls.append(('select', tuple(labels)))
        return self._answer(self.select_answers, -1)

    def input(self, heading, default=''):
        self.calls.append(('input', heading))
        return self._answer(self.input_answers, default)

    def browse(self, browse_type, heading, shares):
        self.calls.append(('browse', heading))
        return self._answer(self.input_answers, '')


DIALOG = _Dialog()


class _Addon(object):
    def getLocalizedString(self, string_id):
        return _STRINGS.get(string_id, '')

    def getSettingInt(self, setting_id):
        return _SETTINGS[setting_id]

    def getSettingBool(self, setting_id):
        return _SETTINGS[setting_id]

    def getSettingString(self, setting_id):
        return _SETTINGS[setting_id]

    def getAddonInfo(self, key):
        return 'special://profile/addon_data/script.edid.manager'

    def openSettings(self):
        DIALOG.calls.append(('openSettings',))


class _Monitor(object):
    def waitForAbort(self, seconds):
        return False

    def abortRequested(self):
        return True


def _stub_module(name, **attributes):
    """Attributes onto whatever module object already answers to this name,
    creating it only if nothing does. Another test module may have stubbed it
    first and had its object bound into ui.py's namespace by `import xbmcgui`,
    which a plain sys.modules replacement would not reach.
    """
    module = sys.modules.get(name)
    if module is None:
        module = types.ModuleType(name)
        sys.modules[name] = module
    for key, value in attributes.items():
        setattr(module, key, value)


def _install_stubs():
    _stub_module('xbmc', LOGDEBUG=0, LOGINFO=1, LOGWARNING=2, LOGERROR=3,
                 log=lambda message, level=1: None, Monitor=_Monitor,
                 executebuiltin=lambda command: DIALOG.executebuiltin(command))
    _stub_module('xbmcgui', Dialog=lambda: DIALOG)
    _stub_module('xbmcaddon', Addon=_Addon)
    _stub_module('xbmcvfs', translatePath=lambda path: path)


_install_stubs()

import backend  # noqa: E402
import persist  # noqa: E402
import runtime  # noqa: E402
import state  # noqa: E402
import store  # noqa: E402

import default  # noqa: E402
import service  # noqa: E402
import ui  # noqa: E402

_VSVDB_HEX = 'eb0146d000480287835c6d8e'


def _checksum(block):
    block = bytearray(block)
    block[127] = 0
    block[127] = (256 - sum(block[:127])) % 256
    return bytes(block)


def _base_block(ext_count=0):
    b = bytearray(128)
    b[0:8] = bytes.fromhex('00ffffffffffff00')
    b[0x7e] = ext_count
    return _checksum(b)


def _cta_extension(data_blocks=b''):
    b = bytearray(128)
    b[0] = 0x02
    b[1] = 3
    b[2] = 4 + len(data_blocks)
    b[4:4 + len(data_blocks)] = data_blocks
    return _checksum(b)


_EDID_WITH_VSVDB = _base_block(ext_count=1) + _cta_extension(bytes.fromhex(_VSVDB_HEX))
_EDID_HEX = _EDID_WITH_VSVDB.hex()


@pytest.fixture(autouse=True)
def kodi(tmp_path, monkeypatch):
    DIALOG.reset()
    monkeypatch.setenv('EDIDMGR_PROFILES_PATH', str(tmp_path / 'profiles.json'))
    persist_dir = tmp_path / 'edid-manager'
    persist_dir.mkdir()
    monkeypatch.setenv('EDIDMGR_PERSIST_DIR', str(persist_dir))
    monkeypatch.setenv('EDIDMGR_MODPROBE_D', str(tmp_path / 'absent'))
    ui._ADDON = None
    return DIALOG


@pytest.fixture
def sysfs(monkeypatch):
    """The whole sysfs surface as plain values, with every write recorded.
    `writes` is the assertion surface: a flow that never writes is a flow
    that never touched the box.
    """
    node = {'force_vsvdb': 0, 'vsvdb_data': b'', 'rawedid': b'', 'dv_cap': '',
            'hpd_state': 1, 'writes': []}

    def write_force_vsvdb(value):
        node['writes'].append(('force_vsvdb', value))

    def write_vsvdb_data(data):
        node['writes'].append(('vsvdb_data', data.hex()))

    def write_edid(data):
        node['writes'].append(('edid', data.hex()))

    def revert_edid():
        node['writes'].append(('edid', 'sentinel'))

    monkeypatch.setattr(backend, 'read_force_vsvdb', lambda: node['force_vsvdb'])
    monkeypatch.setattr(backend, 'read_vsvdb_data', lambda: node['vsvdb_data'])
    monkeypatch.setattr(backend, 'read_rawedid', lambda: node['rawedid'])
    monkeypatch.setattr(backend, 'read_hpd_state', lambda: node['hpd_state'])
    monkeypatch.setattr(backend, 'read_effective_vsvdb_hex', lambda: node['dv_cap'])
    monkeypatch.setattr(backend, 'write_force_vsvdb', write_force_vsvdb)
    monkeypatch.setattr(backend, 'write_vsvdb_data', write_vsvdb_data)
    monkeypatch.setattr(backend, 'write_edid', write_edid)
    monkeypatch.setattr(backend, 'revert_edid', revert_edid)
    return node


@pytest.fixture
def unit(monkeypatch):
    calls = []
    monkeypatch.setattr(persist, 'enable_unit', lambda: (calls.append('enable'), (True, ''))[1])
    monkeypatch.setattr(persist, 'disable_unit',
                        lambda: (calls.append('disable'), (True, ''))[1])
    return calls


def _messages(dialog):
    return [call[1] for call in dialog.calls]


class TestForcedEdidEvidence:
    """B1. `_forced_edid_hex` names the bytes a VSVDB re-parse re-loads, so a
    false positive silently turns every VSVDB apply into a full EDID load
    that skipped the mandatory second confirmation.
    """

    def test_a_captured_edid_profile_is_not_treated_as_forced(self, kodi, sysfs):
        sysfs['rawedid'] = _EDID_WITH_VSVDB
        store.add_profile(store.KIND_EDID, 'sink snapshot', _EDID_HEX, store.SOURCE_CAPTURED)
        vsvdb_profile = store.add_profile(
            store.KIND_VSVDB, 'block', _VSVDB_HEX, store.SOURCE_CAPTURED)
        live = default._read_live()

        assert default._forced_edid_hex(live) is None

        kodi.yesnocustom_answers = [1]
        default._apply_runtime(live, vsvdb_profile)

        assert ('edid', _EDID_HEX) not in sysfs['writes']
        assert sysfs['writes'] == [
            ('vsvdb_data', _VSVDB_HEX),
            ('force_vsvdb', 255),
            ('edid', 'sentinel'),
        ]
        assert not any(call[0] == 'yesno' for call in kodi.calls)

    def test_a_persisted_effective_edid_profile_is_the_reparse_target(self, kodi, sysfs, unit):
        sysfs['rawedid'] = _EDID_WITH_VSVDB
        edid_profile = store.add_profile(
            store.KIND_EDID, 'forced', _EDID_HEX, store.SOURCE_CAPTURED)
        persist.persist_profile(edid_profile)
        vsvdb_profile = store.add_profile(
            store.KIND_VSVDB, 'block', _VSVDB_HEX, store.SOURCE_CAPTURED)
        live = default._read_live()

        assert default._forced_edid_hex(live) == _EDID_HEX

        kodi.yesnocustom_answers = [1]
        default._apply_runtime(live, vsvdb_profile)

        assert sysfs['writes'][-1] == ('edid', _EDID_HEX)

    def test_the_capture_gate_stays_wider_than_the_reparse_target(self, kodi, sysfs):
        sysfs['rawedid'] = _EDID_WITH_VSVDB
        store.add_profile(store.KIND_EDID, 'sink snapshot', _EDID_HEX, store.SOURCE_CAPTURED)
        live = default._read_live()

        assert default._edid_override_suspected(live) is True

        default._capture_edid(live)

        assert kodi.calls[0] == (
            'select',
            (_STRINGS[ui.S_CAPTURE_REVERT_FIRST], _STRINGS[ui.S_CAPTURE_AS_COPY]))


class TestCountdownMapping:
    """R1. yesnocustom reports a cancel separately from a No, so only the No
    button is destructive. The countdown itself is the addon's own timer:
    Kodi's autoclose returns 0, exactly what the No button returns, so the
    timer's own flag is what tells a run-out dialog from a decline.
    """

    @pytest.mark.parametrize('answer,expected', [
        (1, 'yes'),
        (0, 'no'),
        (2, 'timeout'),
        (-1, 'timeout'),
        (99, 'timeout'),
    ])
    def test_answer_mapping_while_the_timer_still_has_time(self, kodi, answer, expected):
        kodi.yesnocustom_answers = [answer]

        assert ui.countdown_confirm('heading', 'message', 300) == expected
        assert kodi.builtins == []

    def test_the_timer_closes_the_dialog_and_that_reads_as_a_timeout(self, kodi):
        kodi.wait_for_close = True
        # an externally closed yesnocustom returns 0, device-verified
        kodi.yesnocustom_answers = [0]

        assert ui.countdown_confirm('heading', 'message', 0.01) == 'timeout'
        assert kodi.builtins == ['Dialog.Close(yesnodialog)']

    def test_a_no_landing_as_the_timer_fires_reads_as_a_timeout(self, kodi):
        # the race the mapping cannot resolve: a No press and the timer's own
        # close both return 0 with the flag already set. Asking again next
        # boot is the direction chosen over reverting a profile just kept
        kodi.wait_for_close = True
        kodi.on_yesnocustom = lambda: kodi.yesnocustom_answers.append(0)

        assert ui.countdown_confirm('heading', 'message', 0.01) == 'timeout'

    def test_an_answered_dialog_leaves_no_timer_behind(self, kodi):
        # a surviving thread would close whatever yesnodialog is open when its
        # wait ends, which is any later dialog the addon shows
        threads = threading.active_count()
        kodi.yesnocustom_answers = [1]

        ui.countdown_confirm('heading', 'message', 300)

        assert threading.active_count() == threads
        assert kodi.builtins == []

    def test_the_dialog_carries_the_three_labels_and_no_autoclose(self, kodi):
        kodi.yesnocustom_answers = [1]

        ui.countdown_confirm('heading', 'message', 30)

        call = kodi.calls[0]
        assert call[0] == 'yesnocustom'
        assert call[2] == _STRINGS[ui.S_COUNTDOWN_LATER]
        assert call[3] == _STRINGS[ui.S_COUNTDOWN_REVERT]
        assert call[4] == _STRINGS[ui.S_COUNTDOWN_KEEP]
        assert call[5] == 0


class TestBootTrial:
    """Design section 3: the state write happens the instant the dialog
    opens, and only an explicit No disarms.
    """

    @pytest.fixture
    def armed(self, kodi, sysfs):
        profile = store.add_profile(
            store.KIND_VSVDB, 'armed', _VSVDB_HEX, store.SOURCE_CAPTURED)
        persist.persist_profile(profile)
        persist.set_state(persist.STATE_TRIAL)
        sysfs['dv_cap'] = _VSVDB_HEX
        return profile

    def test_trial_becomes_acked_before_the_answer_is_read(self, kodi, armed, unit):
        seen = []
        kodi.on_yesnocustom = lambda: seen.append(persist.current_state())
        kodi.yesnocustom_answers = [1]

        service._boot_trial(_Monitor())

        assert seen == [persist.STATE_TRIAL_ACKED]

    def test_yes_confirms(self, kodi, armed, unit):
        kodi.yesnocustom_answers = [1]

        service._boot_trial(_Monitor())

        assert persist.current_state() == persist.STATE_CONFIRMED
        assert unit == []

    def test_no_reverts_and_disarms_without_removing_the_unit(self, kodi, armed, sysfs, unit):
        kodi.yesnocustom_answers = [0]

        service._boot_trial(_Monitor())

        assert sysfs['writes'] == [('force_vsvdb', 0), ('edid', 'sentinel')]
        assert persist.current_state() is None
        assert persist.load_persisted() == {}
        assert unit == []

    @pytest.mark.parametrize('answer', [2, -1])
    def test_an_unanswered_dialog_asks_again_next_boot(self, kodi, armed, sysfs, unit, answer):
        kodi.yesnocustom_answers = [answer]

        service._boot_trial(_Monitor())

        assert persist.current_state() == persist.STATE_TRIAL_ACKED
        assert sysfs['writes'] == []
        assert persist.load_persisted() != {}

    def test_nothing_is_asked_when_the_profile_never_became_effective(self, kodi, armed, sysfs):
        sysfs['dv_cap'] = ''

        service._boot_trial(_Monitor())

        assert kodi.calls == []
        assert persist.current_state() == persist.STATE_TRIAL

    @pytest.mark.parametrize('state_value', [
        persist.STATE_PENDING, persist.STATE_CONFIRMED])
    def test_no_dialog_outside_the_two_trial_states(self, kodi, armed, state_value):
        persist.set_state(state_value)

        service._boot_trial(_Monitor())

        assert kodi.calls == []


class TestDeferredEdidApply:
    """The boot unit skips an EDID layer with hpd_state at 0, where a forced
    EDID wedges Kodi before it is up. The service finishes the apply once the
    GUI exists, and everything downstream has to see the applied state.
    """

    @pytest.fixture
    def deferred(self, kodi, sysfs):
        profile = store.add_profile(
            store.KIND_EDID, 'forced', _EDID_HEX, store.SOURCE_CAPTURED)
        persist.persist_profile(profile)
        persist.set_state(persist.STATE_TRIAL)
        with open(persist.deferred_path(), 'w', encoding='utf-8') as handle:
            handle.write('edid-deferred\n')
        return profile

    def test_the_layer_lands_and_the_marker_clears(self, kodi, sysfs, deferred):
        sysfs['hpd_state'] = 0

        service._apply_deferred()

        assert sysfs['writes'] == [('edid', _EDID_HEX)]
        assert persist.deferred() is False

    def test_a_vsvdb_layer_goes_on_top_in_the_kernel_order(self, kodi, sysfs, deferred):
        sysfs['hpd_state'] = 0
        persist.persist_profile(
            {'id': 'v1', 'kind': store.KIND_VSVDB, 'raw': _VSVDB_HEX})

        service._apply_deferred()

        assert sysfs['writes'] == [
            ('edid', _EDID_HEX),
            ('vsvdb_data', _VSVDB_HEX),
            ('force_vsvdb', 255),
        ]
        assert persist.deferred() is False

    def test_nothing_is_applied_without_the_marker(self, kodi, sysfs, deferred):
        persist.clear_deferred()

        service._apply_deferred()

        assert sysfs['writes'] == []

    def test_a_marker_with_no_edid_layer_just_clears(self, kodi, sysfs, deferred):
        persist.unpersist(store.KIND_EDID)
        with open(persist.deferred_path(), 'w', encoding='utf-8') as handle:
            handle.write('edid-deferred\n')

        service._apply_deferred()

        assert sysfs['writes'] == []
        assert persist.deferred() is False

    def test_a_failed_apply_clears_the_marker_rather_than_retrying(self, kodi, sysfs, deferred,
                                                                   monkeypatch):
        def refuse(data):
            raise OSError('device or resource busy')

        monkeypatch.setattr(backend, 'write_edid', refuse)

        service._apply_deferred()

        assert persist.deferred() is False
        assert persist.current_state() == persist.STATE_TRIAL
        assert persist.load_persisted() != {}

    def test_the_trial_judges_the_state_the_service_just_applied(self, kodi, sysfs, deferred,
                                                                 unit, monkeypatch):
        # rawedid only reads back the forced block once the load has landed, so
        # a trial that ran before the deferred apply would time out on a
        # profile that is about to be perfectly effective
        def write_edid(data):
            sysfs['writes'].append(('edid', data.hex()))
            sysfs['rawedid'] = data

        monkeypatch.setattr(backend, 'write_edid', write_edid)
        kodi.yesnocustom_answers = [1]

        service.run()

        assert sysfs['writes'] == [('edid', _EDID_HEX)]
        assert persist.deferred() is False
        assert persist.current_state() == persist.STATE_CONFIRMED


class TestRestartHole:
    """Finding 11 again, at the other end of the session: a Kodi restart puts
    display init through the forced EDID the same way a boot does, and the
    unit's deferral only runs at boot. The service hands the restart a clean
    kernel and re-owes the layer to the next start.
    """

    @pytest.fixture
    def forced(self, kodi, sysfs):
        sysfs['hpd_state'] = 0
        sysfs['rawedid'] = _EDID_WITH_VSVDB
        return store.add_profile(
            store.KIND_EDID, 'forced', _EDID_HEX, store.SOURCE_CAPTURED)

    def test_a_persisted_layer_is_reverted_and_re_owed(self, kodi, sysfs, forced, unit):
        persist.persist_profile(forced)

        service._revert_for_restart()

        assert sysfs['writes'] == [('edid', 'sentinel')]
        assert persist.deferred() is True
        assert persist.load_persisted()[store.KIND_EDID]['raw'] == _EDID_HEX

    def test_the_next_start_applies_what_the_shutdown_deferred(self, kodi, sysfs, forced, unit):
        persist.persist_profile(forced)
        service._revert_for_restart()

        service._apply_deferred()

        assert sysfs['writes'] == [('edid', 'sentinel'), ('edid', _EDID_HEX)]
        assert persist.deferred() is False

    def test_a_live_chain_is_left_alone(self, kodi, sysfs, forced, unit):
        sysfs['hpd_state'] = 1
        persist.persist_profile(forced)

        service._revert_for_restart()

        assert sysfs['writes'] == []
        assert persist.deferred() is False

    def test_an_unpersisted_profile_is_reverted_without_being_re_owed(self, kodi, sysfs, forced):
        service._revert_for_restart()

        assert sysfs['writes'] == [('edid', 'sentinel')]
        assert persist.deferred() is False

    def test_an_edid_nothing_here_armed_is_left_alone(self, kodi, sysfs):
        # finding 8: a forced EDID that matches no profile of ours is
        # indistinguishable from the sink's own block, so reverting it would
        # be speculation, and with the chain off nothing re-reads it back
        sysfs['hpd_state'] = 0
        sysfs['rawedid'] = _base_block()

        service._revert_for_restart()

        assert sysfs['writes'] == []
        assert persist.deferred() is False

    def test_an_empty_rawedid_is_nothing_to_revert(self, kodi, sysfs, forced, unit):
        sysfs['rawedid'] = b''
        persist.persist_profile(forced)

        service._revert_for_restart()

        assert sysfs['writes'] == []
        assert persist.deferred() is False

    def test_the_service_reverts_when_kodi_aborts(self, kodi, sysfs, forced, unit):
        persist.persist_profile(forced)

        service.run()

        assert sysfs['writes'] == [('edid', 'sentinel')]
        assert persist.deferred() is True


class TestEffectiveLayers:
    """What _wait_effective polls: a combined profile counts as effective only
    when both layers read back, never on the strength of one.
    """

    @pytest.fixture
    def combined(self, kodi, sysfs):
        persist.persist_profile({'id': 'e1', 'kind': store.KIND_EDID, 'raw': _EDID_HEX})
        persist.persist_profile({'id': 'v1', 'kind': store.KIND_VSVDB, 'raw': _VSVDB_HEX})
        return persist.load_persisted()

    def test_the_edid_layer_alone_is_not_enough(self, kodi, sysfs, combined):
        sysfs['rawedid'] = _EDID_WITH_VSVDB

        assert service._effective(combined) is False

    def test_the_vsvdb_layer_alone_is_not_enough(self, kodi, sysfs, combined):
        sysfs['dv_cap'] = _VSVDB_HEX

        assert service._effective(combined) is False

    def test_both_layers_reading_back_is(self, kodi, sysfs, combined):
        sysfs['rawedid'] = _EDID_WITH_VSVDB
        sysfs['dv_cap'] = _VSVDB_HEX

        assert service._effective(combined) is True


class TestChainOffPersist:
    """B3, design section 5: the params go in even with the chain off, minus
    the re-parse that would flatten dv_cap. That is what makes
    persisted-pending a state that actually occurs.
    """

    def test_params_are_written_without_a_reparse(self, kodi, sysfs, unit):
        sysfs['hpd_state'] = 0
        profile = store.add_profile(
            store.KIND_VSVDB, 'pending', _VSVDB_HEX, store.SOURCE_CAPTURED)
        live = default._read_live()

        default._persist(live, profile)

        assert sysfs['writes'] == [('vsvdb_data', _VSVDB_HEX), ('force_vsvdb', 255)]
        assert persist.current_state() == persist.STATE_PENDING
        assert persist.load_persisted()[store.KIND_VSVDB]['raw'] == _VSVDB_HEX

    def test_the_user_is_told_it_waits_for_the_display(self, kodi, sysfs, unit):
        sysfs['hpd_state'] = 0
        profile = store.add_profile(
            store.KIND_VSVDB, 'pending', _VSVDB_HEX, store.SOURCE_CAPTURED)

        default._persist(default._read_live(), profile)

        assert _STRINGS[ui.S_CHAIN_OFF_PENDING] in _messages(kodi)
        assert not any(call[0] == 'yesnocustom' for call in kodi.calls)

    def test_the_classifier_then_reads_persisted_pending(self, kodi, sysfs, unit):
        sysfs['hpd_state'] = 0
        profile = store.add_profile(
            store.KIND_VSVDB, 'pending', _VSVDB_HEX, store.SOURCE_CAPTURED)
        default._persist(default._read_live(), profile)

        sysfs['force_vsvdb'] = runtime.FORCE_CUSTOM
        sysfs['vsvdb_data'] = bytes.fromhex(_VSVDB_HEX)
        live = default._read_live()

        assert live['vsvdb'].state == state.PERSISTED_PENDING
        assert live['vsvdb'].profile_id == profile['id']


class TestEdidSecondConfirm:
    """Design section 3: an EDID profile takes the intent confirmation before
    either apply point, and declining it leaves sysfs and the store alone.
    """

    @pytest.fixture
    def edid_profile(self, kodi):
        return store.add_profile(
            store.KIND_EDID, 'forced', _EDID_HEX, store.SOURCE_CAPTURED)

    def test_declining_stops_a_runtime_apply(self, kodi, sysfs, edid_profile):
        kodi.yesno_answers = [False]

        default._apply_runtime(default._read_live(), edid_profile)

        assert sysfs['writes'] == []
        assert not any(call[0] == 'yesnocustom' for call in kodi.calls)

    def test_declining_stops_a_persist(self, kodi, sysfs, unit, edid_profile):
        kodi.yesno_answers = [False]

        default._persist(default._read_live(), edid_profile)

        assert sysfs['writes'] == []
        assert unit == []
        assert persist.load_persisted() == {}

    def test_accepting_applies_and_then_asks_the_countdown(self, kodi, sysfs, edid_profile):
        kodi.yesno_answers = [True]
        kodi.yesnocustom_answers = [1]

        default._apply_runtime(default._read_live(), edid_profile)

        assert sysfs['writes'] == [('edid', _EDID_HEX)]
        assert [call[0] for call in kodi.calls][:2] == ['yesno', 'yesnocustom']

    def test_the_persist_confirmation_ends_on_the_recovery_line(self, kodi, sysfs, unit,
                                                                edid_profile):
        kodi.yesno_answers = [False]

        default._persist(default._read_live(), edid_profile)

        assert _messages(kodi)[0].endswith(_STRINGS[ui.S_EDID_RECOVERY])

    def test_a_declined_countdown_reverts_and_unpersists(self, kodi, sysfs, unit, edid_profile):
        kodi.yesno_answers = [True]
        kodi.yesnocustom_answers = [0]

        default._persist(default._read_live(), edid_profile)

        assert sysfs['writes'] == [('edid', _EDID_HEX), ('edid', 'sentinel')]
        assert persist.load_persisted() == {}


class TestImportIntent:
    """Design section 3: a full EDID always asks what it is for, and the two
    answers are labeled for what they do.
    """

    def test_the_question_uses_the_binding_labels(self, kodi):
        kodi.select_answers = [-1]

        default._ingest(_EDID_WITH_VSVDB, 'sink.bin')

        assert kodi.calls[0] == (
            'select',
            (_STRINGS[ui.S_IMPORT_USE_FULL_EDID], _STRINGS[ui.S_IMPORT_EXTRACT_VSVDB]))
        assert store.list_profiles() == []

    def test_the_full_edid_answer_stores_the_whole_blob(self, kodi):
        kodi.select_answers = [0]
        kodi.input_answers = ['whole thing']

        default._ingest(_EDID_WITH_VSVDB, 'sink.bin')

        profiles = store.list_profiles()
        assert [p['kind'] for p in profiles] == [store.KIND_EDID]
        assert profiles[0]['raw'] == _EDID_HEX
        assert profiles[0]['origin_file'] == 'sink.bin'
        assert _STRINGS[ui.S_IMPORT_CONFIRM_FULL_EDID] % 'sink.bin' in _messages(kodi)

    def test_the_extract_answer_stores_only_the_block(self, kodi):
        kodi.select_answers = [1]
        kodi.input_answers = ['just the block']

        default._ingest(_EDID_WITH_VSVDB, 'sink.bin')

        profiles = store.list_profiles()
        assert [p['kind'] for p in profiles] == [store.KIND_VSVDB]
        assert profiles[0]['raw'] == _VSVDB_HEX
        assert profiles[0]['extracted_from_edid'] is True
        assert _STRINGS[ui.S_IMPORT_CONFIRM_EXTRACTED] % 'sink.bin' in _messages(kodi)

    def test_a_bare_block_never_asks(self, kodi):
        kodi.input_answers = ['pasted block']

        default._ingest(bytes.fromhex(_VSVDB_HEX))

        assert not any(call[0] == 'select' for call in kodi.calls)
        assert [p['kind'] for p in store.list_profiles()] == [store.KIND_VSVDB]


class TestModprobeLeftovers:
    """R2: the service toasts, the manager UI is where a file gets deleted,
    and only a file that configures nothing but aml_media is deletable.
    """

    @pytest.fixture
    def leftovers(self, tmp_path, monkeypatch):
        directory = tmp_path / 'modprobe.d'
        directory.mkdir()
        (directory / 'vsvdb.conf').write_text('options aml_media force_vsvdb=2\n')
        monkeypatch.setenv('EDIDMGR_MODPROBE_D', str(directory))
        return directory

    def test_the_service_toasts_rather_than_blocking(self, kodi, leftovers):
        service._warn_modprobe_leftovers()

        assert [call[0] for call in kodi.calls] == ['notification']

    def test_the_ui_shows_the_content_before_deleting(self, kodi, leftovers):
        path = str(leftovers / 'vsvdb.conf')
        kodi.select_answers = [0]
        kodi.yesno_answers = [True]

        default._delete_modprobe_leftovers([path])

        assert 'options aml_media force_vsvdb=2' in _messages(kodi)[1]
        assert not os.path.exists(path)

    def test_declining_keeps_the_file(self, kodi, leftovers):
        path = str(leftovers / 'vsvdb.conf')
        kodi.select_answers = [0]
        kodi.yesno_answers = [False]

        default._delete_modprobe_leftovers([path])

        assert os.path.exists(path)

    def test_a_file_with_other_options_is_reported_not_deleted(self, kodi, leftovers):
        path = str(leftovers / 'mixed.conf')
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write('options aml_media force_vsvdb=2\noptions snd_hda_intel model=auto\n')
        kodi.select_answers = [0]

        default._delete_modprobe_leftovers([path])

        assert _messages(kodi)[1] == _STRINGS[ui.S_MODPROBE_NOT_ONLY_OURS] % path
        assert os.path.exists(path)


class TestMenuNavigation:
    """The manager menu is three levels (top, Profiles, Add a profile)
    instead of one flat list. Back is select returning -1 at every level,
    and only the Profiles submenu re-lists itself after an action; the Add
    submenu and the top level both hand control back up.
    """

    def test_top_level_rows_and_order(self, kodi, sysfs):
        kodi.select_answers = [-1]

        default._main_menu()

        assert kodi.calls[0][0] == 'select'
        assert kodi.calls[0][1][1:] == (
            _STRINGS[ui.S_MENU_PROFILES] % 0,
            _STRINGS[ui.S_MENU_ADD_PROFILE],
            _STRINGS[ui.S_MENU_REVERT_REAL],
            _STRINGS[ui.S_MENU_SETTINGS],
        )

    def test_profiles_row_counts_stored_profiles(self, kodi, sysfs):
        store.add_profile(store.KIND_VSVDB, 'one', _VSVDB_HEX, store.SOURCE_CAPTURED)
        kodi.select_answers = [-1]

        default._main_menu()

        assert kodi.calls[0][1][1] == _STRINGS[ui.S_MENU_PROFILES] % 1

    def test_profiles_submenu_shows_the_empty_placeholder_and_returns_up(self, kodi, sysfs):
        # 1 opens Profiles, -1 backs out of it, -1 backs out of the top level
        kodi.select_answers = [1, -1, -1]

        default._main_menu()

        assert kodi.calls[1] == ('select', (_STRINGS[ui.S_MENU_NO_PROFILES],))
        assert [call[0] for call in kodi.calls] == ['select', 'select', 'select']

    def test_a_profile_action_returns_to_the_re_listed_profiles_submenu(self, kodi, sysfs):
        profile = store.add_profile(store.KIND_VSVDB, 'one', _VSVDB_HEX, store.SOURCE_CAPTURED)
        # 1 opens Profiles, 0 opens the profile, -1 backs out of the profile
        # menu (no action taken), -1 backs out of Profiles, -1 exits the top
        kodi.select_answers = [1, 0, -1, -1, -1]

        default._main_menu()

        assert sysfs['writes'] == []
        label = (default._profile_label(profile, default._read_live()),)
        profiles_calls = [call for call in kodi.calls if call[0] == 'select' and call[1] == label]
        assert len(profiles_calls) == 2

    def test_add_submenu_lists_the_five_intake_actions_in_order(self, kodi, sysfs):
        # 2 opens Add a profile, -1 backs out without choosing one, -1 exits
        kodi.select_answers = [2, -1, -1]

        default._main_menu()

        assert kodi.calls[1] == ('select', (
            _STRINGS[ui.S_MENU_CAPTURE_VSVDB], _STRINGS[ui.S_MENU_CAPTURE_EDID],
            _STRINGS[ui.S_MENU_ADD_PRESET], _STRINGS[ui.S_MENU_PASTE_HEX],
            _STRINGS[ui.S_MENU_IMPORT_FILE]))

    def test_a_successful_add_redisplays_the_add_menu_not_the_top_level(self, kodi, sysfs):
        # 2 opens Add a profile, 3 picks Paste hex, then the hex text and the
        # profile name; the Add menu redisplays rather than unwinding to the
        # top, -1 backs out of it, -1 exits the re-rendered top level
        kodi.select_answers = [2, 3, -1, -1]
        kodi.input_answers = [_VSVDB_HEX, 'pasted block']

        default._main_menu()

        assert [p['kind'] for p in store.list_profiles()] == [store.KIND_VSVDB]
        assert [call[0] for call in kodi.calls] == [
            'select', 'select', 'input', 'input', 'select', 'select']
        assert kodi.calls[4] == kodi.calls[1]

    def test_a_cancelled_ask_text_redisplays_the_add_menu_not_the_top_level(self, kodi, sysfs):
        # 2 opens Add a profile, 3 picks Paste hex; the hex prompt is
        # cancelled (empty text), so nothing is stored and the Add menu
        # redisplays instead of unwinding to the top; -1 backs out of it,
        # -1 exits the re-rendered top level
        kodi.select_answers = [2, 3, -1, -1]
        kodi.input_answers = ['']

        default._main_menu()

        assert store.list_profiles() == []
        assert [call[0] for call in kodi.calls] == ['select', 'select', 'input', 'select', 'select']
        assert kodi.calls[3] == kodi.calls[1]

    def test_add_preset_lists_the_decoded_fact_labels(self, kodi, sysfs):
        kodi.select_answers = [-1]

        default._add_preset()

        assert kodi.calls[0] == ('select', (
            _STRINGS[ui.S_PRESET_1_LABEL], _STRINGS[ui.S_PRESET_2_LABEL],
            _STRINGS[ui.S_PRESET_3_LABEL], _STRINGS[ui.S_PRESET_4_LABEL]))

    def test_settings_row_opens_the_native_settings_dialog(self, kodi, sysfs):
        kodi.select_answers = [4, -1]

        default._main_menu()

        assert ('openSettings',) in kodi.calls

    def test_the_modprobe_leftover_row_is_last_and_dispatches_to_its_handler(
            self, kodi, sysfs, monkeypatch, tmp_path):
        directory = tmp_path / 'modprobe.d'
        directory.mkdir()
        (directory / 'vsvdb.conf').write_text('options aml_media force_vsvdb=2\n')
        monkeypatch.setenv('EDIDMGR_MODPROBE_D', str(directory))
        leftover_path = str(directory / 'vsvdb.conf')
        # 5 opens the modprobe row, -1 backs out of it without picking a
        # file, -1 exits the re-rendered top level
        kodi.select_answers = [5, -1, -1]

        default._main_menu()

        assert kodi.calls[0][1][-1] == _STRINGS[ui.S_MENU_MODPROBE_DELETE]
        assert kodi.calls[1] == ('select', (leftover_path,))


class TestBootTimingClamp:
    """boot_trial_wait and startup_delay can arrive out of range via a direct
    userdata edit, so both are clamped again in code rather than trusted to
    the settings.xml constraints alone.
    """

    @pytest.mark.parametrize('raw,expected', [
        (10, service._TRIAL_WAIT_FLOOR),
        (120, 120),
        (300, 300),
        (900, service._TRIAL_WAIT_CEILING),
    ])
    def test_boot_trial_wait_is_clamped(self, kodi, monkeypatch, raw, expected):
        monkeypatch.setitem(_SETTINGS, 'boot_trial_wait', raw)

        assert service._trial_wait() == expected

    @pytest.mark.parametrize('raw,expected', [
        (-5, service._STARTUP_DELAY_FLOOR),
        (0, service._STARTUP_DELAY_FLOOR),
        (5, 5),
        (30, 30),
        (120, service._STARTUP_DELAY_CEILING),
    ])
    def test_startup_delay_is_clamped(self, kodi, monkeypatch, raw, expected):
        monkeypatch.setitem(_SETTINGS, 'startup_delay', raw)

        assert service._startup_delay() == expected

    @pytest.mark.parametrize('raw', [-100, -1, 0])
    def test_startup_delay_never_disables_the_boot_block(self, kodi, monkeypatch, raw):
        # waitForAbort(0) blocks forever in real Kodi (timeoutMS<=0 reads as
        # infinite), so a clamp that could still return 0 would silently
        # skip every boot check
        monkeypatch.setitem(_SETTINGS, 'startup_delay', raw)

        assert service._startup_delay() > 0

    @pytest.mark.parametrize('raw,expected', [
        (1, service._SAFE_MODE_TIMEOUT_FLOOR),
        (30, 30),
        (500, service._SAFE_MODE_TIMEOUT_CEILING),
    ])
    def test_safe_mode_timeout_is_clamped_in_the_service(self, kodi, monkeypatch, raw, expected):
        monkeypatch.setitem(_SETTINGS, 'safe_mode_timeout', raw)

        assert service._safe_mode_timeout() == expected

    @pytest.mark.parametrize('raw,expected', [
        (1, default._SAFE_MODE_TIMEOUT_FLOOR),
        (30, 30),
        (500, default._SAFE_MODE_TIMEOUT_CEILING),
    ])
    def test_safe_mode_timeout_is_clamped_in_the_manager(self, kodi, monkeypatch, raw, expected):
        monkeypatch.setitem(_SETTINGS, 'safe_mode_timeout', raw)

        assert default._safe_mode_timeout() == expected


def test_an_unimportable_sidedata_is_logged_not_swallowed(kodi, monkeypatch):
    logged = []
    monkeypatch.setitem(sys.modules, 'sidedata', None)
    monkeypatch.setattr(sys.modules['xbmc'], 'log',
                        lambda message, level=1: logged.append((message, level)))

    assert backend.read_effective_vsvdb_hex() is None
    assert len(logged) == 1
    assert logged[0][1] == sys.modules['xbmc'].LOGERROR


def test_the_script_extension_comes_first_so_the_addon_lands_under_programs():
    root = xml.etree.ElementTree.parse(os.path.join(_ROOT, 'addon.xml')).getroot()
    points = [e.get('point') for e in root.findall('extension')]

    assert points[:2] == ['xbmc.python.script', 'xbmc.service']


def test_reconcile_reports_disarmed_not_drifted_after_a_boot_disarm(kodi, sysfs, monkeypatch):
    # device drill: active.disarmed present, active.profile retained, params
    # back at defaults. The stale persisted raw disagreeing with defaulted
    # params is technically drifted per the classifier, but misleading right
    # next to the disarmed notice the user just saw.
    logged = []
    monkeypatch.setattr(sys.modules['xbmc'], 'log', lambda message, level=1: logged.append(message))
    profile = store.add_profile(
        store.KIND_VSVDB, 'armed', _VSVDB_HEX, store.SOURCE_CAPTURED)
    persist.persist_profile(profile)
    persist.disarm()

    service._reconcile()

    assert any('vsvdb disarmed at boot (profile retained)' in message for message in logged)
    assert not any('drifted' in message for message in logged)


def test_a_failed_rearm_says_so(kodi):
    persist.disarm()
    kodi.yesno_answers = [True]

    service._disarmed_notice()

    assert _STRINGS[ui.S_REARM_UNAVAILABLE] in _messages(kodi)
    assert persist.disarmed() is False
