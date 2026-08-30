#!/bin/sh
# Boot-time half of the pending/trial/trial-acked/confirmed machine
# (~/ce22-docs/vsvdb-manager-design.md sections 3 and 8), run by
# system.d/script.edid.manager.service before kodi.service.
#
# This is the shell mirror of resources/lib/persist.py's boot_decision: the
# state semantics live in both files and neither may change alone. Shell
# rather than python because the unit runs before Kodi and no interpreter is
# guaranteed that early.
#
# Every step is best-effort and the script always exits 0. A box that cannot
# apply its profile still has to boot.

DIR=/storage/.config/edid-manager
PARAMS_DIR=/sys/module/aml_media/parameters
HDMI_DIR=/sys/class/amhdmitx/amhdmitx0

# the unit env stays clean in production; only the test harness redirects
# these, and it has to say so explicitly
if [ "$EDIDMGR_TEST" = "1" ]; then
    DIR="${EDIDMGR_PERSIST_DIR:-$DIR}"
    PARAMS_DIR="${EDIDMGR_PARAMS_DIR:-$PARAMS_DIR}"
    HDMI_DIR="${EDIDMGR_HDMI_DIR:-$HDMI_DIR}"
fi

PROFILE_FILE="$DIR/active.profile"
STATE_FILE="$DIR/active.state"
DISARMED_FILE="$DIR/active.disarmed"

# 255 selects the custom bytes in vsvdb_data. Presets 1-4 would memcpy their
# own array over vsvdb_data at every parse (design finding 4), so a
# preset-sourced profile applies as custom bytes like any other.
FORCE_CUSTOM=255

# any load string starting with eight zero bytes reverts instead of loading
# (design finding 7); the kernel tests it as a 16-char prefix compare
EDID_SENTINEL=0000000000000000

field() {
    sed -n "s/^$1=//p" "$PROFILE_FILE" 2>/dev/null | head -n 1
}

write_state() {
    printf '%s\n' "$1" > "$STATE_FILE.tmp" 2>/dev/null && \
        mv -f "$STATE_FILE.tmp" "$STATE_FILE" 2>/dev/null
    return 0
}

disarm() {
    rm -f "$STATE_FILE" 2>/dev/null
    printf 'the previous boot applied this profile and never showed the confirmation dialog\n' \
        > "$DISARMED_FILE" 2>/dev/null
    return 0
}

apply_edid() {
    [ -n "$1" ] || return 0
    printf 'load %s\n' "$1" > "$HDMI_DIR/edid" 2>/dev/null
    return 0
}

apply_vsvdb() {
    [ -n "$1" ] || return 0
    list=$(printf '%s' "$1" | sed 's/../0x&,/g; s/,$//' 2>/dev/null)
    [ -n "$list" ] || return 0
    printf '%s' "$list" > "$PARAMS_DIR/vsvdb_data" 2>/dev/null
    printf '%s' "$FORCE_CUSTOM" > "$PARAMS_DIR/force_vsvdb" 2>/dev/null
    return 0
}

# force_vsvdb is consumed inside the EDID parse (design finding 3), so params
# written on their own never reach dv_cap and the service's trial check would
# time out and disarm a working profile at the next boot. Loading the
# sentinel forces the DDC re-read that carries them. With hpd_state at 0 that
# re-read fails validation and flattens dv_cap instead, so skip it and let the
# next real hotplug do the parse. DEVICE-CONFIRM in the smoke pass: this is
# the one step here that is source-reasoned rather than observed.
reparse_for_params() {
    [ "$(cat "$HDMI_DIR/hpd_state" 2>/dev/null)" = "1" ] || return 0
    printf 'load %s\n' "$EDID_SENTINEL" > "$HDMI_DIR/edid" 2>/dev/null
    return 0
}

# EDID first, VSVDB second: the forced EDID parses on the write and
# force_vsvdb layers onto that same parse (design finding 7).
apply_profile() {
    edid_raw=$(field edid_raw)
    vsvdb_raw=$(field vsvdb_raw)

    apply_edid "$edid_raw"
    apply_vsvdb "$vsvdb_raw"
    if [ -z "$edid_raw" ] && [ -n "$vsvdb_raw" ]; then
        reparse_for_params
    fi
    return 0
}

[ -f "$STATE_FILE" ] || exit 0

state=$(tr -d ' \t\r\n' < "$STATE_FILE" 2>/dev/null)

case "$state" in
    confirmed)
        apply_profile
        ;;
    pending)
        # trial before the apply, never after: a crash mid-apply would
        # otherwise leave pending behind and re-apply forever
        write_state trial
        apply_profile
        ;;
    trial-acked)
        apply_profile
        ;;
    *)
        disarm
        ;;
esac

exit 0
