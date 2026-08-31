# The manager UI: stock dialogs only, no WindowXML and no skin files
# (~/ce22-docs/vsvdb-manager-design.md sections 1 and 8). Everything that
# touches sysfs goes through runtime.py, everything that touches the store
# goes through store.py, and the dialogs themselves live in ui.py, so the
# flows below read as flows.

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', 'lib'))

import backend  # noqa: E402
import edid  # noqa: E402
import encode  # noqa: E402
import persist  # noqa: E402
import runtime  # noqa: E402
import state  # noqa: E402
import store  # noqa: E402
import ui  # noqa: E402

import xbmc  # noqa: E402

_LOG_PREFIX = 'script.edid.manager: '


def _log(message, level=xbmc.LOGINFO):
    xbmc.log(_LOG_PREFIX + message, level)


_STATE_STRINGS = {
    state.FOREIGN: ui.S_STATE_FOREIGN,
    state.DRIFTED: ui.S_STATE_DRIFTED,
    state.PERSISTED_EFFECTIVE: ui.S_STATE_PERSISTED_EFFECTIVE,
    state.PERSISTED_PENDING: ui.S_STATE_PERSISTED_PENDING,
    state.RUNTIME_ONLY: ui.S_STATE_RUNTIME_ONLY,
    state.INACTIVE: ui.S_STATE_INACTIVE,
}

_BOOL_FIELDS = frozenset({
    'yuv422_12bit', 'sup_2160p60', 'global_dimming', 'backlight_control',
    'colorimetry_p3d65', 'low_latency',
})

# codes rather than nits: the setter takes the 0-3 code and the decoder
# reports the 25/50/75/100 nits it stands for
_BACKLIGHT_MIN_LUMA = ((25, 0), (50, 1), (75, 2), (100, 3))

_CODE_FIELDS = {'interface_bits': (0, 3), 'sup_10b_12b_444': (0, 3)}

_PRIMARIES = (
    ('BT.709', {'rx': 0.640, 'ry': 0.330, 'gx': 0.300, 'gy': 0.600,
                'bx': 0.150, 'by': 0.060}),
    ('DCI-P3 D65', {'rx': 0.680, 'ry': 0.320, 'gx': 0.265, 'gy': 0.690,
                    'bx': 0.150, 'by': 0.060}),
    ('BT.2020', {'rx': 0.708, 'ry': 0.292, 'gx': 0.170, 'gy': 0.797,
                 'bx': 0.131, 'by': 0.046}),
)

# the only editable field whose decoded key is not its own name
_DECODED_KEYS = {'backlight_min_luma': 'backlight_min_luma_nits'}

# matches settings.xml's declared range; a userdata edit can still put any
# value in the setting, so the same range is enforced again here
_SAFE_MODE_TIMEOUT_FLOOR = 5
_SAFE_MODE_TIMEOUT_CEILING = 90


def _clamp(value, floor, ceiling):
    return max(floor, min(ceiling, value))


def _safe_mode_timeout():
    return _clamp(ui.setting_int('safe_mode_timeout', 30),
                  _SAFE_MODE_TIMEOUT_FLOOR, _SAFE_MODE_TIMEOUT_CEILING)


def _read_live():
    profiles = store.list_profiles()
    persisted = persist.load_persisted()

    force_vsvdb = 0
    vsvdb_data = b''
    rawedid = b''
    error = None
    try:
        force_vsvdb = backend.read_force_vsvdb()
        vsvdb_data = backend.read_vsvdb_data()
        rawedid = backend.read_rawedid()
    except (OSError, ValueError) as read_error:
        error = str(read_error)

    dv_cap = backend.read_effective_vsvdb_hex() or ''

    return {
        'profiles': profiles,
        'persisted': persisted,
        'force_vsvdb': force_vsvdb,
        'dv_cap': dv_cap,
        'rawedid': rawedid,
        'error': error,
        'vsvdb': state.classify_vsvdb(
            force_vsvdb, vsvdb_data, dv_cap,
            [p for p in profiles if p.get('kind') == store.KIND_VSVDB],
            persisted.get(store.KIND_VSVDB)),
        'edid': state.classify_edid(
            rawedid,
            [p for p in profiles if p.get('kind') == store.KIND_EDID],
            persisted.get(store.KIND_EDID)),
    }


