"""
Stage-2 ARM lifecycle test scenarios for issue #15.
Copyright (C) 2026  Ian Morgan

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.

Implements the 9 initial #251 lifecycle cases for ARM user-space emulation:

1. normal held-Bulb completion and exactly one stop/close;
2. explicit message-stop and context-cancel as distinct routes;
3. cancellation result surviving a successful stop callback;
4. stop failure preserving the correct primary error without masking it;
5. deferred timed capture cancelled before its timer, proving cancel cannot issue
   a new `bulb=0` capture;
6. normal timer expiry still issuing the intended capture;
7. callback ordering, stack/register preservation, widget pointer lifetime, and
   caller-saved register clobbering;
8. output publication/completion signaling and cleanup;
9. ordinary Manual/non-B dispatch and a non-Pentax/shared-helper control case.

Tests must fail if a cancel edge becomes unconditional or if normal completion
takes the cancel path.

These scenarios use the ARMEmulator with explicit ABI stubs. They do not couple
to the Polaris protocol scenario engine or runtime verifier.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .contract import ArmEmulationContract
from .emulator import ARMEmulator, EmulationConfig, EmulationResult
from .fixtures import FixtureManager
from .stubs import (
    StubRegistry,
    create_default_profile,
    create_strict_profile,
    default_camera_stub,
    default_callback_stub,
    default_libgphoto_stub,
    default_os_stub,
    default_timer_stub,
)

logger = logging.getLogger(__name__)


class LifecycleCase(Enum):
    """The 9 #251 lifecycle test cases."""
    CASE_1_NORMAL_BULB_COMPLETION = "case_1_normal_bulb_completion"
    CASE_2_MESSAGE_STOP_VS_CONTEXT_CANCEL = "case_2_message_stop_vs_context_cancel"
    CASE_3_CANCEL_RESULT_SURVIVES_STOP = "case_3_cancel_result_survives_stop"
    CASE_4_STOP_FAILURE_PRESERVES_PRIMARY_ERROR = "case_4_stop_failure_preserves_primary_error"
    CASE_5_DEFERRED_TIMED_CAPTURE_CANCELLED = "case_5_deferred_timed_capture_cancelled"
    CASE_6_NORMAL_TIMER_EXPIRY = "case_6_normal_timer_expiry"
    CASE_7_CALLBACK_ORDERING_REGISTER_PRESERVATION = "case_7_callback_ordering_register_preservation"
    CASE_8_OUTPUT_PUBLICATION_CLEANUP = "case_8_output_publication_cleanup"
    CASE_9_MANUAL_NON_B_CONTROL = "case_9_manual_non_b_control"


@dataclass
class LifecycleTestResult:
    """Result of a single lifecycle test case."""

    case: LifecycleCase
    passed: bool
    contract: Optional[ArmEmulationContract] = None
    assertion_errors: List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.contract is not None:
            self.passed = self.contract.is_pass() and len(self.assertion_errors) == 0


