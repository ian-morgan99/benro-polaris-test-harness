"""Deterministic session-generation and recovery contract tests."""

from pathlib import Path

import yaml

from polaris_harness.scenario_service import load_scenario
from polaris_harness.session_recovery import SessionGuard
from polaris_harness.validation import validate_scenario_file


SCENARIOS = sorted(Path("scenarios").glob("recovery-*/scenario.yaml"))


def test_recovery_scenarios_are_schema_valid_and_synthetic():
    assert len(SCENARIOS) == 3
    for path in SCENARIOS:
        assert validate_scenario_file(path) == []
        scenario = yaml.safe_load(path.read_text())
        assert scenario["source"] == "synthetic"
        assert scenario["status"] == "synthetic"
        assert scenario["source_issue"].endswith("#9")


def test_owner_and_duplicate_request_are_rejected():
    guard = SessionGuard()

    assert guard.open("client-a").code == "SESSION_OPENED"
    busy = guard.open("client-b")
    assert busy.code == "SESSION_OWNER_BUSY"
    assert not busy.accepted

    assert guard.begin("capture-1", 0, mutating=True).accepted
    duplicate = guard.begin("capture-1", 1, mutating=True)
    assert duplicate.code == "DUPLICATE_REQUEST_REJECTED"
    assert not duplicate.accepted


def test_reconnect_increments_generation_and_discards_late_completion():
    guard = SessionGuard()
    first = guard.open("client-a")
    assert guard.begin("capture-1", 0, mutating=True).accepted

    closed, second = guard.reconnect("client-b")
    assert closed.code == "SESSION_CLOSED"
    assert second.code == "SESSION_OPENED"
    assert second.generation == first.generation + 1
    assert guard.pending(first.generation) == ()

    late = guard.complete("capture-1", first.generation)
    assert late.code == "LATE_COMPLETION_DISCARDED"
    assert not late.accepted

    assert guard.begin("capture-1", 10, mutating=True).accepted
    current = guard.complete("capture-1", second.generation)
    assert current.code == "REQUEST_COMPLETED"


def test_close_invalidates_pending_work_without_retry():
    guard = SessionGuard()
    opened = guard.open("client-a")
    assert guard.begin("capture-1", 0, mutating=True).accepted

    assert guard.close().code == "SESSION_CLOSED"
    assert guard.pending() == ()
    late = guard.complete("capture-1", opened.generation)
    assert late.code == "LATE_COMPLETION_DISCARDED"


def test_no_progress_becomes_unknown_and_does_not_retry():
    guard = SessionGuard()
    opened = guard.open("client-a")
    assert guard.begin("capture-1", 0, mutating=True).accepted

    events = guard.expire(100, no_progress_timeout_ms=100, safety_ceiling_ms=1000)

    assert [event.code for event in events] == ["OUTCOME_UNKNOWN_NO_PROGRESS"]
    assert guard.pending(opened.generation) == ()


def test_progress_resets_stall_but_safety_ceiling_still_bounds_operation():
    guard = SessionGuard()
    opened = guard.open("client-a")
    assert guard.begin("long-op", 0).accepted

    assert guard.progress("long-op", opened.generation, 90).code == "PROGRESS_RECORDED"
    assert guard.expire(150, no_progress_timeout_ms=100, safety_ceiling_ms=300) == []
    assert guard.progress("long-op", opened.generation, 200).code == "PROGRESS_RECORDED"
    events = guard.expire(300, no_progress_timeout_ms=100, safety_ceiling_ms=300)
    assert [event.code for event in events] == ["OUTCOME_UNKNOWN_SAFETY_CEILING"]


def test_late_progress_and_cancel_target_are_rejected():
    guard = SessionGuard()
    opened = guard.open("client-a")
    assert guard.begin("request-1", 0).accepted

    assert (
        guard.progress("request-1", opened.generation - 1, 10).code
        == "LATE_PROGRESS_DISCARDED"
    )
    assert guard.cancel("missing", opened.generation).code == "CANCEL_TARGET_UNKNOWN"
    assert guard.cancel("request-1", opened.generation).code == "REQUEST_CANCELLED"


def test_loader_accepts_recovery_fixture_from_external_contract_path():
    scenario = load_scenario(SCENARIOS[0])
    assert scenario["id"] == "recovery-late-event-after-reconnect-v1"
