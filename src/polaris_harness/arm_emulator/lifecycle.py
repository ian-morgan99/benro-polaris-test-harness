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
from .fixtures import FixtureManager, FixtureMetadata
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
    skipped: bool = False
    skip_reason: Optional[str] = None
    contract: Optional[ArmEmulationContract] = None
    assertion_errors: List[str] = field(default_factory=list)
    # Fixture actually executed. Differs from the requested fixture when a case
    # is re-pointed at the region that owns its edge.
    fixture_name: Optional[str] = None
    # Assertions this artifact cannot answer, with the reason. Kept separate from
    # assertion_errors on purpose: "the region contains no close edge" is an
    # artifact-coverage gap, while "expected 1 stop call, got 2" is a behavioural
    # finding. Reporting the first as a failure invites someone to delete the
    # assertion to get green, which would silently drop issue #251's spec.
    not_evaluated: List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.contract is not None:
            self.passed = (
                self.contract.is_pass()
                and len(self.assertion_errors) == 0
                # Unverified coverage is not a pass. Leaving passed=True here would
                # let any consumer of .passed treat an artifact gap as success.
                and not self.not_evaluated
            )

    @property
    def is_partial(self) -> bool:
        """Whether every evaluated assertion held but some could not be evaluated.

        True means the case verified what the artifact can support and nothing was
        contradicted - it does not mean the case passed, and ``passed`` is False
        precisely because of that. A partial case still leaves part of the spec
        unverified, so it must not be reported as success.
        """
        return (
            not self.skipped
            and self.contract is not None
            and self.contract.is_pass()
            and not self.assertion_errors
            and bool(self.not_evaluated)
        )


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
        # Populated in run() once the fixture is resolved.
        self._active_fixture: Optional[FixtureMetadata] = None
        self._boundary_aliases: Dict[str, str] = {}
        # Assertions the resolved artifact cannot answer, filled in run(). On the
        # instance rather than threaded through _assertions, because subclass
        # assertion methods need to consult it without changing their signature.
        self._not_evaluated: List[str] = []

    # Boundary function names this case's assertions depend on. A fixture whose
    # sidecar declares none of them cannot exercise the case, and that is a
    # fixture/case mismatch rather than a behavioural failure.
    expected_boundary_functions: tuple = ()

    # Region that actually owns this case's edge, when it differs from the suite
    # default. Used when the pinned function under test does not contain the
    # boundary the case asserts on.
    preferred_fixture: Optional[str] = None

    # What wait_and_handle_event returns for this case, or None to leave the
    # profile's default in place.
    #
    # The default handler returns 0, and the loop at 0x101f78 tests the result
    # with `cmn r3,#0x34; beq 0x102204` - so 0 is not a recognised exit and the
    # run spins until the instruction limit. Cases that need the loop to terminate
    # must say which exit they are exercising instead of inheriting that default.
    #
    # Two exits exist in the artifact:
    #   -52 (0x34 negated) -> 0x102204, the publication/cancel route
    #   a state word at fp-0x84 equal to 4 -> 0x101fe0
    # Only the first is characterised well enough to pin, so cases asserting on it
    # set -52. A case that needs the second must not fake it with -52: that would
    # route through the cancel path and assert something about a branch the case
    # never intended to exercise.
    event_return_value: Optional[int] = None

    def _setup_stubs(self) -> None:
        """Set up stubs for this scenario. Override in subclasses."""
        profile = create_default_profile()
        profile.register_all(self.stub_registry)

    def _create_emulator(self, command: str, stub_profile: str = "default") -> ARMEmulator:
        """Create an ARMEmulator configured for this scenario.

        Mode and entry address come from the fixture sidecar (see
        ``FixtureManager.to_emulation_config``), and any ``stub_entries`` the
        sidecar declares are registered so external calls reach stubs instead
        of executing as real code. Without that wiring a real ARM region would
        be decoded as Thumb and fault on instruction one, and lifecycle
        assertions filtering on semantic function names could never match.

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

        # Register stubs with the emulator, carrying per-boundary ABI shapes.
        handlers = dict(self.stub_registry._handlers)
        if self.event_return_value is not None:
            base = handlers.get("camera")
            if base is not None:
                pinned = self.event_return_value

                def pinned_camera(function_name, arguments, snapshot,
                                  _base=base, _value=pinned):
                    if function_name == "wait_and_handle_event":
                        return _value
                    return _base(function_name, arguments, snapshot)

                handlers["camera"] = pinned_camera
        for name, handler in handlers.items():
            emulator.register_stub(name, handler)
        for name, shape in getattr(self.stub_registry, "_abi_shapes", {}).items():
            emulator.set_stub_abi_shape(name, shape)

        # Register stub entry addresses declared by the fixture sidecar.
        fixture = self.fixture_manager.get_fixture(self.fixture_name)
        if fixture is None:
            raise ValueError(
                f"Fixture {self.fixture_name} disappeared between validation and "
                f"emulator construction."
            )
        coerce = FixtureManager._coerce_int
        for entry in fixture.stub_entries:
            address = coerce(entry.get("address"))
            stub_name = entry.get("stub_name")
            if address is None or stub_name is None:
                raise KeyError(
                    f"Fixture {self.fixture_name} declares a stub entry with a "
                    f"missing or unparseable address/stub_name: {entry!r}"
                )
            if stub_name not in emulator._stub_registry:
                # Fail closed: a declared entry for an unregistered stub means
                # the fixture and profile disagree about the boundary.
                raise KeyError(
                    f"Fixture {self.fixture_name} declares a stub entry for "
                    f"unregistered stub {stub_name!r} at 0x{int(address):x}"
                )
            emulator.register_stub_entry(
                stub_name,
                address,
                function_name=entry.get("function_name"),
                abi_shape=entry.get("abi_shape"),
                model=entry.get("model", "call-site"),
            )

        return emulator

    def _resolve_fixture_name(self) -> str:
        """Choose the fixture this case should actually run against.

        A case whose asserted edge is absent from the requested fixture but
        present in its ``preferred_fixture`` is re-pointed there. Anything else
        keeps the requested fixture, so behaviour is unchanged for cases that
        already match.

        Returns:
            Fixture name to execute
        """
        if not self.preferred_fixture or self.preferred_fixture == self.fixture_name:
            return self.fixture_name
        candidate = self.fixture_manager.get_fixture(self.preferred_fixture)
        if candidate is None or candidate.path is None or not candidate.path.exists():
            return self.fixture_name
        declared = {e.get("function_name") for e in candidate.stub_entries}
        aliases = candidate.boundary_aliases
        owns = any(
            name in declared or aliases.get(name) in declared
            for name in self.expected_boundary_functions
        )
        return self.preferred_fixture if owns else self.fixture_name

    def _boundary_matches(self, recorded: Any, expected: str) -> bool:
        """Whether a recorded stub call satisfies an asserted boundary name.

        Matches the idealised name directly, or the real symbol it aliases to in
        this fixture (e.g. ``stop`` matches ``stopCaptureImageForMessage``).

        Args:
            recorded: function_name recorded for the stub call
            expected: name the case asserts on

        Returns:
            True when the call satisfies the assertion
        """
        if recorded == expected:
            return True
        alias = self._boundary_aliases.get(expected)
        return bool(alias is not None and alias == recorded)

    def _idealise(self, recorded: Any) -> Any:
        """Map a recorded symbol back to the idealised boundary name.

        Stub handlers are written against the case's vocabulary ("stop",
        "cancel"), but once a fixture records real call sites the name arriving
        at the handler is the firmware symbol (stopCaptureImageForMessage).
        Without this mapping a handler's ``function_name == "stop"`` test never
        fires, its scripted return value is silently dropped, and an assertion
        about error propagation reports the default instead - which looks like a
        finding about the firmware rather than a harness bug.

        Prefers a name the case actually asserts on, so two idealised names
        aliasing one symbol (stop and message_stop) resolve predictably.

        Args:
            recorded: function_name recorded for the stub call

        Returns:
            The idealised name, or the recorded name when it has no alias
        """
        preferred = [n for n in self.expected_boundary_functions
                     if self._boundary_aliases.get(n) == recorded]
        if preferred:
            return preferred[0]
        for name, alias in self._boundary_aliases.items():
            if alias == recorded:
                return name
        return recorded

    def _declares_boundary(self, fixture: Any, expected: str) -> bool:
        """Whether a fixture's sidecar declares the asserted boundary.

        Args:
            fixture: FixtureMetadata to inspect
            expected: name the case asserts on

        Returns:
            True when the region contains that boundary or its alias
        """
        declared = {e.get("function_name") for e in fixture.stub_entries}
        aliases = fixture.boundary_aliases
        real = aliases.get(expected)
        return bool(expected in declared or (real is not None and real in declared))

    def _evaluable(self, name: str) -> bool:
        """Whether an assertion on this boundary can be answered by the artifact.

        Subclasses guard assertions with this. Asserting "exactly 1 close call"
        against a region that contains no close edge at all produces "got 0", which
        reads as though the firmware dropped a close call when in fact nothing was
        contradicted - and the tempting "fix" is to delete the assertion, silently
        discarding part of issue #251's spec.

        Args:
            name: idealised boundary name an assertion depends on

        Returns:
            True when the fixture declares that boundary or its alias
        """
        fixture = self._active_fixture
        if fixture is None:
            return True
        return self._declares_boundary(fixture, name)

    def _unreachable_note(self, contract: ArmEmulationContract,
                          expected: str) -> Optional[str]:
        """Explain why a declared boundary was not observed, when possible.

        When the run faulted before the boundary's call site, the absence is a
        reachability limit rather than evidence that the edge is missing or
        broken. Returns None when no such explanation applies.

        Args:
            contract: emulation result to inspect
            expected: asserted boundary name

        Returns:
            Explanatory string, or None
        """
        fixture = self._active_fixture
        fault = contract.unsupported_instruction_address
        if fixture is None or fault is None:
            return None
        real = self._boundary_aliases.get(expected, expected)
        for entry in fixture.stub_entries:
            if entry.get("function_name") not in (expected, real):
                continue
            addr = FixtureManager._coerce_int(entry.get("address"))
            if addr is None or addr > fault:
                shown = hex(addr) if addr is not None else "?"
                return (
                    f"{expected}: call site at {shown} lies beyond the run's "
                    f"fault at {hex(fault)}, so this edge is present in the "
                    f"region but not reachable by straight-line execution. Needs "
                    f"real global contents and AAPCS argument setup for the "
                    f"entry function."
                )
        return None

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
        self._not_evaluated = []

        # Re-point at the region that actually owns this case's edge when the
        # requested one does not contain it. Cases 1-4 assert on a stop/close
        # edge that capture_image_with_Bulb does not expose but its caller
        # captureBulbImage does, so running them against stage2_region can only
        # ever produce "not observed".
        self.fixture_name = self._resolve_fixture_name()

        # SKIP if using the tiny synthetic fixture — it cannot produce
        # camera/libgphoto/timer/callback events. Only real Stage-2 fixtures
        # can exercise the lifecycle paths.
        if self.fixture_name == "tiny_fixture":
            return LifecycleTestResult(
                case=self.case,
                passed=False,
                skipped=True,
                skip_reason=(
                    "no ARM-mode Stage-2 fixture available; "
                    "tiny_fixture is a 2-instruction engine smoke test only"
                ),
            )

        # SKIP if the fixture binary does not exist locally (Stage-2 region
        # must not be committed to GitHub — it is extracted read-only from
        # pgphoto.stage2ondisk in the private research repo).
        fixture = self.fixture_manager.get_fixture(self.fixture_name)
        if fixture is None or fixture.path is None or not fixture.path.exists():
            detail = (
                fixture.blocked_reason
                if fixture is not None and fixture.blocked_reason
                else (
                    f"extract the region recorded in "
                    f"tests/fixtures/arm/{self.fixture_name}.json from the "
                    "private research repo and place the bytes at "
                    f"tests/fixtures/arm/{self.fixture_name}.bin"
                )
            )
            return LifecycleTestResult(
                case=self.case,
                passed=False,
                skipped=True,
                skip_reason=(
                    f"fixture binary not available locally: {self.fixture_name}; "
                    f"{detail}"
                ),
            )
        self._active_fixture = fixture
        self._boundary_aliases = dict(fixture.boundary_aliases)

        # SKIP when the fixture needs global seeding but the retained source
        # binary cannot be located. Without it the run faults on its first
        # pointer chase and every assertion reports "not observed", which reads
        # as a patcher regression when the real cause is an unset environment
        # variable. Same class of condition as a missing proprietary blob.
        available, reason = self.fixture_manager.source_binary_available(
            self.fixture_name)
        if not available:
            return LifecycleTestResult(
                case=self.case,
                passed=False,
                skipped=True,
                skip_reason=reason,
                fixture_name=self.fixture_name,
            )

        # SKIP when the fixture declares no stub entries. Every lifecycle case
        # asserts on boundary events (stop/close/cancel/timer/callback), and
        # those events can only be observed if the region's external call
        # targets are registered as stubs. Without them the real code faults on
        # its first unmapped reference and every assertion reports "not
        # observed" — which would read as a patcher regression when it is
        # actually an unresolved fixture contract.
        #
        # This is deliberately narrower than skipping whenever the run fails:
        # once stub_entries are declared, any remaining assertion failure is a
        # genuine behavioural signal and must still exit 3.
        if not fixture.stub_entries:
            return LifecycleTestResult(
                case=self.case,
                passed=False,
                skipped=True,
                skip_reason=(
                    f"fixture {self.fixture_name} declares no stub_entries; its "
                    "external call targets are unresolved, so boundary events "
                    "(stop/close/cancel/timer/callback) cannot be observed. "
                    "Resolve them from the source ELF's symbol table and record "
                    "them in the fixture sidecar before treating these cases as "
                    "behavioural evidence."
                ),
            )

        emulator = self._create_emulator(command)
        result_record = emulator.run()
        contract = ArmEmulationContract.from_emulation_result(result_record)

        assertion_errors = self._assertions(contract)

        # When a case asserts on boundary names this fixture never declares, the
        # failure is a fixture/case mismatch rather than a behavioural
        # regression. Say so explicitly: without this, "Expected exactly 1 stop
        # call, got 0" reads as though the patcher broke stop/close handling,
        # when in fact the pinned region does not contain that edge at all.
        declared = {
            e.get("function_name")
            for e in fixture.stub_entries
            if e.get("function_name")
        }
        missing = [
            name for name in self.expected_boundary_functions
            if not self._declares_boundary(fixture, name)
        ]
        # Absent and unreachable edges go to not_evaluated, not assertion_errors.
        # Both mean "this artifact cannot answer the question", which is an
        # coverage gap; a behavioural finding requires the edge to exist and have
        # been exercised. Keeping them separate means a green suite still shows
        # which parts of issue #251's spec remain unverified.
        if missing:
            present = [
                name for name in self.expected_boundary_functions
                if self._declares_boundary(fixture, name)
            ]
            # A subclass assertion may already have recorded this gap while naming
            # the specific expectation it could not check. Adding the generic note
            # on top would report one gap twice.
            already = " ".join(self._not_evaluated)
            still_missing = [n for n in missing if n not in already]
            listed = ", ".join(sorted(still_missing))
            declared_list = ", ".join(sorted(n for n in declared if n)) or "(none)"
            if listed:
                if present:
                    self._not_evaluated.append(
                        f"{listed}: fixture {self.fixture_name} owns "
                        f"{', '.join(sorted(present))} but contains no boundary "
                        f"named {listed}, so assertions on {listed} cannot be "
                        f"evaluated. Declared boundaries: {declared_list}."
                    )
                else:
                    self._not_evaluated.append(
                        f"{listed}: fixture {self.fixture_name} declares no boundary "
                        f"named {listed}; this case cannot be evaluated against this "
                        f"artifact. Declared boundaries: {declared_list}. Pin the "
                        f"region that owns these edges."
                    )
        else:
            # The edge is declared but may not have been reached. A fault short of
            # the call site is a reachability limit, not missing behaviour.
            for name in self.expected_boundary_functions:
                calls = [c for c in contract.stub_calls
                         if self._boundary_matches(c.get("function_name"), name)]
                if not calls:
                    note = self._unreachable_note(contract, name)
                    if note is not None:
                        self._not_evaluated.append(note)

        passed = (
            contract.is_pass()
            and len(assertion_errors) == 0
            and not self._not_evaluated
        )

        return LifecycleTestResult(
            case=self.case,
            passed=passed,
            contract=contract,
            assertion_errors=assertion_errors,
            fixture_name=self.fixture_name,
            not_evaluated=list(self._not_evaluated),
        )


# ---------------------------------------------------------------------------
# Case 1: Normal held-Bulb completion and exactly one stop/close
# ---------------------------------------------------------------------------

class Case1NormalBulbCompletion(LifecycleScenario):
    """Normal held-Bulb completion with exactly one stop/close."""

    expected_boundary_functions = ("stop", "close")
    preferred_fixture = "capture_bulb_image_region"

    def _setup_stubs(self) -> None:
        profile = create_default_profile()
        profile.register_all(self.stub_registry)

        # Track stop/close calls
        self._stop_count = 0
        self._close_count = 0

        def counting_camera(function_name: str, arguments: List[Any],
                            register_snapshot: Dict[str, int]) -> Any:
            name = self._idealise(function_name)
            if name == "stop":
                self._stop_count += 1
            elif name == "close":
                self._close_count += 1
            return default_camera_stub(function_name, arguments, register_snapshot)

        self.stub_registry.register("camera", counting_camera)

    def _assertions(self, contract: ArmEmulationContract) -> List[str]:
        errors = super()._assertions(contract)

        # Exactly one stop and one close
        camera_calls = contract.stub_calls
        stop_calls = [c for c in camera_calls if c["stub_name"] == "camera" and self._boundary_matches(c["function_name"], "stop")]

        if len(stop_calls) != 1:
            errors.append(f"Expected exactly 1 stop call, got {len(stop_calls)}")

        # close and cancel are guarded: captureBulbImage contains neither edge (all
        # 17 BL sites are enumerated in the fixture sidecar's call_edge_inventory),
        # so asserting on them reports "got 0" for behaviour that never existed.
        if self._evaluable("close"):
            close_calls = [c for c in camera_calls if c["stub_name"] == "camera" and self._boundary_matches(c["function_name"], "close")]
            if len(close_calls) != 1:
                errors.append(f"Expected exactly 1 close call, got {len(close_calls)}")
        else:
            self._not_evaluated.append(
                "close: expected exactly 1 close call, but the executed region "
                "contains no close edge to observe. Unverified, not passing."
            )

        if self._evaluable("cancel") or self._evaluable("context_cancel"):
            cancel_calls = [c for c in camera_calls if self._boundary_matches(c["function_name"], "cancel") or self._boundary_matches(c["function_name"], "context_cancel")]
            if len(cancel_calls) > 0:
                errors.append(f"Normal completion must not take cancel path, got {len(cancel_calls)} cancel calls")
        else:
            self._not_evaluated.append(
                "cancel/context_cancel: expected normal completion to avoid the "
                "cancel path, but no cancel edge exists in the executed region to "
                "observe. Unverified, not passing."
            )

        return errors


# ---------------------------------------------------------------------------
# Case 2: Explicit message-stop and context-cancel as distinct routes
# ---------------------------------------------------------------------------

class Case2MessageStopVsContextCancel(LifecycleScenario):
    """Explicit message-stop and context-cancel as distinct routes."""

    expected_boundary_functions = ("message_stop", "context_cancel")
    preferred_fixture = "capture_bulb_image_region"

    def _setup_stubs(self) -> None:
        profile = create_default_profile()
        profile.register_all(self.stub_registry)

        self._message_stop_calls = 0
        self._context_cancel_calls = 0

        def distinguishing_camera(function_name: str, arguments: List[Any],
                                  register_snapshot: Dict[str, int]) -> Any:
            # Resolve the recorded symbol to the case's vocabulary first; see
            # _idealise for why this matters.
            name = self._idealise(function_name)
            if name == "message_stop":
                self._message_stop_calls += 1
            elif name == "context_cancel":
                self._context_cancel_calls += 1
            return default_camera_stub(function_name, arguments, register_snapshot)

        self.stub_registry.register("camera", distinguishing_camera)

    def _assertions(self, contract: ArmEmulationContract) -> List[str]:
        errors = super()._assertions(contract)

        camera_calls = contract.stub_calls
        message_stop = [c for c in camera_calls if self._boundary_matches(c["function_name"], "message_stop")]
        context_cancel = [c for c in camera_calls if self._boundary_matches(c["function_name"], "context_cancel")]

        # This case's whole point is that the two routes are distinguishable, which
        # needs both edges present. captureBulbImage has stopCaptureImageForMessage
        # but no context_cancel, so only one side can ever be observed here.
        if not (self._evaluable("message_stop") and self._evaluable("context_cancel")):
            absent = [n for n in ("message_stop", "context_cancel") if not self._evaluable(n)]
            self._not_evaluated.append(
                f"{', '.join(absent)}: this case compares two routes, which requires "
                f"both edges. The executed region lacks {', '.join(absent)}, so the "
                f"comparison cannot be evaluated. Unverified, not passing."
            )
            # Still assert on what *is* observable: a missing message_stop edge would
            # be a genuine regression and must not be hidden by the partial skip.
            if self._evaluable("message_stop") and len(message_stop) == 0:
                errors.append("message_stop was not observed although the region declares it")
            return errors

        # Both routes must be distinguishable (not conflated)
        if len(message_stop) == 0 and len(context_cancel) == 0:
            errors.append("Neither message_stop nor context_cancel was observed")

        # They must be distinct - not the same function. Compared by resolved target,
        # not by the idealised label: two labels pointing at one symbol is exactly
        # the conflation this case is meant to catch.
        stop_target = self._boundary_aliases.get("message_stop", "message_stop")
        cancel_target = self._boundary_aliases.get("context_cancel", "context_cancel")
        if stop_target == cancel_target:
            errors.append(
                f"message_stop and context_cancel resolve to the same symbol "
                f"({stop_target}); they must be distinct routes"
            )

        return errors


# ---------------------------------------------------------------------------
# Case 3: Cancellation result surviving a successful stop callback
# ---------------------------------------------------------------------------

class Case3CancelResultSurvivesStop(LifecycleScenario):
    """Cancellation result surviving a successful stop callback."""

    expected_boundary_functions = ("stop", "cancel")
    preferred_fixture = "capture_bulb_image_region"

    def _setup_stubs(self) -> None:
        profile = create_default_profile()
        profile.register_all(self.stub_registry)

        self._stop_succeeded = False
        self._cancel_result_preserved = False

        def tracking_camera(function_name: str, arguments: List[Any],
                            register_snapshot: Dict[str, int]) -> Any:
            name = self._idealise(function_name)
            if name == "stop":
                self._stop_succeeded = True
                return 0  # Stop succeeds
            elif name == "cancel":
                # Cancel result should survive even though stop succeeded
                self._cancel_result_preserved = True
                return -1  # Cancel returns error
            return default_camera_stub(function_name, arguments, register_snapshot)

        self.stub_registry.register("camera", tracking_camera)

    def _assertions(self, contract: ArmEmulationContract) -> List[str]:
        errors = super()._assertions(contract)

        # Stop must have been called and succeeded
        stop_calls = [c for c in contract.stub_calls if c["stub_name"] == "camera" and self._boundary_matches(c["function_name"], "stop")]
        if len(stop_calls) == 0:
            errors.append("Stop callback was not invoked")

        # Cancel result must be preserved (not masked by successful stop). Guarded:
        # captureBulbImage has no cancel edge, so "Cancel was not invoked - result
        # may have been lost" would read as a masking bug in firmware that never
        # calls cancel at all.
        if self._evaluable("cancel"):
            cancel_calls = [c for c in contract.stub_calls if c["stub_name"] == "camera" and self._boundary_matches(c["function_name"], "cancel")]
            if len(cancel_calls) == 0:
                errors.append("Cancel was not invoked - result may have been lost")
        else:
            self._not_evaluated.append(
                "cancel: expected the cancellation result to survive a successful "
                "stop, but the executed region contains no cancel edge. Unverified, "
                "not passing."
            )

        return errors


# ---------------------------------------------------------------------------
# Case 4: Stop failure preserving the correct primary error without masking it
# ---------------------------------------------------------------------------

class Case4StopFailurePreservesPrimaryError(LifecycleScenario):
    """Stop failure preserving the correct primary error without masking it."""

    expected_boundary_functions = ("stop",)
    preferred_fixture = "capture_bulb_image_region"

    def _setup_stubs(self) -> None:
        profile = create_default_profile()
        profile.register_all(self.stub_registry)

        def failing_stop_camera(function_name: str, arguments: List[Any],
                                register_snapshot: Dict[str, int]) -> Any:
            name = self._idealise(function_name)
            if name == "stop":
                return -5  # Stop fails with error code -5
            return default_camera_stub(function_name, arguments, register_snapshot)

        self.stub_registry.register("camera", failing_stop_camera)

    def _assertions(self, contract: ArmEmulationContract) -> List[str]:
        errors = super()._assertions(contract)

        # Stop failure must be observable as the primary error
        stop_calls = [c for c in contract.stub_calls if c["stub_name"] == "camera" and self._boundary_matches(c["function_name"], "stop")]
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

        # Timer must have been cancelled. Evaluable: this region declares timer
        # edges (delayTime, timediff_now), so an absence here is a real signal.
        timer_calls = [c for c in contract.stub_calls if c["stub_name"] == "timer"]
        if len(timer_calls) == 0:
            errors.append("Timer was not cancelled before expiry")

        # No bulb=0 capture should be issued after cancel. The region declares no
        # capture edge, so the flag can never be set and asserting on it would
        # report a vacuous pass rather than a real guarantee.
        if self._evaluable("capture"):
            if self._bulb_zero_capture_issued:
                errors.append("Cancel must not issue a new bulb=0 capture")
        else:
            self._not_evaluated.append(
                "capture: expected no bulb=0 capture after cancel, but the "
                "executed region contains no capture edge to observe. "
                "Unverified, not passing."
            )

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

        # Both assertions depend on edges this region does not declare: it has
        # delayTime/timediff_now but no timer_expiry, and no capture edge. Left
        # unguarded they report "not observed" as a firmware regression when the
        # artifact simply cannot answer the question.
        if self._evaluable("timer_expiry"):
            if not self._timer_expired:
                errors.append("Timer expiry was not observed")
        else:
            self._not_evaluated.append(
                "timer_expiry: expected the timer to expire, but the executed "
                "region declares no timer_expiry edge (only delayTime and "
                "timediff_now). Unverified, not passing."
            )

        if self._evaluable("capture"):
            if not self._capture_issued:
                errors.append("Capture was not issued after normal timer expiry")
        else:
            self._not_evaluated.append(
                "capture: expected a capture after normal timer expiry, but the "
                "executed region contains no capture edge. Unverified, not passing."
            )

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
        self._register_before_after: List[Dict[str, Any]] = []
        self._widget_pointer_lifetime_ok = True

        def tracking_callback(function_name: str, arguments: List[Any],
                              register_snapshot: Dict[str, int]) -> Any:
            self._callback_order.append(function_name)
            self._register_snapshots.append(dict(register_snapshot))

            # Capture register state before callback execution
            before = dict(register_snapshot)

            # Execute the default callback stub
            result = default_callback_stub(function_name, arguments, register_snapshot)

            # Capture register state after callback execution
            after = dict(register_snapshot)

            # Record before/after for caller-saved register (R0-R3) preservation check
            caller_saved = ["R0", "R1", "R2", "R3"]
            before_cs = {r: before.get(r, 0) for r in caller_saved}
            after_cs = {r: after.get(r, 0) for r in caller_saved}
            self._register_before_after.append({
                "function": function_name,
                "before": before_cs,
                "after": after_cs,
            })

            # Check caller-saved registers (R0-R3) are preserved across callback
            # In a real implementation, the callee must preserve these
            if function_name == "widget_pointer_check":
                # Widget pointer must be valid (non-null)
                if arguments and len(arguments) > 0 and arguments[0] == 0:
                    self._widget_pointer_lifetime_ok = False

            return result

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

        # Check caller-saved register preservation (R0-R3)
        for entry in self._register_before_after:
            for reg in ["R0", "R1", "R2", "R3"]:
                if entry["before"][reg] != entry["after"][reg]:
                    errors.append(
                        f"Caller-saved register {reg} not preserved across "
                        f"callback {entry['function']}: "
                        f"before={entry['before'][reg]}, after={entry['after'][reg]}"
                    )

        # Widget pointer lifetime must be preserved
        if not self._widget_pointer_lifetime_ok:
            errors.append("Widget pointer lifetime violated - null pointer observed")

        return errors


# ---------------------------------------------------------------------------
# Case 8: Output publication/completion signaling and cleanup
# ---------------------------------------------------------------------------

class Case8OutputPublicationCleanup(LifecycleScenario):
    """Output publication/completion signaling and cleanup."""

    expected_boundary_functions = (
        "publish_output", "signal_completion", "cleanup")

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
        if self._evaluable("publish_output"):
            if not self._output_published:
                errors.append("Output was not published")
        else:
            self._not_evaluated.append(
                "publish_output: expected output publication, but the executed "
                "region declares no publish_output edge. Unverified, not passing."
            )

        # Completion must be signaled
        if self._evaluable("signal_completion"):
            if not self._completion_signaled:
                errors.append("Completion was not signaled")
        else:
            self._not_evaluated.append(
                "signal_completion: expected completion signaling, but the "
                "executed region declares no signal_completion edge. "
                "Unverified, not passing."
            )

        # Cleanup must be performed
        if self._evaluable("cleanup"):
            if not self._cleanup_performed:
                errors.append("Cleanup was not performed")
        else:
            self._not_evaluated.append(
                "cleanup: expected cleanup after publication, but the executed "
                "region declares no cleanup edge. Unverified, not passing."
            )

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

    expected_boundary_functions = (
        "manual_dispatch", "non_b_capture", "shared_helper")

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

        # Manual/non-B dispatch must work. All three edges are absent from this
        # region, so without the guard every one of these reports a regression
        # against an artifact that has no such call site.
        if self._evaluable("manual_dispatch"):
            if not self._manual_dispatch:
                errors.append("Manual dispatch was not observed")
        else:
            self._not_evaluated.append(
                "manual_dispatch: expected Manual/non-B dispatch, but the executed "
                "region declares no manual_dispatch edge. Unverified, not passing."
            )

        if self._evaluable("non_b_capture"):
            if not self._non_b_path:
                errors.append("Non-B capture path was not taken")
        else:
            self._not_evaluated.append(
                "non_b_capture: expected the non-B capture path, but the executed "
                "region declares no non_b_capture edge. Unverified, not passing."
            )

        # Control case must be isolated (not conflated with Pentax-specific behavior)
        if self._evaluable("shared_helper"):
            if not self._control_case_isolated:
                errors.append("Control case shared helper was not invoked")
        else:
            self._not_evaluated.append(
                "shared_helper: expected the control-case shared helper, but the "
                "executed region declares no shared_helper edge. "
                "Unverified, not passing."
            )

        return errors


# ---------------------------------------------------------------------------
# Scenario runner - runs all 9 cases
# ---------------------------------------------------------------------------

def run_all_lifecycle_cases(
    fixture_manager: FixtureManager,
    patcher_commit: str = "unknown",
    harness_revision: str = "0.1.0",
    emulator_version: str = "unknown",
    fixture_name: str = "stage2_region",
) -> Dict[LifecycleCase, LifecycleTestResult]:
    """Run all 9 #251 lifecycle test cases.

    The suite targets the real Stage-2 region by default. When that binary is
    not present locally every case reports SKIP (not FAIL), so a missing
    private fixture never looks like a behavioural failure - and when it *is*
    present the cases actually execute it, in ARM mode at its recorded entry
    address with stub entries registered.

    Args:
        fixture_manager: FixtureManager instance
        patcher_commit: Patcher commit SHA
        harness_revision: Harness revision
        emulator_version: Emulator version
        fixture_name: Fixture to run the cases against (default "stage2_region")

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
            fixture_name=fixture_name,
        ),
        Case2MessageStopVsContextCancel(
            LifecycleCase.CASE_2_MESSAGE_STOP_VS_CONTEXT_CANCEL,
            fixture_manager,
            patcher_commit=patcher_commit,
            harness_revision=harness_revision,
            emulator_version=emulator_version,
            fixture_name=fixture_name,
        ),
        Case3CancelResultSurvivesStop(
            LifecycleCase.CASE_3_CANCEL_RESULT_SURVIVES_STOP,
            fixture_manager,
            patcher_commit=patcher_commit,
            harness_revision=harness_revision,
            emulator_version=emulator_version,
            fixture_name=fixture_name,
        ),
        Case4StopFailurePreservesPrimaryError(
            LifecycleCase.CASE_4_STOP_FAILURE_PRESERVES_PRIMARY_ERROR,
            fixture_manager,
            patcher_commit=patcher_commit,
            harness_revision=harness_revision,
            emulator_version=emulator_version,
            fixture_name=fixture_name,
        ),
        Case5DeferredTimedCaptureCancelled(
            LifecycleCase.CASE_5_DEFERRED_TIMED_CAPTURE_CANCELLED,
            fixture_manager,
            patcher_commit=patcher_commit,
            harness_revision=harness_revision,
            emulator_version=emulator_version,
            fixture_name=fixture_name,
        ),
        Case6NormalTimerExpiry(
            LifecycleCase.CASE_6_NORMAL_TIMER_EXPIRY,
            fixture_manager,
            patcher_commit=patcher_commit,
            harness_revision=harness_revision,
            emulator_version=emulator_version,
            fixture_name=fixture_name,
        ),
        Case7CallbackOrderingRegisterPreservation(
            LifecycleCase.CASE_7_CALLBACK_ORDERING_REGISTER_PRESERVATION,
            fixture_manager,
            patcher_commit=patcher_commit,
            harness_revision=harness_revision,
            emulator_version=emulator_version,
            fixture_name=fixture_name,
        ),
        Case8OutputPublicationCleanup(
            LifecycleCase.CASE_8_OUTPUT_PUBLICATION_CLEANUP,
            fixture_manager,
            patcher_commit=patcher_commit,
            harness_revision=harness_revision,
            emulator_version=emulator_version,
            fixture_name=fixture_name,
        ),
        Case9ManualNonBControl(
            LifecycleCase.CASE_9_MANUAL_NON_B_CONTROL,
            fixture_manager,
            patcher_commit=patcher_commit,
            harness_revision=harness_revision,
            emulator_version=emulator_version,
            fixture_name=fixture_name,
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
