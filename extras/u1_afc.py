# U1_AFC bridge: keeps AFC-Klipper-Add-On lanes in step with what the
# Snapmaker U1 itself does to its four heads.
#
# AFC follows unloads made on the U1 (its feeder mirror clears the lane), but
# not loads or spool changes: after a load from the U1 screen the lane stays
# "not loaded" and empty, and RFID tags / SpoolLink only reach the U1's
# print_task_config. This module closes that gap:
#
#   - a feeder that reports load_finish for a lane AFC thinks is empty marks
#     the lane loaded (no movement), importing the spool, or the material and
#     color, from print_task_config first;
#   - a spool change in print_task_config (RFID scan, U1 screen, SpoolLink)
#     is applied to the loaded lane;
#   - a loaded lane without a Spoolman spool gets a default weight, so UIs
#     don't draw it as an empty spool.
#
# The first pass after Klipper starts also migrates a fresh AFC install
# (all lanes empty, all heads loaded).
#
# Copyright (C) 2026 DIEHARDave
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging
import os

LOAD_FINISHED = "load_finish"


def rgba_to_hex(rgba):
    """'2564E8FF' -> '#2564E8'; '' / None -> ''."""
    if not rgba or len(rgba) < 6:
        return ""
    return "#" + rgba[:6].upper()


UNSET = object()


class Settle:
    """
    Tells when a value has held still for settle_time seconds. A tag scan
    makes SpoolLink report "no spool" for a moment before the scanned spool;
    acting on that would clear the lane and set it again.
    """
    def __init__(self, settle_time):
        self.settle_time = settle_time
        self.seen = {}

    def update(self, key, value, now):
        """:return: True once value has been unchanged for settle_time"""
        prev = self.seen.get(key)
        if prev is None or prev[0] != value:
            self.seen[key] = (value, now)
            return self.settle_time <= 0
        return now - prev[1] >= self.settle_time


def plan_tool_map(map_table, tool_cmds, lane_heads):
    """
    Turn the U1's extruder_map_table (filament T<i> -> head index, set by
    Snapmaker Orca or the U1 screen at print start) into AFC tool_cmds
    entries. Pure function (no Klipper).

    :param map_table: list of head indexes, one per logical tool
    :param tool_cmds: AFC's current {"T0": lane_name, ...}
    :param lane_heads: {lane_name: head index}
    :return: {"T<i>": lane_name} for the tools that change (several tools
             may go to one lane; tools AFC doesn't have are left out)
    """
    by_head = {}
    for name, head in sorted(lane_heads.items()):
        by_head.setdefault(head, name)
    changes = {}
    for i, head in enumerate(map_table or []):
        tool = "T%d" % (i,)
        if tool not in tool_cmds:
            continue
        try:
            lane = by_head.get(int(head))
        except (TypeError, ValueError):
            continue
        if lane is not None and tool_cmds[tool] != lane:
            changes[tool] = lane
    return changes


