# Startup reconciliation and the boot-trial half of safe mode
# (~/ce22-docs/vsvdb-manager-design.md sections 3 and 8). The runtime-apply
# countdown is not here: default.py shows that one inline, because the script
# is already the thing running at that moment and there is no boot state to
# reconcile.
#
# Everything below runs once at Kodi start and the service then exits;
# monitor.waitForAbort is used for every wait so a shutdown during the trial
# dialog's polling does not hold Kodi up.

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', 'lib'))

import backend  # noqa: E402
import persist  # noqa: E402
import runtime  # noqa: E402
import state  # noqa: E402
import store  # noqa: E402
import ui  # noqa: E402

import xbmc  # noqa: E402

_LOG_PREFIX = 'script.edid.manager: '

# the GUI has to be up before a dialog is worth showing, and dv_cap is read
# through the same sysfs the boot unit wrote before Kodi started
_STARTUP_DELAY = 5.0

# a persisted profile is normally already effective at startup, but an EDID
# applied with the chain off only reaches dv_cap at the hotplug parse
_TRIAL_POLL = 2.0
_TRIAL_WAIT = 120.0


def _log(message, level=xbmc.LOGINFO):
    xbmc.log(_LOG_PREFIX + message, level)


def _debug(message):
    if ui.setting_bool('debug_logging'):
        _log(message, xbmc.LOGDEBUG)


def _disarmed_layers(persisted):
    """Layers a boot disarm left the marker for. active.profile is retained
    across a disarm, so feeding it into classify would score params-at-
    defaults against a stale persisted raw and land on drifted, technically
    correct per the classifier but misleading right next to the disarmed
    notice the user just saw.
    """
    if not persist.disarmed():
        return set()
    return set(persisted)


def _classify():
    profiles = store.list_profiles()
    persisted = persist.load_persisted()
    disarmed_layers = _disarmed_layers(persisted)

    force_vsvdb = 0
    vsvdb_data = b''
    rawedid = b''
    try:
        force_vsvdb = backend.read_force_vsvdb()
        vsvdb_data = backend.read_vsvdb_data()
        rawedid = backend.read_rawedid()
    except (OSError, ValueError) as error:
        _log('sysfs read failed: %s' % error, xbmc.LOGWARNING)

    dv_cap = backend.read_effective_vsvdb_hex() or ''

    vsvdb = state.classify_vsvdb(
        force_vsvdb, vsvdb_data, dv_cap,
        [p for p in profiles if p.get('kind') == store.KIND_VSVDB],
        None if store.KIND_VSVDB in disarmed_layers else persisted.get(store.KIND_VSVDB))
    edid_state = state.classify_edid(
        rawedid,
        [p for p in profiles if p.get('kind') == store.KIND_EDID],
        None if store.KIND_EDID in disarmed_layers else persisted.get(store.KIND_EDID))
    return vsvdb, edid_state, disarmed_layers


def _reconcile():
    vsvdb, edid_state, disarmed_layers = _classify()
    vsvdb_text = ('vsvdb disarmed at boot (profile retained)'
                  if store.KIND_VSVDB in disarmed_layers else 'vsvdb %s' % vsvdb.state)
    edid_text = ('edid disarmed at boot (profile retained)' if store.KIND_EDID in disarmed_layers
                 else 'edid %s%s' % (
                     edid_state.state, ' (uncertain)' if edid_state.uncertain else ''))
    _log('startup state: %s, %s' % (vsvdb_text, edid_text))


def _warn_modprobe_leftovers():
    """A toast, not a modal: this is a leftover from the gist's wrong advice,
    not something that needs an answer before Kodi is usable. Deleting one is
    the manager UI's job, where the file's content can be shown first.
    """
    leftovers = persist.modprobe_leftovers()
    if not leftovers:
        return

    _log('inert modprobe.d leftovers: %s' % ', '.join(leftovers), xbmc.LOGWARNING)
    ui.notify(ui.L(ui.S_MODPROBE_MESSAGE) % ', '.join(leftovers),
              heading=ui.L(ui.S_MODPROBE_HEADING))


