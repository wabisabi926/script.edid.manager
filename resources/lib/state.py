# Ordered first-match-wins classifier from
# ~/ce22-docs/vsvdb-manager-design.md section 4, run once per layer (vsvdb,
# edid). Pure: every input is a plain value the caller already read from
# backend.py or looked up in the store, never sysfs or the store itself.
#
# VSVDB and EDID share the same six states but not the same observables.
# The VSVDB layer has two independent reads, the live params
# (force_vsvdb/vsvdb_data) and the effective block (dv_cap), so a
# disagreement between them is a real, decidable signal. The EDID layer has
# only rawedid: forced_edid cannot be read back (finding 8), so there is no
# separate params reading to disagree with rawedid the way vsvdb_data
# disagrees with dv_cap, and DRIFTED is therefore never reachable for EDID.
# A persisted profile whose bytes rawedid doesn't (yet) show is always
# persisted-pending: the write applies on the boot unit's own schedule
# (design section 4, "persisted-pending is reachable"), so between persist
# and reboot rawedid can read anything, including the sink's own real EDID,
# and that is still the pending case, not a disagreement to report. Only
# when nothing is persisted does an unmatched rawedid become undecidable:
# forced_edid cannot be read back, so it reports inactive with the
# uncertainty flag rather than asserting the sink's own EDID.

import collections

FOREIGN = 'foreign'
DRIFTED = 'drifted'
PERSISTED_EFFECTIVE = 'persisted-effective'
PERSISTED_PENDING = 'persisted-pending'
RUNTIME_ONLY = 'runtime-only'
INACTIVE = 'inactive'

STATES = (FOREIGN, DRIFTED, PERSISTED_EFFECTIVE, PERSISTED_PENDING, RUNTIME_ONLY, INACTIVE)

# live_profile_id is only ever set in the persisted-pending compound case:
# params already match the persisted profile (profile_id) but dv_cap still
# shows a different, previously-live stored profile. Defaulted so every
# other call site keeps its existing three-argument construction.
Classification = collections.namedtuple(
    'Classification', ['state', 'profile_id', 'uncertain', 'live_profile_id'],
    defaults=(None,))


def _match(profiles, raw_hex):
    if not raw_hex:
        return None
    for profile in profiles:
        if profile.get('raw') == raw_hex:
            return profile
    return None


def classify_vsvdb(force_vsvdb, vsvdb_data, dv_cap_hex, profiles, persisted=None):
    """Classify the VSVDB layer.

    force_vsvdb: int read from the kernel param (0, 1-4, 255).
    vsvdb_data: bytes read from the kernel param.
    dv_cap_hex: hex string from sidedata.read_vsvdb_raw(), '' if absent.
    profiles: the stored kind=vsvdb profiles.
    persisted: the stored profile the persistence marker names for this
        layer, or None if nothing is persisted.
    """
    vsvdb_data_hex = vsvdb_data.hex() if vsvdb_data else None
    dv_cap_hex = dv_cap_hex.lower() if dv_cap_hex else None
    persisted_raw = persisted.get('raw') if persisted else None
    override_active = force_vsvdb != 0

    # force_vsvdb 1-4 is a preset by construction (design finding 4), so it
    # is always decidable even with no matching store record; 255 is custom
    # bytes, and unmatched custom bytes are exactly what foreign means. A
    # match against the persisted profile's own raw counts too, even when
    # that profile isn't in the profiles list: a persisted-but-not-stored
    # profile is a persistence state, never someone-used-SSH.
    matches_persisted_raw = persisted_raw is not None and vsvdb_data_hex == persisted_raw
    if force_vsvdb == 255 and _match(profiles, vsvdb_data_hex) is None and not matches_persisted_raw:
        return Classification(FOREIGN, None, False)

    if override_active and persisted_raw is not None and vsvdb_data_hex == persisted_raw:
        if dv_cap_hex == persisted_raw:
            return Classification(PERSISTED_EFFECTIVE, persisted.get('id'), False)
        live = _match(profiles, dv_cap_hex)
        return Classification(
            PERSISTED_PENDING, persisted.get('id'), False,
            live.get('id') if live else None)

    if override_active and persisted_raw is not None:
        # a profile is persisted for this layer but the live override does
        # not match it: a real disagreement, not the "hasn't re-parsed yet"
        # case above.
        matched = _match(profiles, dv_cap_hex)
        return Classification(DRIFTED, matched.get('id') if matched else None, False)

    if override_active:
        matched = _match(profiles, dv_cap_hex)
        if vsvdb_data_hex != dv_cap_hex:
            return Classification(DRIFTED, matched.get('id') if matched else None, False)
        return Classification(RUNTIME_ONLY, matched.get('id') if matched else None, False)

    if persisted_raw is not None:
        matched = _match(profiles, dv_cap_hex)
        return Classification(DRIFTED, matched.get('id') if matched else None, False)

    return Classification(INACTIVE, None, False)


def classify_edid(rawedid, profiles, persisted=None, runtime_marker_raw=None):
    """Classify the EDID layer.

    rawedid: bytes read from the rawedid sysfs node.
    profiles: the stored kind=edid profiles.
    persisted: the stored profile the persistence marker names for this
        layer, or None if nothing is persisted.
    runtime_marker_raw: the addon's own tmpfs runtime marker
        (runtime.read_runtime_marker()), the raw hex it last forced this
        boot, or None. A match against rawedid proves nothing about forcing
        on its own (finding 8: forced_edid cannot be read back, so a
        captured-from-this-sink profile matches rawedid whether or not
        anything is forced); this is the addon-authored evidence that can.
        Only agreement is decisive: the marker disagreeing or absent rules
        out this addon, not a terminal-forced EDID (the public gist this
        addon supersedes documents forcing one directly), so that case
        stays uncertain the same as an unmatched rawedid.

    Never returns foreign: forced_edid cannot be read back (finding 8), so
    an unrecognized rawedid reports inactive with the uncertainty flag
    instead of asserting anything about it. Never returns drifted either:
    with a profile persisted, any rawedid that doesn't match it is the
    pending case, empty or not, since there is no independent params
    reading to tell "not applied yet" apart from "applied to something
    else" the way the VSVDB layer can.
    """
    rawedid_hex = rawedid.hex() if rawedid else None
    persisted_raw = persisted.get('raw') if persisted else None
    runtime_marker_raw = runtime_marker_raw.lower() if runtime_marker_raw else None
    matched = _match(profiles, rawedid_hex)

    if persisted_raw is not None:
        if rawedid_hex == persisted_raw:
            return Classification(PERSISTED_EFFECTIVE, persisted.get('id'), False)
        return Classification(PERSISTED_PENDING, persisted.get('id'), False)

    if rawedid_hex is not None:
        if matched is not None and runtime_marker_raw == rawedid_hex:
            return Classification(RUNTIME_ONLY, matched.get('id'), False)
        return Classification(INACTIVE, None, True)

    return Classification(INACTIVE, None, False)
