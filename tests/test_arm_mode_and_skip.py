"""Regression tests for instruction-mode mis-decoding and SKIP semantics (#23).

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

#23 lists three areas: ARM mode, AAPCS marshalling and SKIP semantics. Marshalling
is covered by test_arm_environment_seeding.py. Invalid mode strings and ARM-decoded-
as-Thumb are already covered in test_arm_emulator.py (TestInvalidMode,
test_arm_binary_in_thumb_mode_fails_closed). What was missing is the other direction
and the durability of the SKIP/FAIL distinction, which is what this file adds.

The mis-decoding tests matter because a region's mode comes from its sidecar. A
region recorded with the wrong mode does not fail loudly - it decodes every
instruction against the wrong encoding and can report success over garbage. A
lifecycle case would then assert against register values produced by instructions
that never ran, and the report would look like a valid emulation of the artifact.

Observed behaviour these tests pin down:
  ARM blob, thumb mode : PASS, exit 0, R0=0, ~4279 instructions
  Thumb blob, arm mode : usually MEMORY_FAULT, but PASS in roughly 1 run in 20

Both directions leave R0 unset and execute far more than one instruction, so the
register value is the reliable signal. The reported classification is not: run()
decides PASS/exit 0 unless one of three flags is set (instruction limit, unsupported
instruction, unexpected syscall), so a run that stops for any other reason is
indistinguishable from a successful one. That gap is recorded on #23.
"""

import hashlib
import json
from pathlib import Path

import pytest

from polaris_harness.arm_emulator.emulator import (
    ARMEmulator,
    EmulationConfig,
    EmulationResult,
)
from polaris_harness.arm_emulator.fixtures import FixtureManager

# "movs r0, #1" as 16-bit Thumb.
THUMB_MOV_R0_1 = bytes.fromhex("0120")
# "mov r0, #1" as 32-bit ARM (0xE3A00001).
ARM_MOV_R0_1 = bytes.fromhex("0100a0e3")


def _run(blob: bytes, mode: str, tmp_path: Path) -> object:
    """Execute a one-instruction blob in the given mode.

    Args:
        blob: Machine code to execute
        mode: "arm" or "thumb"
        tmp_path: Directory for the throwaway binary

    Returns:
        The EmulationResultRecord from run()
    """
    binary = tmp_path / f"probe_{mode}.bin"
    binary.write_bytes(blob)
    config = EmulationConfig(
        binary_path=binary,
        binary_sha256=hashlib.sha256(blob).hexdigest(),
        patcher_commit="test",
        harness_revision="0.1.0",
        emulator_version="test",
        fixture_stub_profile="default",
        command=f"probe_{mode}",
        mode=mode,
        entry_address=0x1000,
        code_address=0x1000,
        code_size=0x4000,
        stack_address=0x200000,
        stack_size=0x4000,
    )
    return ARMEmulator(config).run()


class TestCorrectModeBaseline:
    """The probes are only meaningful if the correct mode behaves correctly."""

    def test_thumb_blob_in_thumb_mode_sets_r0(self, tmp_path):
        """Baseline: a Thumb blob decoded as Thumb executes its one instruction."""
        result = _run(THUMB_MOV_R0_1, "thumb", tmp_path)

        assert result.result.name == "PASS"
        assert result.final_registers.get("R0") == 1
        assert result.instructions_executed == 1

    def test_arm_blob_in_arm_mode_sets_r0(self, tmp_path):
        """Baseline: an ARM blob decoded as ARM executes its one instruction.

        Without this the mis-decoding assertions below could pass simply because
        the encodings used are wrong, which is exactly how a first draft of these
        tests failed - E1A00001 is MOV R0,R1, not MOV R0,#1.
        """
        result = _run(ARM_MOV_R0_1, "arm", tmp_path)

        assert result.result.name == "PASS"
        assert result.final_registers.get("R0") == 1
        assert result.instructions_executed == 1


