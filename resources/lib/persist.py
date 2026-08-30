# Boot persistence: the persisted-profile file, the four-value state file
# (pending/trial/trial-acked/confirmed) and the systemd unit that consumes
# them at boot. See ~/ce22-docs/vsvdb-manager-design.md sections 3 and 8.
# Pure logic plus small-file IO, no Kodi import anywhere, so the whole state
# machine is testable off-device.
#
# resources/bin/edidmgr-boot-apply.sh implements boot_decision() a second
# time in POSIX sh, because the unit runs before kodi.service and no python
# is guaranteed that early. The two are mirrors: neither may change state
# semantics without the other.
#
# Everything this module arms lives in one directory as active.*, including
# the disarmed marker and the temp files of an interrupted write, so the
# documented SSH recovery `rm /storage/.config/edid-manager/active.*` really
# does leave nothing armed behind.

import collections
import os
import subprocess
import tempfile

import store

STATE_PENDING = store.PERSIST_PENDING
STATE_TRIAL = store.PERSIST_TRIAL
STATE_TRIAL_ACKED = store.PERSIST_TRIAL_ACKED
STATE_CONFIRMED = store.PERSIST_CONFIRMED

_DEFAULT_DIR = '/storage/.config/edid-manager'
_DEFAULT_SYSTEM_D = '/storage/.config/system.d'
_DEFAULT_MODPROBE_D = '/storage/.config/modprobe.d'

UNIT_NAME = 'script.edid.manager.service'

_DISARMED_TEXT = ('the previous boot applied this profile and never showed the'
                  ' confirmation dialog\n')

_DEFERRED_TEXT = 'edid-deferred\n'

# apply, the state the unit leaves behind, and whether this boot disarms.
BootAction = collections.namedtuple('BootAction', ['apply', 'next_state', 'disarm'])


def persist_dir():
    return os.environ.get('EDIDMGR_PERSIST_DIR', _DEFAULT_DIR)


def profile_path():
    return os.path.join(persist_dir(), 'active.profile')


def state_path():
    return os.path.join(persist_dir(), 'active.state')


def disarmed_path():
    return os.path.join(persist_dir(), 'active.disarmed')


