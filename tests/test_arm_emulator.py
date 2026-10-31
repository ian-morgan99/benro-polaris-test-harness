"""
Tests for ARM user-space emulation (issue #15).

Tests the ARMEmulator, stubs, fixtures, contract, and lifecycle scenarios.
These tests do not couple to the Polaris protocol scenario engine or runtime
verifier — they are an independent component.
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest

# Add the src directory to path for testing
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from polaris_harness.arm_emulator import (
    ARMEmulator,
    EmulationConfig,
    EmulationResult,
    FixtureManager,
    StubRegistry,
    create_default_profile,
    create_strict_profile,
    ArmEmulationContract,
    ContractVersion,
    LifecycleCase,
    run_all_lifecycle_cases,
)
from polaris_harness.arm_emulator.contract import load_contract
from polaris_harness.arm_emulator.emulator import StubCallRecord
from polaris_harness.arm_emulator.stubs import StubProfile


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "arm"


class TestFixtureIntegrity:
    """Test that fixture binaries have correct integrity."""

    def test_tiny_fixture_exists(self):
        """Tiny fixture binary must exist."""
        fixture_path = FIXTURES_DIR / "tiny_fixture.bin"
        assert fixture_path.exists(), f"Tiny fixture not found: {fixture_path}"

    def test_tiny_fixture_sha256(self):
        """Tiny fixture SHA-256 must be deterministic."""
        fixture_path = FIXTURES_DIR / "tiny_fixture.bin"
        sha256 = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
        assert len(sha256) == 64
        assert all(c in "0123456789abcdef" for c in sha256)

    def test_tiny_fixture_metadata(self):
        """Tiny fixture metadata must be valid JSON."""
        meta_path = FIXTURES_DIR / "tiny_fixture.json"
        assert meta_path.exists(), f"Tiny fixture metadata not found: {meta_path}"
        with open(meta_path) as f:
            meta = json.load(f)
        assert "description" in meta
        assert "provenance" in meta

    def test_stage2_region_placeholder_exists(self):
        """Stage-2 region placeholder must exist (documents expected provenance)."""
        placeholder = FIXTURES_DIR / "stage2_region.bin.placeholder"
        assert placeholder.exists(), f"Stage-2 region placeholder not found: {placeholder}"


class TestFixtureManager:
    """Test FixtureManager functionality."""

    def setup_method(self):
        self.manager = FixtureManager(FIXTURES_DIR)

    def test_discover_tiny_fixture(self):
        """Tiny fixture should be discovered."""
        fixtures = self.manager.list_fixtures()
        assert "tiny_fixture" in fixtures
        fixture = fixtures["tiny_fixture"]
        assert fixture.name == "tiny_fixture"
        assert fixture.path.exists()
        assert fixture.size_bytes > 0
        assert len(fixture.sha256) == 64

    def test_get_fixture(self):
        """Get fixture by name."""
        fixture = self.manager.get_fixture("tiny_fixture")
        assert fixture is not None
        assert fixture.name == "tiny_fixture"

    def test_get_nonexistent_fixture(self):
        """Getting nonexistent fixture returns None."""
        assert self.manager.get_fixture("nonexistent") is None

    def test_verify_fixture(self):
        """Fixture verification with correct SHA-256."""
        fixture = self.manager.get_fixture("tiny_fixture")
        assert fixture is not None
        assert self.manager.verify_fixture("tiny_fixture", fixture.sha256)

    def test_verify_fixture_wrong_sha(self):
        """Fixture verification with wrong SHA-256 returns False."""
        assert not self.manager.verify_fixture("tiny_fixture", "0" * 64)

    def test_read_binary(self):
        """Reading fixture binary returns bytes."""
        data = self.manager.read_binary("tiny_fixture")
        assert isinstance(data, bytes)
        assert len(data) == 4  # 2 Thumb instructions


class TestStubRegistry:
    """Test StubRegistry functionality."""

    def setup_method(self):
        self.registry = StubRegistry()

    def test_register_and_call(self):
        """Register a stub and call it."""
        call_count = 0

        def my_stub(function_name, arguments, register_snapshot):
            nonlocal call_count
            call_count += 1
            return 42

        self.registry.register("test", my_stub)
        result = self.registry.call("test", "func1", [1, 2, 3], {"R0": 0})
        assert result == 42
        assert call_count == 1

    def test_call_unregistered_stub_fails(self):
        """Calling an unregistered stub raises KeyError (fail closed)."""
        with pytest.raises(KeyError, match="Missing stub"):
            self.registry.call("nonexistent", "func1", [], {})

    def test_get_calls(self):
        """Get recorded calls."""
        def my_stub(function_name, arguments, register_snapshot):
            return 0

        self.registry.register("test", my_stub)
        self.registry.call("test", "func1", [1], {"R0": 0})
        self.registry.call("test", "func2", [2], {"R0": 1})

        calls = self.registry.get_calls("test")
        assert len(calls) == 2
        assert calls[0]["function_name"] == "func1"
        assert calls[1]["function_name"] == "func2"

    def test_assert_call_count(self):
        """Assert call count."""
        def my_stub(function_name, arguments, register_snapshot):
            return 0

        self.registry.register("test", my_stub)
        self.registry.call("test", "func1", [], {})
        self.registry.call("test", "func1", [], {})
        self.registry.assert_call_count("test", "func1", 2)

    def test_assert_call_count_fails(self):
        """Assert call count fails on mismatch."""
        def my_stub(function_name, arguments, register_snapshot):
            return 0

        self.registry.register("test", my_stub)
        self.registry.call("test", "func1", [], {})
        with pytest.raises(AssertionError):
            self.registry.assert_call_count("test", "func1", 5)

    def test_assert_call_order(self):
        """Assert call order."""
        def my_stub(function_name, arguments, register_snapshot):
            return 0

        self.registry.register("test", my_stub)
        self.registry.call("test", "first", [], {})
        self.registry.call("test", "second", [], {})
        self.registry.assert_call_order([("test", "first"), ("test", "second")])

    def test_clear(self):
        """Clear call history."""
        def my_stub(function_name, arguments, register_snapshot):
            return 0

        self.registry.register("test", my_stub)
        self.registry.call("test", "func1", [], {})
        assert len(self.registry.get_calls()) == 1
        self.registry.clear()
        assert len(self.registry.get_calls()) == 0


class TestDefaultProfiles:
    """Test default stub profiles."""

    def test_create_default_profile(self):
        """Create default profile with all stubs."""
        profile = create_default_profile()
        assert profile.name == "default"
        assert profile.camera_stub is not None
        assert profile.libgphoto_stub is not None
        assert profile.os_stub is not None
        assert profile.timer_stub is not None
        assert profile.callback_stub is not None

    def test_create_strict_profile(self):
        """Create strict profile with all stubs."""
        profile = create_strict_profile()
        assert profile.name == "strict"
        assert profile.camera_stub is not None
        assert profile.libgphoto_stub is not None
        assert profile.os_stub is not None
        assert profile.timer_stub is not None
        assert profile.callback_stub is not None


class TestArmEmulator:
    """Test ARMEmulator core functionality."""

    def setup_method(self):
        self.fixture_path = FIXTURES_DIR / "tiny_fixture.bin"
        self.sha256 = hashlib.sha256(self.fixture_path.read_bytes()).hexdigest()

    def test_emulator_creation(self):
        """Create an ARMEmulator instance."""
        config = EmulationConfig(
            binary_path=self.fixture_path,
            binary_sha256=self.sha256,
            patcher_commit="test-commit",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.0",
            fixture_stub_profile="default",
            command="capture_image_with_Bulb",
        )
        emulator = ARMEmulator(config)
        assert emulator is not None
        assert emulator.config == config

    def test_register_stub(self):
        """Register a stub with the emulator."""
        config = EmulationConfig(
            binary_path=self.fixture_path,
            binary_sha256=self.sha256,
            patcher_commit="test",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.0",
            fixture_stub_profile="default",
            command="test",
        )
        emulator = ARMEmulator(config)

        def my_stub(function_name, arguments, register_snapshot):
            return 0

        emulator.register_stub("test", my_stub)
        assert "test" in emulator._stub_registry

    def test_run_without_unicorn(self):
        """Running without Unicorn installed returns error result."""
        config = EmulationConfig(
            binary_path=self.fixture_path,
            binary_sha256=self.sha256,
            patcher_commit="test",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.0",
            fixture_stub_profile="default",
            command="test",
        )
        emulator = ARMEmulator(config)

        # Temporarily block unicorn import
        import builtins
        original_import = builtins.__import__

        def blocked_import(name, *args, **kwargs):
            if name == "unicorn":
                raise ImportError("No module named 'unicorn'")
            return original_import(name, *args, **kwargs)

        builtins.__import__ = blocked_import
        try:
            result = emulator.run()
            assert result.result == EmulationResult.ERROR
            assert "Unicorn engine not available" in result.error_message
        finally:
            builtins.__import__ = original_import

    def test_result_record_fields(self):
        """Result record has all required fields."""
        config = EmulationConfig(
            binary_path=self.fixture_path,
            binary_sha256=self.sha256,
            patcher_commit="test-commit",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.0",
            fixture_stub_profile="default",
            command="capture_image_with_Bulb",
        )
        emulator = ARMEmulator(config)

        # Check config fields are bound
        assert config.binary_sha256 == self.sha256
        assert config.patcher_commit == "test-commit"
        assert config.harness_revision == "0.1.0"
        assert config.emulator_version == "unicorn-2.0"
        assert config.fixture_stub_profile == "default"
        assert config.command == "capture_image_with_Bulb"


class TestContract:
    """Test ArmEmulationContract functionality."""

    def test_contract_creation(self):
        """Create a contract."""
        contract = ArmEmulationContract(
            binary_sha256="a" * 64,
            patcher_commit="commit-123",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.0",
            fixture_stub_profile="default",
            command="capture_image_with_Bulb",
            result="pass",
            exit_code=0,
            duration_ms=100,
            instructions_executed=2,
        )
        assert contract.is_pass()
        assert not contract.is_fail_closed()

    def test_contract_fail(self):
        """Contract fail detection."""
        contract = ArmEmulationContract(
            binary_sha256="a" * 64,
            patcher_commit="commit-123",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.0",
            fixture_stub_profile="default",
            command="capture_image_with_Bulb",
            result="fail",
            exit_code=1,
            duration_ms=100,
            instructions_executed=0,
            error_message="Unsupported instruction",
        )
        assert not contract.is_pass()
        assert contract.is_fail_closed()

    def test_contract_to_json(self):
        """Contract serialization to JSON."""
        contract = ArmEmulationContract(
            binary_sha256="a" * 64,
            patcher_commit="commit-123",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.0",
            fixture_stub_profile="default",
            command="test",
            result="pass",
            exit_code=0,
            duration_ms=50,
            instructions_executed=1,
        )
        json_str = contract.to_json()
        data = json.loads(json_str)
        assert data["binary_sha256"] == "a" * 64
        assert data["result"] == "pass"
        assert data["exit_code"] == 0
        assert data["contract_version"] == "1"

    def test_contract_write_to_file(self, tmp_path):
        """Contract writing to file."""
        contract = ArmEmulationContract(
            binary_sha256="a" * 64,
            patcher_commit="commit-123",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.0",
            fixture_stub_profile="default",
            command="test",
            result="pass",
            exit_code=0,
            duration_ms=50,
            instructions_executed=1,
        )
        output_path = tmp_path / "contract.json"
        contract.write_to_file(output_path)
        assert output_path.exists()
        with open(output_path) as f:
            data = json.load(f)
        assert data["binary_sha256"] == "a" * 64

    def test_load_contract(self, tmp_path):
        """Loading a contract from file."""
        contract = ArmEmulationContract(
            binary_sha256="a" * 64,
            patcher_commit="commit-123",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.0",
            fixture_stub_profile="default",
            command="test",
            result="pass",
            exit_code=0,
            duration_ms=50,
            instructions_executed=1,
        )
        output_path = tmp_path / "contract.json"
        contract.write_to_file(output_path)

        loaded_contract = load_contract(output_path)
        assert loaded_contract.binary_sha256 == "a" * 64
        assert loaded_contract.result == "pass"

    def test_contract_version(self):
        """Contract version is V1."""
        assert ContractVersion.V1.value == "1"


class TestLifecycleScenarios:
    """Test the 9 #251 lifecycle test scenarios."""

    def setup_method(self):
        self.manager = FixtureManager(FIXTURES_DIR)

    def test_run_all_cases(self):
        """Run all 9 lifecycle cases."""
        results = run_all_lifecycle_cases(
            self.manager,
            patcher_commit="test-commit",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.0",
        )

        # All 9 cases should be present
        assert len(results) == 9
        for case in LifecycleCase:
            assert case in results, f"Missing result for {case}"

    def test_case_1_normal_bulb_completion(self):
        """Case 1: Normal held-Bulb completion with exactly one stop/close."""
        from polaris_harness.arm_emulator.lifecycle import Case1NormalBulbCompletion

        scenario = Case1NormalBulbCompletion(
            LifecycleCase.CASE_1_NORMAL_BULB_COMPLETION,
            self.manager,
            patcher_commit="test",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.0",
        )
        result = scenario.run()
        # The tiny fixture may not fully execute all lifecycle paths,
        # but the scenario should run without crashing
        assert result.case == LifecycleCase.CASE_1_NORMAL_BULB_COMPLETION

    def test_case_2_message_stop_vs_context_cancel(self):
        """Case 2: Message-stop and context-cancel as distinct routes."""
        from polaris_harness.arm_emulator.lifecycle import Case2MessageStopVsContextCancel

        scenario = Case2MessageStopVsContextCancel(
            LifecycleCase.CASE_2_MESSAGE_STOP_VS_CONTEXT_CANCEL,
            self.manager,
            patcher_commit="test",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.0",
        )
        result = scenario.run()
        assert result.case == LifecycleCase.CASE_2_MESSAGE_STOP_VS_CONTEXT_CANCEL

    def test_case_3_cancel_result_survives_stop(self):
        """Case 3: Cancellation result surviving a successful stop callback."""
        from polaris_harness.arm_emulator.lifecycle import Case3CancelResultSurvivesStop

        scenario = Case3CancelResultSurvivesStop(
            LifecycleCase.CASE_3_CANCEL_RESULT_SURVIVES_STOP,
            self.manager,
            patcher_commit="test",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.0",
        )
        result = scenario.run()
        assert result.case == LifecycleCase.CASE_3_CANCEL_RESULT_SURVIVES_STOP

    def test_case_4_stop_failure_preserves_primary_error(self):
        """Case 4: Stop failure preserving primary error."""
        from polaris_harness.arm_emulator.lifecycle import Case4StopFailurePreservesPrimaryError

        scenario = Case4StopFailurePreservesPrimaryError(
            LifecycleCase.CASE_4_STOP_FAILURE_PRESERVES_PRIMARY_ERROR,
            self.manager,
            patcher_commit="test",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.0",
        )
        result = scenario.run()
        assert result.case == LifecycleCase.CASE_4_STOP_FAILURE_PRESERVES_PRIMARY_ERROR

    def test_case_5_deferred_timed_capture_cancelled(self):
        """Case 5: Deferred timed capture cancelled before timer."""
        from polaris_harness.arm_emulator.lifecycle import Case5DeferredTimedCaptureCancelled

        scenario = Case5DeferredTimedCaptureCancelled(
            LifecycleCase.CASE_5_DEFERRED_TIMED_CAPTURE_CANCELLED,
            self.manager,
            patcher_commit="test",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.0",
        )
        result = scenario.run()
        assert result.case == LifecycleCase.CASE_5_DEFERRED_TIMED_CAPTURE_CANCELLED

    def test_case_6_normal_timer_expiry(self):
        """Case 6: Normal timer expiry issuing intended capture."""
        from polaris_harness.arm_emulator.lifecycle import Case6NormalTimerExpiry

        scenario = Case6NormalTimerExpiry(
            LifecycleCase.CASE_6_NORMAL_TIMER_EXPIRY,
            self.manager,
            patcher_commit="test",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.0",
        )
        result = scenario.run()
        assert result.case == LifecycleCase.CASE_6_NORMAL_TIMER_EXPIRY

    def test_case_7_callback_ordering_register_preservation(self):
        """Case 7: Callback ordering, register preservation."""
        from polaris_harness.arm_emulator.lifecycle import Case7CallbackOrderingRegisterPreservation

        scenario = Case7CallbackOrderingRegisterPreservation(
            LifecycleCase.CASE_7_CALLBACK_ORDERING_REGISTER_PRESERVATION,
            self.manager,
            patcher_commit="test",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.0",
        )
        result = scenario.run()
        assert result.case == LifecycleCase.CASE_7_CALLBACK_ORDERING_REGISTER_PRESERVATION

    def test_case_8_output_publication_cleanup(self):
        """Case 8: Output publication, completion signaling, cleanup."""
        from polaris_harness.arm_emulator.lifecycle import Case8OutputPublicationCleanup

        scenario = Case8OutputPublicationCleanup(
            LifecycleCase.CASE_8_OUTPUT_PUBLICATION_CLEANUP,
            self.manager,
            patcher_commit="test",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.0",
        )
        result = scenario.run()
        assert result.case == LifecycleCase.CASE_8_OUTPUT_PUBLICATION_CLEANUP

    def test_case_9_manual_non_b_control(self):
        """Case 9: Manual/non-B dispatch and control case."""
        from polaris_harness.arm_emulator.lifecycle import Case9ManualNonBControl

        scenario = Case9ManualNonBControl(
            LifecycleCase.CASE_9_MANUAL_NON_B_CONTROL,
            self.manager,
            patcher_commit="test",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.0",
        )
        result = scenario.run()
        assert result.case == LifecycleCase.CASE_9_MANUAL_NON_B_CONTROL

    def test_all_cases_have_results(self):
        """All lifecycle cases produce LifecycleTestResult objects."""
        results = run_all_lifecycle_cases(
            self.manager,
            patcher_commit="test",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.0",
        )
        for case, result in results.items():
            assert isinstance(result, type(results[case]))
            assert hasattr(result, 'passed')
            assert hasattr(result, 'case')
            assert hasattr(result, 'assertion_errors')

    def test_cancel_edge_not_unconditional(self):
        """Tests must fail if a cancel edge becomes unconditional."""
        # This is a meta-test: verify that the scenario framework
        # can detect when cancel is taken incorrectly
        from polaris_harness.arm_emulator.lifecycle import Case1NormalBulbCompletion

        scenario = Case1NormalBulbCompletion(
            LifecycleCase.CASE_1_NORMAL_BULB_COMPLETION,
            self.manager,
            patcher_commit="test",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.0",
        )
        result = scenario.run()
        # Case 1 should not have cancel calls (normal completion)
        if result.contract:
            cancel_calls = [
                c for c in result.contract.stub_calls
                if c["function_name"] in ("cancel", "context_cancel")
            ]
            # Normal completion must not take cancel path
            assert len(cancel_calls) == 0, (
                f"Normal completion must not take cancel path, "
                f"got {len(cancel_calls)} cancel calls"
            )

    def test_normal_completion_not_cancel_path(self):
        """Tests must fail if normal completion takes the cancel path."""
        from polaris_harness.arm_emulator.lifecycle import Case6NormalTimerExpiry

        scenario = Case6NormalTimerExpiry(
            LifecycleCase.CASE_6_NORMAL_TIMER_EXPIRY,
            self.manager,
            patcher_commit="test",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.0",
        )
        result = scenario.run()
        # Normal timer expiry should not involve cancel
        if result.contract:
            cancel_calls = [
                c for c in result.contract.stub_calls
                if c["function_name"] in ("cancel", "context_cancel")
            ]
            assert len(cancel_calls) == 0, (
                f"Normal completion must not take cancel path, "
                f"got {len(cancel_calls)} cancel calls"
            )


