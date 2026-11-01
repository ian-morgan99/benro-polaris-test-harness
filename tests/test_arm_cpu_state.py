"""Tests for CPU-state initialisation and fault classification.

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

Two related defects that together caused a misdiagnosis:

1. Unicorn leaves FPEXC.EN cleared, so it rejects every VFP encoding with
   UC_ERR_INSN_INVALID. A real ARM Linux process has the FPU enabled at entry,
   so this was uninitialised harness state, not an engine limit.

2. Every UcError was reported as result=unsupported_instruction. A memory fault
   therefore looked like a decoding failure, which is what made the VFP fault
   read as "the engine cannot do VFP" instead of "this instruction was never
   decoded because the FPU was off".
"""

from pathlib import Path

from polaris_harness.arm_emulator.emulator import (
    ARMEmulator,
    EmulationConfig,
    EmulationResult,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "arm"


def _config(tmp_path: Path, code: bytes, **overrides) -> EmulationConfig:
    """Build a minimal config executing ``code`` at 0x1000.

    Args:
        tmp_path: pytest temporary directory for the binary
        code: instructions to execute
        overrides: EmulationConfig field overrides

    Returns:
        EmulationConfig ready for ARMEmulator
    """
    blob = tmp_path / "probe.bin"
    blob.write_bytes(code)
    defaults = dict(
        binary_path=blob,
        binary_sha256="0" * 64,
        patcher_commit="test",
        harness_revision="0.1.0",
        emulator_version="test",
        fixture_stub_profile="lifecycle",
        command="probe",
        entry_address=0x1000,
        code_address=0x1000,
        code_size=0x4000,
        stack_address=0x200000,
        stack_size=0x4000,
        mode="arm",
    )
    defaults.update(overrides)
    return EmulationConfig(**defaults)


VMOV_I32_Q8_0 = bytes.fromhex("5000c0f2")   # vmov.i32 q8, #0
UDF = bytes.fromhex("f000f0e7")             # udf #0 (undefined instruction)
LDR_R1_FROM_R1 = bytes.fromhex("001091e5")  # ldr r1,[r1] with r1 == 0


class TestFpuEnablement:
    """The emulator must start with the VFP coprocessor enabled."""

    def test_vfp_executes_by_default(self, tmp_path):
        """A VFP instruction must run without being reported as unsupported.

        This is the regression that was recorded as a "hard engine limit".
        """
        result = ARMEmulator(_config(tmp_path, VMOV_I32_Q8_0)).run()

        assert result.result != EmulationResult.UNSUPPORTED_INSTRUCTION, (
            f"VFP rejected: {result.error_message}"
        )

    def test_vfp_write_has_correct_semantics(self, tmp_path):
        """vmov.i32 q8,#0 must actually zero Q8, not merely avoid faulting.

        Accepting the encoding but computing nothing would pass the previous
        assertion while being wrong. Q8 is seeded with a nonzero pattern first,
        because reading back an untouched default of 0 would prove nothing.
        """
        seed = 0xDEADBEEFDEADBEEF

        class Seeded(ARMEmulator):
            def _initialize_unicorn(self):
                super()._initialize_unicorn()
                self._uc.reg_write(self._arm_const.UC_ARM_REG_Q8, seed)

        emulator = Seeded(_config(tmp_path, VMOV_I32_Q8_0))
        result = emulator.run()

        assert result.result == EmulationResult.PASS, (
            f"run did not complete: {result.error_message}"
        )
        q8 = emulator._uc.reg_read(emulator._arm_const.UC_ARM_REG_Q8)
        assert q8 == 0, f"vmov.i32 q8,#0 left Q8={q8:#x} (seed was {seed:#x})"

    def test_disable_fpu_reproduces_coprocessor_off(self, tmp_path):
        """enable_fpu=False must reproduce UC_ERR_INSN_INVALID.

        Proves the default is doing the work and that the option is a real
        switch for studying coprocessor-disabled behaviour.
        """
        result = ARMEmulator(
            _config(tmp_path, VMOV_I32_Q8_0, enable_fpu=False)
        ).run()

        assert result.result == EmulationResult.UNSUPPORTED_INSTRUCTION, (
            "expected the coprocessor-off fault to be reproducible"
        )


class TestFaultClassification:
    """Memory faults must not be reported as unsupported instructions."""

    def test_unmapped_read_is_memory_fault(self, tmp_path):
        """A load from unmapped memory is an environment gap, not a bad opcode.

        Conflating the two is what made an uninitialised FPU look like an engine
        limit: the run reported "unsupported_instruction" for an instruction that
        was never decoded only because the coprocessor was off.
        """
        # ldr r1,[r1] with r1 left at 0 by the harness
        code = LDR_R1_FROM_R1 + UDF
        result = ARMEmulator(_config(tmp_path, code)).run()

        assert result.result == EmulationResult.MEMORY_FAULT, (
            f"unmapped read reported as {result.result}: {result.error_message}"
        )

    def test_undefined_opcode_stays_unsupported_instruction(self, tmp_path):
        """A genuinely undecodable instruction must keep its old classification.

        Without this, broadening the classifier would silently reclassify real
        decoding failures as environment gaps.
        """
        result = ARMEmulator(_config(tmp_path, UDF)).run()

        assert result.result == EmulationResult.UNSUPPORTED_INSTRUCTION, (
            f"udf reported as {result.result}"
        )
        assert result.unsupported_instruction_address is not None

    def test_memory_fault_records_pc(self, tmp_path):
        """A memory fault must still record where it happened.

        The fault address is what makes the gap actionable; dropping it while
        reclassifying would lose the diagnostic.
        """
        code = LDR_R1_FROM_R1 + UDF
        result = ARMEmulator(_config(tmp_path, code)).run()

        assert result.unsupported_instruction_address is not None
        assert result.error_message

    def test_memory_fault_records_accessed_address(self, tmp_path):
        """A memory fault must name the address it was looking for.

        Reporting only the PC leaves "Invalid memory read" unidentifiable: the
        reader has to re-run under a hook to learn which structure was missing.
        ldr r1,[r1] with r1 == 0 must record the accessed address as 0.
        """
        code = LDR_R1_FROM_R1 + UDF
        result = ARMEmulator(_config(tmp_path, code)).run()

        assert result.fault_access_address == 0, (
            f"expected the dereferenced address, got "
            f"{result.fault_access_address!r}"
        )

    def test_memory_fault_records_readable_access_type(self, tmp_path):
        """The access reason must be a readable label, not a raw enum value.

        Unicorn passes the specific reason (UC_MEM_READ_UNMAPPED) rather than the
        generic UC_MEM_READ, so an unmapped read should say so.
        """
        code = LDR_R1_FROM_R1 + UDF
        result = ARMEmulator(_config(tmp_path, code)).run()

        assert result.fault_access_type == "read_unmapped", (
            f"expected a readable reason, got {result.fault_access_type!r}"
        )
        assert f"0x{result.fault_access_address:08x}" in result.error_message, (
            "the error message should name the missing address without a hook"
        )

    def test_passing_run_records_no_fault(self, tmp_path):
        """A clean run must not report fault fields.

        Without this, a stale value from a previous run could be read as a real
        gap on a passing result.
        """
        result = ARMEmulator(_config(tmp_path, VMOV_I32_Q8_0)).run()

        assert result.result == EmulationResult.PASS
        assert result.fault_access_address is None
        assert result.fault_access_type is None