class TestModeMisDecoding:
    """A region recorded with the wrong mode must not look like a valid run."""

    def test_thumb_blob_in_arm_mode_never_produces_correct_result(self, tmp_path):
        """A 2-byte Thumb blob decoded as ARM must never yield the Thumb result.

        Asserted on the register value rather than the reported classification,
        because the classification is not stable across runs: measured over 20
        identical runs in one process this input returned MEMORY_FAULT 19 times and
        PASS once. R0 was 0 in all 20.

        The instability is itself a finding, recorded on #23: run() classifies by
        three flags (instruction limit, unsupported instruction, unexpected
        syscall) and falls through to PASS with exit 0 when none is set, so a run
        that stops for any other reason is indistinguishable from a successful one.
        """
        result = _run(THUMB_MOV_R0_1, "arm", tmp_path)

        assert result.final_registers.get("R0") != 1, (
            "Thumb blob decoded as ARM produced the Thumb-mode result; the probe "
            "encoding is wrong"
        )
        assert result.instructions_executed != 1, (
            f"expected mis-decoding to run more than one instruction, got "
            f"{result.instructions_executed}"
        )

    def test_arm_blob_in_thumb_mode_does_not_produce_correct_result(self, tmp_path):
        """A 4-byte ARM blob decoded as Thumb must not yield the ARM result.

        The first two bytes E3 A0 decode as unrelated Thumb instructions, so R0
        is never set to 1.
        """
        result = _run(ARM_MOV_R0_1, "thumb", tmp_path)

        assert result.final_registers.get("R0") != 1, (
            "ARM blob decoded as Thumb produced the ARM-mode result; the probe "
            "encodings are wrong"
        )

    def test_arm_blob_in_thumb_mode_reports_anomalous_instruction_count(self, tmp_path):
        """Mis-decoding is detectable from instruction count alone.

        One ARM instruction becomes several Thumb ones, and execution runs to the
        region end rather than stopping after one. The count is the only signal
        that survives when the result is reported as PASS.
        """
        correct = _run(ARM_MOV_R0_1, "arm", tmp_path)
        misdecoded = _run(ARM_MOV_R0_1, "thumb", tmp_path)

        assert misdecoded.instructions_executed > correct.instructions_executed * 10, (
            f"expected mis-decoding to inflate the instruction count, got "
            f"{misdecoded.instructions_executed} vs {correct.instructions_executed}"
        )

    def test_mis_decoding_never_looks_like_a_clean_run(self, tmp_path):
        """Neither mis-decoding direction may resemble a correct single step.

        This is the invariant that survives the classification instability: whatever
        the harness reports, a mis-decoded blob never executes exactly one
        instruction and never sets R0 to the value the correctly-decoded blob sets.
        A caller comparing register values therefore still detects the problem; a
        caller checking only exit_code does not, because both directions can report
        PASS with exit 0.
        """
        correct_arm = _run(ARM_MOV_R0_1, "arm", tmp_path)
        correct_thumb = _run(THUMB_MOV_R0_1, "thumb", tmp_path)
        assert (correct_arm.final_registers.get("R0"),
                correct_thumb.final_registers.get("R0")) == (1, 1)

        for blob, wrong_mode in ((ARM_MOV_R0_1, "thumb"), (THUMB_MOV_R0_1, "arm")):
            result = _run(blob, wrong_mode, tmp_path)
            assert result.final_registers.get("R0") != 1, (
                f"{blob.hex()} in {wrong_mode} mode produced the correct result"
            )
            assert result.instructions_executed > 1, (
                f"{blob.hex()} in {wrong_mode} mode ran exactly one instruction; "
                f"expected mis-decoded garbage"
            )


class TestRunCompletionReporting:
    """A run that does not reach the end of its region must not report PASS.

    Unicorn has no UC_ERR_TIMEOUT and emu_start returns normally when its
    timeout expires, so a wall-clock-truncated run sets none of the flags the
    classifier inspects. Before the completion check it fell through to PASS
    with exit 0, making a partial execution indistinguishable from a complete
    one - which is what let assertions pass against firmware state that was
    never actually produced.
    """

    @staticmethod
    def _config(tmp_path, code, mode, entry, timeout_ms, max_instructions=100_000):
        blob = tmp_path / "region.bin"
        blob.write_bytes(code)
        # Size the code region to the blob rather than a fixed 0x4000: the
        # truncation case uses an 800 KB blob, and a stack placed at a fixed
        # offset would land inside it.
        code_size = (len(code) + 0xFFF) & ~0xFFF
        return EmulationConfig(
            binary_path=blob,
            binary_sha256=hashlib.sha256(code).hexdigest(),
            patcher_commit="test-commit",
            harness_revision="0.1.0",
            emulator_version="unicorn-2.1.4",
            fixture_stub_profile="default",
            command="completion_test",
            mode=mode,
            entry_address=entry,
            code_address=entry,
            code_size=code_size,
            stack_address=entry + code_size,
            stack_size=0x4000,
            timeout_ms=timeout_ms,
            max_instructions=max_instructions,
        )

    def test_complete_run_reaches_until_and_passes(self, tmp_path):
        """A run that reaches the end of the region still reports PASS."""
        code = bytes.fromhex("0100a0e3")  # MOV R0,#1
        result = ARMEmulator(
            self._config(tmp_path, code, "arm", 0x1000, 5000)
        ).run()

        assert result.result == EmulationResult.PASS
        assert result.final_registers["PC"] == 0x1000 + len(code)

    def test_truncated_run_is_timeout_not_pass(self, tmp_path):
        """A run cut short by the wall clock must report TIMEOUT.

        200k ARM NOPs cannot finish inside a 1 ms budget. The point of the
        assertion is that this is reported as TIMEOUT rather than PASS: the
        engine returns from emu_start without raising, so nothing else in the
        classifier distinguishes it.
        """
        # 50k NOPs takes ~2.5 ms to emulate, so a 1 ms budget cuts it short,
        # while staying under the default max_instructions of 100000 so the
        # instruction cap cannot be what stops the run.
        code = (0xE1A00000).to_bytes(4, "little") * 50_000  # MOV R0,R0 x N
        result = ARMEmulator(
            self._config(tmp_path, code, "arm", 0x1000, 1)
        ).run()

        assert result.result == EmulationResult.TIMEOUT, (
            f"A truncated run must not report PASS; got {result.result}: "
            f"{result.error_message}"
        )
        assert result.exit_code != 0, "a truncated run must not exit 0"
        assert result.instructions_executed > 0, (
            "the run should have made progress before being cut short"
        )
        assert "did not reach the end" in (result.error_message or "")

    def test_timeout_budget_is_milliseconds(self, tmp_path):
        """timeout_ms must mean milliseconds, not microseconds.

        emu_start's timeout is microseconds. Passing timeout_ms raw made a
        5000 ms budget behave as 5 ms, silently truncating real firmware runs:
        the capture_bulb fixture executed 120 of its 154 instructions and still
        reported PASS.

        200k NOPs needs roughly 10 ms, so it cannot finish inside a 5 ms budget
        but finishes comfortably inside a real 5000 ms one. max_instructions is
        raised above the instruction count so the cap cannot be what stops the
        run - only the timeout can.
        """
        code = (0xE1A00000).to_bytes(4, "little") * 200_000
        result = ARMEmulator(
            self._config(tmp_path, code, "arm", 0x1000, 5000,
                         max_instructions=1_000_000)
        ).run()

        assert result.result == EmulationResult.PASS, (
            f"a 5000 ms budget must complete a run that takes ~10 ms; got "
            f"{result.result}: {result.error_message}"
        )