def _state_label(classification):
    if classification.state == state.INACTIVE and classification.uncertain:
        return ui.L(ui.S_STATE_INACTIVE_UNCERTAIN)
    return ui.L(_STATE_STRINGS[classification.state])


def _forced_edid_hex(live):
    """The bytes a VSVDB re-parse has to re-load instead of the sentinel, and
    only that. It is a forced EDID only on the one evidence that proves it:
    a persisted EDID profile whose raw is what rawedid reads back.

    A plain stored profile matching rawedid is not evidence. Capturing the
    sink's own EDID produces exactly that, and forced_edid is not readable
    (design finding 8), so treating a match as proof would turn every later
    VSVDB apply and revert into a full EDID load that never asked the user.

    The cost is that a runtime-applied, unpersisted EDID profile does not
    survive a VSVDB apply on top of it: the re-parse sentinels back to the
    DDC read. That is the safe direction of the trade, and layering the two
    is what persisting the EDID profile is for (design section 6 step 16).
    """
    rawedid_hex = live['rawedid'].hex() if live['rawedid'] else ''
    if not rawedid_hex:
        return None

    persisted = live['persisted'].get(store.KIND_EDID)
    if persisted and persisted['raw'] == rawedid_hex:
        return rawedid_hex
    return None


def _edid_override_suspected(live):
    """The capture gate from design section 5, which is deliberately wider
    than _forced_edid_hex: capturing a copy of an override is a wasted
    profile, so any stored EDID profile matching rawedid is worth asking
    about even though it may equally be the sink's own EDID.
    """
    rawedid_hex = live['rawedid'].hex() if live['rawedid'] else ''
    if not rawedid_hex:
        return False
    return any(profile.get('kind') == store.KIND_EDID and profile.get('raw') == rawedid_hex
               for profile in live['profiles'])


def _is_hdr10plus_preset(profile):
    return (profile.get('kind') == store.KIND_VSVDB and
            profile.get('raw') == store.PRESET_HEX[3])


def _profile_state(profile, live):
    for classification in (live['vsvdb'], live['edid']):
        if classification.profile_id == profile.get('id'):
            return _state_label(classification)
    return None


def _profile_label(profile, live):
    parts = [profile.get('name') or '', profile.get('kind') or '']
    if _is_hdr10plus_preset(profile):
        parts.append(ui.L(ui.S_PRESET_4_LABEL))
    current = _profile_state(profile, live)
    if current:
        parts.append(current)
    return ' - '.join(part for part in parts if part)


def _profile_details(profile):
    lines = [
        '%s (%s, %s)' % (profile.get('name'), profile.get('kind'), profile.get('source')),
    ]
    if profile.get('origin_file'):
        lines.append(profile['origin_file'])
        if profile.get('extracted_from_edid'):
            lines.append(ui.L(ui.S_IMPORT_EXTRACT_VSVDB))
    if _is_hdr10plus_preset(profile):
        lines.append(ui.L(ui.S_PRESET_4_NOTE))

    decoded = profile.get('decoded')
    if isinstance(decoded, dict):
        for key in sorted(decoded):
            lines.append('%s: %s' % (key, ui.format_value(decoded[key])))
    else:
        lines.append(ui.L(ui.S_NO_DECODED_FIELDS))

    lines.append(profile.get('raw') or '')
    ui.ok('\n'.join(lines), heading=ui.L(ui.S_DETAILS_HEADING))


def _ask_name(default=''):
    return ui.ask_text(ui.L(ui.S_ASK_NAME), default)


