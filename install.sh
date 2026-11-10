#!/usr/bin/env bash
# U1_AFC installer: AFC-Klipper-Add-On on a Snapmaker U1 running PAXX
# extended firmware, replacing AFC-Lite.
#
# Run as root on the printer, from the U1_AFC folder (e.g. /home/lava/U1_AFC):
#     bash /home/lava/U1_AFC/install.sh [--afc-tarball FILE] [--no-restart]
# Safe to run again (updates AFC to AFC_COMMIT, re-applies patches).
#
# Copyright (C) 2026 DIEHARDave
# This file may be distributed under the terms of the GNU GPLv3 license.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LAVA=/home/lava
AFC_DIR="$LAVA/AFC-Klipper-Add-On"
CFG="$LAVA/printer_data/config"
EXT_K="$CFG/extended/klipper"
KLIPPY="$LAVA/klipper/klippy"
MOON="http://127.0.0.1:7125"
AFC_COMMIT="$(tr -d ' \r\n' < "$HERE/AFC_COMMIT")"
BACKUP="/userdata/u1_afc_backup/$(date +%Y%m%d-%H%M%S)"
SPOOLLINK_SRC=/usr/local/share/spoollink/klipper.cfg

TARBALL=""
RESTART=1
while [ $# -gt 0 ]; do
    case "$1" in
        --afc-tarball) TARBALL="$2"; shift 2 ;;
        --no-restart) RESTART=0; shift ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

say() { echo ">> $*"; }
die() { echo "!! $*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "Run as root."
grep -q 'RK_BUILD_INFO="snapmaker' /etc/os-release 2>/dev/null || die "This is not a Snapmaker U1."
[ -d "$KLIPPY/extras" ] || die "Klipper not found at $KLIPPY."
[ -n "$AFC_COMMIT" ] || die "AFC_COMMIT is empty."

state="$(curl -s -m 5 "$MOON/printer/objects/query?print_stats=state" \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"]["status"]["print_stats"]["state"])' 2>/dev/null || echo unknown)"
case "$state" in
    printing|paused) die "Printer is $state; run this with the printer idle." ;;
esac

# 1. Backup ------------------------------------------------------------------
say "Backup to $BACKUP"
mkdir -p "$BACKUP"
cp -a "$KLIPPY/kinematics/extruder.py" "$BACKUP/"
for f in printer.cfg fluidd.cfg; do
    if [ -f "$CFG/$f" ]; then cp -a "$CFG/$f" "$BACKUP/"; fi
done
ls -la "$EXT_K" > "$BACKUP/extended_klipper.txt" 2>&1 || true
if [ -f "$CFG/AFC/AFC.var.unit" ]; then cp -a "$CFG/AFC/AFC.var.unit" "$BACKUP/"; fi
if [ -L "$EXT_K/afc.cfg" ]; then echo "afc-lite tweak was enabled" > "$BACKUP/afc_lite_enabled"; fi

# 2. AFC-Klipper-Add-On at AFC_COMMIT ---------------------------------------
current=""
if [ -f "$AFC_DIR/.u1_afc_commit" ]; then
    current="$(cat "$AFC_DIR/.u1_afc_commit")"
elif [ -f "$AFC_DIR/AFC_COMMIT.txt" ]; then   # early manual install
    current="$(tr -d ' \r\n' < "$AFC_DIR/AFC_COMMIT.txt")"
fi
if [ "$current" != "$AFC_COMMIT" ] || [ -n "$TARBALL" ]; then
    tmp="$(mktemp -d /userdata/u1_afc_dl.XXXXXX)"
    if [ -z "$TARBALL" ]; then
        say "Downloading AFC-Klipper-Add-On $AFC_COMMIT"
        curl -fsSL -m 120 -o "$tmp/afc.tgz" \
            "https://codeload.github.com/AFCProject/AFC-Klipper-Add-On/tar.gz/$AFC_COMMIT" \
            || die "Download failed (no internet?). Pass --afc-tarball FILE."
        TARBALL="$tmp/afc.tgz"
    fi
    mkdir -p "$tmp/x"
    tar -xzf "$TARBALL" -C "$tmp/x"
    src="$(find "$tmp/x" -mindepth 1 -maxdepth 1 -type d | head -1)"
    [ -f "$src/extras/AFC.py" ] || die "Tarball doesn't look like AFC-Klipper-Add-On."
    # Windows checkouts and tarballs can carry CRLF; AFC's shell scripts fail on it.
    find "$src" -type f \( -name '*.sh' -o -name '*.py' -o -name '*.cfg' -o -name '*.txt' \) \
        -exec sed -i 's/\r$//' {} +
    echo "$AFC_COMMIT" > "$src/.u1_afc_commit"
    # Keep the same path: Klipper's extras symlinks point into it.
    rm -rf "$AFC_DIR.old"
    if [ -d "$AFC_DIR" ]; then mv "$AFC_DIR" "$AFC_DIR.old"; fi
    mv "$src" "$AFC_DIR"
    rm -rf "$tmp" "$AFC_DIR.old"
    chown -R lava:lava "$AFC_DIR"
