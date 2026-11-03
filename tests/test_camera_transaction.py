"""Camera transaction and capability degradation contract tests."""

from polaris_harness.camera_transaction import CaptureTransactionGuard
from polaris_harness.session_recovery import SessionGuard


def _guard() -> tuple[SessionGuard, CaptureTransactionGuard, int]:
    session = SessionGuard()
    opened = session.open("client-a")
    camera = CaptureTransactionGuard(session)
    return session, camera, opened.generation


def test_acknowledgement_does_not_complete_and_second_capture_is_blocked():
    _, camera, _ = _guard()

    accepted = camera.begin("capture-1", 100, ["/IMG_0001.JPG"])
    assert accepted.code == "CAPTURE_ACCEPTED"
    assert camera.acknowledge(accepted.capture_id).code == "CAPTURE_ACKNOWLEDGED"
    assert camera.state == "pending"
    blocked = camera.begin("capture-2", 110, ["/IMG_0002.JPG"])
    assert blocked.code == "CAPTURE_BLOCKED_OUTPUT_PENDING"


def test_preexisting_and_unexpected_candidates_do_not_satisfy_output_obligation():
    _, camera, _ = _guard()
    accepted = camera.begin("capture-1", 100, ["/IMG_0001.JPG"])
    capture_id = accepted.capture_id

    old = camera.observe_candidate(
        capture_id, "old", "/IMG_0001.JPG", created_ms=100
    )
    assert old.code == "PREEXISTING_CANDIDATE_DISCARDED"
    unexpected = camera.observe_candidate(
        capture_id, "new", "/IMG_0002.JPG", created_ms=101
    )
    assert unexpected.code == "UNEXPECTED_OUTPUT_DISCARDED"
    missing = camera.finalize(capture_id, transfer_ok=True, restore_ok=True)
    assert missing.code == "OUTPUT_REQUIRED_BEFORE_COMPLETION"
    assert camera.state == "pending"


def test_only_new_expected_candidate_can_complete_after_transfer_and_restore():
    _, camera, _ = _guard()
    accepted = camera.begin("capture-1", 100, ["/IMG_0001.JPG"])
    capture_id = accepted.capture_id
    candidate = camera.observe_candidate(
        capture_id, "new", "/IMG_0001.JPG", created_ms=101
    )
    assert candidate.code == "CANDIDATE_ACCEPTED"
    complete = camera.finalize(capture_id, transfer_ok=True, restore_ok=True)
    assert complete.code == "CAPTURE_COMPLETE"
    assert camera.state == "idle"
    assert camera.active is None


def test_transfer_failure_is_unknown_and_never_implicitly_retried():
    _, camera, _ = _guard()
    accepted = camera.begin("capture-1", 100, ["/IMG_0001.JPG"])
    camera.observe_candidate(accepted.capture_id, "new", "/IMG_0001.JPG", 101)

    failed = camera.finalize(
        accepted.capture_id, transfer_ok=False, restore_ok=True
    )
    assert failed.code == "OUTCOME_UNKNOWN_TRANSFER_FAILED"
    assert camera.state == "unknown"
    assert camera.active is None


def test_restore_failure_is_degraded_not_false_success():
    _, camera, _ = _guard()
    accepted = camera.begin("capture-1", 100, ["/IMG_0001.JPG"])
    camera.observe_candidate(accepted.capture_id, "new", "/IMG_0001.JPG", 101)

    degraded = camera.finalize(
        accepted.capture_id, transfer_ok=True, restore_ok=False
    )
    assert degraded.code == "CAPTURE_DEGRADED_RESTORE_FAILED"
    assert camera.state == "degraded"
    assert camera.capabilities.degraded


def test_stale_capture_after_reconnect_cannot_complete_current_state():
    session = SessionGuard()
    first = session.open("client-a")
    camera = CaptureTransactionGuard(session)
    accepted = camera.begin("capture-1", 100, ["/IMG_0001.JPG"])
    session.reconnect("client-b")

    stale = camera.observe_candidate(
        accepted.capture_id, "old", "/IMG_0001.JPG", 101
    )
    assert stale.code == "STALE_CANDIDATE_DISCARDED"
    assert camera.fail(accepted.capture_id, "late").code == "STALE_FAILURE_DISCARDED"
    assert first.generation < session.generation


def test_capability_degradation_is_independent_of_connected_state():
    _, camera, _ = _guard()
    camera.capabilities.reconnect_observation(
        connected=True, vendor_control=True, preview=False
    )
    assert camera.capabilities.connected
    assert camera.capabilities.vendor_control
    assert camera.capabilities.degraded
    camera.capabilities.reconnect_observation(
        connected=True, vendor_control=True, preview=True
    )
    assert not camera.capabilities.degraded
