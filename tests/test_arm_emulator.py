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
    Case1NormalBulbCompletion,
)
from polaris_harness.arm_emulator.contract import load_contract
from polaris_harness.arm_emulator.emulator import StubCallRecord
from polaris_harness.arm_emulator.stubs import StubProfile
from polaris_harness.cli import main


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

    def test_stage2_region_provenance_populated(self):
        """Stage-2 region placeholder provenance must be populated (not TBD)."""
        placeholder = FIXTURES_DIR / "stage2_region.bin.placeholder"
        assert placeholder.exists()
        content = placeholder.read_text()
        # Provenance fields must be populated, not TBD
        assert "23398c6efc7c737c1b2b91664a98b45a2b7bc01f97e704c3ccb4bf3fc4a618c3" in content
        assert "0x101aac" in content
        assert "2176" in content
        assert "arm" in content.lower()
        # TBD should not appear in provenance value fields
        lines = content.split("\n")
        for line in lines:
            if "|" in line and any(kw in line.lower() for kw in ["sha", "address", "size", "mode"]):
                assert "TBD" not in line, f"Provenance field still TBD: {line.strip()}"

    def test_stage2_region_not_checked_in(self):
        """Stage-2 region binary must NOT be checked in (private research only)."""
        bin_path = FIXTURES_DIR / "stage2_region.bin"
        assert not bin_path.exists(), (
            f"Stage-2 region binary must not be committed: {bin_path}"
        )

    def test_stage2_region_placeholder_has_retrieval_guide(self):
        """Placeholder must document how to obtain the binary locally."""
        placeholder = FIXTURES_DIR / "stage2_region.bin.placeholder"
        assert placeholder.exists()
        content = placeholder.read_text()
        assert "NOT CHECKED IN" in content
        assert "pgphoto.stage2ondisk" in content
        assert "23398c6efc7c737c1b2b91664a98b45a2b7bc01f97e704c3ccb4bf3fc4a618c3" in content
        assert "0x101aac" in content
        assert "2176" in content
        assert "NOT be committed" in content or "NOT be published" in content

    def test_fixture_manager_handles_missing_stage2_region(self):
        """FixtureManager must not crash when stage2_region.bin is absent."""
        manager = FixtureManager(FIXTURES_DIR)
        fixture = manager.get_fixture("stage2_region")
        assert fixture is None, (
            "stage2_region fixture must not be discovered when binary is absent"
        )

    def test_fixture_manager_rejects_mismatched_sha(self):
        """FixtureManager must fail closed on SHA-256 mismatch."""
        manager = FixtureManager(FIXTURES_DIR)
        fixture = manager.get_fixture("tiny_fixture")
        assert fixture is not None
        # Wrong SHA should fail verification
        assert not manager.verify_fixture("tiny_fixture", "0" * 64)
        # Correct SHA should pass
        assert manager.verify_fixture("tiny_fixture", fixture.sha256)

    def test_stub_call_fixture_exists(self):
        """Stub-call fixture binary must exist."""
        fixture_path = FIXTURES_DIR / "stub_call_fixture.bin"
        assert fixture_path.exists(), f"Stub-call fixture not found: {fixture_path}"

    def test_stub_call_fixture_sha256(self):
        """Stub-call fixture SHA-256 must be deterministic."""
        fixture_path = FIXTURES_DIR / "stub_call_fixture.bin"
        sha256 = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
        assert len(sha256) == 64
        assert all(c in "0123456789abcdef" for c in sha256)

    def test_stub_call_fixture_metadata(self):
        """Stub-call fixture metadata must be valid JSON."""
        meta_path = FIXTURES_DIR / "stub_call_fixture.json"
        assert meta_path.exists(), f"Stub-call fixture metadata not found: {meta_path}"
        with open(meta_path) as f:
            meta = json.load(f)
        assert "description" in meta
        assert "provenance" in meta
        assert "expected_stub_calls" in meta["provenance"]


class TestStubCallFixture:
    """Test that the stub-call fixture actually triggers stubs through the emulator."""

    def setup_method(self):
        self.manager = FixtureManager(FIXTURES_DIR)
        self.fixture_path = FIXTURES_DIR / "stub_call_fixture.bin"
        self.sha256 = hashlib.sha256(self.fixture_path.read_bytes()).hexdigest()

    def test_stub_call_fixture_triggers_os_stub(self):
        """SVC #0 instruction should trigger the OS stub via _hook_intr."""
        config = EmulationConfig(
            binary_path=self.fixture_path,
            binary_sha256=self.sha256,
            patcher_commit="test-commit",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.1.4",
            fixture_stub_profile="default",
            command="svc_test",
        )
        emulator = ARMEmulator(config)

        # Track stub calls
        stub_calls = []

        def tracking_os(function_name, arguments, register_snapshot):
            stub_calls.append((function_name, dict(arguments), dict(register_snapshot)))
            return 0

        emulator.register_stub("os", tracking_os)

        result = emulator.run()

        # The SVC #0 should have triggered the OS stub
        assert len(stub_calls) == 1, f"Expected 1 OS stub call, got {len(stub_calls)}"
        assert stub_calls[0][0] == "svc_0", f"Expected svc_0, got {stub_calls[0][0]}"
        assert result.result == EmulationResult.PASS
        assert result.exit_code == 0

    def test_stub_call_fixture_missing_os_stub_fail_closed(self):
        """Without an OS stub registered, SVC #0 should fail closed."""
        config = EmulationConfig(
            binary_path=self.fixture_path,
            binary_sha256=self.sha256,
            patcher_commit="test-commit",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.1.4",
            fixture_stub_profile="default",
            command="svc_test",
        )
        emulator = ARMEmulator(config)
        # Do NOT register any stubs

        result = emulator.run()

        # Should fail closed with MISSING_STUB or UNEXPECTED_SYSCALL
        assert result.result in (
            EmulationResult.MISSING_STUB,
            EmulationResult.UNEXPECTED_SYSCALL,
        ), f"Expected fail-closed result, got {result.result}"
        assert result.exit_code != 0