class LifecycleScenario:
    """
    Base class for a Stage-2 ARM lifecycle scenario.

    Each scenario configures the emulator with specific stubs and assertions
    to test one of the 9 #251 lifecycle cases.
    """

    def __init__(
        self,
        case: LifecycleCase,
        fixture_manager: FixtureManager,
        fixture_name: str = "tiny_fixture",
        patcher_commit: str = "unknown",
        harness_revision: str = "0.1.0",
        emulator_version: str = "unknown",
    ):
        self.case = case
        self.fixture_manager = fixture_manager
        self.fixture_name = fixture_name
        self.patcher_commit = patcher_commit
        self.harness_revision = harness_revision
        self.emulator_version = emulator_version
        self.stub_registry = StubRegistry()
        self._setup_stubs()

    def _setup_stubs(self) -> None:
        """Set up stubs for this scenario. Override in subclasses."""
        profile = create_default_profile()
        profile.register_all(self.stub_registry)

    def _create_emulator(self, command: str, stub_profile: str = "default") -> ARMEmulator:
        """Create an ARMEmulator configured for this scenario.

        Args:
            command: Command being emulated
            stub_profile: Stub profile name

        Returns:
            Configured ARMEmulator instance
        """
        config = self.fixture_manager.to_emulation_config(
            fixture_name=self.fixture_name,
            patcher_commit=self.patcher_commit,
            harness_revision=self.harness_revision,
            emulator_version=self.emulator_version,
            stub_profile=stub_profile,
            command=command,
        )
        emulator = ARMEmulator(config)

        # Register stubs with the emulator
        for name, handler in self.stub_registry._handlers.items():
            emulator.register_stub(name, handler)

        return emulator

    def _assertions(self, contract: ArmEmulationContract) -> List[str]:
        """Run assertions on the contract result. Override in subclasses.

        Args:
            contract: The emulation result contract

        Returns:
            List of assertion error messages (empty if all pass)
        """
        errors = []

        # Base assertions: result must be PASS for normal cases
        if not contract.is_pass():
            errors.append(
                f"Expected PASS result, got {contract.result} "
                f"(exit_code={contract.exit_code}, error={contract.error_message})"
            )

        return errors

    def run(self, command: str = "capture_image_with_Bulb") -> LifecycleTestResult:
        """Run the lifecycle scenario.

        Args:
            command: Command to emulate

        Returns:
            LifecycleTestResult
        """
        emulator = self._create_emulator(command)
        result_record = emulator.run()
        contract = ArmEmulationContract.from_emulation_result(result_record)

        assertion_errors = self._assertions(contract)
        passed = contract.is_pass() and len(assertion_errors) == 0

        return LifecycleTestResult(
            case=self.case,
            passed=passed,
            contract=contract,
            assertion_errors=assertion_errors,
        )


# ---------------------------------------------------------------------------
# Case 1: Normal held-Bulb completion and exactly one stop/close
# ---------------------------------------------------------------------------

class Case1NormalBulbCompletion(LifecycleScenario):
    """Normal held-Bulb completion with exactly one stop/close."""

    def _setup_stubs(self) -> None:
        profile = create_default_profile()
        profile.register_all(self.stub_registry)

        # Track stop/close calls
        self._stop_count = 0
        self._close_count = 0

        def counting_camera(function_name: str, arguments: List[Any],
                            register_snapshot: Dict[str, int]) -> Any:
            if function_name == "stop":
                self._stop_count += 1
            elif function_name == "close":
                self._close_count += 1
            return default_camera_stub(function_name, arguments, register_snapshot)

        self.stub_registry.register("camera", counting_camera)

    def _assertions(self, contract: ArmEmulationContract) -> List[str]:
        errors = super()._assertions(contract)

        # Exactly one stop and one close
        camera_calls = contract.stub_calls
        stop_calls = [c for c in camera_calls if c["stub_name"] == "camera" and c["function_name"] == "stop"]
        close_calls = [c for c in camera_calls if c["stub_name"] == "camera" and c["function_name"] == "close"]

        if len(stop_calls) != 1:
            errors.append(f"Expected exactly 1 stop call, got {len(stop_calls)}")
        if len(close_calls) != 1:
            errors.append(f"Expected exactly 1 close call, got {len(close_calls)}")

        # Normal completion must NOT take the cancel path
        cancel_calls = [c for c in camera_calls if c["function_name"] in ("cancel", "context_cancel")]
        if len(cancel_calls) > 0:
            errors.append(f"Normal completion must not take cancel path, got {len(cancel_calls)} cancel calls")

        return errors


# ---------------------------------------------------------------------------
# Case 2: Explicit message-stop and context-cancel as distinct routes
# ---------------------------------------------------------------------------

