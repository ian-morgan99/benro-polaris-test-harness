"""Deterministic session-generation and progress-aware recovery contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RecoveryEvent:
    """Result of a session or operation transition."""

    code: str
    generation: int
    request_id: str | None = None
    accepted: bool = True
    reason: str | None = None


@dataclass
class PendingOperation:
    request_id: str
    generation: int
    started_ms: int
    last_progress_ms: int
    mutating: bool


class SessionGuard:
    """Guard request ownership and reject stale or unsafe late events.

    This model does not open sockets or retry operations. It is the deterministic
    consumer-visible contract used by recovery scenarios: each reconnect creates
    a new generation, completion must name that generation, and a timeout/stall
    becomes an unknown outcome rather than an implicit retry.
    """

    def __init__(self) -> None:
        self.generation = 0
        self.active = False
        self.owner: str | None = None
        self._pending: dict[tuple[int, str], PendingOperation] = {}

    def open(self, owner: str) -> RecoveryEvent:
        """Open one session, rejecting a competing owner."""
        if self.active:
            return RecoveryEvent(
                "SESSION_OWNER_BUSY",
                self.generation,
                accepted=False,
                reason=f"session owned by {self.owner}",
            )
        self.generation += 1
        self.active = True
        self.owner = owner
        return RecoveryEvent("SESSION_OPENED", self.generation)

    def close(self) -> RecoveryEvent:
        """Close the current session without making old events current."""
        if not self.active:
            return RecoveryEvent(
                "SESSION_ALREADY_CLOSED", self.generation, accepted=False
            )
        self.active = False
        self.owner = None
        # Pending work belongs to the closed generation and must not leak into
        # the next session. The generation check still rejects late events.
        self._pending.clear()
        return RecoveryEvent("SESSION_CLOSED", self.generation)

    def reconnect(self, owner: str) -> tuple[RecoveryEvent, RecoveryEvent]:
        """Close and open a new generation without carrying stale state forward."""
        closed = self.close()
        opened = self.open(owner)
        return closed, opened

    def begin(
        self, request_id: str, now_ms: int, *, mutating: bool = False
    ) -> RecoveryEvent:
        """Start one request in the active generation."""
        if not self.active:
            return RecoveryEvent(
                "NO_ACTIVE_SESSION",
                self.generation,
                request_id,
                accepted=False,
            )
        key = (self.generation, request_id)
        if key in self._pending:
            return RecoveryEvent(
                "DUPLICATE_REQUEST_REJECTED",
                self.generation,
                request_id,
                accepted=False,
            )
        self._pending[key] = PendingOperation(
            request_id, self.generation, now_ms, now_ms, mutating
        )
        return RecoveryEvent("REQUEST_ACCEPTED", self.generation, request_id)

    def progress(
        self, request_id: str, generation: int, now_ms: int
    ) -> RecoveryEvent:
        """Record progress only for the current generation/request."""
        operation = self._pending.get((generation, request_id))
        if not self.active or generation != self.generation or operation is None:
            return RecoveryEvent(
                "LATE_PROGRESS_DISCARDED",
                self.generation,
                request_id,
                accepted=False,
            )
        operation.last_progress_ms = now_ms
        return RecoveryEvent("PROGRESS_RECORDED", generation, request_id)

    def complete(
        self, request_id: str, generation: int
    ) -> RecoveryEvent:
        """Complete only the exact current generation/request pair."""
        operation = self._pending.get((generation, request_id))
        if not self.active or generation != self.generation or operation is None:
            return RecoveryEvent(
                "LATE_COMPLETION_DISCARDED",
                self.generation,
                request_id,
                accepted=False,
            )
        del self._pending[(generation, request_id)]
        return RecoveryEvent("REQUEST_COMPLETED", generation, request_id)

    def cancel(self, request_id: str, generation: int) -> RecoveryEvent:
        """Cancel the exact operation without retrying it."""
        if (generation, request_id) not in self._pending:
            return RecoveryEvent(
                "CANCEL_TARGET_UNKNOWN",
                self.generation,
                request_id,
                accepted=False,
            )
        del self._pending[(generation, request_id)]
        return RecoveryEvent("REQUEST_CANCELLED", generation, request_id)

    def expire(
        self, now_ms: int, *, no_progress_timeout_ms: int, safety_ceiling_ms: int
    ) -> list[RecoveryEvent]:
        """Expire stalls and safety ceilings without generating retries."""
        events: list[RecoveryEvent] = []
        for key, operation in list(self._pending.items()):
            if now_ms - operation.started_ms >= safety_ceiling_ms:
                del self._pending[key]
                events.append(
                    RecoveryEvent(
                        "OUTCOME_UNKNOWN_SAFETY_CEILING",
                        operation.generation,
                        operation.request_id,
                        accepted=False,
                    )
                )
            elif now_ms - operation.last_progress_ms >= no_progress_timeout_ms:
                del self._pending[key]
                events.append(
                    RecoveryEvent(
                        "OUTCOME_UNKNOWN_NO_PROGRESS",
                        operation.generation,
                        operation.request_id,
                        accepted=False,
                    )
                )
        return events

    def pending(self, generation: int | None = None) -> tuple[PendingOperation, ...]:
        """Return pending operations, optionally limited to one generation."""
        return tuple(
            operation
            for operation in self._pending.values()
            if generation is None or operation.generation == generation
        )
