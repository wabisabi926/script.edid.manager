# JSON CRUD for both profile kinds (vsvdb, edid), see
# ~/ce22-docs/vsvdb-manager-design.md sections 4 and 8. `raw` hex is the
# truth for a profile; `decoded` is a rebuildable cache, never the other way
# around. Every write goes through a temp file plus os.replace, so a crash
# mid-write never leaves a partial profiles.json behind, and a missing or
# corrupt file is never fatal: callers get a clean, empty store instead of
# an exception.
#
# The four-value persistence state (pending/trial/trial-acked/confirmed,
# section 3) is owned by the systemd unit and service.py, not this module
# (section 8: "the persistence half is the unit... the persisted profile
# file, and the four-value state file next to it"). The constants below
# exist so every caller spells the same four values; this module never
# reads or writes that file.

import json
import os
import tempfile
import time
import uuid

import edid
from sidedata import vsvdb

SCHEMA_VERSION = 1

KIND_VSVDB = 'vsvdb'
KIND_EDID = 'edid'
KINDS = frozenset({KIND_VSVDB, KIND_EDID})

SOURCE_CAPTURED = 'captured'
SOURCE_PRESET = 'preset'
SOURCE_PASTED = 'pasted'
SOURCE_IMPORTED = 'imported'
SOURCE_DERIVED = 'derived'
SOURCES = frozenset({
    SOURCE_CAPTURED, SOURCE_PRESET, SOURCE_PASTED, SOURCE_IMPORTED, SOURCE_DERIVED,
})

# The four force_vsvdb 1-4 kernel preset arrays, source-traced against
# common_drivers 1db67e8e hdmitx_edid_parse.c tv_vsvdb[]. Index 0 is preset
# 1. A profile's raw bytes matching one of these gets source=preset
# regardless of how it arrived (design section 4).
PRESET_HEX = (
    'eb0146d000450b908660768f',
    'ee0146d000240f8ba8534b9d270b00',
    'eb0146d000444f428c46568e',
    'e5018b849001',
)

# Shared spelling for the section 3 persistence state file, owned by the
# systemd unit and service.py.
PERSIST_PENDING = 'pending'
PERSIST_TRIAL = 'trial'
PERSIST_TRIAL_ACKED = 'trial-acked'
PERSIST_CONFIRMED = 'confirmed'
PERSIST_STATES = (PERSIST_PENDING, PERSIST_TRIAL, PERSIST_TRIAL_ACKED, PERSIST_CONFIRMED)

_DEFAULT_PATH = os.path.join(
    os.path.expanduser('~'), '.kodi', 'userdata', 'addon_data',
    'script.edid.manager', 'profiles.json')


def profiles_path():
    override = os.environ.get('EDIDMGR_PROFILES_PATH')
    if override:
        return override

    try:
        import xbmcaddon
        import xbmcvfs
    except ImportError:
        return _DEFAULT_PATH

    profile_dir = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
    return os.path.join(profile_dir, 'profiles.json')


def _normalize_raw(raw):
    """Strip whitespace and case-fold raw hex the same way edid.normalize_input
    does, so a profile's stored raw always matches the classifier's stored
    profiles regardless of how the caller spelled it. Falls back to a bare
    case-fold for input normalize_input refuses (garbage hex), since store
    itself never validates raw, only the decode path does.
    """
    data = edid.normalize_input(raw)
    if data is not None:
        return data.hex()
    return raw.lower()


def _preset_index(raw_hex):
    for index, preset in enumerate(PRESET_HEX):
        if raw_hex == preset:
            return index + 1
    return None


def _empty_store():
    return {'schema': SCHEMA_VERSION, 'profiles': []}


def _quarantine(path, suffix):
    """Move an unusable profiles.json aside so the next save can't clobber
    whatever might still be salvageable in it. A no-op if path doesn't
    exist; the rename itself is best-effort, same as the corrupt-file case
    it shares this helper with.
    """
    if not os.path.exists(path):
        return
    try:
        os.replace(path, '%s.%s' % (path, suffix))
    except OSError:
        pass


def _cleanup_orphaned_tmp(directory):
    # mkstemp files from a save_store that never reached os.replace, e.g.
    # the process was killed mid-write. Harmless to remove: save_store
    # always recreates its own tmp file on the next write.
    try:
        entries = os.listdir(directory)
    except OSError:
        return
    for name in entries:
        if name.startswith('.profiles-') and name.endswith('.tmp'):
            try:
                os.remove(os.path.join(directory, name))
            except OSError:
                pass