def plan_lane(lane, feed, ptc, last_ptc_spool, default_weight=1000.0,
              sync_loads=True, sync_spools=True, last_lane_spool=UNSET):
    """
    Decide what to change on one AFC lane. Pure function (no Klipper).

    :param lane: dict with loaded (bool), other_loaded (bool: the lane's
                 extruder holds another lane), spool_id (int|None),
                 material (str), color ('#RRGGBB'|''), weight (float)
    :param feed: dict with present (bool), state (str), or None if unknown
    :param ptc: dict with spool_id (int), material (str), color (str), the
                U1's print_task_config entry for this head
    :param last_ptc_spool: ptc spool_id seen on the previous pass, None on
                           the first pass after start
    :param last_lane_spool: the lane's spool_id after the previous pass,
                            UNSET on the first pass
    :return: list of actions: ("set_spool", id), ("clear_spool",),
             ("set_filament", material, color), ("set_weight", w),
             ("mark_loaded",), ("push_spool", id) (tell the U1 / SpoolLink
             about a spool assigned on the AFC side)
    """
    actions = []
    lane_spool = lane.get("spool_id") or None
    ptc_spool = ptc.get("spool_id") or 0
    ptc_material = ptc.get("material") or ""
    ptc_color = ptc.get("color") or ""
    has_ptc_filament = bool(ptc_material and ptc_material != "NONE" and ptc_color)

    def import_from_ptc():
        if not sync_spools:
            return
        if ptc_spool > 0:
            if ptc_spool != lane_spool:
                actions.append(("set_spool", ptc_spool))
        elif lane_spool is None and has_ptc_filament:
            actions.append(("set_filament", ptc_material, ptc_color))

    if not lane.get("loaded"):
        if (sync_loads and feed is not None and feed.get("present")
                and feed.get("state") == LOAD_FINISHED
                and not lane.get("other_loaded")):
            import_from_ptc()
            if ptc_spool <= 0 and (lane.get("weight") or 0) <= 0:
                actions.append(("set_weight", float(default_weight)))
            actions.append(("mark_loaded",))
        return actions

    # Lane is loaded
    weight = lane.get("weight") or 0
    if sync_spools:
        changed = last_ptc_spool is not None and ptc_spool != last_ptc_spool
        if changed:
            if ptc_spool > 0 and ptc_spool != lane_spool:
                actions.append(("set_spool", ptc_spool))
            elif ptc_spool <= 0 and lane_spool is not None:
                actions.append(("clear_spool",))  # AFC's clear_values zeroes weight too
                if has_ptc_filament:
                    actions.append(("set_filament", ptc_material, ptc_color))
                lane_spool = None
                weight = 0
        elif (last_lane_spool is not UNSET and lane_spool != last_lane_spool
              and lane_spool is not None and ptc_spool != lane_spool):
            # Spool assigned on the AFC side (panel, SET_SPOOL_ID): hand it to
            # the U1 so both agree and SpoolLink binds the scanned tag to it.
            actions.append(("push_spool", lane_spool))
        elif lane_spool is None and ptc_spool > 0 and (
                last_ptc_spool is None
                or (not lane.get("material") and not lane.get("color"))):
            # First pass after start: migrate a spool AFC doesn't know yet.
            # Later: the lane lost everything (AFC clears a lane on runout)
            # while the head still has a spool -> take it back.
            actions.append(("set_spool", ptc_spool))
        elif (lane_spool is None and ptc_spool <= 0 and has_ptc_filament
              and not lane.get("material") and not lane.get("color")):
            actions.append(("set_filament", ptc_material, ptc_color))

    will_have_spool = any(a[0] == "set_spool" for a in actions) or (
        lane_spool is not None and not any(a[0] == "clear_spool" for a in actions))
    if not will_have_spool and weight <= 0:
        actions.append(("set_weight", float(default_weight)))
    return actions


