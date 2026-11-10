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

LOAD_FINISHED = "load_finish"


def rgba_to_hex(rgba):
    """'2564E8FF' -> '#2564E8'; '' / None -> ''."""
    if not rgba or len(rgba) < 6:
        return ""
    return "#" + rgba[:6].upper()


def plan_lane(lane, feed, ptc, last_ptc_spool, default_weight=1000.0,
              sync_loads=True, sync_spools=True):
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
    :return: list of actions: ("set_spool", id), ("clear_spool",),
             ("set_filament", material, color), ("set_weight", w),
             ("mark_loaded",)
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
        elif last_ptc_spool is None and lane_spool is None and ptc_spool > 0:
            # First pass after start: migrate a spool AFC doesn't know yet
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
        self.last_ptc_spool = {}
        self.last_actions = []
        self.timer = None
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        gcode = self.printer.lookup_object("gcode")
        gcode.register_command("U1_AFC_STATUS", self.cmd_U1_AFC_STATUS,
                               desc="Show what U1_AFC would change on each AFC lane")
        gcode.register_command("U1_AFC_SYNC", self.cmd_U1_AFC_SYNC,
                               desc="Run the U1_AFC lane sync now")

    def _handle_ready(self):
        self.timer = self.reactor.register_timer(
            self._timer_cb, self.reactor.monotonic() + self.start_delay)

    def _timer_cb(self, eventtime):
        try:
            self.sync()
        except Exception:
            logging.exception("u1_afc: sync failed")
        return eventtime + self.poll_interval

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

    def _apply(self, afc, lane, actions):
        for action in actions:
            kind = action[0]
            if kind == "set_spool":
                afc.spool.set_spoolID(lane, action[1], save_vars=False)
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
            actions = plan_lane(state, feed, ptc, self.last_ptc_spool.get(lane.name),
                                self.default_weight, self.sync_loads, self.sync_spools)
            out.append((lane, index, state, feed, ptc, actions))
        return afc, out

    def sync(self, eventtime=None):
        eventtime = eventtime or self.reactor.monotonic()
        afc, passes = self._passes(eventtime)
        for lane, index, state, feed, ptc, actions in passes:
            if actions:
                msg = "U1_AFC: %s (head %d): %s" % (
                    lane.name, index + 1, ", ".join(_fmt(a) for a in actions))
                logging.info(msg)
                try:
                    afc.logger.info(msg)
                except Exception:
                    pass
                try:
                    self._apply(afc, lane, actions)
                except Exception:
                    logging.exception("u1_afc: applying %s to %s failed", actions, lane.name)
                self.last_actions = (self.last_actions + [msg])[-10:]
            self.last_ptc_spool[lane.name] = ptc["spool_id"]

    def cmd_U1_AFC_STATUS(self, gcmd):
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
                ", ".join(_fmt(a) for a in actions) or "in step"))
        gcmd.respond_info("\n".join(lines))

    def cmd_U1_AFC_SYNC(self, gcmd):
        self.sync()
        gcmd.respond_info("U1_AFC: sync done")

    def get_status(self, eventtime=None):
        return {"sync_loads": self.sync_loads, "sync_spools": self.sync_spools,
                "last_actions": list(self.last_actions)}


def _fmt(action):
    return action[0] + ("(" + ", ".join(str(a) for a in action[1:]) + ")" if len(action) > 1 else "")


def load_config(config):
    return U1AFC(config)
