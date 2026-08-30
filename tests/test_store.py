import json
import os
import sys

import pytest

_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, '..', 'resources', 'lib'))
sys.path.insert(0, os.environ.get(
    'SIDEDATA_LIB_PATH',
    os.path.join(_HERE, '..', '..', 'script.module.sidedata', 'lib')))

import state  # noqa: E402
import store  # noqa: E402

# a plain captured/pasted-style VSVDB, not one of the four presets
_SAMPLE_VSVDB = 'eb0146d000480287835c6d8e'


def _checksum(block):
    block = bytearray(block)
    block[127] = 0
    block[127] = (256 - sum(block[:127])) % 256
    return bytes(block)


def _sample_edid_hex():
    base = bytearray(128)
    base[0:8] = bytes.fromhex('00ffffffffffff00')
    base[8] = 0x1e  # manufacturer id ACM (letters clamp within 5-bit range)
    base[9] = 0xa5
    base[10] = 0x34
    base[11] = 0x12
    base[0x7e] = 1
    base = _checksum(base)

    ext = bytearray(128)
    ext[0] = 0x02
    ext[1] = 3
    vsvdb_bytes = bytes.fromhex(_SAMPLE_VSVDB)
    ext[2] = 4 + len(vsvdb_bytes)
    ext[4:4 + len(vsvdb_bytes)] = vsvdb_bytes
    ext = _checksum(ext)

    return (bytes(base) + bytes(ext)).hex()


@pytest.fixture
def store_path(tmp_path, monkeypatch):
    path = tmp_path / 'profiles.json'
    monkeypatch.setenv('EDIDMGR_PROFILES_PATH', str(path))
    return path


def test_missing_file_starts_clean(store_path):
    data = store.load_store()
    assert data == {'schema': store.SCHEMA_VERSION, 'profiles': []}


def test_schema_version_round_trips(store_path):
    store.add_profile(store.KIND_VSVDB, 'sample', _SAMPLE_VSVDB, store.SOURCE_CAPTURED)
    on_disk = json.loads(store_path.read_text())
    assert on_disk['schema'] == store.SCHEMA_VERSION
    assert store.load_store()['schema'] == store.SCHEMA_VERSION


def test_corrupt_json_recovers_clean(store_path):
    store_path.write_text('{not json')
    assert store.load_store() == {'schema': store.SCHEMA_VERSION, 'profiles': []}


def test_non_dict_json_recovers_clean(store_path):
    store_path.write_text('[]')
    assert store.load_store() == {'schema': store.SCHEMA_VERSION, 'profiles': []}


def test_missing_profiles_key_recovers_clean(store_path):
    store_path.write_text(json.dumps({'schema': 1}))
    assert store.load_store() == {'schema': store.SCHEMA_VERSION, 'profiles': []}


def test_corrupt_file_is_backed_up_before_recovery(store_path):
    store_path.write_text('{not json')
    store.load_store()

    backup = store_path.parent / (store_path.name + '.corrupt')
    assert backup.read_text() == '{not json'
    assert not store_path.exists()


def test_corrupt_backup_survives_a_subsequent_save(store_path):
    store_path.write_text('{not json')
    store.load_store()
    backup = store_path.parent / (store_path.name + '.corrupt')

    store.add_profile(store.KIND_VSVDB, 'fresh', _SAMPLE_VSVDB, store.SOURCE_CAPTURED)

    assert backup.read_text() == '{not json'
    on_disk = json.loads(store_path.read_text())
    assert len(on_disk['profiles']) == 1


def test_schema_newer_than_supported_is_backed_up_and_starts_clean(store_path):
    newer_schema = store.SCHEMA_VERSION + 1
    store_path.write_text(json.dumps({'schema': newer_schema, 'profiles': [{'id': 'old'}]}))

    assert store.load_store() == {'schema': store.SCHEMA_VERSION, 'profiles': []}
    backup = store_path.parent / ('profiles.json.schema-backup-%d' % newer_schema)
    assert json.loads(backup.read_text())['profiles'] == [{'id': 'old'}]
    assert not store_path.exists()