def deferred_path():
    return os.path.join(persist_dir(), 'active.deferred')


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _read_text(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    except OSError:
        return None


def _write_atomic(path, text):
    directory = os.path.dirname(path) or '.'
    os.makedirs(directory, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(prefix='active.', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        _remove(tmp_path)
        raise


def load_persisted():
    """Read active.profile into {kind: {'kind', 'id', 'raw'}}. Unreadable or
    malformed content reads as nothing persisted rather than raising: every
    caller is either a boot path or a service startup.
    """
    text = _read_text(profile_path())
    if text is None:
        return {}

    fields = {}
    for line in text.splitlines():
        key, sep, value = line.partition('=')
        if sep:
            fields[key.strip()] = value.strip()

    persisted = {}
    for kind in (store.KIND_VSVDB, store.KIND_EDID):
        raw = fields.get('%s_raw' % kind)
        if not raw:
            continue
        persisted[kind] = {
            'kind': kind,
            'id': fields.get('%s_id' % kind) or None,
            'raw': raw.lower(),
        }
    return persisted


def persisted_profile(kind):
    return load_persisted().get(kind)


def _dump_persisted(persisted):
    lines = []
    for kind in (store.KIND_EDID, store.KIND_VSVDB):
        entry = persisted.get(kind)
        if not entry:
            continue
        lines.append('%s_id=%s' % (kind, entry.get('id') or ''))
        lines.append('%s_raw=%s' % (kind, entry['raw']))
    return ''.join(line + '\n' for line in lines)


def persist_profile(profile):
    """Arm profile for the next boot: write its bytes into active.profile,
    replacing whatever was armed for the same layer, and set state=pending.
    """
    kind = profile.get('kind')
    if kind not in store.KINDS:
        raise ValueError('unknown profile kind %r' % (kind,))

    persisted = load_persisted()
    persisted[kind] = {
        'kind': kind,
        'id': profile.get('id'),
        'raw': profile['raw'].lower(),
    }
    _write_atomic(profile_path(), _dump_persisted(persisted))
    _remove(disarmed_path())
    set_state(STATE_PENDING)
    return persisted[kind]


def unpersist(kind=None):
    """Disarm one layer, or everything when kind is None. Clearing the last
    armed layer has the same effect as the manual `rm active.*` recovery.
    """
    persisted = load_persisted()
    if kind is None:
        persisted = {}
    else:
        persisted.pop(kind, None)

    if persisted:
        _write_atomic(profile_path(), _dump_persisted(persisted))
        return persisted

    _remove(profile_path())
    _remove(state_path())
    _remove(disarmed_path())
    _remove(deferred_path())
    return {}


def current_state():
    """The state file's contents, or None when the file is absent. An
    unrecognized value is returned as-is, including the empty string an
    empty or whitespace-only file gives: the sh mirror disarms on that
    rather than treating it as nothing armed, and boot_decision has to agree.
    """
    text = _read_text(state_path())
    if text is None:
        return None
    return text.strip()


def set_state(value):
    if value not in store.PERSIST_STATES:
        raise ValueError('unknown persistence state %r' % (value,))
    _write_atomic(state_path(), value + '\n')


def boot_decision(state):
    """What the unit does with the state it finds, design section 3.

    An unrecognized value, the empty string included, takes the plain-trial
    arm for the same reason plain trial does: nothing proves the last boot
    got as far as asking.

    Two orderings the sh mirror has to hold that this return value cannot
    express, both recorded here because the two files are one contract:

    On the pending arm the state is rewritten to trial *before* the apply,
    never after. A crash between the two would otherwise leave pending
    behind and re-apply a profile that already wedged one boot.

    A profile with no EDID layer only writes the two module params, and
    force_vsvdb is consumed inside the EDID parse (design finding 3), so
    with hpd_state at 1 the mirror follows the params with the sentinel
    `load 0000000000000000` to force a DDC re-read and reach dv_cap before
    the service's trial check times out. At hpd_state 0 it writes no
    sentinel: the re-parse would take the invalid-EDID early return, and the
    next real hotplug parses the params anyway.

    An EDID layer is written at hpd_state 1 only. At 0 the mirror skips the
    load and leaves `active.deferred` behind instead, because a forced EDID
    at boot with HPD low wedges Kodi: the vendor mode chooser never looks at
    HPD, so a valid VIC out of the block drives real TMDS into a disabled PHY
    and no flip completes. The service applies that layer once Kodi is up,
    where the same write raises no DRM hotplug event. The params still go in
    either way, and none of the four states or transitions change with it.
    """
    if state == STATE_CONFIRMED:
        return BootAction(True, STATE_CONFIRMED, False)
    if state == STATE_PENDING:
        return BootAction(True, STATE_TRIAL, False)
    if state == STATE_TRIAL_ACKED:
        return BootAction(True, STATE_TRIAL_ACKED, False)
    if state is None:
        return BootAction(False, None, False)
    return BootAction(False, None, True)


def dialog_shown():
    """trial -> trial-acked. The service calls this the instant the dialog
    opens and before it waits for an answer, so a service that dies mid
    dialog still leaves the next boot able to tell "dialog shown, unanswered"
    from "dialog never reached".
    """
    if current_state() != STATE_TRIAL:
        return False
    set_state(STATE_TRIAL_ACKED)
    return True


def confirm():
    set_state(STATE_CONFIRMED)


def disarm():
    """The unit's plain-trial arm: stop applying, and leave a marker so the
    next healthy boot can say why the profile stopped coming back. Keeps
    active.profile so re-arming is one touch.
    """
    _remove(state_path())
    _write_atomic(disarmed_path(), _DISARMED_TEXT)


def disarmed():
    return os.path.exists(disarmed_path())


def clear_disarmed():
    _remove(disarmed_path())


def deferred():
    """Whether the EDID layer of a persisted apply is still owed. The unit
    writes the marker when it skips that layer at boot, the service writes it
    when it pulls the layer back out before a restart, and only the service
    clears it, right after applying the layer at runtime.
    """
    return os.path.exists(deferred_path())


def defer_edid():
    _write_atomic(deferred_path(), _DEFERRED_TEXT)


def clear_deferred():
    _remove(deferred_path())


def rearm():
    """Answer to the disarmed notification. False when there is no longer a
    profile to re-arm, in which case the stale marker goes away too.
    """
    if not load_persisted():
        clear_disarmed()
        return False
    set_state(STATE_PENDING)
    clear_disarmed()
    return True


def addon_dir():
    return os.environ.get(
        'EDIDMGR_ADDON_DIR',
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def system_d_dir():
    return os.environ.get('EDIDMGR_SYSTEM_D', _DEFAULT_SYSTEM_D)


def unit_source_path():
    return os.path.join(addon_dir(), 'system.d', UNIT_NAME)


def unit_link_path():
    return os.path.join(system_d_dir(), UNIT_NAME)


def unit_is_ours():
    """systemctl enable creates the system.d entry as a symlink back into the
    addon's own directory (design finding 2), so ownership is a symlink
    check. A plain file, or a symlink pointing anywhere else, was written by
    someone else and is never touched.
    """
    link = unit_link_path()
    if not os.path.lexists(link):
        return True
    if not os.path.islink(link):
        return False
    return os.path.realpath(link) == os.path.realpath(unit_source_path())


def unit_enabled():
    return os.path.lexists(unit_link_path()) and unit_is_ours()


def _systemctl(*args):
    binary = os.environ.get('EDIDMGR_SYSTEMCTL', 'systemctl')
    return subprocess.call([binary] + list(args))


def enable_unit():
    """Install the boot unit the way the docker addon does: enable the file
    shipped inside the addon, by absolute path, and let systemctl create the
    symlink in system.d. Returns (ok, detail).
    """
    if not unit_is_ours():
        return False, unit_link_path()

    source = unit_source_path()
    if not os.path.exists(source):
        return False, source

    try:
        rc = _systemctl('enable', source)
    except OSError as error:
        return False, str(error)
    return rc == 0, 'systemctl enable returned %d' % rc


def disable_unit():
    if not unit_is_ours():
        return False, unit_link_path()

    try:
        rc = _systemctl('disable', UNIT_NAME)
    except OSError as error:
        return False, str(error)
    return rc == 0, 'systemctl disable returned %d' % rc


def modprobe_dir():
    return os.environ.get('EDIDMGR_MODPROBE_D', _DEFAULT_MODPROBE_D)


def _is_aml_media_options(line):
    fields = line.split('#', 1)[0].split()
    return len(fields) >= 2 and fields[0] == 'options' and fields[1] == 'aml_media'


def modprobe_leftovers():
    """Conf files carrying an `options aml_media ...` line. They are inert on
    CE22, since aml_media is built into vmlinux and no modprobe ever runs for
    it (design finding 1), but the public gist told people to write one. This
    only ever reports them; deletion is delete_modprobe_leftover, which the
    manager UI drives behind an explicit confirmation.
    """
    try:
        names = sorted(os.listdir(modprobe_dir()))
    except OSError:
        return []

    found = []
    for name in names:
        if not name.endswith('.conf'):
            continue
        path = os.path.join(modprobe_dir(), name)
        text = _read_text(path)
        if text is None:
            continue
        if any(_is_aml_media_options(line) for line in text.splitlines()):
            found.append(path)
    return found


def modprobe_leftover_lines(path):
    """The file's own directive lines, so the UI can show what it is about to
    delete. None when the file holds a directive that is not an aml_media
    options line, which is the refusal: whatever else it configures is not
    ours to take away.
    """
    text = _read_text(path)
    if text is None:
        return None

    lines = []
    for line in text.splitlines():
        if not line.split('#', 1)[0].strip():
            continue
        if not _is_aml_media_options(line):
            return None
        lines.append(line.rstrip())
    return lines or None


def delete_modprobe_leftover(path):
    """Delete one leftover, but only when every directive in it is an
    aml_media options line. Returns (ok, detail).
    """
    if modprobe_leftover_lines(path) is None:
        return False, path

    try:
        os.remove(path)
    except OSError as error:
        return False, str(error)
    return True, path
