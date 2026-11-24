"""Counterexamples for the v115 guest observations that qualify service readiness."""

import runpy
from pathlib import Path

evaluate = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/arm-system/pgphoto-result.py")
)["evaluate"]


def test_listener_must_belong_to_service():
    row = "0: 00000000:1F90 00000000:0000 0A 0:0 0:0 0 0 0 712 1"
    assert not evaluate(row + "\nsocket:[713]\n", True)["checks"]["owned_8080_listener"]
    assert evaluate(row + "\nsocket:[712]\n", True)["checks"]["owned_8080_listener"]


def test_http_header_alone_cannot_pass_startup():
    report = evaluate(
        "HTTP/1.0 200 OK\r\nContent-Type: multipart/x-mixed-replace", True
    )
    assert report["checks"]["stream_http_response"]
    assert report["status"] == "FAIL"


def test_missing_library_and_no_camera_are_distinct():
    report = "Could not detect any camera\nsp_Gphoto_Init ret -105\nstate:-5;"
    assert evaluate(report, True)["checks"]["expected_camera_boundary"]
    assert not evaluate(report, False)["checks"]["expected_camera_boundary"]
