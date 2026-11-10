# U1_AFC

The full [AFC-Klipper-Add-On](https://github.com/AFCProject/AFC-Klipper-Add-On)
(the AFC Project) on a **Snapmaker U1** running
[PAXX extended firmware](https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware),
in place of PAXX's AFC-Lite status stub.

**Experimental.** Tested on one U1 (PAXX stable, AFC `5ea84ea`) since
2026-11-10. Reports welcome in Issues.

Upstream AFC already supports the U1 (its four heads as a toolchanger, the
side feeders, Snapmaker's pick/park, `print_task_config`). PAXX ships it only
in the rolling `extended-afc` image. U1_AFC puts it on the stable image and
adds what was missing in testing:

- **`u1_afc` bridge** (Klipper module). AFC follows unloads done on the U1,
  but not loads or spool changes. The bridge marks a lane loaded when its
  feeder finishes a load on the U1, and takes the spool (RFID tag,
  SpoolLink, U1 screen), or the material and color, from the U1's
  `print_task_config`. Its first pass after a fresh install brings the
  loaded heads and their spools into AFC. Lanes without a Spoolman spool get
  a 1000 g default so the AFC panels don't draw an empty spool.
- **Tool changes before Z is homed.** The U1's start gcode runs `G28 X Y`
  and then `T0`; AFC refuses tool changes until X, Y and Z are homed, and the
  print then runs on whichever head was picked last. With
  [patches/0001](patches/0001-afc-u1-toolchange-before-z-home.patch) AFC hands
  `T<n>` to Snapmaker's own `_T<n>` (for the head that tool is mapped to)
  while only X/Y are homed.
- **PAXX clashes.** Disables the AFC-Lite tweak, points SpoolLink's Klipper
  config at a copy without its `SET_SPOOL_ID` macro (AFC registers
  `SET_SPOOL_ID` per lane), and turns Klipper's "Invalid extruder name"
  error into a warning (the same fix as PAXX's `afc` mod).

## Install

As root on the printer, idle. The printer downloads AFC from GitHub at the
commit in [AFC_COMMIT](AFC_COMMIT) (or pass `--afc-tarball FILE`).

```sh
# from a PC: copy this folder to the printer
tar -czf - --exclude=.git U1_AFC | ssh root@<u1-ip> 'tar -xzf - -C /home/lava'
# on the printer
bash /home/lava/U1_AFC/install.sh
```

Klipper restarts twice (AFC's own enable script, then U1_AFC). Then check
the lanes with `AFC_BRIDGE_STATUS` in the console. Run `install.sh` again after
a firmware update (which likely undoes the changes) or to move to a new
`AFC_COMMIT`. A backup of the files it touches goes to
`/userdata/u1_afc_backup/<date>/`.

Lanes are `e0`-`e3` (AFC-Lite: `E0`-`E3`). Restart HelixScreen once if you
use it, so it drops the old names.

## Uninstall

```sh
bash /home/lava/U1_AFC/uninstall.sh            # back to AFC-Lite
bash /home/lava/U1_AFC/uninstall.sh --no-afc-lite --remove-afc
```

## Bridge options

`printer_data/config/AFC/u1_afc.cfg` (all optional):

| Option | Default | |
|---|---|---|
| `sync_loads` | True | Mark a lane loaded when its feeder finishes a load on the U1 |
| `sync_spools` | True | Take spool / material / color from `print_task_config` |
| `default_weight` | 1000 | Weight (g) for a loaded lane without a Spoolman spool |
| `poll_interval` | 2 | Seconds between checks (only while AFC is idle and not printing) |
| `start_delay` | 10 | Seconds after Klipper start before the first check |

Commands: `AFC_BRIDGE_STATUS` (each lane: AFC vs U1, what the bridge would
change), `AFC_BRIDGE_SYNC` (check now).

Rules: the U1 wins when *its* spool changes (a scan, the screen, SpoolLink);
a spool you set in the AFC panel stays until the U1's changes again.

## Tested (2026-11-10, one U1)

- PREP, idle `T0`-`T3` (~5 s, pick/park, no feed), `LANE_UNLOAD` + reload
- 2-color print (T0 then T3 at layer 29): mid-print change 5.2 s with
  preheat, usage per lane matches the slicer
- Print start with `G28 X Y` + `T0` (patch 0001)
- Reboot: everything persists (`/oem/.debug`)
- U1 screen unload/load with an RFID-tagged spool (by hand, before the
  bridge existed)

Not yet: the bridge on hardware, runout, a firmware update, `install.sh` on
a printer that never had AFC.

## Known issues

- **Two writers of the active spool.** SpoolLink still sets Moonraker's
  active spool on head changes, next to AFC; during a change it briefly
  set the old head's spool. Ends right, but usage could land on the wrong
  spool for a moment.
- **SpoolLink's tag binding is off.** Without its `SET_SPOOL_ID` macro,
  assigning a spool no longer binds the scanned card UID to it in Spoolman.
- **AFC's unload is shallow on the U1.** `LANE_UNLOAD` retracts 60 mm out
  of the hotend; the filament stays in the head and the U1 feeder still
  reports it loaded. The AFC panel's unload sends `TOOL_UNLOAD`, which needs
  the printer homed. Use the U1's own unload for spool swaps.
- AFC's writes to `print_task_config` set the sub-type to `NONE`.
- `git` isn't on the U1, so AFC's version shows without a hash.

## Layout

| Path | |
|---|---|
| `install.sh`, `uninstall.sh` | Run on the printer as root |
| `extras/u1_afc.py` | Bridge module, symlinked into `klippy/extras` |
| `config/u1_afc.cfg` | Copied to `printer_data/config/AFC/` |
| `patches/` | Applied to the AFC checkout (marker: `U1_AFC`) |
| `AFC_COMMIT` | AFC commit the patches are made for |
| `tests/` | `python tests/test_plan.py` (bridge decisions, no Klipper) |

GPLv3, like AFC.
