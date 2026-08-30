# script.edid.manager

A CoreELEC addon that manages what the box believes about its display: the EDID and the Dolby Vision VSVDB block inside it. It stores named profiles, lets you edit VSVDB fields directly, and applies overrides at runtime or across reboots.

This is for people whose display or AVR misreports its capabilities, calibrators who want to try alternate VSVDB values against a known panel, anyone spoofing Dolby Vision onto an SDR-only display, and headless boots where no display is attached at boot time and the box needs to come up believing in one anyway. Headless boot-apply is marked experimental below; it works on the one device it has been tested on, but a chain-off boot has an open failure mode that is not yet fully understood.

Needs stock CoreELEC 22 on an Amlogic device, using the built-in aml_media kernel module. Requires script.module.sidedata 1.7.0 or later for decoding.

## What it does

Two profile kinds. A VSVDB profile is just the Dolby Vision sink capability block, six to thirty-two bytes. An EDID profile is the whole EDID, up to eight 128-byte blocks.

Profiles come from four sources: capture the currently connected sink's block, start from one of the four Amlogic-defined VSVDB presets, paste hex by hand, or import a file. Importing a full EDID asks whether you want it stored whole or want just the VSVDB extracted out of it, since those are different tools for different jobs.

VSVDB profiles have a field editor: peak and minimum nits, colorimetry, primaries, dimming and interface bits, depending on which VSVDB version the block uses. Editing patches only the bytes a field occupies and leaves everything else untouched, so an edited block stays the same structural version it started as. Peak and minimum nits are quantized: the block only has 32 reachable peak values, so the editor shows the value you asked for next to the value the block actually ends up encoding.

EDID profiles are captured, imported or applied whole. There is no EDID field editor. Editing checksums, block counts and CTA offsets safely is a much bigger job than this addon takes on, and getting it wrong produces a display that mis-modes on every boot.

Applying a profile shows a countdown confirmation dialog first. Only an explicit yes keeps it; a no, or the timer running out with nobody answering, reverts on the spot. EDID profiles get an extra confirmation naming the profile before that, since a forced EDID is a sharper tool than a forced VSVDB block and deserves its own explicit yes.

Persisting a profile survives reboots. A systemd unit installed by the addon reapplies it before Kodi starts, using a pending/trial/trial-acked/confirmed state that only the unit and the addon's own service ever touch, never anything that needs Kodi alive to work. A newly persisted profile gets one trial boot. If that boot never reaches the point of showing the confirmation dialog, meaning Kodi wedged or crashed, the very next boot disarms the profile automatically and comes up on the sink's real EDID with no one touching anything. Answering the dialog with yes on a trial boot is what marks a profile confirmed, after which every future boot applies it without asking.

The main menu also carries a permanent "Revert to display's real EDID" action, separate from any profile. It always works, regardless of what the addon believes is currently applied, and is the answer for an override set outside the addon that the addon has no way to detect.

With the HDMI chain off, a VSVDB runtime apply is refused outright, because applying it goes through a DDC re-read that fails with nothing attached and would flatten the sink's capabilities to fallback instead of applying anything. An EDID apply has no such restriction: forcing a full EDID never touches DDC, so it works with the display off, which is what makes the headless case possible at all.

## Risk

This addon writes directly to `/sys/class/amhdmitx/amhdmitx0/edid` and `/sys/module/aml_media/parameters/force_vsvdb`, undocumented vendor sysfs nodes with no public specification. The kernel does no validation on the EDID path beyond a length check, so the addon does its own structural validation (header, checksums, block count) before anything gets written, and VSVDB blocks are only accepted if CoreELEC's own decoder can parse them back. Even so, the wrong block is the wrong picture: a bad VSVDB gets Dolby Vision wrong, a bad EDID gets the display mode wrong. Use at your own risk.

## Recovery

The safe-mode countdown and the trial-boot backstop are the built-in recovery paths and should catch most bad states without you doing anything. If you still need to get in over SSH:

```
rm /storage/.config/edid-manager/active.*
systemctl disable script.edid.manager.service
```

removes any persisted profile so the next boot comes up clean. To drop an override immediately without waiting for a reboot:

```
echo 0 > /sys/module/aml_media/parameters/force_vsvdb
echo "load 0000000000000000" > /sys/class/amhdmitx/amhdmitx0/edid
```

The second command re-reads the EDID over DDC, so run it with the display on.

## Status

Not released yet. No CoreELEC repo submission, no tagged version. Install from source: copy this repository into `addons/script.edid.manager` on the box and enable it from Kodi's addon browser.

## Prior art

cpm's VSVDB injection work (a patched CE/Kodi fork with a compiled-in Dolby Vision settings UI, described at [discourse.coreelec.org/t/edid-override-injecting-a-dolby-vsvdb-block/51510](https://discourse.coreelec.org/t/edid-override-injecting-a-dolby-vsvdb-block/51510)) reaches the same sysfs surface this addon uses, at a different layer: compiled into the firmware image rather than shipped as an addon on stock CoreELEC.

## License

GPL-2.0-or-later.