def test_schema_lower_than_supported_loads_normally(store_path):
    store_path.write_text(json.dumps({'schema': 0, 'profiles': []}))
    assert store.load_store() == {'schema': 0, 'profiles': []}


def test_load_store_cleans_up_orphaned_tmp_files(store_path):
    directory = store_path.parent
    directory.mkdir(parents=True, exist_ok=True)
    orphan = directory / '.profiles-abc123.tmp'
    orphan.write_text('leftover from a killed write')

    store.load_store()

    assert not orphan.exists()


def test_atomic_write_leaves_no_partial_file(store_path, monkeypatch):
    store.add_profile(store.KIND_VSVDB, 'first', _SAMPLE_VSVDB, store.SOURCE_CAPTURED)
    original = store_path.read_text()

    def _boom(*args, **kwargs):
        raise RuntimeError('simulated write failure')

    monkeypatch.setattr(json, 'dump', _boom)
    with pytest.raises(RuntimeError):
        store.add_profile(store.KIND_VSVDB, 'second', _SAMPLE_VSVDB, store.SOURCE_PASTED)

    assert store_path.read_text() == original
    leftovers = [p for p in os.listdir(store_path.parent) if p != store_path.name]
    assert leftovers == []


def test_atomic_write_leaves_no_file_on_first_write_failure(tmp_path, monkeypatch):
    path = tmp_path / 'nested' / 'profiles.json'
    monkeypatch.setenv('EDIDMGR_PROFILES_PATH', str(path))

    monkeypatch.setattr(json, 'dump', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))
    with pytest.raises(RuntimeError):
        store.add_profile(store.KIND_VSVDB, 'x', _SAMPLE_VSVDB, store.SOURCE_CAPTURED)

    assert not path.exists()
    assert os.listdir(path.parent) == []


def test_add_and_get_profile_round_trip(store_path):
    created = store.add_profile(store.KIND_VSVDB, 'my vsvdb', _SAMPLE_VSVDB, store.SOURCE_CAPTURED)
    fetched = store.get_profile(created['id'])
    assert fetched == created
    assert fetched['raw'] == _SAMPLE_VSVDB
    assert fetched['kind'] == store.KIND_VSVDB
    assert fetched['decoded'] is not None
    assert fetched['created'] == fetched['modified']


def test_get_profile_missing_returns_none(store_path):
    assert store.get_profile('does-not-exist') is None


def test_list_profiles_filters_by_kind(store_path):
    vsvdb_profile = store.add_profile(store.KIND_VSVDB, 'v', _SAMPLE_VSVDB, store.SOURCE_CAPTURED)
    edid_profile = store.add_profile(store.KIND_EDID, 'e', _sample_edid_hex(), store.SOURCE_CAPTURED)

    assert [p['id'] for p in store.list_profiles(store.KIND_VSVDB)] == [vsvdb_profile['id']]
    assert [p['id'] for p in store.list_profiles(store.KIND_EDID)] == [edid_profile['id']]
    assert len(store.list_profiles()) == 2


def test_update_profile_renames_and_bumps_modified(store_path):
    created = store.add_profile(store.KIND_VSVDB, 'orig', _SAMPLE_VSVDB, store.SOURCE_CAPTURED)
    updated = store.update_profile(created['id'], name='renamed')
    assert updated['name'] == 'renamed'
    assert updated['id'] == created['id']
    assert updated['created'] == created['created']
    assert updated['modified'] >= created['modified']


def test_update_profile_ignores_id_and_created(store_path):
    created = store.add_profile(store.KIND_VSVDB, 'orig', _SAMPLE_VSVDB, store.SOURCE_CAPTURED)
    updated = store.update_profile(created['id'], id='forged-id', created=0)
    assert updated['id'] == created['id']
    assert updated['created'] == created['created']


def test_update_profile_missing_returns_none(store_path):
    assert store.update_profile('nope', name='x') is None


def test_update_profile_raw_recomputes_decoded(store_path):
    created = store.add_profile(store.KIND_VSVDB, 'orig', _SAMPLE_VSVDB, store.SOURCE_CAPTURED)
    other_hex = store.PRESET_HEX[2]
    updated = store.update_profile(created['id'], raw=other_hex.upper())
    assert updated['raw'] == other_hex
    assert updated['source'] == store.SOURCE_PRESET
    assert updated['preset_index'] == 3


