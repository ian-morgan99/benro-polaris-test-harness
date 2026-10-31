"""Contract tests for the synthetic Astro deployment scenarios."""

import socket
import sys
from pathlib import Path

import pytest
import yaml

from polaris_harness.cli import main
from polaris_harness.engine import ScenarioEngine
from polaris_harness.framing import Frame, FramingType
from polaris_harness.scenario_service import ScenarioService, load_scenario as load_service_scenario
from polaris_harness.validation import validate_scenario


SCENARIO_ROOT = Path(__file__).parents[1] / "scenarios"
ASTRO_SCENARIOS = sorted(SCENARIO_ROOT.glob("astro-*/scenario.yaml"))


def load_manifest(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.mark.parametrize("scenario_path", ASTRO_SCENARIOS, ids=lambda p: p.parent.name)
def test_astro_scenario_is_schema_valid_and_synthetic(scenario_path: Path):
    scenario = load_manifest(scenario_path)
    assert scenario["source"] == "synthetic"
    assert validate_scenario(scenario) == []


def run_request(engine: ScenarioEngine, text: str) -> list[str]:
    frame = Frame(
        raw_bytes=text.encode("utf-8"),
        text=text,
        framing_type=FramingType.DELIMITER,
        delimiter="#",
    )
    actions = engine.execute_scenario(frame, "astro-contract-test")
    events = engine.execute_actions(actions)
    return [
        event.data["text"]
        for event in events
        if event.data.get("action") == "send"
    ]


@pytest.mark.parametrize(
    ("scenario_name", "requests", "expected"),
    [
        (
            "astro-session-boundary-v1",
            [
                "ASTRO_ENTER session=2#",
                "CAPTURE_COMPLETE session=1 frame=old#",
                "CAPTURE_COMPLETE session=2 frame=1#",
                "CANCEL session=2 frame=1#",
            ],
            [
                "ASTRO_READY session=2#",
                "REJECT stale_session expected=2#",
                "ACCEPT pending_file frame=1#",
                "CANCELLED session=2 frame=1#",
            ],
        ),
        (
            "astro-capture-publication-v1",
            [
                "CAPTURE_COMPLETE id=1 path=/app/sd/IMG_0001.JPG#",
                "FILE_PARTIAL id=1#",
                "FILE_STABLE id=1 path=/app/sd/IMG_0001.JPG sha=good#",
                "CAPTURE_COMPLETE id=1 path=/app/sd/IMG_0001.JPG#",
            ],
            [
                "WAITING_FOR_FILE id=1#",
                "WAITING_FOR_STABLE_FILE id=1#",
                "FILE_ADMITTED id=1 immutable_snapshot=1#",
                "DUPLICATE_IGNORED id=1#",
            ],
        ),
        (
            "astro-solve-admission-v1",
            ["SOLVE frame=1 solved=1 matches=2 logodds=3.0 finite=1#"],
            ["SOLVE_REJECTED reason=insufficient_matches#"],
        ),
        (
            "astro-calibration-model-v1",
            [
                "POINT index=1 az=10 alt=30 solved=1#",
                "POINT index=2 az=130 alt=45 solved=1#",
                "POINT index=3 az=250 alt=55 solved=1#",
                "POINT index=4 holdout=1 solved=1#",
            ],
            [
                "POINT_ACCEPTED index=1#",
                "POINT_ACCEPTED index=2#",
                "MODEL_READY points=3 holdout_required=1#",
                "HOLDOUT_RECORDED rate_control=unavailable#",
            ],
        ),
        (
            "astro-drift-measurement-v1",
            [
                "SOLVE t=0 ra_error=0.10 dec_error=-0.20 roll_error=0.01#",
                "SOLVE t=10000 ra_error=0.20 dec_error=-0.20 roll_error=0.02#",
                "SOLVE t=20000 ra_error=0.30 dec_error=-0.20 roll_error=0.03#",
                "SET_RATE axis=ra value=0.01#",
            ],
            [
                "DRIFT_SAMPLE_ACCEPTED t=0#",
                "DRIFT_SAMPLE_ACCEPTED t=10000#",
                "DRIFT_ESTIMATE ra_rate=0.01 dec_rate=0 roll_rate=0.001 rate_control=unavailable#",
                "RATE_REJECTED reason=unqualified_api#",
            ],
        ),
        (
            "astro-firmware-install-v1",
            [
                "STAGE artifact=FwPkt.zip tree=0#",
                "STAGE artifact=FwPkt.zip tree=1 hash=match manifest=match#",
                "REBOOT_RESULT ap_flap=1 uptime_reset=0#",
            ],
            [
                "INSTALL_REJECTED reason=extracted_tree_required#",
                "STAGED_VERIFIED reboot_pending=1#",
                "INSTALL_UNCERTAIN reason=no_uptime_reset#",
            ],
        ),
        (
            "astro-offset-provenance-v1",
            [
                "OFFSET app=sha-app gimbal=sha-other ledger=match#",
                "AC4A axis=0 rate=0.01#",
            ],
            [
                "OFFSET_REJECTED reason=component_mismatch#",
                "COMMAND_BLOCKED reason=unqualified_rate_api#",
            ],
        ),
    ],
)
def test_astro_scenario_key_paths(scenario_name, requests, expected):
    scenario = load_manifest(SCENARIO_ROOT / scenario_name / "scenario.yaml")
    engine = ScenarioEngine(scenario["id"], scenario)
    actual = []
    for request in requests:
        actual.extend(run_request(engine, request))
    assert actual == expected


def test_validate_cli_fails_closed_for_invalid_manifest(tmp_path, monkeypatch):
    invalid = tmp_path / "scenario.yaml"
    invalid.write_text("schema_version: '1'\nid: !invalid\n", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["polaris-harness", "validate", str(invalid)],
    )
    assert main() == 2


def test_validate_cli_accepts_scenario_directory(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["polaris-harness", "validate", str(SCENARIO_ROOT / "astro-session-boundary-v1")],
    )
    assert main() == 0


def test_scenario_service_real_tcp_smoke():
    scenario = load_service_scenario(
        SCENARIO_ROOT / "astro-session-boundary-v1" / "scenario.yaml"
    )
    service = ScenarioService(scenario)
    port = service.start()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.sendall(b"ASTRO_ENTER session=2#")
            response = bytearray()
            while b"#" not in response:
                response.extend(client.recv(4096))
            assert bytes(response).split(b"#", 1)[0] + b"#" == (
                b"ASTRO_READY session=2#"
            )
    finally:
        service.stop()


def test_replay_cli_runs_ndjson_against_service(monkeypatch):
    scenario_path = SCENARIO_ROOT / "astro-session-boundary-v1"
    scenario = load_service_scenario(scenario_path)
    service = ScenarioService(scenario)
    port = service.start()
    try:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "polaris-harness",
                "replay",
                "--scenario",
                str(scenario_path),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
        )
        assert main() == 0
    finally:
        service.stop()


def test_trace_ingest_requires_sanitisation_and_preserves_hash(tmp_path, monkeypatch):
    source = tmp_path / "trace.ndjson"
    source.write_bytes(b"sanitised trace\n")
    output = tmp_path / "imported"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "polaris-harness",
            "trace",
            "ingest",
            "--input",
            str(source),
            "--output",
            str(output),
        ],
    )
    assert main() == 2
    assert not output.exists()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "polaris-harness",
            "trace",
            "ingest",
            "--input",
            str(source),
            "--output",
            str(output),
            "--sanitised",
            "--source",
            "synthetic",
        ],
    )
    assert main() == 0
    assert (output / "raw" / source.name).read_bytes() == source.read_bytes()
    assert "evidence_id:" in (output / "evidence.yaml").read_text()