class TestSkipSemanticsDurability:
    """SKIP must survive fixture damage rather than degrading into FAIL or PASS."""

    def _fixture_dir(self, tmp_path: Path, sidecar: dict | None) -> Path:
        """Create a fixture binary with an optional sidecar."""
        (tmp_path / "region.bin").write_bytes(b"\x00" * 16)
        if sidecar is not None:
            (tmp_path / "region.json").write_text(json.dumps(sidecar))
        return tmp_path

    def test_corrupt_sidecar_still_yields_a_fixture_object(self, tmp_path):
        """A sidecar that cannot be parsed must not make the fixture vanish.

        The binary exists, so callers should still see a fixture and report it as
        unusable for running. Raising here would surface a metadata problem as a
        crash rather than a skip.
        """
        self._fixture_dir(tmp_path, None)
        (tmp_path / "region.json").write_text("{ truncated")

        manager = FixtureManager(tmp_path)
        fixture = manager.get_fixture("region")

        assert fixture is not None
        assert fixture.mode is None
        available, reason = manager.source_binary_available("region")
        assert available is True, "no seeding declared, so nothing is missing"

    def test_sidecar_with_wrong_types_is_usable_but_unrunnable(self, tmp_path):
        """Non-dict provenance must not crash discovery.

        A sidecar whose provenance is a list yields a fixture with no execution
        parameters. Callers then report it as unusable rather than crashing on a
        type error mid-run.
        """
        self._fixture_dir(tmp_path, {"provenance": ["not", "a", "dict"]})

        manager = FixtureManager(tmp_path)
        fixture = manager.get_fixture("region")

        assert fixture is not None
        assert fixture.provenance == ["not", "a", "dict"]
        assert fixture.mode is None
        assert fixture.entry_address is None

    def test_renamed_sidecar_keeps_binary_discoverable(self, tmp_path):
        """Renaming a sidecar leaves the binary discoverable with no metadata.

        The fixture becomes unrunnable for want of an entry address, which is a
        skip condition. It must not disappear from the listing, because "fixture
        present but unusable" and "fixture absent" are reported differently.
        """
        self._fixture_dir(tmp_path, {"provenance": {"mode": "arm",
                                                    "entry_address": "0x1000"}})
        (tmp_path / "region.json").rename(tmp_path / "orphan.json")

        manager = FixtureManager(tmp_path)
        assert manager.get_fixture("region") is not None
        assert manager.get_fixture("region").mode is None

    def test_missing_fixture_is_reported_as_absent_not_usable(self, tmp_path):
        """An absent fixture must never read as available.

        If this returned True, a caller would attempt to run nothing and report a
        failure as though the artifact misbehaved.
        """
        manager = FixtureManager(tmp_path / "empty")

        assert manager.get_fixture("absent") is None
        available, reason = manager.source_binary_available("absent")
        assert available is False
        assert "not found" in reason

    def test_fixture_needing_source_reports_reason_not_exception(self, tmp_path,
                                                                   monkeypatch):
        """A fixture that cannot seed must explain why, for the skip message.

        The reason is what distinguishes SKIP from FAIL in a report: an operator
        needs to know which artifact to provide.
        """
        monkeypatch.delenv("POLARIS_FIRMWARE_ROOT", raising=False)
        self._fixture_dir(tmp_path, {
            "provenance": {
                "mode": "arm",
                "memory_patches": [{
                    "address": "0x1000", "size": "0x10", "source_offset": "0x0",
                }],
            }
        })

        available, reason = FixtureManager(tmp_path).source_binary_available("region")
        assert available is False
        assert "POLARIS_FIRMWARE_ROOT" in reason
