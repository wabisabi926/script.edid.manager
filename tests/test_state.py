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

_PROFILE_A = {'id': 'profile-a', 'raw': 'aa11'}
_PROFILE_B = {'id': 'profile-b', 'raw': 'bb22'}
_UNKNOWN_HEX = 'cc33'


def _profile(profile_id, raw):
    return {'id': profile_id, 'raw': raw}


# --- VSVDB layer, one case per state, in the design's own order -----------

class TestClassifyVsvdb:

    def test_foreign_custom_bytes_match_nothing_stored(self):
        result = state.classify_vsvdb(255, bytes.fromhex(_UNKNOWN_HEX), None, [_PROFILE_A])
        assert result.state == state.FOREIGN
        assert result.profile_id is None
        assert result.uncertain is False

    def test_foreign_wins_over_a_persisted_profile(self):
        # design section 4's ordered check puts foreign first: even with a
        # profile persisted for this layer, custom bytes matching nothing
        # stored is still someone-used-SSH, not a persistence state.
        persisted = _profile('persisted-id', _PROFILE_A['raw'])
        result = state.classify_vsvdb(
            255, bytes.fromhex(_UNKNOWN_HEX), _PROFILE_A['raw'],
            [_PROFILE_A], persisted=persisted)
        assert result.state == state.FOREIGN
        assert result.profile_id is None

    def test_custom_bytes_matching_a_stored_profile_is_not_foreign(self):
        result = state.classify_vsvdb(
            255, bytes.fromhex(_PROFILE_A['raw']), _PROFILE_A['raw'], [_PROFILE_A])
        assert result.state != state.FOREIGN

    def test_drifted_params_and_dv_cap_disagree(self):
        result = state.classify_vsvdb(
            255, bytes.fromhex(_PROFILE_A['raw']), _UNKNOWN_HEX, [_PROFILE_A])
        assert result.state == state.DRIFTED
        assert result.profile_id is None

    def test_drifted_persisted_profile_not_reflected_by_live_params(self):
        persisted = _profile('persisted-id', _PROFILE_A['raw'])
        result = state.classify_vsvdb(
            255, bytes.fromhex(_PROFILE_B['raw']), _PROFILE_B['raw'],
            [_PROFILE_A, _PROFILE_B], persisted=persisted)
        assert result.state == state.DRIFTED
        assert result.profile_id == _PROFILE_B['id']

    def test_drifted_force_vsvdb_zero_with_persisted_profile(self):
        persisted = _profile('persisted-id', _PROFILE_A['raw'])
        result = state.classify_vsvdb(0, b'', None, [_PROFILE_A], persisted=persisted)
        assert result.state == state.DRIFTED
        assert result.profile_id is None

    def test_drifted_force_vsvdb_zero_names_the_dv_cap_profile_when_known(self):
        # the force_vsvdb=0 drifted branch attempts a dv_cap match for
        # profile_id the same way the other drifted branches do.
        persisted = _profile('persisted-id', _PROFILE_A['raw'])
        result = state.classify_vsvdb(
            0, b'', _PROFILE_B['raw'], [_PROFILE_A, _PROFILE_B], persisted=persisted)
        assert result.state == state.DRIFTED
        assert result.profile_id == _PROFILE_B['id']

    def test_persisted_effective(self):
        persisted = _profile('persisted-id', _PROFILE_A['raw'])
        result = state.classify_vsvdb(
            255, bytes.fromhex(_PROFILE_A['raw']), _PROFILE_A['raw'],
            [_PROFILE_A], persisted=persisted)
        assert result.state == state.PERSISTED_EFFECTIVE
        assert result.profile_id == 'persisted-id'

    def test_persisted_pending_dv_cap_not_yet_caught_up(self):
        persisted = _profile('persisted-id', _PROFILE_A['raw'])
        result = state.classify_vsvdb(
            255, bytes.fromhex(_PROFILE_A['raw']), None,
            [_PROFILE_A], persisted=persisted)
        assert result.state == state.PERSISTED_PENDING
        assert result.profile_id == 'persisted-id'

    def test_persisted_pending_dv_cap_matches_a_different_stored_profile(self):
        # params already match the persisted profile, but dv_cap still shows
        # a different, previously-live block: the old override is still in
        # effect and the new one applies at the next parse, so this stays
        # pending, not drifted. The still-live profile surfaces separately.
        persisted = _profile('persisted-id', _PROFILE_A['raw'])
        result = state.classify_vsvdb(
            255, bytes.fromhex(_PROFILE_A['raw']), _PROFILE_B['raw'],
            [_PROFILE_A, _PROFILE_B], persisted=persisted)
        assert result.state == state.PERSISTED_PENDING
        assert result.profile_id == 'persisted-id'
        assert result.live_profile_id == _PROFILE_B['id']

    def test_runtime_only(self):
        result = state.classify_vsvdb(
            255, bytes.fromhex(_PROFILE_A['raw']), _PROFILE_A['raw'], [_PROFILE_A])
        assert result.state == state.RUNTIME_ONLY
        assert result.profile_id == _PROFILE_A['id']

    def test_runtime_only_preset_known_without_a_store_match(self):
        # force_vsvdb 1-4 is never its own branch (design section 4): a
        # preset is decidable purely from its numeric range, even if nobody
        # ever stored a profile with those exact bytes.
        preset_hex = store.PRESET_HEX[0]
        result = state.classify_vsvdb(1, bytes.fromhex(preset_hex), preset_hex, [])
        assert result.state == state.RUNTIME_ONLY
        assert result.profile_id is None

    def test_inactive(self):
        result = state.classify_vsvdb(0, b'', None, [_PROFILE_A])
        assert result.state == state.INACTIVE
        assert result.profile_id is None
        assert result.uncertain is False

    def test_persisted_preset_overlap_classifies_unambiguously(self):
        # a persisted profile whose bytes happen to be one of the four
        # kernel presets must land in exactly one of the six states, never a
        # seventh "preset" pseudo-state (design section 4).
        preset_hex = store.PRESET_HEX[0]
        persisted_profile = store.build_profile(
            store.KIND_VSVDB, 'preset one', preset_hex, store.SOURCE_CAPTURED)
        assert persisted_profile['source'] == store.SOURCE_PRESET

        result = state.classify_vsvdb(
            1, bytes.fromhex(preset_hex), preset_hex,
            [persisted_profile], persisted=persisted_profile)
        assert result.state == state.PERSISTED_EFFECTIVE
        assert result.state in state.STATES
        assert result.profile_id == persisted_profile['id']

    def test_match_is_length_aware_not_a_prefix_match(self):
        short_profile = _profile('short', 'eb01')
        result = state.classify_vsvdb(
            255, bytes.fromhex('eb0146d0'), 'eb0146d0', [short_profile])
        assert result.state == state.FOREIGN


