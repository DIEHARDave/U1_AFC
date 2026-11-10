"""Unit tests for U1_AFC's lane sync decisions (no Klipper needed).

    python -m pytest tests      or      python tests/test_plan.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extras"))

from u1_afc import plan_lane, rgba_to_hex  # noqa: E402

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