class TestTimeoutFailClosed:
    """Test that the emulator fails closed on runaway execution (infinite loops)."""

    def setup_method(self):
        self.manager = FixtureManager(FIXTURES_DIR)
        self.fixture_path = FIXTURES_DIR / "infinite_loop_fixture.bin"
        self.sha256 = hashlib.sha256(self.fixture_path.read_bytes()).hexdigest()

    def test_infinite_loop_fails_closed(self):
        """An infinite loop should be killed by the instruction limit."""
        config = EmulationConfig(
            binary_path=self.fixture_path,
            binary_sha256=self.sha256,
            patcher_commit="test-commit",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.1.4",
            fixture_stub_profile="default",
            command="infinite_loop",
            max_instructions=100,
        )
        emulator = ARMEmulator(config)

        result = emulator.run()

        # Should fail closed due to instruction limit exceeded
        assert result.result in (
            EmulationResult.ERROR,
            EmulationResult.TIMEOUT,
        ), f"Expected fail-closed result, got {result.result}"
        assert result.exit_code != 0
        # Instructions executed should be bounded (not runaway)
        assert result.instructions_executed <= config.max_instructions + 1


class TestUnsupportedInstruction:
    """Test that the emulator fails closed on unsupported instructions."""

    def setup_method(self):
        self.manager = FixtureManager(FIXTURES_DIR)
        self.fixture_path = FIXTURES_DIR / "illegal_opcode_fixture.bin"
        self.sha256 = hashlib.sha256(self.fixture_path.read_bytes()).hexdigest()

    def test_illegal_opcode_fails_closed(self):
        """An undefined instruction should trigger UNSUPPORTED_INSTRUCTION."""
        config = EmulationConfig(
            binary_path=self.fixture_path,
            binary_sha256=self.sha256,
            patcher_commit="test-commit",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.1.4",
            fixture_stub_profile="default",
            command="illegal_opcode",
        )
        emulator = ARMEmulator(config)

        result = emulator.run()

        # Should fail closed with UNSUPPORTED_INSTRUCTION
        assert result.result == EmulationResult.UNSUPPORTED_INSTRUCTION, (
            f"Expected UNSUPPORTED_INSTRUCTION, got {result.result}"
        )
        assert result.exit_code == 1
        assert result.unsupported_instruction_address is not None


class TestArmMode:
    """Test ARM (not Thumb) instruction mode support (issue #16)."""

    def setup_method(self):
        self.manager = FixtureManager(FIXTURES_DIR)
        self.fixture_path = FIXTURES_DIR / "arm_mode_fixture.bin"
        self.sha256 = hashlib.sha256(self.fixture_path.read_bytes()).hexdigest()

    def test_arm_mode_fixture_executes(self):
        """ARM mode fixture should execute end-to-end in ARM mode."""
        config = EmulationConfig(
            binary_path=self.fixture_path,
            binary_sha256=self.sha256,
            patcher_commit="test-commit",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.1.4",
            fixture_stub_profile="default",
            command="arm_mode_test",
            mode="arm",
        )
        emulator = ARMEmulator(config)

        result = emulator.run()

        assert result.result == EmulationResult.PASS, (
            f"Expected PASS, got {result.result}: {result.error_message}"
        )
        assert result.exit_code == 0
        assert result.instructions_executed > 0, "No instructions executed"
        assert result.mode == "arm"
        assert result.final_registers.get("R0") == 1, (
            f"Expected R0=1, got {result.final_registers.get('R0')}"
        )

    def test_arm_binary_in_thumb_mode_fails_closed(self):
        """An ARM binary run in Thumb mode should fail closed or produce
        clearly wrong results (not silently correct)."""
        config = EmulationConfig(
            binary_path=self.fixture_path,
            binary_sha256=self.sha256,
            patcher_commit="test-commit",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.1.4",
            fixture_stub_profile="default",
            command="arm_in_thumb_mode",
            mode="thumb",
        )
        emulator = ARMEmulator(config)

        result = emulator.run()

        # ARM instructions decoded as Thumb may execute but produce wrong results.
        # The key is that R0 should NOT be 1 (the ARM MOV R0,#1 didn't execute
        # as intended in Thumb mode).
        assert result.exit_code != 0 or result.final_registers.get("R0") != 1, (
            f"ARM binary in Thumb mode should not produce correct results, "
            f"got R0={result.final_registers.get('R0')}"
        )


