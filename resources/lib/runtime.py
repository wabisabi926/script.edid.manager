# Runtime apply and revert, composed over backend.py. Separate from
# backend.py because it is composition rather than sysfs access, and separate
# from default.py because service.py has to revert a declined boot trial
# without importing anything that talks to xbmcgui. No Kodi import here.
#
# See ~/ce22-docs/vsvdb-manager-design.md sections 3, 4 and 5.

import backend

# 255 selects the custom bytes in vsvdb_data. force_vsvdb 1-4 would memcpy
# the kernel's own preset array over vsvdb_data at every parse (design
# finding 4), so a preset-sourced profile applies as custom bytes like any
# other and the store stays the only record of what was set.
FORCE_CUSTOM = 255


class ChainOffError(RuntimeError):
    pass


def hpd_state():
    try:
        return backend.read_hpd_state()
    except (OSError, ValueError):
        return None


def reparse(forced_edid_hex=None):
    """Trigger the EDID parse the VSVDB override rides on. With one of our
    own EDID profiles in force, re-loading its bytes is the trigger, because
    the sentinel would drop the forced EDID and go back to a DDC read
    (design finding 7).
    """
    if forced_edid_hex:
        backend.write_edid(bytes.fromhex(forced_edid_hex))
    else:
        backend.revert_edid()


def write_vsvdb_params(raw_hex):
    """The params without the re-parse, and without the HPD gate. This is the
    chain-off persist path from design section 5: the override is staged for
    the hotplug parse that does come, which is what makes persisted-pending
    reachable at all (design section 4). Never call it with the chain on;
    apply_vsvdb is that path.
    """
    backend.write_vsvdb_data(bytes.fromhex(raw_hex))
    backend.write_force_vsvdb(FORCE_CUSTOM)


def apply_vsvdb(raw_hex, forced_edid_hex=None):
    """Refused with the chain off: the re-parse would take the invalid-EDID
    early return, never reach the force_vsvdb branch, and flatten dv_cap to
    fallback (design finding 3).
    """
    if hpd_state() == 0:
        raise ChainOffError('hpd_state reads 0')

    write_vsvdb_params(raw_hex)
    reparse(forced_edid_hex)


def apply_edid(raw_hex):
    backend.write_edid(bytes.fromhex(raw_hex))


# force_vsvdb is written before the re-parse that consumes it, in both
# directions: the override is applied inside the CTA walk, so a parse
# triggered ahead of the write would carry the old value and the new one
# would sit until the next hotplug.
def revert_vsvdb(forced_edid_hex=None):
    backend.write_force_vsvdb(0)
    reparse(forced_edid_hex)


def revert_edid():
    backend.revert_edid()


def revert_all():
    """The main menu's unconditional escape hatch (design section 4). Every
    step is attempted regardless of what the classifier believed and
    regardless of the previous step failing, and the failures are returned
    rather than raised: this has to do as much as it can in states nobody,
    the addon included, can name.
    """
    errors = []
    for step in (lambda: backend.write_force_vsvdb(0), backend.revert_edid):
        try:
            step()
        except Exception as error:
            errors.append(str(error))
    return errors