def test_delete_profile(store_path):
    created = store.add_profile(store.KIND_VSVDB, 'orig', _SAMPLE_VSVDB, store.SOURCE_CAPTURED)
    assert store.delete_profile(created['id']) is True
    assert store.get_profile(created['id']) is None
    assert store.delete_profile(created['id']) is False


def test_unknown_kind_rejected(store_path):
    with pytest.raises(ValueError):
        store.build_profile('bogus', 'x', _SAMPLE_VSVDB, store.SOURCE_CAPTURED)


def test_unknown_source_rejected(store_path):
    with pytest.raises(ValueError):
        store.build_profile(store.KIND_VSVDB, 'x', _SAMPLE_VSVDB, 'bogus')


def test_derived_requires_parent_id(store_path):
    with pytest.raises(ValueError):
        store.build_profile(store.KIND_VSVDB, 'x', _SAMPLE_VSVDB, store.SOURCE_DERIVED)


def test_derived_sets_parent_id(store_path):
    parent = store.add_profile(store.KIND_VSVDB, 'parent', _SAMPLE_VSVDB, store.SOURCE_CAPTURED)
    child = store.build_profile(
        store.KIND_VSVDB, 'child', _SAMPLE_VSVDB, store.SOURCE_DERIVED, parent_id=parent['id'])
    assert child['parent_id'] == parent['id']


def test_imported_requires_origin_file(store_path):
    with pytest.raises(ValueError):
        store.build_profile(store.KIND_VSVDB, 'x', _SAMPLE_VSVDB, store.SOURCE_IMPORTED)


def test_imported_vsvdb_carries_extraction_metadata(store_path):
    profile = store.build_profile(
        store.KIND_VSVDB, 'x', _SAMPLE_VSVDB, store.SOURCE_IMPORTED,
        origin_file='sink.edid.bin', extracted_from_edid=True)
    assert profile['origin_file'] == 'sink.edid.bin'
    assert profile['extracted_from_edid'] is True


def test_imported_edid_has_no_extraction_flag(store_path):
    profile = store.build_profile(
        store.KIND_EDID, 'x', _sample_edid_hex(), store.SOURCE_IMPORTED,
        origin_file='sink.edid.bin')
    assert profile['origin_file'] == 'sink.edid.bin'
    assert 'extracted_from_edid' not in profile


@pytest.mark.parametrize('index', [0, 1, 2, 3])
def test_preset_override_applies_regardless_of_arrival_source(store_path, index):
    preset_hex = store.PRESET_HEX[index]
    for arrival_source in (store.SOURCE_CAPTURED, store.SOURCE_PASTED):
        profile = store.build_profile(store.KIND_VSVDB, 'p', preset_hex, arrival_source)
        assert profile['source'] == store.SOURCE_PRESET
        assert profile['preset_index'] == index + 1


def test_preset_override_preserves_parent_id_for_a_derived_profile(store_path):
    # the preset-source override must not cost a derived profile its
    # parent_id, the same way it doesn't cost an imported profile its
    # origin_file: gate on the original source argument, not the field the
    # override already rewrote.
    parent = store.add_profile(store.KIND_VSVDB, 'parent', _SAMPLE_VSVDB, store.SOURCE_CAPTURED)
    preset_hex = store.PRESET_HEX[1]
    profile = store.build_profile(
        store.KIND_VSVDB, 'child', preset_hex, store.SOURCE_DERIVED, parent_id=parent['id'])
    assert profile['source'] == store.SOURCE_PRESET
    assert profile['preset_index'] == 2
    assert profile['parent_id'] == parent['id']


def test_preset_override_via_import(store_path):
    preset_hex = store.PRESET_HEX[3]
    profile = store.build_profile(
        store.KIND_VSVDB, 'p', preset_hex, store.SOURCE_IMPORTED, origin_file='dump.bin')
    assert profile['source'] == store.SOURCE_PRESET
    assert profile['preset_index'] == 4
    # imported metadata survives the source override
    assert profile['origin_file'] == 'dump.bin'