else
    say "AFC-Klipper-Add-On already at $AFC_COMMIT"
fi

# 3. U1_AFC patches to AFC (marker: every patched file contains "U1_AFC") -----
for p in "$HERE"/patches/*.patch; do
    [ -f "$p" ] || continue
    files="$(grep '^+++ b/' "$p" | sed 's|^+++ b/||; s|\t.*||')"
    applied=1
    for f in $files; do grep -q "U1_AFC" "$AFC_DIR/$f" 2>/dev/null || applied=0; done
    if [ "$applied" = 1 ]; then
        say "Patch $(basename "$p") already applied"
    else
        say "Applying $(basename "$p")"
        patch -p1 -d "$AFC_DIR" < "$p" || die "Patch $(basename "$p") failed against AFC $AFC_COMMIT."
    fi
done
chown -R lava:lava "$AFC_DIR"

# 4. Klipper extruder.py: AFC's extruder names aren't "extruderN" ------------
E="$KLIPPY/kinematics/extruder.py"
if grep -q 'raise ValueError("Invalid extruder name")' "$E"; then
    say "Patching extruder.py (invalid extruder name -> warning)"
    sed -i 's|            raise ValueError("Invalid extruder name")|            logging.warning("Extruder index not found for %s" % (extruder_name,))|' "$E"
fi

# 5. AFC-Lite off ------------------------------------------------------------
if [ -e "$EXT_K/afc.cfg" ] || [ -L "$EXT_K/afc.cfg" ]; then
    say "Disabling AFC-Lite tweak"
    rm -f "$EXT_K/afc.cfg"
fi

# 6. SpoolLink: its SET_SPOOL_ID macro clashes with AFC's SET_SPOOL_ID --------
# PAXX's hook re-creates the symlink only when it's missing, so point it at a
# copy without the macro.
mkdir -p "$HERE/generated"
if [ -f "$SPOOLLINK_SRC" ]; then
    awk '/^\[gcode_macro SET_SPOOL_ID\]/{skip=1; next} /^\[/{skip=0} !skip' "$SPOOLLINK_SRC" > "$HERE/generated/spoollink.cfg"
    sed -i '1i # U1_AFC: PAXX spoollink.cfg without SET_SPOOL_ID (AFC registers SET_SPOOL_ID per lane).' "$HERE/generated/spoollink.cfg"
    chown -R lava:lava "$HERE/generated"
    if [ -L "$EXT_K/spoollink.cfg" ]; then
        say "Pointing SpoolLink's Klipper config at U1_AFC's copy"
        ln -sfn "$HERE/generated/spoollink.cfg" "$EXT_K/spoollink.cfg"
        chown -h lava:lava "$EXT_K/spoollink.cfg"
    fi
fi
rm -rf "$LAVA/u1_afc"   # early manual-install location of the same file

# 7. Bridge module --------------------------------------------------------------
say "Linking u1_afc.py into Klipper extras"
ln -sfn "$HERE/extras/u1_afc.py" "$KLIPPY/extras/u1_afc.py"

# 8. AFC's own U1 enable (config, include, extras symlinks; restarts Klipper) --
say "Running AFC's U1 enable"
bash "$AFC_DIR/include/U1_extended_firmware_install.sh" -e

if [ ! -f "$CFG/AFC/u1_afc.cfg" ]; then
    cp "$HERE/config/u1_afc.cfg" "$CFG/AFC/u1_afc.cfg"
    chown lava:lava "$CFG/AFC/u1_afc.cfg"
fi
chown -R lava:lava "$HERE"

# 9. Restart and report ---------------------------------------------------------
if [ "$RESTART" = 1 ]; then
    say "Restarting Klipper"
    /etc/init.d/S60klipper restart >/dev/null 2>&1 || true
    for _ in $(seq 1 60); do
        curl -s -m 3 "$MOON/printer/info" 2>/dev/null | grep -q '"state": "ready"' && break
        sleep 2
    done
    curl -s -m 5 "$MOON/printer/info" | python3 -c 'import sys,json; r=json.load(sys.stdin)["result"]; print(">> Klipper:", r["state"], "-", r["state_message"].strip()[:300])' || true
fi
say "Done. Backup: $BACKUP. Check lanes with U1_AFC_STATUS in the console."