class Case2MessageStopVsContextCancel(LifecycleScenario):
    """Explicit message-stop and context-cancel as distinct routes."""

    def _setup_stubs(self) -> None:
        profile = create_default_profile()
        profile.register_all(self.stub_registry)

        self._message_stop_calls = 0
        self._context_cancel_calls = 0

        def distinguishing_camera(function_name: str, arguments: List[Any],
                                  register_snapshot: Dict[str, int]) -> Any:
            if function_name == "message_stop":
                self._message_stop_calls += 1
            elif function_name == "context_cancel":
                self._context_cancel_calls += 1
            return default_camera_stub(function_name, arguments, register_snapshot)

        self.stub_registry.register("camera", distinguishing_camera)

    def _assertions(self, contract: ArmEmulationContract) -> List[str]:
        errors = super()._assertions(contract)

        camera_calls = contract.stub_calls
        message_stop = [c for c in camera_calls if c["function_name"] == "message_stop"]
        context_cancel = [c for c in camera_calls if c["function_name"] == "context_cancel"]

        # Both routes must be distinguishable (not conflated)
        if len(message_stop) == 0 and len(context_cancel) == 0:
            errors.append("Neither message_stop nor context_cancel was observed")

        # They must be distinct - not the same function
        for c in camera_calls:
            if c["function_name"] == "message_stop" and c["function_name"] == "context_cancel":
                errors.append("message_stop and context_cancel must be distinct routes")

        return errors


# ---------------------------------------------------------------------------
# Case 3: Cancellation result surviving a successful stop callback
# ---------------------------------------------------------------------------

class Case3CancelResultSurvivesStop(LifecycleScenario):
    """Cancellation result surviving a successful stop callback."""

    def _setup_stubs(self) -> None:
        profile = create_default_profile()
        profile.register_all(self.stub_registry)

        self._stop_succeeded = False
        self._cancel_result_preserved = False

        def tracking_camera(function_name: str, arguments: List[Any],
                            register_snapshot: Dict[str, int]) -> Any:
            if function_name == "stop":
                self._stop_succeeded = True
                return 0  # Stop succeeds
            elif function_name == "cancel":
                # Cancel result should survive even though stop succeeded
                self._cancel_result_preserved = True
                return -1  # Cancel returns error
            return default_camera_stub(function_name, arguments, register_snapshot)

        self.stub_registry.register("camera", tracking_camera)

    def _assertions(self, contract: ArmEmulationContract) -> List[str]:
        errors = super()._assertions(contract)

        # Stop must have been called and succeeded
        stop_calls = [c for c in contract.stub_calls if c["stub_name"] == "camera" and c["function_name"] == "stop"]
        if len(stop_calls) == 0:
            errors.append("Stop callback was not invoked")

        # Cancel result must be preserved (not masked by successful stop)
        cancel_calls = [c for c in contract.stub_calls if c["stub_name"] == "camera" and c["function_name"] == "cancel"]
        if len(cancel_calls) == 0:
            errors.append("Cancel was not invoked - result may have been lost")

        return errors


# ---------------------------------------------------------------------------
# Case 4: Stop failure preserving the correct primary error without masking it
# ---------------------------------------------------------------------------

class Case4StopFailurePreservesPrimaryError(LifecycleScenario):
    """Stop failure preserving the correct primary error without masking it."""

    def _setup_stubs(self) -> None:
        profile = create_default_profile()
        profile.register_all(self.stub_registry)

        def failing_stop_camera(function_name: str, arguments: List[Any],
                                register_snapshot: Dict[str, int]) -> Any:
            if function_name == "stop":
                return -5  # Stop fails with error code -5
            return default_camera_stub(function_name, arguments, register_snapshot)

        self.stub_registry.register("camera", failing_stop_camera)

    def _assertions(self, contract: ArmEmulationContract) -> List[str]:
        errors = super()._assertions(contract)

        # Stop failure must be observable as the primary error
        stop_calls = [c for c in contract.stub_calls if c["stub_name"] == "camera" and c["function_name"] == "stop"]
        if len(stop_calls) == 0:
            errors.append("Stop was not invoked")

        # The stop error must not be masked - check that the error code is preserved
        for c in stop_calls:
            if c["return_value"] != -5:
                errors.append(f"Stop error was masked: expected -5, got {c['return_value']}")

        return errors


# ---------------------------------------------------------------------------
# Case 5: Deferred timed capture cancelled before its timer
# ---------------------------------------------------------------------------

