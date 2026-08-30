import os
import re
import sys
import types

import pytest

_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, '..', 'resources', 'lib'))

_PO_PATH = os.path.join(
    _HERE, '..', 'resources', 'language', 'resource.language.en_gb', 'strings.po')
_SETTINGS_PATH = os.path.join(_HERE, '..', 'resources', 'settings.xml')


def _po_strings():
    with open(_PO_PATH, 'r', encoding='utf-8') as handle:
        text = handle.read()
    return dict(
        (int(sid), msgid)
        for sid, msgid in re.findall(r'msgctxt "#(\d+)"\nmsgid "(.*)"\n', text))


_STRINGS = _po_strings()


class _Addon(object):
    def getLocalizedString(self, string_id):
        return _STRINGS.get(string_id, '')


# ui.py imports xbmcgui and xbmcaddon at module level; only the formatting
# helpers are exercised here, so a bare Addon stub is all they need
sys.modules.setdefault('xbmcgui', types.ModuleType('xbmcgui'))
_xbmcaddon = types.ModuleType('xbmcaddon')
_xbmcaddon.Addon = _Addon
sys.modules.setdefault('xbmcaddon', _xbmcaddon)

import ui  # noqa: E402


def _string_ids():
    return dict(
        (getattr(ui, name), name) for name in dir(ui) if name.startswith('S_'))


def test_every_string_constant_has_a_msgid():
    missing = sorted(sid for sid in _string_ids() if sid not in _STRINGS)

    assert missing == []


def test_every_msgid_is_referenced_by_a_constant():
    orphaned = sorted(sid for sid in _STRINGS if sid not in _string_ids())

    assert orphaned == []


def test_settings_labels_resolve():
    with open(_SETTINGS_PATH, 'r', encoding='utf-8') as handle:
        settings = handle.read()
    labels = [int(value) for value in re.findall(r'label="(\d+)"', settings)]

    assert labels
    assert [label for label in labels if label not in _STRINGS] == []


def test_binding_import_labels_are_verbatim():
    assert _STRINGS[ui.S_IMPORT_USE_FULL_EDID] == 'Use as a full EDID profile.'
    assert _STRINGS[ui.S_IMPORT_EXTRACT_VSVDB] == 'Extract just the VSVDB.'
    assert _STRINGS[ui.S_IMPORT_CONFIRM_FULL_EDID] == (
        'Full EDID profile from %s; applying this replaces what the box believes'
        ' its display is.')
    assert _STRINGS[ui.S_IMPORT_CONFIRM_EXTRACTED] == (
        'VSVDB extracted from %s; the rest of the EDID is not used.')


def test_preset_4_is_labeled_for_what_it_is():
    assert _STRINGS[ui.S_PRESET_4_LABEL] == 'HDR10+ advertisement (not Dolby Vision)'


@pytest.mark.parametrize('value,expected', [
    (1037.1000, '1037.1'),
    (0.0, '0'),
    (1000, '1000'),
    (None, '-'),
    ('v2', 'v2'),
    (True, 'Yes'),
    (False, 'No'),
])
def test_format_value(value, expected):
    assert ui.format_value(value) == expected


def test_format_value_of_a_primaries_dict():
    assert ui.format_value({'gy': 0.6, 'gx': 0.3}) == 'gx 0.3, gy 0.6'


def test_format_requested_vs_achieved():
    line = ui.format_requested_vs_achieved(
        _STRINGS[ui.S_REQUESTED_VS_ACHIEVED], 1000, 1037.1)

    assert line == 'Requested 1000, applied 1037.1'