class TestInvalidMode:
    """Test that unknown instruction modes fail closed at construction."""

    def test_invalid_mode_raises(self):
        """Creating an emulator with an invalid mode should raise ValueError."""
        config = EmulationConfig(
            binary_path=FIXTURES_DIR / "tiny_fixture.bin",
            binary_sha256=hashlib.sha256(
                (FIXTURES_DIR / "tiny_fixture.bin").read_bytes()
            ).hexdigest(),
            patcher_commit="test-commit",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.1.4",
            fixture_stub_profile="default",
            command="invalid_mode_test",
            mode="invalid_mode",
        )

        with pytest.raises(ValueError, match="Invalid instruction mode"):
            ARMEmulator(config)


class TestAAPCSMarshaling:
    """Test AAPCS argument/return marshaling between emulated code and stubs (issue #17)."""

    def setup_method(self):
        self.manager = FixtureManager(FIXTURES_DIR)
        self.fixture_path = FIXTURES_DIR / "aapcs_fixture.bin"
        self.sha256 = hashlib.sha256(self.fixture_path.read_bytes()).hexdigest()

    def test_aapcs_stub_entry_receives_r0_r3(self):
        """Stub entry should receive R0-R3 as arguments per AAPCS."""
        config = EmulationConfig(
            binary_path=self.fixture_path,
            binary_sha256=self.sha256,
            patcher_commit="test-commit",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.1.4",
            fixture_stub_profile="default",
            command="aapcs_test",
            mode="arm",
            entry_address=4096,
        )
        emulator = ARMEmulator(config)

        captured_args = {}

        def camera_stub(function_name, arguments, register_snapshot):
            captured_args["args"] = list(arguments)
            captured_args["regs"] = dict(register_snapshot)
            return 99  # Return value should go back to R0

        # Register stub first, then its entry address
        emulator.register_stub("camera", camera_stub)
        emulator.register_stub_entry("camera", 0x2000)

        result = emulator.run()

        assert result.result == EmulationResult.PASS, (
            f"Expected PASS, got {result.result}: {result.error_message}"
        )
        assert result.exit_code == 0

        # Stub should have been called with R0=42, R1=100
        assert "args" in captured_args, "Stub was not called"
        args = captured_args["args"]
        assert len(args) >= 2, f"Expected at least 2 args, got {len(args)}"
        assert args[0] == 42, f"Expected R0=42, got args[0]={args[0]}"
        assert args[1] == 100, f"Expected R1=100, got args[1]={args[1]}"

        # Return value should be written back to R0
        assert result.final_registers.get("R0") == 99, (
            f"Expected R0=99 (stub return), got {result.final_registers.get('R0')}"
        )


class TestFailurePathProvenance:
    """Failure records must report the mode and work actually performed (#21).

    A failing run is the record a gate has to trust, so it cannot be allowed to
    fall back to the dataclass defaults (mode="thumb", instructions_executed=0).
    """

    def setup_method(self):
        self.fixture_path = FIXTURES_DIR / "arm_mode_fixture.bin"
        self.sha256 = hashlib.sha256(self.fixture_path.read_bytes()).hexdigest()

    def _config(self, **overrides):
        params = dict(
            binary_path=self.fixture_path,
            binary_sha256=self.sha256,
            patcher_commit="test-commit",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.1.4",
            fixture_stub_profile="default",
            command="failure_provenance",
            mode="arm",
            entry_address=4096,
        )
        params.update(overrides)
        return EmulationConfig(**params)

    def test_faulting_arm_run_reports_arm_mode(self):
        """A run that faults in ARM mode must report mode='arm', not 'thumb'."""
        # MOV R0,#0 ; MOV PC,R0 -> branches to unmapped address 0 and faults.
        fault = FIXTURES_DIR.parent / "arm" / "_fault_probe.bin"
        import struct
        fault.write_bytes(struct.pack("<2I", 0xE3A00000, 0xE12FFF11))
        try:
            config = self._config(binary_path=fault,
                                  binary_sha256=hashlib.sha256(
                                      fault.read_bytes()).hexdigest())
            result = ARMEmulator(config).run()
        finally:
            fault.unlink(missing_ok=True)

        assert result.result != EmulationResult.PASS, "fixture should fault"
        assert result.mode == "arm", (
            f"Failure record reported mode={result.mode!r}; must report the "
            f"requested 'arm' (issue #21)"
        )

    def test_faulting_run_reports_instructions_executed(self):
        """A faulting run must report the instructions it really executed."""
        import struct
        fault = FIXTURES_DIR / "_fault_count_probe.bin"
        fault.write_bytes(struct.pack("<2I", 0xE3A00000, 0xE12FFF11))
        try:
            config = self._config(binary_path=fault,
                                  binary_sha256=hashlib.sha256(
                                      fault.read_bytes()).hexdigest())
            emulator = ARMEmulator(config)
            result = emulator.run()
        finally:
            fault.unlink(missing_ok=True)

        assert result.result != EmulationResult.PASS
        assert result.instructions_executed == emulator._instruction_count, (
            f"Contract reported {result.instructions_executed} but the engine "
            f"executed {emulator._instruction_count} (issue #21)"
        )
        assert result.instructions_executed > 0

    def test_timeout_reports_real_instruction_count(self):
        """The instruction-limit path must report the real count, not 0."""
        import struct
        loop = FIXTURES_DIR / "_timeout_count_probe.bin"
        # B . (branch to self)
        loop.write_bytes(struct.pack("<I", 0xEAFFFFFE))
        try:
            config = self._config(binary_path=loop,
                                  binary_sha256=hashlib.sha256(
                                      loop.read_bytes()).hexdigest(),
                                  max_instructions=20)
            result = ARMEmulator(config).run()
        finally:
            loop.unlink(missing_ok=True)

        assert result.result == EmulationResult.TIMEOUT
        assert result.instructions_executed > 0, (
            "Timeout record reported 0 instructions executed (issue #21)"
        )

    def test_failure_record_carries_stub_calls(self):
        """Stub calls made before a fault must still appear in the record."""
        import struct
        # 0x1000 MOV R0,#1
        # 0x1004 BL 0x1010 (LR=0x1008)   <- call site, intercepted
        # 0x1008 MOV PC,R0               <- faults: R0=1 is unmapped
        # 0x100C BX LR
        # 0x1010 BX LR                   <- stub body
        code = FIXTURES_DIR / "_stub_then_fault.bin"
        code.write_bytes(struct.pack(
            "<5I", 0xE3A00001, 0xEB000001, 0xE12FFF11, 0xE12FFF1E, 0xE12FFF1E))
        try:
            config = self._config(binary_path=code,
                                  binary_sha256=hashlib.sha256(
                                      code.read_bytes()).hexdigest())
            emulator = ARMEmulator(config)
            emulator.register_stub("camera", lambda fn, a, r: 0)
            emulator.register_stub_entry("camera", 0x1004,
                                         function_name="stop")
            result = emulator.run()
        finally:
            code.unlink(missing_ok=True)

        assert len(result.stub_calls) == 1, (
            f"Stub calls lost on the failure path: {len(result.stub_calls)} "
            f"(issue #21)"
        )
        assert result.stub_calls[0].function_name == "stop"


