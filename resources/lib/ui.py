# Every dialog the addon shows, plus the string ids they are drawn from, so
# default.py and service.py read as flows rather than as xbmcgui calls. The
# formatting helpers at the bottom take plain values and localized templates
# and are unit-tested directly.
#
# See ~/ce22-docs/vsvdb-manager-design.md sections 1 (stock dialogs only), 3
# (the countdown, the import intent question, the EDID second confirmation)
# and 4 (requested versus achieved).

import threading

import xbmc
import xbmcaddon
import xbmcgui

S_ADDON_NAME = 32000
S_SETTINGS_GENERAL = 32001
S_SETTINGS_TIMEOUT = 32002
S_SETTINGS_APPLY_MODE = 32003
S_SETTINGS_APPLY_RUNTIME = 32004
S_SETTINGS_APPLY_PERSIST = 32005
S_SETTINGS_AVR_WARN = 32006
S_SETTINGS_DEBUG = 32007
S_SETTINGS_CAPTURE = 32008
S_SETTINGS_ADVANCED = 32009
S_SETTINGS_BOOT_TRIAL_WAIT = 32010
S_HELP_BOOT_TRIAL_WAIT = 32011
S_SETTINGS_STARTUP_DELAY = 32012
S_HELP_STARTUP_DELAY = 32013

S_MENU_CAPTURE_VSVDB = 32020
S_MENU_CAPTURE_EDID = 32021
S_MENU_ADD_PRESET = 32022
S_MENU_PASTE_HEX = 32023
S_MENU_IMPORT_FILE = 32024
S_MENU_REVERT_REAL = 32025
S_MENU_NO_PROFILES = 32026
S_MENU_LIVE_STATE = 32027
S_LAYER_VSVDB = 32028
S_LAYER_EDID = 32029

S_STATE_INACTIVE = 32030
S_STATE_INACTIVE_UNCERTAIN = 32031
S_STATE_RUNTIME_ONLY = 32032
S_STATE_PERSISTED_EFFECTIVE = 32033
S_STATE_PERSISTED_PENDING = 32034
S_STATE_DRIFTED = 32035
S_STATE_FOREIGN = 32036

S_ACTION_APPLY = 32040
S_ACTION_PERSIST = 32041
S_ACTION_UNPERSIST = 32042
S_ACTION_REVERT = 32043
S_ACTION_EDIT = 32044
S_ACTION_DERIVE = 32045
S_ACTION_RENAME = 32046
S_ACTION_DELETE = 32047
S_ACTION_DETAILS = 32048
S_CONFIRM_DELETE = 32049
S_ASK_NAME = 32050
S_ASK_DERIVED_NAME = 32051
S_ASK_FIELD = 32052
S_ASK_VALUE = 32054
S_ASK_PRIMARIES = 32055
S_PRIMARIES_AS_CAPTURED = 32056
S_SAVE_EDIT = 32057
S_ASK_PRESET = 32058

S_COUNTDOWN_HEADING = 32060
S_COUNTDOWN_MESSAGE = 32061
S_COUNTDOWN_KEEP = 32062
S_COUNTDOWN_REVERT = 32063
S_REVERTED = 32064
S_APPLIED = 32065
S_EDID_APPLY_HEADING = 32066
S_EDID_PERSIST_HEADING = 32067
S_EDID_SECOND_CONFIRM = 32068
S_EDID_RECOVERY = 32069
S_HPD_REFUSED = 32070
S_ADVISE_POWER_CYCLE = 32071
S_CHAIN_OFF_PENDING = 32072
S_PERSISTED = 32073
S_UNPERSISTED = 32074
S_PERSIST_REVERTED = 32075

S_IMPORT_USE_FULL_EDID = 32080
S_IMPORT_EXTRACT_VSVDB = 32081
S_IMPORT_CONFIRM_FULL_EDID = 32082
S_IMPORT_CONFIRM_EXTRACTED = 32083
S_IMPORT_INTENT_HEADING = 32084
S_IMPORT_CHOOSE_FILE = 32085
S_IMPORT_UNRECOGNIZED = 32086
S_IMPORT_NO_VSVDB = 32087