def test_raw_normalizes_whitespace_and_case_for_the_classifier(store_path):
    spaced_upper = ' '.join(_SAMPLE_VSVDB[i:i + 2] for i in range(0, len(_SAMPLE_VSVDB), 2)).upper()
    profile = store.build_profile(store.KIND_VSVDB, 'p', spaced_upper, store.SOURCE_PASTED)
    assert profile['raw'] == _SAMPLE_VSVDB

    result = state.classify_vsvdb(255, bytes.fromhex(_SAMPLE_VSVDB), None, [profile])
    assert result.state != state.FOREIGN


def test_update_profile_raw_normalizes_whitespace_and_case(store_path):
    created = store.add_profile(store.KIND_VSVDB, 'orig', _SAMPLE_VSVDB, store.SOURCE_CAPTURED)
    spaced_upper = ' '.join(
        store.PRESET_HEX[0][i:i + 2] for i in range(0, len(store.PRESET_HEX[0]), 2)).upper()
    updated = store.update_profile(created['id'], raw=spaced_upper)
    assert updated['raw'] == store.PRESET_HEX[0]


def test_non_preset_bytes_keep_their_source(store_path):
    profile = store.build_profile(store.KIND_VSVDB, 'p', _SAMPLE_VSVDB, store.SOURCE_CAPTURED)
    assert profile['source'] == store.SOURCE_CAPTURED
    assert 'preset_index' not in profile


def test_preset_source_requires_index_for_non_preset_bytes(store_path):
    with pytest.raises(ValueError):
        store.build_profile(store.KIND_VSVDB, 'p', _SAMPLE_VSVDB, store.SOURCE_PRESET)


def test_decoded_cache_present_for_valid_vsvdb(store_path):
    profile = store.build_profile(store.KIND_VSVDB, 'p', _SAMPLE_VSVDB, store.SOURCE_CAPTURED)
    assert profile['decoded'] is not None
    assert 'max_nits' in profile['decoded']


def test_decoded_cache_none_for_hdr10plus_preset(store_path):
    # preset 4 is not a Dolby block; sidedata.parse_vsvdb correctly returns
    # None for it (design finding 6), and that is what gets cached, not a
    # special case.
    profile = store.build_profile(store.KIND_VSVDB, 'p', store.PRESET_HEX[3], store.SOURCE_CAPTURED)
    assert profile['decoded'] is None


def test_decoded_cache_none_for_garbage_hex(store_path):
    profile = store.build_profile(store.KIND_VSVDB, 'p', 'zz', store.SOURCE_PASTED)
    assert profile['decoded'] is None


def test_edid_decoded_summary_fields(store_path):
    profile = store.build_profile(store.KIND_EDID, 'e', _sample_edid_hex(), store.SOURCE_CAPTURED)
    decoded = profile['decoded']
    assert decoded['block_count'] == 2
    assert decoded['has_vsvdb'] is True
    assert decoded['manufacturer'] is not None
    assert decoded['product'] == 0x1234


def test_edid_decoded_summary_no_vsvdb(store_path):
    base = bytearray(128)
    base[0:8] = bytes.fromhex('00ffffffffffff00')
    base[0x7e] = 0
    base = _checksum(base)
    profile = store.build_profile(store.KIND_EDID, 'e', base.hex(), store.SOURCE_CAPTURED)
    assert profile['decoded']['has_vsvdb'] is False
    assert profile['decoded']['block_count'] == 1


def test_rebuild_decoded_cache_recomputes(store_path):
    created = store.add_profile(store.KIND_VSVDB, 'v', _SAMPLE_VSVDB, store.SOURCE_CAPTURED)

    store_data = store.load_store()
    for profile in store_data['profiles']:
        if profile['id'] == created['id']:
            profile['decoded'] = {'stale': True}
    store.save_store(store_data)
    assert store.get_profile(created['id'])['decoded'] == {'stale': True}

    store.rebuild_decoded_cache()
    refreshed = store.get_profile(created['id'])
    assert refreshed['decoded'] == created['decoded']


def test_persist_state_constants_are_the_four_values():
    assert store.PERSIST_STATES == ('pending', 'trial', 'trial-acked', 'confirmed')
