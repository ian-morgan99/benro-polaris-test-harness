"""Contract tests for asynchronous K-3 III capture lifecycle."""

from pathlib import Path

import yaml

from polaris_harness.engine import ScenarioEngine
from polaris_harness.framing import Frame, FramingType
from polaris_harness.validation import validate_scenario_file

ROOT = Path("scenarios")
OBSERVED = ROOT / "k3iii-async-capture-accepted-not-complete-v1" / "scenario.yaml"
TIMEOUT = ROOT / "k3iii-async-capture-timeout-v1" / "scenario.yaml"


def _frame(text: str) -> Frame:
    return Frame(
        raw_bytes=text.encode("utf-8"),
        text=text,
        framing_type=FramingType.DELIMITER,
        delimiter="#",
    )


def _exchange(engine: ScenarioEngine, request: str) -> list[str]:
    actions = engine.execute_scenario(_frame(request), "capture-contract")
    events = engine.execute_actions(actions)
    return [
        event.data["text"] for event in events if event.data.get("action") == "send"
    ]


def test_observed_sequence_manifest_is_provisional_and_valid():
    assert validate_scenario_file(OBSERVED) == []
    scenario = yaml.safe_load(OBSERVED.read_text())
    assert scenario["source"] == "derived"
    assert scenario["status"] == "provisional"
    assert scenario["evidence_id"] == "derived-from-issue-2-observed-sequence"


def test_ack_and_intermediate_status_do_not_complete_capture():
    scenario = yaml.safe_load(OBSERVED.read_text())
    engine = ScenarioEngine(scenario["id"], scenario)

    actions = engine.execute_scenario(
        _frame("1&264&4&state:1;bulb:0;c:-1;#"), "capture-contract"
    )
    assert [action.after_ms for action in actions if action.action_type == "send"] == [
        12,
        2380,
    ]
    assert [
        event.data["text"]
        for event in engine.execute_actions(actions)
        if event.data.get("action") == "send"
    ] == ["264@state:1#", "264@state:-1005#"]
    assert engine.state_machine.get_current_state() == "pending"

    assert _exchange(engine, "1&264&4&state:1;bulb:0;c:-1;#") == [
        "CAPTURE_REJECTED reason=output_pending#"
    ]
    assert engine.state_machine.get_current_state() == "pending"

    assert _exchange(engine, "FILE_COMPLETE id=1 path=/app/sd/IMG_0001.JPG#") == [
        "CAPTURE_COMPLETE id=1#"
    ]
    assert engine.state_machine.get_current_state() == "idle"


def test_timeout_is_a_separate_unknown_outcome_without_retry():
    assert validate_scenario_file(TIMEOUT) == []
    scenario = yaml.safe_load(TIMEOUT.read_text())
    engine = ScenarioEngine(scenario["id"], scenario)

    assert _exchange(engine, "CAPTURE id=2#") == ["CAPTURE_ACCEPTED id=2#"]
    assert _exchange(engine, "CAPTURE_TIMEOUT id=2#") == [
        "OUTCOME_UNKNOWN id=2 reason=timeout#"
    ]
    assert engine.state_machine.get_current_state() == "idle"
    assert not any(
        "RETRY" in text for text in engine.get_actions()[0].parameters.values()
    )