S_DISARMED_HEADING = 32100
S_DISARMED_MESSAGE = 32101
S_REARMED = 32102
S_MODPROBE_HEADING = 32103
S_MODPROBE_MESSAGE = 32104
S_BOOT_TRIAL_HEADING = 32105
S_BOOT_TRIAL_MESSAGE = 32106
S_CONFIRMED = 32107

S_CAPTURE_NO_VSVDB = 32110
S_CAPTURE_NO_RAWEDID = 32111
S_CAPTURE_OVERRIDE_ACTIVE = 32112
S_CAPTURE_REVERT_FIRST = 32113
S_CAPTURE_AS_COPY = 32114
S_INVALID_VSVDB = 32115
S_INVALID_EDID = 32116
S_ASK_HEX = 32117
S_SYSFS_UNAVAILABLE = 32118
S_UNIT_NOT_OURS = 32119
S_UNIT_FAILED = 32120
S_PRESET_4_LABEL = 32121
S_PRESET_4_NOTE = 32122
S_CAPTURED_FROM_SINK = 32123
S_AVR_WARNING = 32124
S_REQUESTED_VS_ACHIEVED = 32125
S_NO_DECODED_FIELDS = 32126
S_DETAILS_HEADING = 32127
S_REVERTED_REAL_EDID = 32128
S_YES = 32129
S_NO = 32130
S_COUNTDOWN_LATER = 32131
S_REARM_UNAVAILABLE = 32132

S_MENU_MODPROBE_DELETE = 32140
S_MODPROBE_DELETE_CONFIRM = 32141
S_MODPROBE_NOT_ONLY_OURS = 32142
S_MODPROBE_DELETED = 32143
S_MODPROBE_DELETE_FAILED = 32144

S_MENU_PROFILES = 32150
S_MENU_ADD_PROFILE = 32151
S_MENU_SETTINGS = 32152

S_PRESET_1_LABEL = 32153
S_PRESET_2_LABEL = 32154
S_PRESET_3_LABEL = 32155

S_CAPTURED_EDID_NAME = 32156
S_CAPTURED_VSVDB_NAME = 32157
S_CAPTURED_VSVDB_FALLBACK = 32158

_ADDON = None

_MIN_COUNTDOWN = 0.05
_COUNTDOWN_JOIN = 1.0
_CLOSE_YESNO = 'Dialog.Close(yesnodialog)'


def _addon():
    global _ADDON

    if _ADDON is None:
        _ADDON = xbmcaddon.Addon()
    return _ADDON


def L(string_id):
    return _addon().getLocalizedString(string_id)


def setting_int(setting_id, fallback):
    try:
        return int(_addon().getSettingInt(setting_id))
    except Exception:
        return fallback


def setting_bool(setting_id, fallback=False):
    try:
        return bool(_addon().getSettingBool(setting_id))
    except Exception:
        return fallback


def setting_string(setting_id, fallback=''):
    try:
        return _addon().getSettingString(setting_id) or fallback
    except Exception:
        return fallback


def open_settings():
    _addon().openSettings()


def notify(message, heading=None):
    xbmcgui.Dialog().notification(heading or L(S_ADDON_NAME), message)


def ok(message, heading=None):
    xbmcgui.Dialog().ok(heading or L(S_ADDON_NAME), message)


def yesno(message, heading=None, yes_label=None, no_label=None):
    return bool(xbmcgui.Dialog().yesno(
        heading or L(S_ADDON_NAME), message,
        nolabel=no_label or L(S_NO), yeslabel=yes_label or L(S_YES)))


def select(heading, labels, preselect=-1):
    return xbmcgui.Dialog().select(heading, labels, preselect=preselect)


def ask_text(heading, default=''):
    text = xbmcgui.Dialog().input(heading, default)
    return text.strip() if text else None


def browse_file(heading):
    path = xbmcgui.Dialog().browse(1, heading, 'files')
    return path or None