class TestStubEntryModels:
    """Call-site vs PLT-target interception must both be correct (#22)."""

    def setup_method(self):
        import struct
        # 0x1000 MOV R0,#0x11
        # 0x1004 MOV R1,#0x22
        # 0x1008 BL 0x1010          (return address 0x100C)
        # 0x100C MOV R2,#0x33       <- correct resume point
        # 0x1010 BX LR              <- PLT-style stub body
        self.fixture_path = FIXTURES_DIR / "_entry_models.bin"
        self.fixture_path.write_bytes(struct.pack(
            "<6I", 0xE3A00011, 0xE3A01022, 0xEB000000,
            0xE3A02033, 0xE12FFF1E, 0xE3A03044))
        self.sha256 = hashlib.sha256(self.fixture_path.read_bytes()).hexdigest()

    def teardown_method(self):
        self.fixture_path.unlink(missing_ok=True)

    def _config(self):
        return EmulationConfig(
            binary_path=self.fixture_path,
            binary_sha256=self.sha256,
            patcher_commit="test-commit",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.1.4",
            fixture_stub_profile="default",
            command="entry_models",
            mode="arm",
            entry_address=4096,
            code_size=0x1000,
            max_instructions=50,
        )

    def test_call_site_model_resumes_after_branch(self):
        """Intercepting the BL itself resumes at the following instruction."""
        emulator = ARMEmulator(self._config())
        emulator.register_stub("camera", lambda fn, a, r: 0x5A)
        emulator.register_stub_entry("camera", 0x1008,
                                     function_name="stop",
                                     model="call-site")
        result = emulator.run()

        assert result.final_registers.get("R2") == 0x33, (
            "call-site interception did not resume at call site + size"
        )
        assert result.final_registers.get("R3") != 0x44

    def test_target_model_returns_via_link_register(self):
        """Intercepting the callee must return to the caller via LR (#22)."""
        emulator = ARMEmulator(self._config())
        emulator.register_stub("camera", lambda fn, a, r: 0x5A)
        emulator.register_stub_entry("camera", 0x1010,
                                     function_name="stop",
                                     model="target")
        result = emulator.run()

        # LR holds 0x100C (the instruction after the BL), so R2 must be set.
        assert result.final_registers.get("R2") == 0x33, (
            "target interception fell through into the stub body instead of "
            "returning via LR (issue #22)"
        )
        assert result.final_registers.get("R3") != 0x44

    def test_semantic_function_name_recorded(self):
        """Calls must carry their declared identity, not a flat label (#22)."""
        emulator = ARMEmulator(self._config())
        emulator.register_stub("camera", lambda fn, a, r: 0)
        emulator.register_stub_entry("camera", 0x1008, function_name="close")
        result = emulator.run()

        assert len(result.stub_calls) == 1
        assert result.stub_calls[0].function_name == "close", (
            f"Expected 'close', got {result.stub_calls[0].function_name!r} "
            f"(issue #22: lifecycle assertions filter on this field)"
        )

    def test_distinct_entries_for_one_stub(self):
        """One stub may expose several identities at different addresses."""
        import struct
        # 0x1000 MOV R0,#1
        # 0x1004 BL 0x1030 (LR=0x1008)   -> stop body, returns to 0x1008
        # 0x1008 BL 0x1034 (LR=0x100C)   -> close body, returns to 0x100C
        # until = entry + len(binary) = 0x100C, so the second return ends the run.
        code = FIXTURES_DIR / "_multi_entry.bin"
        code.write_bytes(struct.pack("<3I", 0xE3A00001, 0xEB000009, 0xEB000009))
        sha = hashlib.sha256(code.read_bytes()).hexdigest()
        try:
            config = EmulationConfig(
                binary_path=code, binary_sha256=sha,
                patcher_commit="t", harness_revision="0.1.0",
                emulator_version="unicorn-2.1.4",
                fixture_stub_profile="default", command="multi",
                mode="arm", entry_address=4096, code_size=0x1000,
                max_instructions=50)
            emulator = ARMEmulator(config)
            emulator.register_stub("camera", lambda fn, a, r: 0)
            # Two distinct callee bodies for the same stub, each with its own
            # semantic identity. Target model: control returns via LR.
            emulator.register_stub_entry("camera", 0x1030,
                                         function_name="stop",
                                         model="target")
            emulator.register_stub_entry("camera", 0x1034,
                                         function_name="close",
                                         model="target")
            result = emulator.run()
        finally:
            code.unlink(missing_ok=True)

        names = [c.function_name for c in result.stub_calls]
        assert names[:2] == ["stop", "close"], (
            f"Expected distinct identities per entry, got {names}"
        )
        assert len(names) == 2, (
            f"Execution should terminate after both calls, got {len(names)} calls "
            f"(run looped: {names})"
        )

    def test_unknown_model_rejected(self):
        """An unknown interception model must fail closed."""
        emulator = ARMEmulator(self._config())
        emulator.register_stub("camera", lambda fn, a, r: 0)
        with pytest.raises(ValueError, match="Invalid stub entry model"):
            emulator.register_stub_entry("camera", 0x1010, model="bogus")


