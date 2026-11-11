> **Status:** draft, not reported upstream yet. U1_AFC works around it
> (`fix_tool_runout`, on by default). See [upstream.md](upstream.md).

**Title:** Snapmaker U1: head runout is ignored (no pause) because lanes never reach TOOLED

**Summary**

On a Snapmaker U1 with AFC installed, a filament runout during a print is
detected by the head's `filament_motion_sensor`, but nothing pauses the print.
The print continues without filament until it ends.

**Setup**

- Snapmaker U1, PAXX extended firmware (stable), AFC `5ea84ea` (main). The code
  below is unchanged on DEV `7324836`.
- Stock U1 AFC config: `AFC_extruder e0`-`e3` with `filament_sensor_name`,
  standalone toolheads, no `runout_lane` set.

**Steps**

1. Start a print on a head.
2. Cut the filament between the spool and the side feeder.
3. Let the cut end reach the head.

**Expected:** the print pauses with "Runout triggered for lane eN", which is what
`_perform_pause_runout()` would do.

**Actual:** klippy.log shows

```
Filament Sensor e3_filament: remove event detected
Filament Sensor e3_filament: runout event detected
```

and AFC.log shows only

```
AFC_extruder.py:tool_start_callback() - INFO: Cannot trigger auto load/unload when toolhead is actively printing
```

The print keeps going.

**Cause**

1. `AFC_extruder.py` (DEV L335) takes over the motion sensor's `runout_helper`.
   It sets `runout_pause = False` and replaces `_runout_event_handler` with
   `handle_start_runout`, so Snapmaker's own pause is off.
2. `handle_start_runout` → `_handle_toolhead_sensor_runout` (L562) →
   `lane.handle_toolhead_runout()` (AFC_lane.py L1852) returns early unless
   `_is_normal_printing_state()` (L1843) is true. That needs
   `lane.status in (TOOLED, LOADED)`.
3. On the U1 the lane on the active head is always `NONE`. A U1 tool change
   only swaps heads ("eN already loaded"), so the `set_tool_loaded()` calls in
   `TOOL_LOAD` never run, and PREP doesn't set it for lanes restored from
   AFC.var. All four lanes report `status: None` during a print, including the
   one that is printing.

So the runout is swallowed: AFC has turned Snapmaker's handling off, and its
own handling never runs.

**Possible fixes**

- Set `TOOLED` for the lane in an extruder when it becomes the active lane on
  a U1 tool change (or in PREP when `lane_loaded` matches), or
- in `handle_toolhead_runout`, also accept `tool_loaded` lanes that are the
  extruder's `lane_loaded` for standalone toolheads.

**Workaround, tested on hardware:** for the length of the runout handler, give
the loaded lane `TOOLED`. With that, a head runout (filament cut just above the
head) paused within a second through `_perform_pause_runout` ("Runout
triggered for lane e3 ..."), and resume worked. With a `runout_lane` it would go
to `_perform_infinite_runout`; I haven't tested that on the U1.

Happy to open a PR for either fix if you have a preference.
