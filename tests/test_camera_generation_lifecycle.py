from pathlib import Path

import yaml

from polaris_harness.engine import ScenarioEngine
from polaris_harness.framing import Frame, FramingType
from polaris_harness.validation import validate_scenario_file


SCENARIO = Path("scenarios/synthetic-camera-generation-lifecycle/scenario.yaml")


def frame(text: str) -> Frame:
    return Frame(
        raw_bytes=text.encode(),
        text=text,
        framing_type=FramingType.DELIMITER,
        delimiter="#",
    )


def exchange(engine: ScenarioEngine, request: str) -> list[str]:
    actions = engine.execute_scenario(frame(request), "consumer")
    events = engine.execute_actions(actions)
    return [
        event.data["text"]
        for event in events
        if event.data.get("action") == "send"
    ]


def test_generation_boundary_rejects_stale_completion_and_preserves_recovery():
    assert validate_scenario_file(SCENARIO) == []
    scenario = yaml.safe_load(SCENARIO.read_text())
    engine = ScenarioEngine(scenario["id"], scenario)

    assert exchange(engine, "CAPTURE generation=1#") == [
        "CAPTURE_ACCEPTED generation=1#"
    ]
    assert exchange(engine, "CAMERA_GONE generation=1#") == [
        "SESSION_ENDED generation=1 mount_state=preserved#"
    ]
    assert exchange(engine, "CAPTURE_COMPLETE generation=1#") == [
        "STALE_REJECTED expected_generation=2 received_generation=1#"
    ]
    assert engine.state_machine.get_current_state() == "generation-2-ready"

    assert exchange(engine, "CAPTURE generation=2#") == [
        "CAPTURE_ACCEPTED generation=2#"
    ]
    assert exchange(engine, "CANCEL generation=2#") == [
        "CANCELLED generation=2 ownership=released#"
    ]
    assert engine.state_machine.get_current_state() == "generation-2-ready"
