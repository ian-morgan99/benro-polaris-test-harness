"""Pure, non-mutating firmware-install state simulation.

This module models the evidence required to call an install verified. It never
opens a device connection, copies firmware, reboots anything, or executes a
payload.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class InstallState(Enum):
    NOT_STAGED = "not-staged"
    STAGED_UNVERIFIED = "staged-unverified"
    STAGED_VERIFIED = "staged-verified"
    REBOOT_UNCERTAIN = "reboot-uncertain"
    INSTALLED_VERIFIED = "installed-verified"
    ROLLED_BACK = "rolled-back"


@dataclass(frozen=True)
class InstallObservation:
    state_before: InstallState
    state_after: InstallState
    code: str
    message: str
    details: dict[str, Any]


class InstallSimulator:
    """Fail-closed install state machine for disposable harness fixtures."""

    def __init__(self) -> None:
        self.state = InstallState.NOT_STAGED
        self.history: list[InstallObservation] = []

    def _record(
        self,
        state_before: InstallState,
        state_after: InstallState,
        code: str,
        message: str,
        **details: Any,
    ) -> InstallObservation:
        observation = InstallObservation(
            state_before=state_before,
            state_after=state_after,
            code=code,
            message=message,
            details=details,
        )
        self.state = state_after
        self.history.append(observation)
        return observation

    def stage(
        self,
        *,
        extracted_tree: bool,
        hash_match: bool,
        manifest_match: bool,
        camera_idle: bool,
        free_space: bool,
        rollback_available: bool,
    ) -> InstallObservation:
        """Evaluate staging evidence without touching a filesystem."""
        before = self.state
        if not extracted_tree:
            return self._record(
                before,
                InstallState.STAGED_UNVERIFIED,
                "extracted_tree_required",
                "An extracted FwPkt tree is required; ZIP-only staging is not deployable.",
            )
        if not hash_match:
            return self._record(
                before,
                InstallState.STAGED_UNVERIFIED,
                "candidate_hash_mismatch",
                "Candidate hash does not match its immutable manifest.",
            )
        if not manifest_match:
            return self._record(
                before,
                InstallState.STAGED_UNVERIFIED,
                "manifest_mismatch",
                "Install manifest does not describe this candidate.",
            )
        if not camera_idle:
            return self._record(
                before,
                InstallState.STAGED_UNVERIFIED,
                "camera_busy",
                "Camera activity must be idle before installation.",
            )
        if not free_space:
            return self._record(
                before,
                InstallState.STAGED_UNVERIFIED,
                "insufficient_space",
                "Device free space is insufficient for the staged tree.",
            )
        if not rollback_available:
            return self._record(
                before,
                InstallState.STAGED_UNVERIFIED,
                "rollback_missing",
                "A last-known-good rollback artifact is required.",
            )
        return self._record(
            before,
            InstallState.STAGED_VERIFIED,
            "staged_verified",
            "Candidate tree, manifest, idle state, space, and rollback are verified.",
        )

    def reboot(
        self,
        *,
        ap_flap: bool,
        uptime_reset: bool,
        build_identity_match: bool,
        hashes_match: bool,
    ) -> InstallObservation:
        """Evaluate post-reboot evidence; an AP flap is never sufficient."""
        before = self.state
        if before != InstallState.STAGED_VERIFIED:
            return self._record(
                before,
                InstallState.REBOOT_UNCERTAIN,
                "stage_not_verified",
                "Reboot evidence cannot be evaluated before verified staging.",
                ap_flap=ap_flap,
            )
        if not uptime_reset:
            return self._record(
                before,
                InstallState.REBOOT_UNCERTAIN,
                "no_uptime_reset",
                "Network loss without an uptime reset does not prove reboot.",
                ap_flap=ap_flap,
            )
        if not build_identity_match:
            return self._record(
                before,
                InstallState.REBOOT_UNCERTAIN,
                "build_identity_mismatch",
                "Post-boot firmware identity does not match the candidate.",
                ap_flap=ap_flap,
            )
        if not hashes_match:
            return self._record(
                before,
                InstallState.REBOOT_UNCERTAIN,
                "installed_hash_mismatch",
                "Post-boot mandatory file hashes do not match the candidate.",
                ap_flap=ap_flap,
            )
        return self._record(
            before,
            InstallState.INSTALLED_VERIFIED,
            "installed_verified",
            "Reboot, build identity, and mandatory file hashes are verified.",
            ap_flap=ap_flap,
        )

    def rollback(self, *, available: bool) -> InstallObservation:
        """Record a controlled rollback decision without performing it."""
        before = self.state
        if not available:
            return self._record(
                before,
                InstallState.REBOOT_UNCERTAIN,
                "rollback_missing",
                "Rollback cannot be authorised because the known-good artifact is absent.",
            )
        if before not in {
            InstallState.REBOOT_UNCERTAIN,
            InstallState.STAGED_UNVERIFIED,
        }:
            return self._record(
                before,
                before,
                "rollback_not_required",
                "Rollback is only available for an uncertain or unverified candidate.",
            )
        return self._record(
            before,
            InstallState.ROLLED_BACK,
            "rollback_ready",
            "Rollback is authorised in the simulation; no device was changed.",
        )
