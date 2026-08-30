# Only file in this addon that touches sysfs. Every path below is
# overridable by an environment variable, resolved fresh on each call, so
# tests can point the module at a fake tree without patching anything.
#
# Substrate contract this module encodes, source-traced against
# common_drivers 1db67e8e (~/ce22-docs/vsvdb-manager-design.md, findings 3,
# 5, 7, 8):
#   force_vsvdb / vsvdb_data live under /sys/module/aml_media/parameters/.
#   vsvdb_data reads back as a decimal comma list (param_get_byte, "%hhu")
#   but only accepts 0x-prefixed hex on write (kstrtou8); the array is
#   capped at 32 bytes, the largest legal VSVDB.
#   hpd_state and rawedid live under /sys/class/amhdmitx/amhdmitx0/, along
#   with edid, which takes "load <hex>" to force a whole EDID with no DDC
#   read at all and no validation of its own. The kernel accepts hex
#   strings of length 256*i+1 (i in 1..8, counting the trailing newline),
#   i.e. 128 to 1024 raw bytes. "load 0000000000000000" is the sentinel
#   that reverts to a DDC read; the check is a prefix compare, so any real
#   EDID (header 00ffffffffffff00) can never collide with it, and this
#   module refuses a write that would anyway.
#   forced_edid itself is not readable from userspace; there is no getter
#   to poll here.

import os

_FORCE_VSVDB_PATH = '/sys/module/aml_media/parameters/force_vsvdb'
_VSVDB_DATA_PATH = '/sys/module/aml_media/parameters/vsvdb_data'
_HPD_STATE_PATH = '/sys/class/amhdmitx/amhdmitx0/hpd_state'
_RAWEDID_PATH = '/sys/class/amhdmitx/amhdmitx0/rawedid'
_EDID_PATH = '/sys/class/amhdmitx/amhdmitx0/edid'

_VSVDB_DATA_MAX_BYTES = 32
_EDID_BLOCK_SIZE = 128
_EDID_MAX_BYTES = 1024
_EDID_REVERT_SENTINEL = 'load 0000000000000000\n'


def _path(env_var, default):
    return os.environ.get(env_var, default)


def _read_text(path):
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        return f.read()


def _write_text(path, text):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def read_force_vsvdb():
    path = _path('EDIDMGR_FORCE_VSVDB_PATH', _FORCE_VSVDB_PATH)
    return int(_read_text(path).strip())


def write_force_vsvdb(value):
    path = _path('EDIDMGR_FORCE_VSVDB_PATH', _FORCE_VSVDB_PATH)
    _write_text(path, str(int(value)))


def read_vsvdb_data():
    path = _path('EDIDMGR_VSVDB_DATA_PATH', _VSVDB_DATA_PATH)
    contents = _read_text(path).strip()
    if not contents:
        return b''
    return bytes(int(v) & 0xff for v in contents.split(','))


def write_vsvdb_data(data):
    if len(data) > _VSVDB_DATA_MAX_BYTES:
        raise ValueError('vsvdb_data write of %d bytes exceeds the 32-byte kernel array' % len(data))

    path = _path('EDIDMGR_VSVDB_DATA_PATH', _VSVDB_DATA_PATH)
    _write_text(path, ','.join('0x%02x' % b for b in data))


def read_hpd_state():
    path = _path('EDIDMGR_HPD_STATE_PATH', _HPD_STATE_PATH)
    return int(_read_text(path).strip())


def read_rawedid():
    path = _path('EDIDMGR_RAWEDID_PATH', _RAWEDID_PATH)
    with open(path, 'rb') as f:
        return f.read()


def _log_sidedata_missing():
    # script.module.sidedata is a hard requires in addon.xml, so an import
    # failure is a broken install rather than an optional feature being off
    try:
        import xbmc
    except ImportError:
        return
    xbmc.log('script.edid.manager: script.module.sidedata is not importable,'
             ' the effective VSVDB cannot be read', xbmc.LOGERROR)


def read_effective_vsvdb_hex():
    try:
        import sidedata
    except ImportError:
        _log_sidedata_missing()
        return None
    return sidedata.read_vsvdb_raw()


def _check_edid_bytes(edid):
    if len(edid) == 0 or len(edid) % _EDID_BLOCK_SIZE != 0 or len(edid) > _EDID_MAX_BYTES:
        raise ValueError('EDID length %d is not a 128-byte multiple in 128..1024' % len(edid))
    if edid[:8] == b'\x00' * 8:
        raise ValueError('EDID starts with 8 zero bytes, which collides with the revert sentinel')


def write_edid(edid):
    _check_edid_bytes(edid)

    import edid as edid_module
    edid_module.validate_edid(bytes(edid))

    path = _path('EDIDMGR_EDID_PATH', _EDID_PATH)
    _write_text(path, 'load %s\n' % edid.hex())


def revert_edid():
    path = _path('EDIDMGR_EDID_PATH', _EDID_PATH)
    _write_text(path, _EDID_REVERT_SENTINEL)


# EDID first, VSVDB params second: a forced EDID parses immediately with no
# DDC read, and force_vsvdb/vsvdb_data layer onto that same parse (finding
# 7). Either half may be omitted to apply just one layer.
def compose_apply(edid=None, vsvdb_data=None, force_vsvdb=None):
    if edid is not None:
        write_edid(edid)
    if vsvdb_data is not None:
        write_vsvdb_data(vsvdb_data)
    if force_vsvdb is not None:
        write_force_vsvdb(force_vsvdb)