class U1AFC:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.sync_loads = config.getboolean("sync_loads", True)
        self.sync_spools = config.getboolean("sync_spools", True)
        self.default_weight = config.getfloat("default_weight", 1000., minval=0.)
        self.poll_interval = config.getfloat("poll_interval", 2., minval=0.5)
        self.start_delay = config.getfloat("start_delay", 10., minval=0.)
        self.settle = Settle(config.getfloat("settle_time", 5., minval=0.))
        # Who sets Moonraker's active spool. SpoolLink follows the head on the
        # carriage and print_task_config (which this bridge keeps equal to the
        # AFC lanes); AFC writing too made them race during tool changes.
        self.active_spool = config.getchoice("active_spool",
                                             {"spoollink": "spoollink", "afc": "afc"},
                                             "spoollink")
        self._afc_set_active_spool = None
        self.skipped_active_spool = 0
        # Who writes a spool's filament info (type, sub-type, vendor, color)
        # into print_task_config. AFC writes Spoolman's raw material ("PLA+")
        # and sub-type NONE over SpoolLink's U1 values ("PLA"), and Orca then
        # won't map a PLA filament to that head.
        self.spool_info = config.getchoice("spool_info",
                                           {"spoollink": "spoollink", "afc": "afc"},
                                           "spoollink")
        self._afc_set_filament_params = None
        self.skipped_filament_params = 0
        # Make AFC act on head (filament_motion_sensor) runouts; see _tool_runout
        self.fix_tool_runout = config.getboolean("fix_tool_runout", True)
        self.tool_runouts = 0
        # Like the stock U1: the feeder of the head in use runs empty and there
        # is nothing to switch to (no runout lane) -> pause right away, with the
        # tube still full. With a runout lane, wait for the head sensor.
        self.pause_on_feeder_runout = config.getboolean("pause_on_feeder_runout", True)
        self.feeder_empty_seen = {}     # lane -> consecutive empty polls
        self.feeder_runout_done = set()
        self.feeder_runouts = 0
        # Follow the slicer / U1 screen filament -> head mapping for a print
        self.follow_print_map = config.getboolean("follow_print_map", True)
        self._orig_print_file = None
        self.saved_tool_cmds = None     # AFC's map while a print map is applied
        self.applied_map = {}
        self.map_applied_at = 0.
        self.last_ptc_spool = {}
        self.last_lane_spool = {}
        self.last_actions = []
        self.timer = None
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        gcode = self.printer.lookup_object("gcode")
        # No digits in command names: Klipper parses "U1_..." / "AFC_U1_..." as
        # the command "U1" / "AFC_U1" plus arguments.
        gcode.register_command("AFC_BRIDGE_STATUS", self.cmd_AFC_BRIDGE_STATUS,
                               desc="Show what U1_AFC would change on each AFC lane")
        gcode.register_command("AFC_BRIDGE_SYNC", self.cmd_AFC_BRIDGE_SYNC,
                               desc="Run the U1_AFC lane sync now")

    def _handle_ready(self):
        self._hook_active_spool()
        self._hook_filament_params()
        self._hook_tool_runout()
        self._hook_print_file()
        self.timer = self.reactor.register_timer(
            self._timer_cb, self.reactor.monotonic() + self.start_delay)

    def _timer_cb(self, eventtime):
        try:
            self._check_restore_map(eventtime)
        except Exception:
            logging.exception("u1_afc: restoring the tool map failed")
        try:
            self._check_feeder_runout(eventtime)
        except Exception:
            logging.exception("u1_afc: feeder runout check failed")
        try:
            self.sync()
        except Exception:
            logging.exception("u1_afc: sync failed")
        return eventtime + self.poll_interval

    # Head runout ------------------------------------------------------------
    # AFC takes over each head's filament_motion_sensor (pause_on_runout off,
    # its own _runout_event_handler), but its handler only acts on lanes in
    # state TOOLED/LOADED. U1 tool changes only swap heads and never set
    # TOOLED, so every lane stays NONE and a runout was ignored: no pause at
    # all. While the runout is handled, the bridge gives the lane on that
    # head the TOOLED state AFC expects; AFC then pauses (or switches to the
    # lane's runout lane).

    def _hook_tool_runout(self):
        if not self.fix_tool_runout:
            return
        afc = self._afc()
        if afc is None:
            return
        done = set()
        for lane in afc.lanes.values():
            ext = getattr(lane, "extruder_obj", None)
            sensor = getattr(ext, "fila_tool_start", None)
            helper = getattr(sensor, "runout_helper", None)
            if ext is None or helper is None or id(ext) in done:
                continue
            original = getattr(helper, "_runout_event_handler", None)
            if original is None or getattr(original, "_u1_afc", False):
                continue
            done.add(id(ext))

            def handler(eventtime, ext=ext, original=original):
                self._tool_runout(ext, eventtime, original)
            handler._u1_afc = True
            helper._runout_event_handler = handler
        if done:
            logging.info("u1_afc: head runout handling fixed for %d extruders", len(done))

    def _tool_runout(self, ext, eventtime, original):
        afc = self._afc()
        lane = afc.lanes.get(getattr(ext, "lane_loaded", None) or "") if afc else None
        fixed = None
        if lane is not None and lane.tool_loaded:
            try:
                from extras.AFC_lane import AFCLaneState
            except ImportError:
                AFCLaneState = None
            if AFCLaneState is not None and lane.status not in (
                    AFCLaneState.TOOLED, AFCLaneState.LOADED):
                fixed = lane.status
                lane.status = AFCLaneState.TOOLED
        self.tool_runouts += 1
        msg = "U1_AFC: runout on %s (%s)" % (
            getattr(ext, "name", "?"), lane.name if lane is not None else "no lane loaded")
        logging.info(msg)
        try:
            afc.logger.info(msg)
        except Exception:
            pass
        try:
            original(eventtime)
        finally:
            # AFC sets NONE itself when it acts; put back what was there if not
            if fixed is not None and lane.status == AFCLaneState.TOOLED:
                lane.status = fixed

    def _check_feeder_runout(self, eventtime):
        if not self.pause_on_feeder_runout:
            return
        ps = self.printer.lookup_object("print_stats", None)
        if ps is None or ps.get_status(eventtime).get("state") != "printing":
            if ps is not None and ps.get_status(eventtime).get("state") != "paused":
                self.feeder_runout_done.clear()
            self.feeder_empty_seen.clear()
            return
        afc = self._afc()
        if afc is None or afc.in_toolchange or getattr(afc, "error_state", False):
            return
        toolhead = self.printer.lookup_object("toolhead")
        active = toolhead.get_extruder().get_name()
        for lane in afc.lanes.values():
            ext = getattr(lane, "extruder_obj", None)
            if (ext is None or ext.lane_loaded != lane.name or not lane.tool_loaded
                    or getattr(ext, "th_extruder_name", None) != active):
                self.feeder_empty_seen.pop(lane.name, None)
                continue
            try:
                feed = self._feed_state(lane.lane_extruder_index, eventtime)
            except Exception:
                feed = None
            if feed is None or feed.get("present"):
                self.feeder_empty_seen.pop(lane.name, None)
                self.feeder_runout_done.discard(lane.name)
                continue
            if lane.runout_lane is not None or lane.name in self.feeder_runout_done:
                continue    # a runout lane takes over at the head sensor
            n = self.feeder_empty_seen.get(lane.name, 0) + 1
            self.feeder_empty_seen[lane.name] = n
            if n < 2:       # two polls in a row (~2 s)
                continue
            self.feeder_runout_done.add(lane.name)
            self.feeder_runouts += 1
            msg = "U1_AFC: feeder of %s (head %d) ran empty, no runout lane: pausing" % (
                lane.name, lane.lane_extruder_index + 1)
            logging.info(msg)
            try:
                afc.logger.info(msg)
            except Exception:
                pass
            self.reactor.register_callback(lambda e, lane=lane: self._pause_runout(lane))

    def _pause_runout(self, lane):
        afc = self._afc()
        if afc is None or afc.in_toolchange or getattr(afc, "error_state", False):
            return
        # AFC's own runout pause: pause, save position, "Runout triggered for lane"
        lane._perform_pause_runout()

    # Print tool map ---------------------------------------------------------
    # Snapmaker Orca and the U1 screen start prints with
    # SDCARD_PRINT_FILE_WITH_PARAMETERS MAP_TABLE=..., which lands in
    # print_task_config.extruder_map_table. Snapmaker's T<n> reads it; AFC's
    # T<n> uses its own tool_cmds, so the mapping was ignored. For the length
    # of such a print, point AFC's tool_cmds at the mapped heads. lane.map
    # (what the panels show) and AFC.var stay as they are.

    def _hook_print_file(self):
        if not self.follow_print_map or self._orig_print_file is not None:
            return
        gcode = self.printer.lookup_object("gcode")
        name = "SDCARD_PRINT_FILE_WITH_PARAMETERS"
        original = gcode.register_command(name, None)
        if original is None:
            return
        self._orig_print_file = original

        def print_file(gcmd):
            self._restore_map("new print")
            original(gcmd)      # sets print_task_config, then starts the file
            try:
                self._apply_print_map()
            except Exception:
                logging.exception("u1_afc: applying the print tool map failed")
        gcode.register_command(name, print_file,
                               desc="Start a print with U1 parameters (U1_AFC: follows MAP_TABLE)")

    def _apply_print_map(self):
        afc = self._afc()
        ptc = self.printer.lookup_object("print_task_config", None)
        if afc is None or ptc is None:
            return
        eventtime = self.reactor.monotonic()
        table = ptc.get_status(eventtime).get("extruder_map_table") or []
        heads = {}
        for lane in afc.lanes.values():
            try:
                heads[lane.name] = lane.lane_extruder_index
            except Exception:
                continue
        changes = plan_tool_map(table, afc.tool_cmds, heads)
        if not changes:
            return
        self.saved_tool_cmds = dict(afc.tool_cmds)
        afc.tool_cmds.update(changes)
        self.applied_map = changes
        self.map_applied_at = eventtime
        msg = "U1_AFC: print tool map " + ", ".join(
            "%s->%s" % (t, changes[t]) for t in sorted(changes))
        logging.info(msg)
        try:
            afc.logger.info(msg)
        except Exception:
            pass

    def _restore_map(self, why):
        if self.saved_tool_cmds is None:
            return
        afc = self._afc()
        if afc is not None:
            for tool, lane in self.applied_map.items():
                # leave entries alone that something else changed meanwhile
                if afc.tool_cmds.get(tool) == lane:
                    afc.tool_cmds[tool] = self.saved_tool_cmds.get(tool, lane)
            try:
                afc.logger.info("U1_AFC: tool map restored (%s)" % (why,))
            except Exception:
                pass
        logging.info("u1_afc: tool map restored (%s)", why)
        self.saved_tool_cmds = None
        self.applied_map = {}

    def _check_restore_map(self, eventtime):
        if self.saved_tool_cmds is None or eventtime - self.map_applied_at < 10.:
            return
        ps = self.printer.lookup_object("print_stats", None)
        state = ps.get_status(eventtime).get("state") if ps is not None else None
        if state not in ("printing", "paused"):
            self._restore_map("print %s" % (state,))

    # Active spool ---------------------------------------------------------

    def _spoollink_present(self):
        webhooks = self.printer.lookup_object("webhooks", None)
        methods = getattr(webhooks, "_remote_methods", None) or {}
        return bool(methods.get("spoollink_resolve_spool"))

    def _spoollink_enabled(self):
        # AFC's PREP can run before Moonraker (and SpoolLink) has connected;
        # PAXX links spoollink.cfg into extended/klipper while it is enabled.
        return os.path.exists("/home/lava/printer_data/config/extended/klipper/spoollink.cfg")

    def _hook_active_spool(self):
        if self.active_spool != "spoollink":
            return
        afc = self._afc()
        spool = getattr(afc, "spool", None)
        if spool is None or self._afc_set_active_spool is not None:
            return
        original = spool.set_active_spool
        self._afc_set_active_spool = original

        def set_active_spool(ID):
            # Checked per call: SpoolLink registers with Klipper only once
            # Moonraker is connected, and may be disabled.
            if self._spoollink_present():
                self.skipped_active_spool += 1
                logging.debug("u1_afc: AFC set_active_spool(%s) left to SpoolLink", ID)
                return
            original(ID)
        spool.set_active_spool = set_active_spool
        logging.info("u1_afc: SpoolLink owns the active spool (AFC's writes skipped while it runs)")

    def _hook_filament_params(self):
        if self.spool_info != "spoollink":
            return
        spool = getattr(self._afc(), "spool", None)
        if spool is None or self._afc_set_filament_params is not None:
            return
        original = getattr(spool, "set_snapmaker_filament_params", None)
        if original is None:
            return
        self._afc_set_filament_params = original

        def set_snapmaker_filament_params(lane):
            # A lane with a Spoolman spool: SpoolLink writes that spool's info
            # (the bridge hands it spools set in AFC). Lanes without a spool:
            # AFC's values are the only ones, keep writing them.
            if getattr(lane, "spool_id", None) and (
                    self._spoollink_present() or self._spoollink_enabled()):
                self.skipped_filament_params += 1
                logging.debug("u1_afc: AFC filament info for %s left to SpoolLink",
                              getattr(lane, "name", "?"))
                return
            original(lane)
        spool.set_snapmaker_filament_params = set_snapmaker_filament_params
        logging.info("u1_afc: SpoolLink owns filament info of heads with a spool")

    # Reading state -------------------------------------------------------

    def _afc(self):
        return self.printer.lookup_object("AFC", None)

    def _afc_ready(self, afc):
        if afc is None or getattr(afc, "error_state", False):
            return False
        if str(getattr(afc, "current_state", "")) not in ("Idle", "State.IDLE"):
            return False
        try:
            if afc.function.in_print():
                return False
        except Exception:
            return False
        return True

    def _feed_state(self, index, eventtime):
        key = "extruder%d" % (index,)
        for module in ("left", "right"):
            obj = self.printer.lookup_object("filament_feed " + module, None)
            if obj is None:
                continue
            try:
                status = obj.get_status(eventtime)
            except Exception:
                continue
            ch = status.get(key)
            if ch is not None:
                return {"present": ch.get("filament_detected") is True,
                        "state": ch.get("channel_state", "")}
        return None

    def _ptc_state(self, index, eventtime):
        obj = self.printer.lookup_object("print_task_config", None)
        if obj is None:
            return None
        try:
            status = obj.get_status(eventtime)
        except Exception:
            return None

        def at(key, default):
            vals = status.get(key) or []
            return vals[index] if index < len(vals) else default

        try:
            spool = int(at("filament_spool_id", 0) or 0)
        except (TypeError, ValueError):
            spool = 0
        return {"spool_id": spool,
                "material": at("filament_type", "") or "",
                "color": rgba_to_hex(at("filament_color_rgba", ""))}

    def _lane_state(self, lane):
        ext = lane.extruder_obj
        loaded_in_ext = getattr(ext, "lane_loaded", None)
        spool = lane.spool_id
        try:
            spool = int(spool) if spool not in (None, "") else None
        except (TypeError, ValueError):
            spool = None
        return {"loaded": bool(lane.tool_loaded) and loaded_in_ext == lane.name,
                "other_loaded": loaded_in_ext not in (None, lane.name),
                "spool_id": spool,
                "material": lane.material or "",
                "color": lane.color or "",
                "weight": lane.weight or 0}

    # Acting ----------------------------------------------------------------

    def _card_uid(self, index, eventtime):
        obj = self.printer.lookup_object("filament_detect", None)
        try:
            info = obj.get_status(eventtime).get("info") or []
            uid = info[index].get("CARD_UID") if index < len(info) else None
        except Exception:
            return ""
        if isinstance(uid, (list, tuple)):
            return "".join("%02X" % (int(b),) for b in uid)
        return ""

    def _push_spool(self, index, spool_id):
        # What PAXX's SET_SPOOL_ID macro did: SpoolLink applies the spool to the
        # head (print_task_config) and binds the scanned card UID to it.
        uid = self._card_uid(index, self.reactor.monotonic())
        webhooks = self.printer.lookup_object("webhooks")
        webhooks.call_remote_method("spoollink_resolve_spool", channel=index,
                                    spool_id=int(spool_id), card_uid=uid)

    def _apply(self, afc, lane, index, actions):
        for action in actions:
            kind = action[0]
            if kind == "push_spool":
                self._push_spool(index, action[1])
                continue
            if kind == "set_spool":
                afc.spool.set_spoolID(lane, action[1], save_vars=False)
                if self._spoollink_present():
                    # AFC may have written this head's info while the lane was
                    # empty (type NONE, white); SpoolLink only rewrites it when
                    # the spool changes. Same spool and card: no-op otherwise.
                    self._push_spool(index, action[1])
            elif kind == "clear_spool":
                lane.spool_id = None
                afc.spool.clear_values(lane)
            elif kind == "set_filament":
                lane.material = action[1]
                lane.color = action[2]
            elif kind == "set_weight":
                lane.weight = action[1]
            elif kind == "mark_loaded":
                # Writes the lane's values into print_task_config as AFC does
                # after its own loads; the import above made them the same.
                lane.set_tool_loaded()
        lane.send_lane_data()
        afc.save_vars()

    def _passes(self, eventtime):
        afc = self._afc()
        if not self._afc_ready(afc):
            return afc, []
        out = []
        for lane in list(afc.lanes.values()):
            if not getattr(lane, "_afc_prep_done", False):
                continue
            try:
                index = lane.lane_extruder_index
            except Exception:
                continue
            ptc = self._ptc_state(index, eventtime)
            if ptc is None:
                continue
            state = self._lane_state(lane)
            feed = self._feed_state(index, eventtime)
            settled = self.settle.update(
                lane.name, (ptc["spool_id"], ptc["material"], ptc["color"],
                            feed["state"] if feed else None), eventtime)
            if not settled:
                out.append((lane, index, state, feed, ptc, None))
                continue
            actions = plan_lane(state, feed, ptc, self.last_ptc_spool.get(lane.name),
                                self.default_weight, self.sync_loads, self.sync_spools,
                                self.last_lane_spool.get(lane.name, UNSET))
            out.append((lane, index, state, feed, ptc, actions))
        return afc, out

    def sync(self, eventtime=None):
        eventtime = eventtime or self.reactor.monotonic()
        afc, passes = self._passes(eventtime)
        for lane, index, state, feed, ptc, actions in passes:
            if actions is None:
                continue    # U1 side still changing; look again next pass
            if actions:
                msg = "U1_AFC: %s (head %d): %s" % (
                    lane.name, index + 1, ", ".join(_fmt(a) for a in actions))
                logging.info(msg)
                try:
                    afc.logger.info(msg)
                except Exception:
                    pass
                try:
                    self._apply(afc, lane, index, actions)
                except Exception:
                    logging.exception("u1_afc: applying %s to %s failed", actions, lane.name)
                self.last_actions = (self.last_actions + [msg])[-10:]
            self.last_ptc_spool[lane.name] = ptc["spool_id"]
            self.last_lane_spool[lane.name] = self._lane_state(lane)["spool_id"]

    def cmd_AFC_BRIDGE_STATUS(self, gcmd):
        eventtime = self.reactor.monotonic()
        afc, passes = self._passes(eventtime)
        if afc is None:
            gcmd.respond_info("U1_AFC: no AFC object")
            return
        if not passes:
            gcmd.respond_info("U1_AFC: AFC busy, printing or not prepped; nothing checked")
            return
        lines = []
        for lane, index, state, feed, ptc, actions in passes:
            lines.append("%s head %d: AFC loaded=%s spool=%s %s | U1 spool=%s %s %s | feeder %s -> %s" % (
                lane.name, index + 1, state["loaded"], state["spool_id"], state["color"],
                ptc["spool_id"], ptc["material"], ptc["color"],
                feed["state"] if feed else "?",
                "waiting for the U1 to settle" if actions is None
                else (", ".join(_fmt(a) for a in actions) or "in step")))
        gcmd.respond_info("\n".join(lines))

    def cmd_AFC_BRIDGE_SYNC(self, gcmd):
        self.sync()
        gcmd.respond_info("U1_AFC: sync done")

    def get_status(self, eventtime=None):
        return {"sync_loads": self.sync_loads, "sync_spools": self.sync_spools,
                "active_spool": self.active_spool,
                "active_spool_writer": ("spoollink" if self.active_spool == "spoollink"
                                        and self._spoollink_present() else "afc"),
                "afc_active_spool_skipped": self.skipped_active_spool,
                "print_tool_map": dict(self.applied_map),
                "spool_info": self.spool_info,
                "afc_filament_info_skipped": self.skipped_filament_params,
                "tool_runouts": self.tool_runouts,
                "feeder_runouts": self.feeder_runouts,
                "last_actions": list(self.last_actions)}


def _fmt(action):
    return action[0] + ("(" + ", ".join(str(a) for a in action[1:]) + ")" if len(action) > 1 else "")


def load_config(config):
    return U1AFC(config)