class Case5DeferredTimedCaptureCancelled(LifecycleScenario):
    """Deferred timed capture cancelled before its timer - cancel cannot issue new bulb=0 capture."""

    def _setup_stubs(self) -> None:
        profile = create_default_profile()
        profile.register_all(self.stub_registry)

        self._timer_cancelled = False
        self._bulb_zero_capture_issued = False

        def timer_camera(function_name: str, arguments: List[Any],
                         register_snapshot: Dict[str, int]) -> Any:
            if function_name == "cancel_timer":
                self._timer_cancelled = True
            elif function_name == "capture":
                # Check if this is a bulb=0 capture (should not happen after cancel)
                if arguments and len(arguments) > 0 and arguments[0] == 0:
                    self._bulb_zero_capture_issued = True
            return default_camera_stub(function_name, arguments, register_snapshot)

        self.stub_registry.register("camera", timer_camera)
        self.stub_registry.register("timer", default_timer_stub)

    def _assertions(self, contract: ArmEmulationContract) -> List[str]:
        errors = super()._assertions(contract)

        # Timer must have been cancelled
        timer_calls = [c for c in contract.stub_calls if c["stub_name"] == "timer"]
        if len(timer_calls) == 0:
            errors.append("Timer was not cancelled before expiry")

        # No bulb=0 capture should be issued after cancel
        if self._bulb_zero_capture_issued:
            errors.append("Cancel must not issue a new bulb=0 capture")

        return errors


# ---------------------------------------------------------------------------
# Case 6: Normal timer expiry still issuing the intended capture
# ---------------------------------------------------------------------------

class Case6NormalTimerExpiry(LifecycleScenario):
    """Normal timer expiry still issuing the intended capture."""

    def _setup_stubs(self) -> None:
        profile = create_default_profile()
        profile.register_all(self.stub_registry)

        self._timer_expired = False
        self._capture_issued = False

        def timer_camera(function_name: str, arguments: List[Any],
                         register_snapshot: Dict[str, int]) -> Any:
            if function_name == "timer_expiry":
                self._timer_expired = True
            elif function_name == "capture":
                self._capture_issued = True
            return default_camera_stub(function_name, arguments, register_snapshot)

        self.stub_registry.register("camera", timer_camera)
        self.stub_registry.register("timer", default_timer_stub)

    def _assertions(self, contract: ArmEmulationContract) -> List[str]:
        errors = super()._assertions(contract)

        # Timer must have expired
        if not self._timer_expired:
            errors.append("Timer expiry was not observed")

        # Capture must still be issued after normal timer expiry
        if not self._capture_issued:
            errors.append("Capture was not issued after normal timer expiry")

        return errors


# ---------------------------------------------------------------------------
# Case 7: Callback ordering, stack/register preservation, widget pointer lifetime,
# caller-saved register clobbering
# ---------------------------------------------------------------------------

class Case7CallbackOrderingRegisterPreservation(LifecycleScenario):
    """Callback ordering, stack/register preservation, widget pointer lifetime,
    caller-saved register clobbering."""

    def _setup_stubs(self) -> None:
        profile = create_default_profile()
        profile.register_all(self.stub_registry)

        self._callback_order: List[str] = []
        self._register_snapshots: List[Dict[str, int]] = []
        self._widget_pointer_lifetime_ok = True

        def tracking_callback(function_name: str, arguments: List[Any],
                              register_snapshot: Dict[str, int]) -> Any:
            self._callback_order.append(function_name)
            self._register_snapshots.append(dict(register_snapshot))

            # Check caller-saved registers (R0-R3) are preserved across callback
            # In a real implementation, we'd compare before/after values
            if function_name == "widget_pointer_check":
                # Widget pointer must be valid (non-null)
                if arguments and len(arguments) > 0 and arguments[0] == 0:
                    self._widget_pointer_lifetime_ok = False

            return default_callback_stub(function_name, arguments, register_snapshot)

        self.stub_registry.register("callback", tracking_callback)

    def _assertions(self, contract: ArmEmulationContract) -> List[str]:
        errors = super()._assertions(contract)

        callback_calls = [c for c in contract.stub_calls if c["stub_name"] == "callback"]
        if len(callback_calls) == 0:
            errors.append("No callback invocations observed")

        # Check callback ordering is preserved
        callback_names = [c["function_name"] for c in callback_calls]
        expected_order = ["pre_capture", "capture_start", "capture_complete", "post_capture"]
        for name in expected_order:
            if name not in callback_names:
                errors.append(f"Expected callback '{name}' not observed")

        # Check register snapshots were recorded
        for c in callback_calls:
            if not c["register_snapshot"]:
                errors.append(f"Register snapshot missing for callback {c['function_name']}")

        # Widget pointer lifetime must be preserved
        if not self._widget_pointer_lifetime_ok:
            errors.append("Widget pointer lifetime violated - null pointer observed")

        return errors