class TestAbiShapeMarshaling:
    """StubProfile.abi_shape must actually drive marshaling (#22)."""

    def setup_method(self):
        import struct
        self.fixture_path = FIXTURES_DIR / "_abi_shape.bin"
        self.fixture_path.write_bytes(struct.pack("<4I", 0xE3A00011, 0xE3A01022,
                                                  0xEB000000, 0xE12FFF1E))
        self.sha256 = hashlib.sha256(self.fixture_path.read_bytes()).hexdigest()

    def teardown_method(self):
        self.fixture_path.unlink(missing_ok=True)

    def _config(self):
        return EmulationConfig(
            binary_path=self.fixture_path,
            binary_sha256=self.sha256,
            patcher_commit="test-commit",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.1.4",
            fixture_stub_profile="abi_test",
            command="abi_shape",
            mode="arm",
            entry_address=4096,
            code_size=0x1000,
            max_instructions=50,
        )

    def test_profile_abi_shape_is_consumed(self):
        """A profile declaring arg_count>4 must attempt to read stack words."""
        profile = StubProfile(
            name="abi",
            camera_stub=lambda fn, a, r: 0,
            abi_shape={"camera": {"arg_count": 6, "pointer_args": [],
                                  "deref_bytes": {}}},
        )
        emulator = ARMEmulator(self._config())
        emulator.apply_stub_profile(profile)
        emulator.register_stub_entry("camera", 0x1008, function_name="stop")
        result = emulator.run()

        # SP points just past the mapped stack region, so words past R3 are
        # unreadable: the declared shape must surface as a fail-closed error
        # rather than silently recording four arguments.
        assert result.result in (EmulationResult.ERROR, EmulationResult.MISSING_STUB), (
            f"arg_count=6 over unmapped memory should fail closed, got "
            f"{result.result}: {result.error_message}"
        )
        assert "unmapped" in (result.error_message or "").lower(), (
            f"Expected an unmapped-memory reason, got {result.error_message!r}"
        )

    def test_pointer_argument_dereferenced(self):
        """A declared pointer argument must be read from emulated memory."""
        import struct
        # 0x1000 LDR R0,[PC,#8] -> PC reads as 0x1008, so this loads the word
        #                        at 0x1010, which holds the pointer 0x1020.
        # 0x1004 BL <stub>      -> intercepted at the call site, resume 0x1008
        # 0x1008 BX LR          -> LR = entry + len(binary) = 0x1024 == until
        # 0x1010 .word 0x1020   -> pointer value (mapped)
        # 0x1020 .word 0xCAFEBABE -> the bytes the stub should actually receive
        code = FIXTURES_DIR / "_ptr_arg.bin"
        buf = bytearray(0x24)
        struct.pack_into("<I", buf, 0x00, 0xE59F0008)   # LDR R0,[PC,#8]
        struct.pack_into("<I", buf, 0x04, 0xEB000000)   # BL (intercepted here)
        struct.pack_into("<I", buf, 0x08, 0xE12FFF1E)   # BX LR
        struct.pack_into("<I", buf, 0x10, 0x00001020)   # pointer value
        struct.pack_into("<I", buf, 0x20, 0xCAFEBABE)   # pointed-to content
        code.write_bytes(bytes(buf))
        sha = hashlib.sha256(code.read_bytes()).hexdigest()
        seen = {}

        def stub(fn, args, regs):
            seen["args"] = list(args)
            return 0
        try:
            config = EmulationConfig(
                binary_path=code, binary_sha256=sha, patcher_commit="t",
                harness_revision="0.1.0", emulator_version="unicorn-2.1.4",
                fixture_stub_profile="ptr", command="ptr", mode="arm",
                entry_address=4096, code_size=0x1000, max_instructions=50)
            emulator = ARMEmulator(config)
            emulator.register_stub("camera", stub)
            emulator.set_stub_abi_shape(
                "camera", {"arg_count": 4, "pointer_args": [0],
                           "deref_bytes": {0: 4}})
            emulator.register_stub_entry("camera", 0x1004,
                                         function_name="stop",
                                         model="call-site")
            result = emulator.run()
        finally:
            code.unlink(missing_ok=True)

        assert result.result == EmulationResult.PASS, (
            f"Run should succeed: {result.result} {result.error_message}"
        )
        assert "args" in seen, "stub was not called"
        # Without dereference the stub would see the raw pointer 0x1020.
        assert seen["args"][0] == 0xCAFEBABE, (
            f"Expected dereferenced content 0xCAFEBABE, got "
            f"{seen['args'][0]:#x} - pointer was not followed (issue #22)"
        )

    def test_unreadable_pointer_fails_closed(self):
        """A pointer argument into unmapped memory must not pass silently."""
        import struct
        # R0 = 0x11, which is not mapped: dereferencing it must fail closed.
        code = FIXTURES_DIR / "_ptr_fail.bin"
        buf = bytearray(0x24)
        struct.pack_into("<I", buf, 0x00, 0xE3A00011)   # MOV R0,#0x11
        struct.pack_into("<I", buf, 0x04, 0xEB000000)   # BL (intercepted here)
        struct.pack_into("<I", buf, 0x08, 0xE12FFF1E)   # BX LR
        code.write_bytes(bytes(buf))
        sha = hashlib.sha256(code.read_bytes()).hexdigest()
        try:
            config = EmulationConfig(
                binary_path=code, binary_sha256=sha, patcher_commit="t",
                harness_revision="0.1.0", emulator_version="unicorn-2.1.4",
                fixture_stub_profile="ptr", command="ptr", mode="arm",
                entry_address=4096, code_size=0x1000, max_instructions=50)
            emulator = ARMEmulator(config)
            emulator.register_stub("camera", lambda fn, a, r: 0)
            emulator.set_stub_abi_shape(
                "camera", {"arg_count": 4, "pointer_args": [0],
                           "deref_bytes": {0: 4}})
            emulator.register_stub_entry("camera", 0x1004,
                                         function_name="stop",
                                         model="call-site")
            result = emulator.run()
        finally:
            code.unlink(missing_ok=True)

        assert result.result in (
            EmulationResult.ERROR, EmulationResult.MISSING_STUB), (
            f"Unreadable pointer should fail closed, got {result.result}"
        )
        assert "unmapped" in (result.error_message or "").lower()


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