def load_store():
    path = profiles_path()
    _cleanup_orphaned_tmp(os.path.dirname(path) or '.')

    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        _quarantine(path, 'corrupt')
        return _empty_store()

    if not isinstance(data, dict) or not isinstance(data.get('profiles'), list):
        _quarantine(path, 'corrupt')
        return _empty_store()

    # Schema migration hooks belong here, keyed on the value read below:
    # equal, missing (older files never wrote one) or lower than
    # SCHEMA_VERSION all load as-is since v1 is the only version so far.
    schema = data.get('schema')
    if isinstance(schema, int) and schema > SCHEMA_VERSION:
        _quarantine(path, 'schema-backup-%d' % schema)
        return _empty_store()

    return data


def save_store(store):
    path = profiles_path()
    directory = os.path.dirname(path) or '.'
    os.makedirs(directory, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(prefix='.profiles-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(store, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def list_profiles(kind=None):
    profiles = load_store()['profiles']
    if kind is None:
        return profiles
    return [p for p in profiles if p.get('kind') == kind]


def get_profile(profile_id):
    for profile in load_store()['profiles']:
        if profile.get('id') == profile_id:
            return profile
    return None


def _decode_edid_summary(raw_hex):
    data = bytes.fromhex(raw_hex)
    manufacturer, product = edid.parse_pnp_id(data)

    return {
        'manufacturer': manufacturer,
        'product': product,
        'block_count': len(data) // 128,
        'has_vsvdb': edid.find_vsvdb(data) is not None,
    }


def _decode_for(kind, raw_hex):
    try:
        if kind == KIND_VSVDB:
            return vsvdb.parse_vsvdb(raw_hex)
        if kind == KIND_EDID:
            return _decode_edid_summary(raw_hex)
    except Exception:
        return None
    return None


def build_profile(kind, name, raw, source, preset_index=None, parent_id=None,
                   origin_file=None, extracted_from_edid=None):
    """Build a profile record, applying the preset-source override and the
    per-source metadata rules from design section 4. Pure: does not touch
    the store file.
    """
    if kind not in KINDS:
        raise ValueError('unknown profile kind %r' % (kind,))
    if source not in SOURCES:
        raise ValueError('unknown profile source %r' % (source,))

    raw_hex = _normalize_raw(raw)
    now = time.time()

    profile = {
        'id': uuid.uuid4().hex,
        'kind': kind,
        'name': name,
        'source': source,
        'raw': raw_hex,
        'decoded': _decode_for(kind, raw_hex),
        'created': now,
        'modified': now,
    }

    if kind == KIND_VSVDB:
        detected_preset = _preset_index(raw_hex)
        if detected_preset is not None:
            profile['source'] = SOURCE_PRESET
            profile['preset_index'] = detected_preset
        elif source == SOURCE_PRESET:
            if preset_index is None:
                raise ValueError('source=preset requires preset_index')
            profile['preset_index'] = preset_index

    if source == SOURCE_DERIVED:
        if parent_id is None:
            raise ValueError('source=derived requires parent_id')
        profile['parent_id'] = parent_id

    if source == SOURCE_IMPORTED:
        if origin_file is None:
            raise ValueError('source=imported requires origin_file')
        profile['origin_file'] = origin_file
        if kind == KIND_VSVDB:
            profile['extracted_from_edid'] = bool(extracted_from_edid)

    return profile


def add_profile(kind, name, raw, source, **kwargs):
    profile = build_profile(kind, name, raw, source, **kwargs)
    store = load_store()
    store['profiles'].append(profile)
    save_store(store)
    return profile


def update_profile(profile_id, **fields):
    fields = dict(fields)
    fields.pop('id', None)
    fields.pop('created', None)

    store = load_store()
    for profile in store['profiles']:
        if profile.get('id') != profile_id:
            continue

        profile.update(fields)
        if 'raw' in fields:
            profile['raw'] = _normalize_raw(fields['raw'])
            profile['decoded'] = _decode_for(profile['kind'], profile['raw'])
            if profile['kind'] == KIND_VSVDB:
                detected_preset = _preset_index(profile['raw'])
                if detected_preset is not None:
                    profile['source'] = SOURCE_PRESET
                    profile['preset_index'] = detected_preset
        profile['modified'] = time.time()

        save_store(store)
        return profile

    return None


def delete_profile(profile_id):
    store = load_store()
    before = len(store['profiles'])
    store['profiles'] = [p for p in store['profiles'] if p.get('id') != profile_id]
    if len(store['profiles']) == before:
        return False

    save_store(store)
    return True


def rebuild_decoded_cache():
    store = load_store()
    for profile in store['profiles']:
        profile['decoded'] = _decode_for(profile['kind'], profile['raw'])
    save_store(store)
    return store['profiles']