# --- EDID layer -------------------------------------------------------------

class TestClassifyEdid:

    def test_persisted_effective(self):
        persisted = _profile('persisted-id', _PROFILE_A['raw'])
        result = state.classify_edid(
            bytes.fromhex(_PROFILE_A['raw']), [_PROFILE_A], persisted=persisted)
        assert result.state == state.PERSISTED_EFFECTIVE
        assert result.profile_id == 'persisted-id'
        assert result.uncertain is False

    def test_persisted_pending_even_when_rawedid_matches_a_different_known_profile(self):
        # no independent params reading exists for EDID (forced_edid can't
        # be read back), so a persisted profile not yet showing in rawedid
        # is always the pending case, never drifted, regardless of what
        # rawedid currently shows.
        persisted = _profile('persisted-id', _PROFILE_A['raw'])
        result = state.classify_edid(
            bytes.fromhex(_PROFILE_B['raw']), [_PROFILE_A, _PROFILE_B], persisted=persisted)
        assert result.state == state.PERSISTED_PENDING
        assert result.profile_id == 'persisted-id'

    def test_persisted_pending_no_rawedid_read_yet(self):
        # pending flavor one: no rawedid read at all yet.
        persisted = _profile('persisted-id', _PROFILE_A['raw'])
        result = state.classify_edid(b'', [_PROFILE_A], persisted=persisted)
        assert result.state == state.PERSISTED_PENDING
        assert result.profile_id == 'persisted-id'

    def test_persisted_pending_unrecognized_rawedid(self):
        # pending flavor two: rawedid already reads something, the
        # between-persist-and-reboot case where it's still the sink's real
        # EDID because the unit hasn't applied the override yet.
        persisted = _profile('persisted-id', _PROFILE_A['raw'])
        result = state.classify_edid(
            bytes.fromhex(_UNKNOWN_HEX), [_PROFILE_A], persisted=persisted)
        assert result.state == state.PERSISTED_PENDING
        assert result.profile_id == 'persisted-id'
        assert result.uncertain is False

    def test_runtime_only(self):
        result = state.classify_edid(bytes.fromhex(_PROFILE_A['raw']), [_PROFILE_A])
        assert result.state == state.RUNTIME_ONLY
        assert result.profile_id == _PROFILE_A['id']
        assert result.uncertain is False

    def test_inactive_no_rawedid_no_persisted(self):
        result = state.classify_edid(b'', [_PROFILE_A])
        assert result.state == state.INACTIVE
        assert result.uncertain is False

    def test_foreign_undecidable_reports_inactive_with_uncertainty_flag(self):
        # the headline EDID-layer difference: forced_edid cannot be read
        # back (finding 8), so bytes that match no stored profile can never
        # be told apart from the sink's own real EDID.
        result = state.classify_edid(bytes.fromhex(_UNKNOWN_HEX), [_PROFILE_A])
        assert result.state == state.INACTIVE
        assert result.profile_id is None
        assert result.uncertain is True

    def test_foreign_state_is_never_reported_for_edid(self):
        scenarios = [
            (b'', [], None),
            (bytes.fromhex(_UNKNOWN_HEX), [_PROFILE_A], None),
            (bytes.fromhex(_PROFILE_A['raw']), [_PROFILE_A], None),
            (bytes.fromhex(_PROFILE_B['raw']), [_PROFILE_A],
             _profile('p', _PROFILE_A['raw'])),
        ]
        for rawedid, profiles, persisted in scenarios:
            result = state.classify_edid(rawedid, profiles, persisted=persisted)
            assert result.state != state.FOREIGN

    def test_match_is_length_aware_not_a_prefix_match(self):
        short_profile = _profile('short', 'eb01')
        result = state.classify_edid(bytes.fromhex('eb0146d0'), [short_profile])
        assert result.state == state.INACTIVE
        assert result.uncertain is True


# --- both layers classified independently -----------------------------------

def test_layers_classify_independently_from_composed_fixtures():
    vsvdb_profiles = [_PROFILE_A]
    edid_profiles = [_profile('edid-a', 'ff00ff00')]
    vsvdb_persisted = _profile('vp', _PROFILE_A['raw'])

    vsvdb_result = state.classify_vsvdb(
        255, bytes.fromhex(_PROFILE_A['raw']), _PROFILE_A['raw'],
        vsvdb_profiles, persisted=vsvdb_persisted)
    edid_result = state.classify_edid(b'', edid_profiles, persisted=None)

    assert vsvdb_result.state == state.PERSISTED_EFFECTIVE
    assert edid_result.state == state.INACTIVE
    assert edid_result.uncertain is False