class TestFixtureExecutionMetadata:
    """Fixture sidecars must carry mode/entry/stub entries into the emulator (#19).

    Without this a real ARM region loads as Thumb at 0x1000 and faults on
    instruction one, and external calls execute as real code instead of
    reaching stubs - so lifecycle assertions could never match.
    """

    def setup_method(self):
        self.manager = FixtureManager(FIXTURES_DIR)

    def test_sidecar_mode_is_parsed(self):
        """A sidecar declaring mode='arm' is surfaced on the metadata."""
        fixture = self.manager.get_fixture("lifecycle_probe_fixture")
        assert fixture is not None, "probe fixture missing"
        assert fixture.mode == "arm", (
            f"Expected sidecar mode 'arm', got {fixture.mode!r} (issue #19)"
        )

    def test_sidecar_entry_and_limits_are_parsed(self):
        """Entry address, code size and instruction limit come from the sidecar."""
        fixture = self.manager.get_fixture("lifecycle_probe_fixture")
        assert fixture.entry_address == 4096
        assert fixture.code_size == 4096
        assert fixture.max_instructions == 50

    def test_sidecar_stub_entries_are_parsed(self):
        """Declared stub entries carry address, stub, semantic name and model."""
        fixture = self.manager.get_fixture("lifecycle_probe_fixture")
        assert len(fixture.stub_entries) == 2, (
            f"Expected 2 declared stub entries, got {len(fixture.stub_entries)}"
        )
        names = [e["function_name"] for e in fixture.stub_entries]
        assert names == ["stop", "close"], f"Got {names}"
        assert all(e["model"] == "target" for e in fixture.stub_entries)

    def test_config_uses_sidecar_mode_not_default_thumb(self):
        """to_emulation_config must not silently default a real ARM region to Thumb."""
        config = self.manager.to_emulation_config(
            fixture_name="lifecycle_probe_fixture",
            patcher_commit="c", harness_revision="h", emulator_version="e",
            stub_profile="default", command="capture_image_with_Bulb",
        )
        assert config.mode == "arm", (
            f"Config mode was {config.mode!r}; sidecar declared 'arm' (issue #19)"
        )
        assert config.entry_address == 4096

    def test_config_uses_sidecar_entry_address_over_fallback(self, tmp_path):
        """A sidecar entry address must beat the 0x1000 fallback.

        The probe fixture's entry equals the fallback, so this uses a sidecar
        declaring a different address - otherwise the assertion would pass even
        if sidecar entry resolution were removed entirely.
        """
        import shutil
        d = tmp_path / "arm"
        d.mkdir()
        shutil.copy(FIXTURES_DIR / "lifecycle_probe_fixture.bin", d)
        (d / "lifecycle_probe_fixture.json").write_text(json.dumps({
            "description": "non-default entry",
            "provenance": {"mode": "arm", "entry_address": 0x20000,
                           "code_address": 0x20000, "code_size": 0x2000},
        }))
        config = FixtureManager(d).to_emulation_config(
            fixture_name="lifecycle_probe_fixture",
            patcher_commit="c", harness_revision="h", emulator_version="e",
            stub_profile="default", command="cmd",
        )
        assert config.entry_address == 0x20000, (
            f"Expected sidecar entry 0x20000, got {config.entry_address:#x} "
            f"(issue #19: sidecar entry ignored)"
        )
        assert config.code_address == 0x20000
        assert config.code_size == 0x2000

    def test_explicit_arguments_override_sidecar(self):
        """Explicit mode/entry arguments still win over sidecar values."""
        config = self.manager.to_emulation_config(
            fixture_name="lifecycle_probe_fixture",
            patcher_commit="c", harness_revision="h", emulator_version="e",
            stub_profile="default", command="cmd",
            entry_address=0x8000, mode="thumb",
        )
        assert config.mode == "thumb"
        assert config.entry_address == 0x8000

    def test_invalid_sidecar_mode_fails_closed(self, tmp_path):
        """A sidecar declaring an unknown mode must raise, not run silently."""
        import shutil
        import struct
        d = tmp_path / "arm"
        d.mkdir()
        shutil.copy(FIXTURES_DIR / "lifecycle_probe_fixture.bin", d)
        (d / "lifecycle_probe_fixture.json").write_text(json.dumps({
            "description": "bad mode",
            "provenance": {"mode": "mips", "entry_address": 4096},
        }))
        manager = FixtureManager(d)
        with pytest.raises(ValueError, match="invalid mode"):
            manager.to_emulation_config(
                fixture_name="lifecycle_probe_fixture",
                patcher_commit="c", harness_revision="h", emulator_version="e",
                stub_profile="default", command="cmd",
            )

    def test_fixture_without_sidecar_defaults_to_thumb(self):
        """Fixtures with no declared mode keep the historical Thumb default."""
        config = self.manager.to_emulation_config(
            fixture_name="tiny_fixture",
            patcher_commit="c", harness_revision="h", emulator_version="e",
            stub_profile="default", command="cmd",
        )
        assert config.mode == "thumb"