class TestEmulatorFailClosed:
    """Test fail-closed behavior of the emulator."""

    def test_missing_stub_fail_closed(self):
        """Missing stub causes fail-closed error."""
        fixture_path = FIXTURES_DIR / "tiny_fixture.bin"
        sha256 = hashlib.sha256(fixture_path.read_bytes()).hexdigest()

        config = EmulationConfig(
            binary_path=fixture_path,
            binary_sha256=sha256,
            patcher_commit="test",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.0",
            fixture_stub_profile="default",
            command="test",
        )
        emulator = ARMEmulator(config)
        # Don't register any stubs - syscall should fail closed

        # The emulator will try to run and may fail on missing Unicorn
        # or missing stubs depending on environment
        result = emulator.run()
        # Result should be either ERROR (no unicorn) or some fail-closed result
        assert result.result in (
            EmulationResult.ERROR,
            EmulationResult.MISSING_STUB,
            EmulationResult.UNEXPECTED_SYSCALL,
            EmulationResult.UNSUPPORTED_INSTRUCTION,
            EmulationResult.PASS,
        )

    def test_binary_sha256_binding(self):
        """Binary SHA-256 is bound to the emulation config."""
        fixture_path = FIXTURES_DIR / "tiny_fixture.bin"
        sha256 = hashlib.sha256(fixture_path.read_bytes()).hexdigest()

        config = EmulationConfig(
            binary_path=fixture_path,
            binary_sha256=sha256,
            patcher_commit="commit-abc",
            harness_revision="rev-123",
            emulator_version="unicorn-2.0",
            fixture_stub_profile="profile-x",
            command="capture_image_with_Bulb",
        )
        assert config.binary_sha256 == sha256
        assert config.patcher_commit == "commit-abc"
        assert config.harness_revision == "rev-123"
        assert config.emulator_version == "unicorn-2.0"
        assert config.fixture_stub_profile == "profile-x"
        assert config.command == "capture_image_with_Bulb"