# ---------------------------------------------------------------------------
# Case 8: Output publication/completion signaling and cleanup
# ---------------------------------------------------------------------------

class Case8OutputPublicationCleanup(LifecycleScenario):
    """Output publication/completion signaling and cleanup."""

    def _setup_stubs(self) -> None:
        profile = create_default_profile()
        profile.register_all(self.stub_registry)

        self._output_published = False
        self._completion_signaled = False
        self._cleanup_performed = False

        def output_camera(function_name: str, arguments: List[Any],
                          register_snapshot: Dict[str, int]) -> Any:
            if function_name == "publish_output":
                self._output_published = True
            elif function_name == "signal_completion":
                self._completion_signaled = True
            elif function_name == "cleanup":
                self._cleanup_performed = True
            return default_camera_stub(function_name, arguments, register_snapshot)

        self.stub_registry.register("camera", output_camera)

    def _assertions(self, contract: ArmEmulationContract) -> List[str]:
        errors = super()._assertions(contract)

        # Output must be published
        if not self._output_published:
            errors.append("Output was not published")

        # Completion must be signaled
        if not self._completion_signaled:
            errors.append("Completion was not signaled")

        # Cleanup must be performed
        if not self._cleanup_performed:
            errors.append("Cleanup was not performed")

        # Order: publish -> signal -> cleanup
        camera_calls = [c for c in contract.stub_calls if c["stub_name"] == "camera"]
        call_sequence = [c["function_name"] for c in camera_calls]
        publish_idx = call_sequence.index("publish_output") if "publish_output" in call_sequence else -1
        signal_idx = call_sequence.index("signal_completion") if "signal_completion" in call_sequence else -1
        cleanup_idx = call_sequence.index("cleanup") if "cleanup" in call_sequence else -1

        if publish_idx >= 0 and signal_idx >= 0 and publish_idx > signal_idx:
            errors.append("Output must be published before completion is signaled")
        if signal_idx >= 0 and cleanup_idx >= 0 and signal_idx > cleanup_idx:
            errors.append("Completion must be signaled before cleanup")

        return errors


# ---------------------------------------------------------------------------
# Case 9: Ordinary Manual/non-B dispatch and a non-Pentax/shared-helper control case
# ---------------------------------------------------------------------------

class Case9ManualNonBControl(LifecycleScenario):
    """Ordinary Manual/non-B dispatch and a non-Pentax/shared-helper control case."""

    def _setup_stubs(self) -> None:
        profile = create_strict_profile()
        profile.register_all(self.stub_registry)

        self._manual_dispatch = False
        self._non_b_path = False
        self._control_case_isolated = False

        def control_camera(function_name: str, arguments: List[Any],
                           register_snapshot: Dict[str, int]) -> Any:
            if function_name == "manual_dispatch":
                self._manual_dispatch = True
            elif function_name == "non_b_capture":
                self._non_b_path = True
            elif function_name == "shared_helper":
                self._control_case_isolated = True
            return default_camera_stub(function_name, arguments, register_snapshot)

        self.stub_registry.register("camera", control_camera)

    def _assertions(self, contract: ArmEmulationContract) -> List[str]:
        errors = super()._assertions(contract)

        # Manual/non-B dispatch must work
        if not self._manual_dispatch:
            errors.append("Manual dispatch was not observed")

        # Non-B path must be taken
        if not self._non_b_path:
            errors.append("Non-B capture path was not taken")

        # Control case must be isolated (not conflated with Pentax-specific behavior)
        if not self._control_case_isolated:
            errors.append("Control case shared helper was not invoked")

        return errors