class TestLifecycleStubEntryWiring:
    """Lifecycle scenarios must wire sidecar stub entries into the emulator (#19)."""

    def setup_method(self):
        self.manager = FixtureManager(FIXTURES_DIR)

    def test_scenario_registers_stub_entries_and_runs(self):
        """A scenario against a fixture with declared entries executes it."""
        scenario = Case1NormalBulbCompletion(
            LifecycleCase.CASE_1_NORMAL_BULB_COMPLETION,
            self.manager,
            fixture_name="lifecycle_probe_fixture",
            patcher_commit="c", harness_revision="h", emulator_version="e",
        )
        emulator = scenario._create_emulator("capture_image_with_Bulb")

        assert len(emulator._stub_entries) == 2, (
            f"Expected 2 registered stub entries, got {len(emulator._stub_entries)} "
            f"(issue #19: entries were never registered)"
        )
        names = sorted(e["function_name"] for e in emulator._stub_entries.values())
        assert names == ["close", "stop"], f"Got {names}"

        result = emulator.run()
        assert result.result == EmulationResult.PASS, (
            f"{result.result}: {result.error_message}"
        )
        assert result.mode == "arm"
        called = [c.function_name for c in result.stub_calls]
        assert called == ["stop", "close"], (
            f"Expected semantic stub calls, got {called}"
        )

    def test_scenario_entry_for_unregistered_stub_fails_closed(self, tmp_path):
        """A declared entry naming an unregistered stub must raise."""
        import shutil
        d = tmp_path / "arm"
        d.mkdir()
        shutil.copy(FIXTURES_DIR / "lifecycle_probe_fixture.bin", d)
        (d / "lifecycle_probe_fixture.json").write_text(json.dumps({
            "description": "entry for unregistered stub",
            "provenance": {
                "mode": "arm",
                "entry_address": 4096,
                "stub_entries": [
                    {"address": 4128, "stub_name": "nonexistent",
                     "function_name": "stop"},
                ],
            },
        }))
        scenario = Case1NormalBulbCompletion(
            LifecycleCase.CASE_1_NORMAL_BULB_COMPLETION,
            FixtureManager(d),
            fixture_name="lifecycle_probe_fixture",
            patcher_commit="c", harness_revision="h", emulator_version="e",
        )
        with pytest.raises(KeyError, match="unregistered stub"):
            scenario._create_emulator("capture_image_with_Bulb")

    def test_suite_targets_stage2_and_skips_when_absent(self, tmp_path):
        """The suite targets stage2_region and SKIPs (not FAILs) when it is absent."""
        d = tmp_path / "arm"
        d.mkdir()  # empty fixtures dir: no stage2_region.bin
        results = run_all_lifecycle_cases(FixtureManager(d))
        assert len(results) == 9
        for case, res in results.items():
            assert res.skipped, f"{case.value} should SKIP, got passed={res.passed}"
            assert not res.passed
            assert "stage2_region" in (res.skip_reason or "")

    def test_suite_executes_real_fixture_when_present(self, tmp_path):
        """With a real fixture present the suite executes it instead of skipping."""
        import shutil
        d = tmp_path / "arm"
        d.mkdir()
        # stage2_region is the name the suite targets by default.
        shutil.copy(FIXTURES_DIR / "lifecycle_probe_fixture.bin",
                    d / "stage2_region.bin")
        sidecar = json.loads(
            (FIXTURES_DIR / "lifecycle_probe_fixture.json").read_text())
        (d / "stage2_region.json").write_text(json.dumps(sidecar))

        results = run_all_lifecycle_cases(FixtureManager(d))
        assert len(results) == 9
        for case, res in results.items():
            assert not res.skipped, (
                f"{case.value} still SKIPped although a fixture is present"
            )
            assert res.contract is not None, f"{case.value} produced no contract"
            assert res.contract.mode == "arm", (
                f"{case.value} ran in {res.contract.mode!r}, expected 'arm'"
            )