def _countdown_timer(seconds, expired, answered):
    if answered.wait(seconds):
        return
    expired.set()
    xbmc.executebuiltin(_CLOSE_YESNO)


def countdown_confirm(heading, message, seconds):
    """The confirm-or-revert dialog from design section 3. Returns 'yes',
    'no' or 'timeout', which are three different outcomes at the boot-trial
    apply point: only 'no' disarms, a timeout asks again next boot.

    yesnocustom rather than yesno because it reports a cancel separately:
    1 yes, 0 no, 2 the custom "decide later" button, -1 cancel or ESC. Only
    the explicit No button is destructive; a cancel reads as a timeout, and so
    does any value a future Kodi adds.

    The countdown is the thread above rather than yesnocustom's autoclose,
    because an autoclose returns 0, the same value the No button returns, and
    so does an external Dialog.Close (both device-verified on the AM9 Pro). No
    return value distinguishes them, so the timer records that it fired before
    it closes the dialog and a 0 is read against that flag.

    A No pressed in the same instant the timer fires therefore reads as a
    timeout. That is the chosen direction: a misread timeout asks again, a
    misread No would revert a profile the user had just kept.
    """
    seconds = max(_MIN_COUNTDOWN, float(seconds))
    expired = threading.Event()
    answered = threading.Event()
    timer = threading.Thread(target=_countdown_timer, args=(seconds, expired, answered))
    timer.daemon = True
    timer.start()
    try:
        answer = xbmcgui.Dialog().yesnocustom(
            heading, message,
            customlabel=L(S_COUNTDOWN_LATER),
            nolabel=L(S_COUNTDOWN_REVERT), yeslabel=L(S_COUNTDOWN_KEEP))
    finally:
        answered.set()
        timer.join(_COUNTDOWN_JOIN)

    if answer == 1:
        return 'yes'
    if answer == 0 and not expired.is_set():
        return 'no'
    return 'timeout'


def import_intent():
    """The full-EDID import question from design section 3. Returns 'edid',
    'vsvdb' or None. The two labels say what each answer does, not how they
    differ, and are quoted from the design.
    """
    choice = xbmcgui.Dialog().select(
        L(S_IMPORT_INTENT_HEADING),
        [L(S_IMPORT_USE_FULL_EDID), L(S_IMPORT_EXTRACT_VSVDB)])
    if choice == 0:
        return 'edid'
    if choice == 1:
        return 'vsvdb'
    return None


def confirm_import(intent, filename):
    ok(L(S_IMPORT_CONFIRM_FULL_EDID if intent == 'edid' else S_IMPORT_CONFIRM_EXTRACTED)
       % filename)


def confirm_edid_second(name, persisting):
    """The second confirmation an EDID profile takes before either apply
    point (design section 3). Not a repeated yes/no: it names the profile,
    says what applying it does, and for a persist ends on the recovery line,
    so the text someone needs when it goes wrong is the last thing they read
    before it does.
    """
    message = L(S_EDID_SECOND_CONFIRM) % name
    if persisting:
        message = message + '\n' + L(S_EDID_RECOVERY)
    return yesno(
        message,
        heading=L(S_EDID_PERSIST_HEADING if persisting else S_EDID_APPLY_HEADING))


def format_value(value):
    """Render a decoded or requested field value for a dialog line. Floats
    lose their trailing zeros so a nits value reads 1037.1, not 1037.1000."""
    if isinstance(value, bool):
        return L(S_YES) if value else L(S_NO)
    if isinstance(value, float):
        text = '%.4f' % value
        text = text.rstrip('0').rstrip('.')
        return text or '0'
    if isinstance(value, dict):
        return ', '.join('%s %s' % (key, format_value(value[key])) for key in sorted(value))
    if value is None:
        return '-'
    return str(value)


def format_requested_vs_achieved(template, requested, achieved):
    """The block quantizes, so the UI never silently substitutes: both
    numbers are shown side by side (design section 2)."""
    return template % (format_value(requested), format_value(achieved))