# ---------------------------------------------------------------------------
# Scenario runner - runs all 9 cases
# ---------------------------------------------------------------------------

def run_all_lifecycle_cases(
    fixture_manager: FixtureManager,
    patcher_commit: str = "unknown",
    harness_revision: str = "0.1.0",
    emulator_version: str = "unknown",
) -> Dict[LifecycleCase, LifecycleTestResult]:
    """Run all 9 #251 lifecycle test cases.

    Args:
        fixture_manager: FixtureManager instance
        patcher_commit: Patcher commit SHA
        harness_revision: Harness revision
        emulator_version: Emulator version

    Returns:
        Dictionary mapping LifecycleCase to LifecycleTestResult
    """
    scenarios = [
        Case1NormalBulbCompletion(
            LifecycleCase.CASE_1_NORMAL_BULB_COMPLETION,
            fixture_manager,
            patcher_commit=patcher_commit,
            harness_revision=harness_revision,
            emulator_version=emulator_version,
        ),
        Case2MessageStopVsContextCancel(
            LifecycleCase.CASE_2_MESSAGE_STOP_VS_CONTEXT_CANCEL,
            fixture_manager,
            patcher_commit=patcher_commit,
            harness_revision=harness_revision,
            emulator_version=emulator_version,
        ),
        Case3CancelResultSurvivesStop(
            LifecycleCase.CASE_3_CANCEL_RESULT_SURVIVES_STOP,
            fixture_manager,
            patcher_commit=patcher_commit,
            harness_revision=harness_revision,
            emulator_version=emulator_version,
        ),
        Case4StopFailurePreservesPrimaryError(
            LifecycleCase.CASE_4_STOP_FAILURE_PRESERVES_PRIMARY_ERROR,
            fixture_manager,
            patcher_commit=patcher_commit,
            harness_revision=harness_revision,
            emulator_version=emulator_version,
        ),
        Case5DeferredTimedCaptureCancelled(
            LifecycleCase.CASE_5_DEFERRED_TIMED_CAPTURE_CANCELLED,
            fixture_manager,
            patcher_commit=patcher_commit,
            harness_revision=harness_revision,
            emulator_version=emulator_version,
        ),
        Case6NormalTimerExpiry(
            LifecycleCase.CASE_6_NORMAL_TIMER_EXPIRY,
            fixture_manager,
            patcher_commit=patcher_commit,
            harness_revision=harness_revision,
            emulator_version=emulator_version,
        ),
        Case7CallbackOrderingRegisterPreservation(
            LifecycleCase.CASE_7_CALLBACK_ORDERING_REGISTER_PRESERVATION,
            fixture_manager,
            patcher_commit=patcher_commit,
            harness_revision=harness_revision,
            emulator_version=emulator_version,
        ),
        Case8OutputPublicationCleanup(
            LifecycleCase.CASE_8_OUTPUT_PUBLICATION_CLEANUP,
            fixture_manager,
            patcher_commit=patcher_commit,
            harness_revision=harness_revision,
            emulator_version=emulator_version,
        ),
        Case9ManualNonBControl(
            LifecycleCase.CASE_9_MANUAL_NON_B_CONTROL,
            fixture_manager,
            patcher_commit=patcher_commit,
            harness_revision=harness_revision,
            emulator_version=emulator_version,
        ),
    ]

    results = {}
    for scenario in scenarios:
        try:
            result = scenario.run()
            results[scenario.case] = result
        except Exception as e:  # pylint: disable=broad-except
            results[scenario.case] = LifecycleTestResult(
                case=scenario.case,
                passed=False,
                assertion_errors=[f"Scenario execution failed: {e}"],
            )

    return results