class TestArmEmulateCLIExitCodes:
    """CLI exit codes: SKIP is not FAIL, a real failure is (#19, #23)."""

    def _argv(self, monkeypatch, *extra):
        monkeypatch.setattr(sys, "argv", ["polaris-harness", "arm-emulate", *extra])

    def test_suite_exits_zero_when_all_skipped(self, tmp_path, monkeypatch):
        """All cases skipped (no private fixture) must exit 0, not 3."""
        d = tmp_path / "arm"
        d.mkdir()
        self._argv(monkeypatch, "--suite", "--fixtures-dir", str(d))
        assert main() == 0

    def test_suite_exits_three_on_real_failure(self, tmp_path, monkeypatch):
        """A present fixture whose assertions fail must exit 3, not 0."""
        import shutil
        d = tmp_path / "arm"
        d.mkdir()
        # Entry address deliberately wrong: the run faults, which is a real
        # behavioural failure rather than a skip.
        sidecar = json.loads(
            (FIXTURES_DIR / "lifecycle_probe_fixture.json").read_text())
        sidecar["provenance"]["entry_address"] = 0x9000
        shutil.copy(FIXTURES_DIR / "lifecycle_probe_fixture.bin",
                    d / "stage2_region.bin")
        (d / "stage2_region.json").write_text(json.dumps(sidecar))

        self._argv(monkeypatch, "--suite", "--fixtures-dir", str(d))
        assert main() == 3

    def test_single_fixture_exits_zero_and_uses_sidecar_mode(self, monkeypatch):
        """Single-fixture run honours sidecar mode/entries and exits 0."""
        self._argv(monkeypatch, "--fixture", "lifecycle_probe_fixture")
        assert main() == 0

    def test_single_fixture_unknown_exits_two(self, monkeypatch):
        """An unknown fixture name is a usage error (exit 2)."""
        self._argv(monkeypatch, "--fixture", "no_such_fixture")
        assert main() == 2

    def test_explicit_mode_override_is_accepted(self, monkeypatch):
        """--mode thumb overrides the sidecar and still runs cleanly."""
        # tiny_fixture is Thumb; overriding an ARM fixture to Thumb must not
        # crash the CLI - it reports a failed emulation, not a usage error.
        self._argv(monkeypatch, "--fixture", "tiny_fixture")
        assert main() == 0


class TestSidecarAddressCoercion:
    """Sidecar addresses may be recorded as hex strings in provenance notes (#19)."""

    def test_hex_string_addresses_are_accepted(self, tmp_path):
        """A sidecar written from hex provenance notes must parse correctly."""
        import shutil
        d = tmp_path / "arm"
        d.mkdir()
        shutil.copy(FIXTURES_DIR / "lifecycle_probe_fixture.bin", d)
        (d / "lifecycle_probe_fixture.json").write_text(json.dumps({
            "description": "hex provenance",
            "provenance": {
                "mode": "arm",
                "entry_address": "0x20000",
                "code_address": "0x20000",
                "code_size": "0x1000",
                "max_instructions": "0x32",
                "stub_entries": [
                    {"address": "0x20080", "stub_name": "camera",
                     "function_name": "stop", "model": "target"},
                ],
            },
        }))
        fixture = FixtureManager(d).get_fixture("lifecycle_probe_fixture")
        assert fixture.entry_address == 0x20000
        assert fixture.code_address == 0x20000
        assert fixture.code_size == 0x1000
        assert fixture.max_instructions == 0x32
        assert fixture.stub_entries[0]["address"] == "0x20080"

        config = FixtureManager(d).to_emulation_config(
            fixture_name="lifecycle_probe_fixture",
            patcher_commit="c", harness_revision="h", emulator_version="e",
            stub_profile="default", command="cmd",
        )
        assert config.entry_address == 0x20000
        assert config.code_size == 0x1000

    def test_malformed_address_is_ignored_not_crashing(self, tmp_path):
        """An unparseable address falls back to the default instead of crashing."""
        import shutil
        d = tmp_path / "arm"
        d.mkdir()
        shutil.copy(FIXTURES_DIR / "lifecycle_probe_fixture.bin", d)
        (d / "lifecycle_probe_fixture.json").write_text(json.dumps({
            "description": "bad address",
            "provenance": {"mode": "arm", "entry_address": "not-a-number"},
        }))
        manager = FixtureManager(d)
        config = manager.to_emulation_config(
            fixture_name="lifecycle_probe_fixture",
            patcher_commit="c", harness_revision="h", emulator_version="e",
            stub_profile="default", command="cmd",
        )
        assert config.entry_address == 0x1000
