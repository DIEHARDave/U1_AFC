#!/usr/bin/env bash
# U1_AFC uninstaller: back to PAXX's AFC-Lite (or no AFC).
#
# Run as root on the printer:
#     bash /home/lava/U1_AFC/uninstall.sh [--no-afc-lite] [--remove-afc]
#   --no-afc-lite  don't re-enable the AFC-Lite tweak
#   --remove-afc   also delete /home/lava/AFC-Klipper-Add-On
# AFC's config folder (printer_data/config/AFC, lane state) is kept.
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
SPOOLLINK_SRC=/usr/local/share/spoollink/klipper.cfg
AFC_LITE_TWEAK=/usr/local/share/firmware-config/tweaks/klipper/afc.cfg

AFC_LITE=1
REMOVE_AFC=0
while [ $# -gt 0 ]; do
    case "$1" in
        --no-afc-lite) AFC_LITE=0; shift ;;
        --remove-afc) REMOVE_AFC=1; shift ;;
        -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

say() { echo ">> $*"; }
die() { echo "!! $*" >&2; exit 1; }
# printer.cfg and fluidd.cfg belong to lava, mode 644, on a stock U1.
fix_config_perms() {
    for f in "$CFG/printer.cfg" "$CFG/fluidd.cfg"; do
        if [ -f "$f" ] && [ ! -L "$f" ]; then chown lava:lava "$f"; chmod 644 "$f"; fi
    done
}

[ "$(id -u)" = 0 ] || die "Run as root."
state="$(curl -s -m 5 "$MOON/printer/objects/query?print_stats=state" \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"]["status"]["print_stats"]["state"])' 2>/dev/null || echo unknown)"
case "$state" in
    printing|paused) die "Printer is $state; run this with the printer idle." ;;
esac

if [ -f "$AFC_DIR/include/U1_extended_firmware_install.sh" ]; then
    say "Running AFC's U1 disable (include, symlinks, AFC-Lite modules back)"
    bash "$AFC_DIR/include/U1_extended_firmware_install.sh" -d || true
fi
# AFC's installer runs as root and can leave the files it rewrites or moves
# back as root-only (0600): Klipper (user lava) then fails with "Unable to open
# config file" or "Permission denied: .../extras/AFC.py". Stock owners/modes:
fix_config_perms
for f in AFC.py AFC_lane.py AFC_unit.py; do
    p="$KLIPPY/extras/$f"
    if [ -f "$p" ] && [ ! -L "$p" ]; then chown root:root "$p"; chmod 644 "$p"; fi
done

rm -f "$KLIPPY/extras/u1_afc.py"
rm -f "$CFG/AFC/u1_afc.cfg"

if [ -L "$EXT_K/spoollink.cfg" ] && [ -f "$SPOOLLINK_SRC" ]; then
    say "Restoring SpoolLink's Klipper config"
    ln -sfn "$SPOOLLINK_SRC" "$EXT_K/spoollink.cfg"
    chown -h lava:lava "$EXT_K/spoollink.cfg"
fi

E="$KLIPPY/kinematics/extruder.py"
if grep -q 'logging.warning("Extruder index not found for %s" % (extruder_name,))' "$E"; then
    say "Restoring extruder.py"
    sed -i 's|            logging.warning("Extruder index not found for %s" % (extruder_name,))|            raise ValueError("Invalid extruder name")|' "$E"
fi

if [ "$AFC_LITE" = 1 ] && [ -f "$AFC_LITE_TWEAK" ]; then
    say "Re-enabling AFC-Lite tweak"
    ln -sfn "$AFC_LITE_TWEAK" "$EXT_K/afc.cfg"
    chown -h lava:lava "$EXT_K/afc.cfg"
fi

if [ "$REMOVE_AFC" = 1 ]; then
    say "Removing $AFC_DIR"
    rm -rf "$AFC_DIR"
fi

say "Restarting Klipper"
/etc/init.d/S60klipper restart >/dev/null 2>&1 || true
for _ in $(seq 1 60); do
    curl -s -m 3 "$MOON/printer/info" 2>/dev/null | grep -q '"state": "ready"' && break
    sleep 2
done
curl -s -m 5 "$MOON/printer/info" | python3 -c 'import sys,json; r=json.load(sys.stdin)["result"]; print(">> Klipper:", r["state"], "-", r["state_message"].strip()[:300])' || true
say "Done. U1_AFC folder $HERE left in place; delete it if you don't need it."