def _disarmed_notice():
    """The unit disarmed a profile because the previous boot never showed the
    dialog, which is the wedged-Kodi case safe mode exists for. Say so
    loudly and offer the one-touch re-arm rather than leaving the user to
    notice the display reverted on its own.
    """
    if not persist.disarmed():
        return

    _log('persisted profile was disarmed at boot', xbmc.LOGWARNING)
    if ui.yesno(ui.L(ui.S_DISARMED_MESSAGE), heading=ui.L(ui.S_DISARMED_HEADING)):
        if persist.rearm():
            ui.notify(ui.L(ui.S_REARMED))
            return
        ui.notify(ui.L(ui.S_REARM_UNAVAILABLE))
    persist.clear_disarmed()


def _effective(persisted):
    """Whether every persisted layer is what the box is actually showing:
    dv_cap for the VSVDB layer, rawedid for the EDID layer, compared whole so
    a profile that is a prefix of rawedid never counts as a match.
    """
    vsvdb = persisted.get(store.KIND_VSVDB)
    edid_entry = persisted.get(store.KIND_EDID)

    if vsvdb and (backend.read_effective_vsvdb_hex() or '').lower() != vsvdb['raw']:
        return False
    if edid_entry:
        try:
            rawedid = backend.read_rawedid()
        except OSError:
            return False
        if rawedid.hex() != edid_entry['raw']:
            return False
    return True


def _wait_effective(monitor, persisted):
    waited = 0.0
    while waited < _TRIAL_WAIT:
        try:
            if _effective(persisted):
                return True
        except (OSError, ValueError) as error:
            _log('trial check failed: %s' % error, xbmc.LOGWARNING)
            return False
        _debug('persisted profile not effective yet after %.0fs' % waited)
        if monitor.waitForAbort(_TRIAL_POLL):
            return False
        waited += _TRIAL_POLL
    return False


def _revert_persisted():
    """A declined trial disarms, the same effect as the documented `rm
    active.*` recovery. The unit stays enabled: removing it is the heavier,
    explicit step, and an enabled unit with nothing armed exits immediately.
    """
    errors = runtime.revert_all()
    if errors:
        _log('revert after a declined trial failed: %s' % '; '.join(errors), xbmc.LOGERROR)
    persist.unpersist()


def _boot_trial(monitor):
    current = persist.current_state()
    if current not in (persist.STATE_TRIAL, persist.STATE_TRIAL_ACKED):
        return

    persisted = persist.load_persisted()
    if not persisted:
        return

    if not _wait_effective(monitor, persisted):
        _log('persisted profile is not effective, leaving state %s' % current,
             xbmc.LOGWARNING)
        return

    # written before the dialog waits for an answer: a service that dies mid
    # dialog still leaves the next boot able to tell "dialog shown,
    # unanswered" from "dialog never reached" (design section 3)
    persist.dialog_shown()

    seconds = ui.setting_int('safe_mode_timeout', 30)
    answer = ui.countdown_confirm(
        ui.L(ui.S_BOOT_TRIAL_HEADING),
        ui.L(ui.S_BOOT_TRIAL_MESSAGE) % seconds,
        seconds)

    if answer == 'yes':
        persist.confirm()
        ui.notify(ui.L(ui.S_CONFIRMED))
        _log('boot trial confirmed')
        return

    if answer == 'no':
        _revert_persisted()
        ui.notify(ui.L(ui.S_REVERTED))
        _log('boot trial declined, profile reverted and disarmed')
        return

    # timeout: the profile stays applied for this boot and the next boot asks
    # again. An unattended boot must never destroy a working setup just
    # because nobody was in the room.
    _log('boot trial dialog timed out, staying %s' % persist.STATE_TRIAL_ACKED)


def run():
    monitor = xbmc.Monitor()
    if monitor.waitForAbort(_STARTUP_DELAY):
        return

    try:
        _reconcile()
        _disarmed_notice()
        _boot_trial(monitor)
        _warn_modprobe_leftovers()
    except Exception as error:
        _log('startup failed: %s' % error, xbmc.LOGERROR)


if __name__ == '__main__':
    run()
