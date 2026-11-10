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
- **One writer of the active spool.** AFC and SpoolLink both set
  Moonraker's active spool, and raced on every tool change (SpoolLink
  answered AFC's write with the old head's spool, then corrected). The
  bridge skips AFC's writes while SpoolLink runs; SpoolLink sets the spool
  of the head on the carriage, which the bridge keeps equal to the AFC lane.
- **Tool changes before Z is homed.** The U1's start gcode runs `G28 X Y`
  and then `T0`; AFC refuses tool changes until X, Y and Z are homed (without
  pausing), and the print then runs on whichever head was picked last.
  `install.sh` sets `auto_home: True` in `AFC.cfg`, so AFC homes first. The
  start takes ~8 s longer. A value you set yourself is kept.
- **Filament-to-head mapping from Snapmaker Orca / the U1 screen.** They
  start prints with a `MAP_TABLE` (e.g. filament 1 on head 3), which only
  Snapmaker's own `T<n>` reads. For such a print the bridge points AFC's tool
  map at the chosen heads, and puts it back when the print ends. The AFC
  panels keep showing the lanes' own `T<n>`.
- **One writer of the heads' filament info.** AFC wrote Spoolman's raw
  material (`PLA+`) and sub-type `NONE` over SpoolLink's U1 values (`PLA`)
  after every Klipper restart, and Snapmaker Orca then refused to map a PLA
  filament to that head. SpoolLink now writes it for heads with a spool.
- **PAXX clashes.** Disables the AFC-Lite tweak, points SpoolLink's Klipper
  config at a copy without its `SET_SPOOL_ID` macro (AFC registers
  `SET_SPOOL_ID` per lane; the bridge hands spools set in AFC to SpoolLink,
  so tags still get bound), and turns Klipper's "Invalid extruder name"
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
`AFC_COMMIT` (AFC is downloaded again when the commit or the patch set
changes). A backup of the files it touches goes to
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
| `settle_time` | 5 | Seconds the U1's values for a head must hold still before the bridge acts (a tag scan reports "no spool" for a moment) |
| `active_spool` | spoollink | Who sets Moonraker's active spool: `spoollink` (AFC's writes skipped while SpoolLink runs) or `afc` |
| `follow_print_map` | True | Prints started from Snapmaker Orca or the U1 screen: send each filament to the head chosen there (`MAP_TABLE`), for that print only |
| `spool_info` | spoollink | Who writes a spool's type, sub-type, vendor and color into `print_task_config`: `spoollink` (AFC skipped for heads with a Spoolman spool) or `afc` |

Commands: `AFC_BRIDGE_STATUS` (each lane: AFC vs U1, what the bridge would
change), `AFC_BRIDGE_SYNC` (check now).

Rules: the U1 wins when *its* spool changes (a scan, the screen, SpoolLink);
a spool you set in the AFC panel stays until the U1's changes again.

## Tested (2026-11-10, one U1)

- PREP, idle `T0`-`T3` (~5 s, pick/park, no feed), `LANE_UNLOAD` + reload
- 2-color print (T0 then T3 at layer 29): mid-print change 5.2 s with
  preheat, usage per lane matches the slicer
- Print start with `G28 X Y` + `T0` from head 4, unhomed (`auto_home`):
  full home, then head 1; the whole 2-color print completed
- 4-color print sent from Snapmaker Orca (cloud/LAN start,
  `SDCARD_PRINT_FILE_WITH_PARAMETERS`) with Extrusion Flow Calibration on:
  calibration ran on all four heads (~3.5 min each, via Snapmaker's `_T<n>`),
  6 tool changes at 4.7-5.5 s, one active-spool write each, completed.
  Orca's preprocessing dialog stays at 100% after the start (close it; the
  Device tab works). Not checked yet whether that happens without AFC.
- Orca mapping filament 1 (and 3) to head 3: `T0->e2` for that print, head 3
  printed both, AFC's map restored at the end
- Reboot: everything persists (`/oem/.debug`)
- U1 screen unload/load with an RFID-tagged spool (by hand, before the
  bridge existed)
- `install.sh` over an existing (manual) install; bridge migration of a
  lane AFC had forgotten (spool 74 imported and marked loaded, no movement)

- Bridge with a real U1-screen load (unknown Creality tag): lane loaded,
  filament copied, 1000 g; spool set in AFC handed to SpoolLink, tag bound
  in Spoolman, the next scan resolved to it on its own
- Active spool: T3→T1→T0→T3 with one SpoolLink write per change, no flip
- Settle time: a 4 s "no spool" gap (tag cleared, rescanned) left the lane
  alone; a lasting change was applied after ~7 s, the rescan ~6 s later

Not yet: runout, a firmware update, `install.sh` on a printer that never had
AFC.

## Known issues

- **AFC's unload is shallow on the U1.** `LANE_UNLOAD` retracts 60 mm out
  of the hotend; the filament stays in the head and the U1 feeder still
  reports it loaded. The AFC panel's unload sends `TOOL_UNLOAD`, which needs
  the printer homed. Use the U1's own unload for spool swaps.
- The U1 keeps RFID reads in memory only: after a Klipper restart a head
  has no tag until it is scanned again (`FILAMENT_DT_UPDATE CHANNEL=<n>`, or
  a load). Assigning a spool then can't bind the tag.
- AFC's writes to `print_task_config` set the sub-type to `NONE`.
- `git` isn't on the U1, so AFC's version shows without a hash.

## Layout

| Path | |
|---|---|
| `install.sh`, `uninstall.sh` | Run on the printer as root |
| `extras/u1_afc.py` | Bridge module, symlinked into `klippy/extras` |
| `config/u1_afc.cfg` | Copied to `printer_data/config/AFC/` |
| `patches/` | Optional `*.patch` files applied to the fresh AFC tree (none at the moment) |
| `AFC_COMMIT` | AFC commit U1_AFC is tested with |
| `tests/` | `python tests/test_plan.py` (bridge decisions, no Klipper) |

GPLv3, like AFC.
