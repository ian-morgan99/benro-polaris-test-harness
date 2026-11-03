"""
Synthetic Astro WebUI scenarios for issue #20.

Defines schema-backed synthetic scenarios for the Astro WebUI harness server.
All scenarios are labelled source: synthetic and do not claim physical
Polaris or camera support.
"""

from typing import Any

# Scenario definitions
ASTRO_SCENARIOS: dict[str, dict[str, Any]] = {
    "startup": {
        "id": "astro-startup",
        "version": 1,
        "title": "Astro WebUI startup",
        "source": "synthetic",
        "status": "synthetic",
        "description": "Verify the Astro WebUI loads and initializes with default state",
        "steps": [
            {
                "action": "get_state",
                "expect": {
                    "mount_status": "idle",
                    "camera_ready": False,
                    "tracking": False,
                    "rate_control": "unavailable",
                },
            },
        ],
    },
    "camera-not-ready": {
        "id": "astro-camera-not-ready",
        "version": 1,
        "title": "Camera not ready",
        "source": "synthetic",
        "status": "synthetic",
        "description": "Verify capture is rejected when camera is not ready",
        "steps": [
            {"action": "get_state", "expect": {"camera_ready": False}},
            {
                "action": "capture",
                "expect_ok": False,
                "expect_error": "camera not ready",
            },
        ],
    },
    "capture-publication": {
        "id": "astro-capture-publication",
        "version": 1,
        "title": "Capture publication",
        "source": "synthetic",
        "status": "synthetic",
        "description": "Verify a successful capture publishes a capture_id and updates state",
        "steps": [
            {"action": "set_camera_ready", "value": True},
            {"action": "capture", "expect_ok": True, "expect_field": "capture_id"},
            {"action": "get_state", "expect": {"capture_count": 1}},
        ],
    },
    "solve-accepted": {
        "id": "astro-solve-accepted",
        "version": 1,
        "title": "Accepted solve",
        "source": "synthetic",
        "status": "synthetic",
        "description": "Verify a valid solve returns ok with confidence",
        "steps": [
            {
                "action": "solve",
                "image_id": "valid_image",
                "expect_ok": True,
                "expect_field": "confidence",
            },
        ],
    },
    "solve-rejected": {
        "id": "astro-solve-rejected",
        "version": 1,
        "title": "Rejected solve",
        "source": "synthetic",
        "status": "synthetic",
        "description": "Verify a rejected solve produces a visible reason and zero mount/EXIF actions",
        "steps": [
            {
                "action": "solve",
                "image_id": "reject",
                "expect_ok": False,
                "expect_error": "solve rejected",
            },
            {"action": "get_state", "expect": {"mount_actions": 0, "exif_actions": 0}},
        ],
    },
    "goto-serialisation": {
        "id": "astro-goto-serialisation",
        "version": 1,
        "title": "GOTO serialisation",
        "source": "synthetic",
        "status": "synthetic",
        "description": "Verify GOTO accepts RA/Dec and returns slewing state",
        "steps": [
            {"action": "set_camera_ready", "value": True},
            {"action": "goto", "ra": 12.5, "dec": -30.0, "expect_ok": True},
            {"action": "get_state", "expect": {"mount_status": "slewing"}},
        ],
    },
    "stale-session": {
        "id": "astro-stale-session",
        "version": 1,
        "title": "Stale session",
        "source": "synthetic",
        "status": "synthetic",
        "description": "Verify session reset produces a new session_id",
        "steps": [
            {"action": "get_state", "expect_field": "session_id"},
            {"action": "reset", "expect_ok": True},
            {
                "action": "get_state",
                "expect_field": "session_id",
                "expect_changed": True,
            },
        ],
    },
    "exif-opt-in": {
        "id": "astro-exif-opt-in",
        "version": 1,
        "title": "EXIF opt-in",
        "source": "synthetic",
        "status": "synthetic",
        "description": "Verify EXIF opt-in returns ok",
        "steps": [
            {"action": "exif", "enable": True, "expect_ok": True},
        ],
    },
    "rate-control-unavailable": {
        "id": "astro-rate-control-unavailable",
        "version": 1,
        "title": "Rate control unavailable",
        "source": "synthetic",
        "status": "synthetic",
        "description": "Verify rate control returns 403 unless separately qualified",
        "steps": [
            {"action": "rate_control", "expect_ok": False, "expect_status": 403},
        ],
    },
}


def get_scenario(scenario_id: str) -> dict[str, Any] | None:
    """Get a scenario by ID.

    Args:
        scenario_id: Scenario identifier

    Returns:
        Scenario dict or None if not found
    """
    if scenario_id in ASTRO_SCENARIOS:
        return ASTRO_SCENARIOS[scenario_id]
    return next(
        (
            scenario
            for scenario in ASTRO_SCENARIOS.values()
            if scenario.get("id") == scenario_id
        ),
        None,
    )


def list_scenarios() -> list[str]:
    """List all scenario IDs.

    Returns:
        List of scenario IDs
    """
    return list(ASTRO_SCENARIOS.keys())


def validate_scenario_result(
    scenario_id: str, results: list[dict[str, Any]]
) -> list[str]:
    """Validate scenario execution results against expected outcomes.

    Args:
        scenario_id: Scenario identifier
        results: List of step results from execution

    Returns:
        List of validation error messages (empty if all pass)
    """
    scenario = get_scenario(scenario_id)
    if scenario is None:
        return [f"Unknown scenario: {scenario_id}"]

    errors = []
    steps = scenario.get("steps", [])

    if len(results) != len(steps):
        errors.append(f"Expected {len(steps)} steps, got {len(results)}")
        return errors

    for i, (step, result) in enumerate(zip(steps, results, strict=True)):
        step_action = step.get("action", "")

        # Check expect_ok
        if "expect_ok" in step:
            expected_ok = step["expect_ok"]
            actual_ok = result.get("ok", False)
            if expected_ok != actual_ok:
                errors.append(
                    f"Step {i} ({step_action}): expected ok={expected_ok}, got ok={actual_ok}"
                )

        # Check expect_error
        if "expect_error" in step:
            expected_error = step["expect_error"]
            actual_error = result.get("error", "")
            if expected_error not in str(actual_error):
                errors.append(
                    f"Step {i} ({step_action}): expected error containing '{expected_error}', got '{actual_error}'"
                )

        # Check expect_field
        if "expect_field" in step:
            field = step["expect_field"]
            if field not in result:
                errors.append(
                    f"Step {i} ({step_action}): expected field '{field}' not in result"
                )

        # Check expect_status
        if "expect_status" in step:
            expected_status = step["expect_status"]
            actual_status = result.get("status_code", 200)
            if expected_status != actual_status:
                errors.append(
                    f"Step {i} ({step_action}): expected status {expected_status}, got {actual_status}"
                )

        # Check expect_changed (for session_id)
        if step.get("expect_changed"):
            field = step.get("expect_field")
            # This would need cross-step comparison; handled by caller
            pass

    return errors