def _capture_vsvdb(live):
    if live['force_vsvdb'] != 0:
        choice = ui.select(
            ui.L(ui.S_CAPTURE_OVERRIDE_ACTIVE),
            [ui.L(ui.S_CAPTURE_REVERT_FIRST), ui.L(ui.S_CAPTURE_AS_COPY)])
        if choice < 0:
            return
        if choice == 0:
            try:
                runtime.revert_vsvdb(_forced_edid_hex(live))
            except Exception as error:
                ui.ok(ui.L(ui.S_SYSFS_UNAVAILABLE) % error)
                return
            live = _read_live()

    raw = live['dv_cap']
    if not raw:
        ui.ok(ui.L(ui.S_CAPTURE_NO_VSVDB))
        return

    try:
        raw = encode.load_block(raw)
    except encode.VsvdbEncodeError as error:
        ui.ok(ui.L(ui.S_INVALID_VSVDB) % error)
        return

    if ui.setting_bool('warn_on_avr_capture', True):
        ui.ok(ui.L(ui.S_AVR_WARNING))

    name = _ask_name(ui.L(ui.S_CAPTURED_FROM_SINK))
    if name:
        store.add_profile(store.KIND_VSVDB, name, raw, store.SOURCE_CAPTURED)
        _log('capture stored: %r (%s, %d bytes)' % (name, store.KIND_VSVDB, len(raw) // 2))


def _capture_edid(live):
    if _edid_override_suspected(live):
        choice = ui.select(
            ui.L(ui.S_CAPTURE_OVERRIDE_ACTIVE),
            [ui.L(ui.S_CAPTURE_REVERT_FIRST), ui.L(ui.S_CAPTURE_AS_COPY)])
        if choice < 0:
            return
        if choice == 0:
            try:
                runtime.revert_edid()
            except Exception as error:
                ui.ok(ui.L(ui.S_SYSFS_UNAVAILABLE) % error)
                return
            live = _read_live()

    data = live['rawedid']
    if not data:
        ui.ok(ui.L(ui.S_CAPTURE_NO_RAWEDID))
        return

    try:
        edid.validate_edid(data)
    except edid.EdidValidationError as error:
        ui.ok(ui.L(ui.S_INVALID_EDID) % error)
        return

    name = _ask_name(ui.L(ui.S_CAPTURED_FROM_SINK))
    if name:
        store.add_profile(store.KIND_EDID, name, data.hex(), store.SOURCE_CAPTURED)
        _log('capture stored: %r (%s, %d bytes)' % (name, store.KIND_EDID, len(data)))


def _preset_label(index):
    if index == 4:
        return ui.L(ui.S_PRESET_4_LABEL)
    return ui.L(ui.S_PRESET_N) % index


def _add_preset():
    labels = [_preset_label(index + 1) for index in range(len(store.PRESET_HEX))]
    choice = ui.select(ui.L(ui.S_ASK_PRESET), labels)
    if choice < 0:
        return

    if choice == 3:
        ui.ok(ui.L(ui.S_PRESET_4_NOTE))

    name = _ask_name(labels[choice])
    if name:
        store.add_profile(store.KIND_VSVDB, name, store.PRESET_HEX[choice],
                          store.SOURCE_PRESET, preset_index=choice + 1)


def _add(kind, raw, name_default, source, **kwargs):
    name = _ask_name(name_default)
    if not name:
        return
    store.add_profile(kind, name, raw, source, **kwargs)
    if source in (store.SOURCE_IMPORTED, store.SOURCE_PASTED):
        _log('import stored: intent %s, origin %s' % (kind, kwargs.get('origin_file') or 'pasted'))


def _ingest(data, origin_file=None):
    """Both ways bytes arrive that are not a capture: pasted hex and an
    imported file. A full EDID always asks what it is for and never guesses
    (design section 3).
    """
    source = store.SOURCE_IMPORTED if origin_file else store.SOURCE_PASTED
    extra = {'origin_file': origin_file} if origin_file else {}
    label = origin_file or ui.L(ui.S_MENU_PASTE_HEX)

    if len(data) >= 128 and len(data) % 128 == 0:
        try:
            edid.validate_edid(data)
        except edid.EdidValidationError as error:
            ui.ok(ui.L(ui.S_INVALID_EDID) % error)
            return

        intent = ui.import_intent()
        if intent is None:
            return

        if intent == 'edid':
            ui.confirm_import('edid', label)
            _add(store.KIND_EDID, data.hex(), label, source, **extra)
            return

        block = edid.find_vsvdb(data)
        if block is None:
            ui.ok(ui.L(ui.S_IMPORT_NO_VSVDB))
            return
        ui.confirm_import('vsvdb', label)
        if origin_file:
            extra['extracted_from_edid'] = True
        _add(store.KIND_VSVDB, block.hex(), label, source, **extra)
        return

    try:
        raw = encode.load_block(data.hex())
    except encode.VsvdbEncodeError as error:
        ui.ok(ui.L(ui.S_INVALID_VSVDB) % error)
        return

    if origin_file:
        extra['extracted_from_edid'] = False
    _add(store.KIND_VSVDB, raw, label, source, **extra)


def _paste_hex():
    text = ui.ask_text(ui.L(ui.S_ASK_HEX))
    if not text:
        return

    data = edid.normalize_input(text)
    if data is None:
        ui.ok(ui.L(ui.S_IMPORT_UNRECOGNIZED) % text)
        return
    _ingest(data)


def _file_bytes(blob):
    # a dump is either raw binary or hex text; hex text decodes as ascii and
    # normalize_input refuses anything else, which is the discriminator
    try:
        text = blob.decode('ascii')
    except UnicodeDecodeError:
        return bytes(blob)

    data = edid.normalize_input(text)
    return data if data is not None else bytes(blob)


def _import_file():
    path = ui.browse_file(ui.L(ui.S_IMPORT_CHOOSE_FILE))
    if not path:
        return

    try:
        with open(path, 'rb') as handle:
            blob = handle.read()
    except OSError as error:
        ui.ok(ui.L(ui.S_IMPORT_UNRECOGNIZED) % error)
        return

    _ingest(_file_bytes(blob), os.path.basename(path))


def _field_label(profile, field):
    decoded = profile.get('decoded')
    if not isinstance(decoded, dict):
        return field
    value = decoded.get(_DECODED_KEYS.get(field, field))
    return '%s: %s' % (field, ui.format_value(value))


def _ask_field_value(profile, field):
    if field in _BOOL_FIELDS:
        return ui.yesno(field)

    if field == 'primaries':
        labels = [name for name, _ in _PRIMARIES] + [ui.L(ui.S_PRIMARIES_AS_CAPTURED)]
        choice = ui.select(ui.L(ui.S_ASK_PRIMARIES), labels)
        if choice < 0 or choice >= len(_PRIMARIES):
            return None
        return _PRIMARIES[choice][1]

    if field == 'backlight_min_luma':
        choice = ui.select(ui.L(ui.S_ASK_VALUE),
                           ['%d' % nits for nits, _ in _BACKLIGHT_MIN_LUMA])
        if choice < 0:
            return None
        return _BACKLIGHT_MIN_LUMA[choice][1]

    text = ui.ask_text(ui.L(ui.S_ASK_VALUE))
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        ui.ok(ui.L(ui.S_IMPORT_UNRECOGNIZED) % text)
        return None

    if field in _CODE_FIELDS or field.startswith('dm_version'):
        return int(value)
    return value


def _edit_fields(profile):
    while True:
        try:
            fields = sorted(encode.editable_fields(profile['raw']))
        except encode.VsvdbEncodeError as error:
            ui.ok(ui.L(ui.S_INVALID_VSVDB) % error)
            return

        choice = ui.select(ui.L(ui.S_ASK_FIELD),
                           [_field_label(profile, field) for field in fields])
        if choice < 0:
            return

        field = fields[choice]
        value = _ask_field_value(profile, field)
        if value is None:
            continue

        try:
            patched, achieved = encode.patch_fields(profile['raw'], {field: value})
        except encode.VsvdbEncodeError as error:
            ui.ok(ui.L(ui.S_INVALID_VSVDB) % error)
            continue

        line = ui.format_requested_vs_achieved(
            ui.L(ui.S_REQUESTED_VS_ACHIEVED), value, achieved[field])
        if not ui.yesno(line, heading=ui.L(ui.S_SAVE_EDIT)):
            continue

        store.update_profile(profile['id'], raw=patched)
        profile = store.get_profile(profile['id'])


def _derive(profile):
    name = ui.ask_text(ui.L(ui.S_ASK_DERIVED_NAME), profile.get('name') or '')
    if not name:
        return

    copy = store.add_profile(store.KIND_VSVDB, name, profile['raw'],
                             store.SOURCE_DERIVED, parent_id=profile['id'])
    _edit_fields(copy)


def _rename(profile):
    name = ui.ask_text(ui.L(ui.S_ASK_NAME), profile.get('name') or '')
    if name:
        store.update_profile(profile['id'], name=name)


def _delete(profile):
    if ui.yesno(ui.L(ui.S_CONFIRM_DELETE) % profile.get('name')):
        store.delete_profile(profile['id'])


def _countdown():
    seconds = _safe_mode_timeout()
    outcome = ui.countdown_confirm(
        ui.L(ui.S_COUNTDOWN_HEADING),
        ui.L(ui.S_COUNTDOWN_MESSAGE) % seconds,
        seconds)
    _log('countdown outcome: %s' % outcome)
    return outcome


def _advise(kind):
    if runtime.hpd_state() == 0:
        ui.notify(ui.L(ui.S_CHAIN_OFF_PENDING))
    elif kind == store.KIND_EDID:
        ui.notify(ui.L(ui.S_ADVISE_POWER_CYCLE))


def _apply_layer(live, profile):
    """Apply one profile at runtime. Returns the revert callable on success,
    None when nothing was applied.
    """
    forced_edid = _forced_edid_hex(live)

    if profile['kind'] == store.KIND_EDID:
        try:
            runtime.apply_edid(profile['raw'])
        except Exception as error:
            ui.ok(ui.L(ui.S_INVALID_EDID) % error)
            return None
        _log('applied at runtime: %r (%s)' % (profile.get('name'), profile['kind']))
        return runtime.revert_edid

    try:
        runtime.apply_vsvdb(profile['raw'], forced_edid)
    except runtime.ChainOffError:
        ui.ok(ui.L(ui.S_HPD_REFUSED))
        return None
    except Exception as error:
        ui.ok(ui.L(ui.S_INVALID_VSVDB) % error)
        return None

    _log('applied at runtime: %r (%s)' % (profile.get('name'), profile['kind']))
    return lambda: runtime.revert_vsvdb(forced_edid)


def _apply_runtime(live, profile):
    if profile['kind'] == store.KIND_EDID and \
            not ui.confirm_edid_second(profile.get('name'), False):
        return

    revert = _apply_layer(live, profile)
    if revert is None:
        return

    outcome = _countdown()
    if outcome == 'yes':
        ui.notify(ui.L(ui.S_APPLIED))
        _advise(profile['kind'])
        return

    try:
        revert()
    except Exception as error:
        ui.ok(ui.L(ui.S_SYSFS_UNAVAILABLE) % error)
        return
    _log('profile reverted: %r (%s) trigger=countdown-%s' % (
        profile.get('name'), profile['kind'], outcome))
    ui.notify(ui.L(ui.S_REVERTED))


def _persist(live, profile):
    if profile['kind'] == store.KIND_EDID and \
            not ui.confirm_edid_second(profile.get('name'), True):
        return

    enabled, detail = persist.enable_unit()
    if not enabled:
        ui.ok(ui.L(ui.S_UNIT_NOT_OURS if not persist.unit_is_ours() else ui.S_UNIT_FAILED)
              % detail)
        return

    persist.persist_profile(profile)
    _log('profile persisted: %r (%s)' % (profile.get('name'), profile['kind']))

    # persisting also applies now, which is what makes persisted-pending a
    # state that can actually occur rather than a hole in the model (design
    # section 4). With the chain off the params still go in, but without the
    # re-parse that would flatten dv_cap (design section 5): the profile sits
    # pending until the next boot or hotplug parse picks the params up.
    if profile['kind'] == store.KIND_VSVDB and runtime.hpd_state() == 0:
        try:
            runtime.write_vsvdb_params(profile['raw'])
        except Exception as error:
            ui.ok(ui.L(ui.S_SYSFS_UNAVAILABLE) % error)
            return
        ui.ok(ui.L(ui.S_CHAIN_OFF_PENDING))
        ui.notify(ui.L(ui.S_PERSISTED))
        return

    revert = _apply_layer(live, profile)
    if revert is None:
        ui.notify(ui.L(ui.S_PERSISTED))
        return

    outcome = _countdown()
    if outcome == 'yes':
        ui.notify(ui.L(ui.S_PERSISTED))
        _advise(profile['kind'])
        return

    # a declined countdown has to disarm the boot too: reverting now and
    # leaving the same profile to come back at the next boot is not a revert
    try:
        revert()
    except Exception as error:
        ui.ok(ui.L(ui.S_SYSFS_UNAVAILABLE) % error)
    _log('profile reverted: %r (%s) trigger=countdown-%s' % (
        profile.get('name'), profile['kind'], outcome))
    _unpersist(profile, quiet=True)
    ui.notify(ui.L(ui.S_PERSIST_REVERTED))


def _unpersist(profile, quiet=False):
    remaining = persist.unpersist(profile['kind'])
    if not remaining:
        persist.disable_unit()
    _log('profile unpersisted: %r (%s)' % (profile.get('name'), profile['kind']))
    if not quiet:
        ui.notify(ui.L(ui.S_UNPERSISTED))


def _revert_profile(live, profile):
    forced_edid = _forced_edid_hex(live)
    try:
        if profile['kind'] == store.KIND_EDID:
            runtime.revert_edid()
        else:
            runtime.revert_vsvdb(forced_edid)
    except Exception as error:
        ui.ok(ui.L(ui.S_SYSFS_UNAVAILABLE) % error)
        return
    _log('profile reverted: %r (%s) trigger=menu revert-profile' % (
        profile.get('name'), profile['kind']))

    persisted = live['persisted'].get(profile['kind'])
    if persisted and persisted.get('id') == profile.get('id'):
        _unpersist(profile, quiet=True)
    ui.notify(ui.L(ui.S_REVERTED))


def _revert_real_edid():
    if not ui.yesno(ui.L(ui.S_MENU_REVERT_REAL)):
        return

    errors = runtime.revert_all()
    persist.unpersist()
    persist.disable_unit()

    if errors:
        ui.ok(ui.L(ui.S_SYSFS_UNAVAILABLE) % '; '.join(errors))
        return
    _log('profile reverted: trigger=unconditional revert-real-EDID')
    ui.notify(ui.L(ui.S_REVERTED_REAL_EDID))


def _delete_modprobe_leftovers(leftovers):
    """The actionable half of the service's leftover toast (design section 3,
    "offers to delete"). The file's own lines are shown before anything is
    removed, and a file that configures anything besides aml_media is
    reported rather than touched.
    """
    choice = ui.select(ui.L(ui.S_MODPROBE_HEADING), leftovers)
    if choice < 0:
        return

    path = leftovers[choice]
    lines = persist.modprobe_leftover_lines(path)
    if lines is None:
        ui.ok(ui.L(ui.S_MODPROBE_NOT_ONLY_OURS) % path)
        return

    if not ui.yesno(ui.L(ui.S_MODPROBE_DELETE_CONFIRM) % (path, '\n'.join(lines))):
        return

    deleted, detail = persist.delete_modprobe_leftover(path)
    if deleted:
        ui.notify(ui.L(ui.S_MODPROBE_DELETED) % path)
        return
    ui.ok(ui.L(ui.S_MODPROBE_DELETE_FAILED) % (path, detail))


def _profile_menu(profile_id):
    while True:
        profile = store.get_profile(profile_id)
        if profile is None:
            return
        live = _read_live()
        persisted = live['persisted'].get(profile['kind'])
        is_persisted = bool(persisted and persisted.get('id') == profile_id)

        entries = [
            (ui.L(ui.S_ACTION_APPLY), lambda: _apply_runtime(live, profile)),
            (ui.L(ui.S_ACTION_UNPERSIST if is_persisted else ui.S_ACTION_PERSIST),
             (lambda: _unpersist(profile)) if is_persisted else (lambda: _persist(live, profile))),
            (ui.L(ui.S_ACTION_REVERT), lambda: _revert_profile(live, profile)),
        ]
        if profile['kind'] == store.KIND_VSVDB and isinstance(profile.get('decoded'), dict):
            entries.append((ui.L(ui.S_ACTION_EDIT), lambda: _edit_fields(profile)))
            entries.append((ui.L(ui.S_ACTION_DERIVE), lambda: _derive(profile)))
        entries.extend([
            (ui.L(ui.S_ACTION_RENAME), lambda: _rename(profile)),
            (ui.L(ui.S_ACTION_DELETE), lambda: _delete(profile)),
            (ui.L(ui.S_ACTION_DETAILS), lambda: _profile_details(profile)),
        ])

        preselect = 0
        if not is_persisted and ui.setting_string('default_apply_mode', 'runtime') == 'persist':
            preselect = 1

        choice = ui.select(profile.get('name') or ui.L(ui.S_ADDON_NAME),
                           [label for label, _ in entries], preselect)
        if choice < 0:
            return
        entries[choice][1]()


def _live_state_line(live):
    return '%s: %s | %s: %s' % (
        ui.L(ui.S_LAYER_VSVDB), _state_label(live['vsvdb']),
        ui.L(ui.S_LAYER_EDID), _state_label(live['edid']))


def _show_live_state(live):
    lines = [
        '%s: %s' % (ui.L(ui.S_LAYER_VSVDB), _state_label(live['vsvdb'])),
        '%s: %s' % (ui.L(ui.S_LAYER_EDID), _state_label(live['edid'])),
    ]
    if live['error']:
        lines.append(ui.L(ui.S_SYSFS_UNAVAILABLE) % live['error'])
    ui.ok('\n'.join(lines), heading=ui.L(ui.S_MENU_LIVE_STATE))


def _profiles_menu():
    while True:
        live = _read_live()
        entries = []

        if not live['profiles']:
            entries.append((ui.L(ui.S_MENU_NO_PROFILES), lambda: None))
        for profile in live['profiles']:
            entries.append((_profile_label(profile, live),
                            (lambda pid: lambda: _profile_menu(pid))(profile['id'])))

        choice = ui.select(ui.L(ui.S_MENU_PROFILES) % len(live['profiles']),
                           [label for label, _ in entries])
        if choice < 0:
            return
        entries[choice][1]()


def _add_menu(live):
    entries = [
        (ui.L(ui.S_MENU_CAPTURE_VSVDB), lambda: _capture_vsvdb(live)),
        (ui.L(ui.S_MENU_CAPTURE_EDID), lambda: _capture_edid(live)),
        (ui.L(ui.S_MENU_ADD_PRESET), _add_preset),
        (ui.L(ui.S_MENU_PASTE_HEX), _paste_hex),
        (ui.L(ui.S_MENU_IMPORT_FILE), _import_file),
    ]

    choice = ui.select(ui.L(ui.S_MENU_ADD_PROFILE), [label for label, _ in entries])
    if choice < 0:
        return
    entries[choice][1]()


def _main_menu():
    while True:
        live = _read_live()
        entries = [
            (_live_state_line(live), lambda: _show_live_state(live)),
            (ui.L(ui.S_MENU_PROFILES) % len(live['profiles']), _profiles_menu),
            (ui.L(ui.S_MENU_ADD_PROFILE), lambda: _add_menu(live)),
            (ui.L(ui.S_MENU_REVERT_REAL), _revert_real_edid),
            (ui.L(ui.S_MENU_SETTINGS), ui.open_settings),
        ]

        leftovers = persist.modprobe_leftovers()
        if leftovers:
            entries.append((ui.L(ui.S_MENU_MODPROBE_DELETE),
                            (lambda found: lambda: _delete_modprobe_leftovers(found))(leftovers)))

        choice = ui.select(ui.L(ui.S_ADDON_NAME), [label for label, _ in entries])
        if choice < 0:
            return
        entries[choice][1]()


def run():
    _main_menu()


if __name__ == '__main__':
    run()
