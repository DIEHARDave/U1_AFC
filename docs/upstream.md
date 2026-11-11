# Upstream AFC issues found on the U1

Problems in AFC-Klipper-Add-On that U1_AFC currently works around. Each is
meant to go upstream (AFCProject/AFC-Klipper-Add-On) as an issue or PR; once
it is fixed there, the workaround here can go.

| Issue | Upstream status | U1_AFC workaround |
|---|---|---|
| [Head runout ignored, lanes never reach `TOOLED`](upstream-issue-u1-runout.md) | Draft, not reported | `fix_tool_runout` (lane is `TOOLED` while the runout handler runs) |
| No pause when the side feeder runs empty and there is no runout lane (stock U1 pauses there) | Not reported; may be a feature request | `pause_on_feeder_runout` |
| U1 template has `auto_home: False`, so a tool change before homing fails | [PR #902](https://github.com/AFCProject/AFC-Klipper-Add-On/pull/902) (open) | `install.sh` sets `auto_home: True` |
| AFC writes filament info to `print_task_config` with sub-type `NONE`, which overrides SpoolLink and blocks Orca's filament mapping | Not reported | `spool_info: spoollink` skips AFC's write when SpoolLink owns the head |
| `SDCARD_PRINT_FILE_WITH_PARAMETERS` `MAP_TABLE` (Orca / screen mapping) not applied to AFC's tool map | Not reported; may be a feature request | `follow_print_map` |

All of these were found and tested on one U1 (PAXX stable, AFC `5ea84ea`);
the cause lines in the runout draft are checked against DEV `7324836`.
