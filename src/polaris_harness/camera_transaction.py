"""Camera capture transaction and capability-degradation contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .session_recovery import SessionGuard


@dataclass(frozen=True)
class CameraTransactionEvent:
    """Result of a camera transaction transition."""

    code: str
    capture_id: str | None = None
    candidate_id: str | None = None
    accepted: bool = True
    reason: str | None = None


@dataclass
class ActiveCapture:
    capture_id: str
    request_id: str
    generation: int
    started_ms: int
    expected_paths: frozenset[str]
    candidate_id: str | None = None
    candidate_path: str | None = None


@dataclass
class CameraCapabilityState:
    """Observed capability state independent of transport connectivity."""

    connected: bool = False
    vendor_control: bool = False
    preview: bool = False
    degraded: bool = False
    unknown_reason: str | None = None

    def set_capability(self, name: str, available: bool) -> None:
        if name not in {"vendor_control", "preview"}:
            raise ValueError(f"unknown camera capability: {name}")
        setattr(self, name, available)
        self.degraded = self.connected and not (self.vendor_control and self.preview)

    def reconnect_observation(
        self, *, connected: bool, vendor_control: bool, preview: bool
    ) -> None:
        self.connected = connected
        self.vendor_control = vendor_control
        self.preview = preview
        self.degraded = connected and not (vendor_control and preview)
        self.unknown_reason = None if connected else "camera state not observed"


class CaptureTransactionGuard:
    """Prevent stale files and partial success from becoming capture success."""

    def __init__(self, session: SessionGuard) -> None:
        self.session = session
        self.sequence = 0
        self.active: ActiveCapture | None = None
        self.state = "idle"
        self.capabilities = CameraCapabilityState()

    def begin(
        self, request_id: str, now_ms: int, expected_paths: Iterable[str]
    ) -> CameraTransactionEvent:
        """Start one output-obligated capture; no second capture is implicit."""
        if not self.session.active:
            return CameraTransactionEvent(
                "NO_ACTIVE_SESSION", accepted=False
            )
        if self.active is not None:
            return CameraTransactionEvent(
                "CAPTURE_BLOCKED_OUTPUT_PENDING",
                self.active.capture_id,
                accepted=False,
            )
        request = self.session.begin(request_id, now_ms, mutating=True)
        if not request.accepted:
            return CameraTransactionEvent(request.code, accepted=False)
        self.sequence += 1
        capture_id = f"{request.generation}:{self.sequence}"
        self.active = ActiveCapture(
            capture_id,
            request_id,
            request.generation,
            now_ms,
            frozenset(expected_paths),
        )
        self.state = "pending"
        return CameraTransactionEvent("CAPTURE_ACCEPTED", capture_id=capture_id)

    def acknowledge(self, capture_id: str) -> CameraTransactionEvent:
        """Represent an acknowledgement without treating it as completion."""
        if not self._current(capture_id):
            return CameraTransactionEvent(
                "STALE_CAPTURE_ACK_DISCARDED", capture_id, accepted=False
            )
        return CameraTransactionEvent("CAPTURE_ACKNOWLEDGED", capture_id)

    def observe_candidate(
        self,
        capture_id: str,
        candidate_id: str,
        path: str,
        created_ms: int,
    ) -> CameraTransactionEvent:
        """Accept only a new, expected output belonging to this transaction."""
        active = self.active
        if not self._current(capture_id):
            return CameraTransactionEvent(
                "STALE_CANDIDATE_DISCARDED", capture_id, candidate_id, False
            )
        if created_ms <= active.started_ms:
            return CameraTransactionEvent(
                "PREEXISTING_CANDIDATE_DISCARDED", capture_id, candidate_id, False
            )
        if path not in active.expected_paths:
            return CameraTransactionEvent(
                "UNEXPECTED_OUTPUT_DISCARDED", capture_id, candidate_id, False
            )
        if active.candidate_id is not None:
            return CameraTransactionEvent(
                "DUPLICATE_CANDIDATE_DISCARDED", capture_id, candidate_id, False
            )
        active.candidate_id = candidate_id
        active.candidate_path = path
        return CameraTransactionEvent("CANDIDATE_ACCEPTED", capture_id, candidate_id)

    def finalize(
        self,
        capture_id: str,
        *,
        transfer_ok: bool,
        restore_ok: bool,
    ) -> CameraTransactionEvent:
        """Finalize only after publication and state restoration are evaluated."""
        active = self.active
        if not self._current(capture_id):
            return CameraTransactionEvent(
                "STALE_FINALIZE_DISCARDED", capture_id, accepted=False
            )
        if active.candidate_id is None:
            return CameraTransactionEvent(
                "OUTPUT_REQUIRED_BEFORE_COMPLETION", capture_id, accepted=False
            )
        self.active = None
        if not transfer_ok:
            self.state = "unknown"
            return CameraTransactionEvent(
                "OUTCOME_UNKNOWN_TRANSFER_FAILED", capture_id, active.candidate_id, False
            )
        if not restore_ok:
            self.state = "degraded"
            self.capabilities.degraded = True
            return CameraTransactionEvent(
                "CAPTURE_DEGRADED_RESTORE_FAILED",
                capture_id,
                active.candidate_id,
                False,
            )
        self.state = "idle"
        self.session.complete(active.request_id, active.generation)
        return CameraTransactionEvent(
            "CAPTURE_COMPLETE", capture_id, active.candidate_id
        )

    def fail(self, capture_id: str, reason: str) -> CameraTransactionEvent:
        """End a partial operation as unknown without a duplicate shutter."""
        if not self._current(capture_id):
            return CameraTransactionEvent(
                "STALE_FAILURE_DISCARDED", capture_id, accepted=False
            )
        self.active = None
        self.state = "unknown"
        return CameraTransactionEvent(
            "OUTCOME_UNKNOWN", capture_id, accepted=False, reason=reason
        )

    def _current(self, capture_id: str) -> bool:
        return (
            self.active is not None
            and self.active.capture_id == capture_id
            and self.active.generation == self.session.generation
            and self.session.active
        )
