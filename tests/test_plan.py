"""Unit tests for U1_AFC's lane sync decisions (no Klipper needed).

    python -m pytest tests      or      python tests/test_plan.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extras"))

from u1_afc import Settle, plan_lane, plan_tool_map, rgba_to_hex  # noqa: E402

TOOLS = {"T0": "e0", "T1": "e1", "T2": "e2", "T3": "e3"}
HEADS = {"e0": 0, "e1": 1, "e2": 2, "e3": 3}


def test_tool_map_identity_changes_nothing():
    assert plan_tool_map([0, 1, 2, 3] + [0] * 28, TOOLS, HEADS) == {}


def test_tool_map_orca_filament1_to_head3():
    # 2026-11-10 Orca print: MAP_TABLE [[0,2],[1,1],[2,2],[3,3]]
    assert plan_tool_map([2, 1, 2, 3] + [0] * 28, TOOLS, HEADS) == {"T0": "e2"}


def test_tool_map_swap_and_missing_tools():
    assert plan_tool_map([3, 2, 1, 0], TOOLS, HEADS) == {
        "T0": "e3", "T1": "e2", "T2": "e1", "T3": "e0"}
    # tools AFC doesn't have, bad values and heads without a lane are skipped
    assert plan_tool_map([0, 1, 2, 3, 1, "x"], TOOLS, HEADS) == {}
    assert plan_tool_map([5, 1], TOOLS, HEADS) == {}
    assert plan_tool_map(None, TOOLS, HEADS) == {}

LOADED_FEED = {"present": True, "state": "load_finish"}
EMPTY_LANE = {"loaded": False, "other_loaded": False, "spool_id": None,
              "material": "", "color": "", "weight": 0}


def lane(**kw):
    d = dict(EMPTY_LANE)
    d.update(kw)
    return d


def ptc(spool=0, material="PLA", color="#2564E8"):
    return {"spool_id": spool, "material": material, "color": color}


def test_rgba_to_hex():
    assert rgba_to_hex("2564e8FF") == "#2564E8"
    assert rgba_to_hex("") == ""
    assert rgba_to_hex(None) == ""


def test_u1_load_with_tagged_spool_imports_spool_then_marks_loaded():
    # The 2026-11-10 head-4 test: U1 screen load of a tagged spool (59)
    assert plan_lane(lane(), LOADED_FEED, ptc(59), None) == [
        ("set_spool", 59), ("mark_loaded",)]


def test_u1_load_without_spool_copies_filament_and_default_weight():
    assert plan_lane(lane(), LOADED_FEED, ptc(0, "PLA", "#003776"), 0) == [
        ("set_filament", "PLA", "#003776"), ("set_weight", 1000.0), ("mark_loaded",)]


def test_fresh_install_migration_same_as_u1_load():
    # all heads loaded, AFC lanes empty, first pass after start
    assert plan_lane(lane(), LOADED_FEED, ptc(79, "PLA", "#000000"), None) == [
        ("set_spool", 79), ("mark_loaded",)]


def test_feeder_not_finished_does_nothing():
    for feed in (None, {"present": False, "state": "unload_finish"},
                 {"present": True, "state": "preload_finish"}):
        assert plan_lane(lane(), feed, ptc(59), None) == []


def test_extruder_holding_other_lane_is_left_alone():
    assert plan_lane(lane(other_loaded=True), LOADED_FEED, ptc(59), None) == []


def test_loaded_lane_in_step_does_nothing():
    l = lane(loaded=True, spool_id=79, material="PLA", color="#000000", weight=776)
    assert plan_lane(l, LOADED_FEED, ptc(79), 79) == []


def test_rfid_rescan_changes_spool_on_loaded_lane():
    l = lane(loaded=True, spool_id=79, weight=776)
    assert plan_lane(l, LOADED_FEED, ptc(59), 79) == [("set_spool", 59)]


def test_spool_cleared_on_u1_keeps_filament_and_sets_weight():
    l = lane(loaded=True, spool_id=79, material="PLA", color="#000000", weight=776)
    assert plan_lane(l, LOADED_FEED, ptc(0, "PETG", "#FF0000"), 79) == [
        ("clear_spool",), ("set_filament", "PETG", "#FF0000"), ("set_weight", 1000.0)]


def test_afc_side_assignment_not_reverted_when_u1_unchanged():
    # User set spool 74 in the AFC panel; the U1 still says 59 (unchanged since last pass)
    l = lane(loaded=True, spool_id=74, weight=500)
    assert plan_lane(l, LOADED_FEED, ptc(59), 59) == []


def test_afc_side_assignment_pushed_to_u1():
    # The 2026-11-10 Creality tag: U1 has no spool (unknown card), user sets
    # spool 3 in the AFC panel -> hand it to SpoolLink (binds the card)
    l = lane(loaded=True, spool_id=3, material="PLA", color="#FF7F37", weight=900)
    assert plan_lane(l, LOADED_FEED, ptc(0, "PLA", "#FF7F37"), 0,
                     last_lane_spool=None) == [("push_spool", 3)]
    # spool changed from another one on the AFC side
    assert plan_lane(l, LOADED_FEED, ptc(59), 59, last_lane_spool=59) == [("push_spool", 3)]


def test_push_not_repeated_once_u1_agrees_or_unchanged():
    l = lane(loaded=True, spool_id=3, weight=900)
    # next pass: SpoolLink applied it (ptc 0 -> 3)
    assert plan_lane(l, LOADED_FEED, ptc(3), 0, last_lane_spool=3) == []
    # SpoolLink didn't (no Spoolman): no retry loop
    assert plan_lane(l, LOADED_FEED, ptc(0), 0, last_lane_spool=3) == []


def test_u1_change_wins_over_afc_change_in_same_pass():
    l = lane(loaded=True, spool_id=3, weight=900)
    assert plan_lane(l, LOADED_FEED, ptc(59), 0, last_lane_spool=79) == [("set_spool", 59)]


def test_first_pass_keeps_afc_spool_when_both_set():
    l = lane(loaded=True, spool_id=74, weight=500)
    assert plan_lane(l, LOADED_FEED, ptc(59), None) == []


def test_first_pass_migrates_spool_into_loaded_empty_lane():
    l = lane(loaded=True, material="PLA", color="#003776", weight=999)
    assert plan_lane(l, LOADED_FEED, ptc(59), None) == [("set_spool", 59)]


def test_no_spool_lane_with_zero_weight_gets_default():
    l = lane(loaded=True, material="PLA", color="#003776", weight=0)
    assert plan_lane(l, LOADED_FEED, ptc(0, "PLA", "#003776"), 0) == [("set_weight", 1000.0)]


def test_sync_switches():
    assert plan_lane(lane(), LOADED_FEED, ptc(59), None, sync_loads=False) == []
    assert plan_lane(lane(), LOADED_FEED, ptc(59), None, sync_spools=False) == [
        ("mark_loaded",)]
    l = lane(loaded=True, spool_id=79, weight=776)
    assert plan_lane(l, LOADED_FEED, ptc(59), 79, sync_spools=False) == []


def test_settle():
    s = Settle(5.0)
    assert s.update("e3", 3, 0.0) is False      # first sight
    assert s.update("e3", 3, 4.9) is False
    assert s.update("e3", 3, 5.0) is True
    assert s.update("e3", 0, 6.0) is False      # changed: clock restarts
    assert s.update("e3", 0, 11.0) is True
    assert Settle(0).update("x", 1, 0.0) is True


def _poll(samples, lane_state, settle_time=5.0, interval=2.0):
    """Drive Settle + plan_lane like U1AFC.sync: samples = [(t, ptc_spool)]."""
    s = Settle(settle_time)
    last = None
    done = []
    for t, spool in samples:
        p = ptc(spool)
        if not s.update("e3", (spool, p["material"], p["color"], "load_finish"), t):
            continue
        acts = plan_lane(lane_state, LOADED_FEED, p, last, last_lane_spool=3)
        done.extend(acts)
        last = spool
    return done


def test_scan_blip_ignored():
    # 2026-11-10 rescan of a bound tag: 3 -> 0 for 0.25 s -> 3
    l = lane(loaded=True, spool_id=3, weight=990)
    samples = [(0, 3), (2, 3), (4, 3), (6, 3),     # settled on 3
               (8, 0),                             # scan starts (poll lands in the blip)
               (10, 3), (12, 3), (14, 3), (16, 3)]
    assert _poll(samples, l) == []


def test_real_change_still_applied_after_settle():
    l = lane(loaded=True, spool_id=3, weight=990)
    samples = [(0, 3), (2, 3), (4, 3), (6, 3),
               (8, 59), (10, 59), (12, 59), (14, 59)]
    assert _poll(samples, l) == [("set_spool", 59)]


def test_unknown_tag_clears_after_settle():
    l = lane(loaded=True, spool_id=3, weight=990)
    samples = [(0, 3), (2, 3), (4, 3), (6, 3)] + [(t, 0) for t in range(8, 20, 2)]
    assert _poll(samples, l)[0] == ("clear_spool",)


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as e:
                failed += 1
                print("FAIL", name, e)
    sys.exit(1 if failed else 0)
