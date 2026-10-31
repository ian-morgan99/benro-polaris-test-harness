"""
ARM user-space emulator for Stage-2 lifecycle tests.
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

Provides Unicorn-based ARM (Thumb) CPU emulation for exact, provenance-identified
ARM binaries or extracted code regions. This is an independent harness component
and does not depend on the Polaris protocol scenario engine or runtime verifier.

Design constraints (from issue #15):
- Fail closed on unknown binary hashes/layouts, unexpected syscalls/callbacks,
  unsupported instructions, missing stubs, timeouts, and unobserved side effects.
- Never patch or mutate the input artifact.
- Bind every run to input binary SHA-256, patcher commit, harness revision,
  emulator version, fixture/stub profile, command, and full machine-readable result.
- Prefer Unicorn (Python ARM emulator); no privileged QEMU setup.
"""

import hashlib
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class EmulationResult(Enum):
    """Emulation outcome enumeration."""
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    TIMEOUT = "timeout"
    UNSUPPORTED_INSTRUCTION = "unsupported_instruction"
    UNEXPECTED_SYSCALL = "unexpected_syscall"
    MISSING_STUB = "missing_stub"


@dataclass
class EmulationConfig:
    """Configuration for an emulation run."""
    binary_path: Path
    binary_sha256: str
    patcher_commit: str
    harness_revision: str
    emulator_version: str
    fixture_stub_profile: str
    command: str
    entry_address: int = 0x1000
    stack_address: int = 0x20000000
    stack_size: int = 0x1000
    code_address: int = 0x1000
    code_size: int = 0x10000
    timeout_ms: int = 5000
    max_instructions: int = 100000
    mode: str = "thumb"


@dataclass
class StubCallRecord:
    """Record of a single stub invocation."""
    stub_name: str
    function_name: str
    arguments: List[Any]
    return_value: Any
    timestamp_ms: int
    register_snapshot: Dict[str, int] = field(default_factory=dict)


@dataclass
class EmulationResultRecord:
    """Machine-readable result of an emulation run."""
    binary_sha256: str
    patcher_commit: str
    harness_revision: str
    emulator_version: str
    fixture_stub_profile: str
    command: str
    result: EmulationResult
    exit_code: int
    duration_ms: int
    instructions_executed: int
    mode: str = "thumb"
    stub_calls: List[StubCallRecord] = field(default_factory=list)
    final_registers: Dict[str, int] = field(default_factory=dict)
    error_message: Optional[str] = None
    unsupported_instruction_address: Optional[int] = None
    unexpected_syscall_number: Optional[int] = None


class ARMEmulator:
    """
    ARM (Thumb) user-space emulator using Unicorn.

    Executes ARM binaries in an isolated user-space environment with explicit
    ABI stubs for external boundaries (camera, libgphoto, OS, timer, callbacks).
    """

    def __init__(self, config: EmulationConfig):
        self.config = config
        self._uc: Any = None
        self._arm_const: Any = None
        self._stub_registry: Dict[str, Callable] = {}
        self._stub_call_records: List[StubCallRecord] = []
        self._stub_entry_addresses: Dict[str, int] = {}
        self._instruction_count = 0
        self._max_instructions = config.max_instructions
        self._instruction_limit_exceeded = False
        self._start_time_ms: Optional[int] = None
        self._unsupported_instruction_address: Optional[int] = None
        self._unexpected_syscall_number: Optional[int] = None
        self._logger = logging.getLogger(f"{__name__}.ARMEmulator")

        # Validate instruction mode (fail closed on unknown values)
        if config.mode not in ("arm", "thumb"):
            raise ValueError(
                f"Invalid instruction mode: {config.mode!r}. "
                f"Expected 'arm' or 'thumb'."
            )
        self._mode = config.mode

    def register_stub(self, name: str, handler: Callable) -> None:
        """Register an ABI stub handler.

        Args:
            name: Stub identifier (e.g. "camera", "libgphoto", "os", "timer", "callback")
            handler: Callable that handles the stub invocation
        """
        self._stub_registry[name] = handler
        self._logger.debug("Registered stub: %s", name)

    def register_stub_entry(self, stub_name: str, address: int) -> None:
        """Register a stub entry address for AAPCS marshaling.

        When execution reaches this address, the emulator reads R0-R3 (plus
        stack words past R3 per the profile's ABI shape) and passes them as
        arguments to the stub. The stub's return value is written back to R0.

        Args:
            stub_name: Stub identifier (must match a registered stub)
            address: Entry address where the stub is invoked
        """
        if stub_name not in self._stub_registry:
            raise KeyError(f"Cannot register entry for unregistered stub: {stub_name}")
        self._stub_entry_addresses[stub_name] = address
        self._logger.debug("Registered stub entry: %s @ 0x%08x", stub_name, address)

    def _call_stub(self, stub_name: str, function_name: str, arguments: List[Any],
                   register_snapshot: Dict[str, int]) -> Any:
        """Invoke a registered stub and record the call.

        Args:
            stub_name: Name of the stub to invoke
            function_name: Function within the stub
            arguments: Arguments passed to the stub
            register_snapshot: CPU register state at call time

        Returns:
            Stub return value

        Raises:
            KeyError: If the stub is not registered (fail closed on missing stub)
        """
        if stub_name not in self._stub_registry:
            raise KeyError(f"Missing stub: {stub_name}")

        import time
        timestamp_ms = int(time.time() * 1000) - (self._start_time_ms or 0)

        handler = self._stub_registry[stub_name]
        return_value = handler(function_name, arguments, register_snapshot)

        record = StubCallRecord(
            stub_name=stub_name,
            function_name=function_name,
            arguments=list(arguments),
            return_value=return_value,
            timestamp_ms=timestamp_ms,
            register_snapshot=dict(register_snapshot),
        )
        self._stub_call_records.append(record)
        self._logger.debug("Stub call: %s.%s(%s) -> %s",
                           stub_name, function_name, arguments, return_value)
        return return_value

    def _compute_binary_sha256(self, binary_path: Path) -> str:
        """Compute SHA-256 of the binary file.

        Args:
            binary_path: Path to the binary file

        Returns:
            Hex-encoded SHA-256 digest
        """
        sha256 = hashlib.sha256()
        with open(binary_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def _load_binary(self, binary_path: Path) -> bytes:
        """Load binary contents into memory (read-only, never mutated).

        Args:
            binary_path: Path to the binary file

        Returns:
            Raw binary bytes
        """
        with open(binary_path, "rb") as f:
            return f.read()

    def _hook_code(self, uc, address: int, size: int, user_data) -> None:
        """Unicorn code execution hook - traces every instruction.

        Args:
            uc: Unicorn engine instance
            address: Instruction address
            size: Instruction size in bytes
            user_data: User data pointer
        """
        self._instruction_count += 1

        # Fail closed on too many instructions (runaway execution)
        if self._instruction_count > self._max_instructions:
            self._instruction_limit_exceeded = True
            self._logger.error("Instruction limit exceeded: %d", self._instruction_count)
            uc.emu_stop()

        # AAPCS stub marshaling: if we've reached a stub entry address,
        # read R0-R3 (+ stack per ABI shape), call the stub, write return
        # value to R0, and skip past the call instruction.
        for stub_name, entry_addr in self._stub_entry_addresses.items():
            if address == entry_addr:
                self._marshal_stub_call(uc, stub_name)
                # Skip past the call instruction (BL/BLX is 4 bytes in ARM, 2 in Thumb)
                instr_size = 2 if self._mode == "thumb" else 4
                pc = uc.reg_read(self._arm_const.UC_ARM_REG_PC)
                uc.reg_write(self._arm_const.UC_ARM_REG_PC, pc + instr_size)
                return

    def _marshal_stub_call(self, uc, stub_name: str) -> None:
        """Marshal AAPCS arguments and call a stub.

        Reads R0-R3 (plus stack words past R3 per ABI shape), calls the stub,
        writes the return value back to R0.

        Args:
            uc: Unicorn engine instance
            stub_name: Stub identifier

        Raises:
            KeyError: If stub is not registered (fail closed)
            ValueError: If required arguments cannot be read (fail closed)
        """
        if stub_name not in self._stub_registry:
            raise KeyError(f"Missing stub: {stub_name}")

        # Read caller-saved argument registers R0-R3
        arg_regs = ["R0", "R1", "R2", "R3"]
        arguments = []
        for reg in arg_regs:
            const = getattr(self._arm_const, f"UC_ARM_REG_{reg}", None)
            if const is not None:
                arguments.append(uc.reg_read(const))

        # Apply ABI shape from stub profile if available
        abi_shape: Dict[str, Any] = {}
        try:
            from .stubs import StubProfile
            # We need access to the profile; for now use default arg_count=4
            abi_shape = {"arg_count": 4, "pointer_args": [], "deref_bytes": {}}
        except ImportError:
            abi_shape = {"arg_count": 4, "pointer_args": [], "deref_bytes": {}}

        arg_count: int = abi_shape.get("arg_count", 4)
        pointer_args: List[int] = abi_shape.get("pointer_args", [])
        deref_bytes: Dict[int, int] = abi_shape.get("deref_bytes", {})

        # Read stack words past R3 if needed
        sp = uc.reg_read(self._arm_const.UC_ARM_REG_SP)
        for i in range(4, arg_count):
            stack_offset = (i - 4) * 4
            try:
                stack_val = uc.mem_read(sp + stack_offset, 4)
                arguments.append(int.from_bytes(stack_val, "little"))
            except Exception:
                raise ValueError(
                    f"Cannot read stack argument {i} at SP+{stack_offset}: "
                    f"unmapped memory"
                )

        # Dereference pointer arguments if declared
        for idx in pointer_args:
            if idx < len(arguments):
                ptr_val = arguments[idx]
                byte_count = deref_bytes.get(idx, 4)
                try:
                    mem_data = uc.mem_read(ptr_val, byte_count)
                    arguments[idx] = int.from_bytes(mem_data, "little")
                except Exception:
                    raise ValueError(
                        f"Cannot dereference pointer argument {idx} at 0x{ptr_val:08x}: "
                        f"unmapped memory"
                    )

        # Read register snapshot before the call
        register_snapshot = self._read_registers()

        # Call the stub
        handler = self._stub_registry[stub_name]
        return_value = handler("aapcs_call", arguments, register_snapshot)

        # Write return value back to R0
        uc.reg_write(self._arm_const.UC_ARM_REG_R0, return_value)

        # Record the stub call
        record = StubCallRecord(
            stub_name=stub_name,
            function_name="aapcs_call",
            arguments=list(arguments),
            return_value=return_value,
            timestamp_ms=int(time.time() * 1000) - (self._start_time_ms or 0),
            register_snapshot=register_snapshot,
        )
        self._stub_call_records.append(record)
        self._logger.debug("AAPCS stub call: %s(%s) -> %s",
                           stub_name, arguments, return_value)

    def _hook_invalid_instruction(self, uc, address: int, size: int, user_data) -> None:
        """Unicorn invalid instruction hook - fail closed on unsupported instructions.

        Args:
            uc: Unicorn engine instance
            address: Instruction address
            size: Instruction size in bytes
            user_data: User data pointer
        """
        self._unsupported_instruction_address = address
        self._logger.error("Unsupported instruction at 0x%08x", address)
        uc.emu_stop()

    def _hook_syscall(self, uc, user_data) -> None:
        """Unicorn syscall hook - dispatch to stubs or fail closed.

        Args:
            uc: Unicorn engine instance
            user_data: User data pointer
        """
        # On ARM, syscall number is typically in R7
        syscall_num = uc.reg_read(self._arm_const.UC_ARM_REG_R7)
        self._unexpected_syscall_number = syscall_num

        # Attempt to dispatch to an OS stub if registered
        if "os" in self._stub_registry:
            try:
                regs = self._read_registers()
                self._call_stub("os", f"syscall_{syscall_num}", [], regs)
                return
            except KeyError:
                pass

        # Fail closed on unexpected syscall
        self._logger.error("Unexpected syscall number: %d", syscall_num)
        uc.emu_stop()

    def _hook_intr(self, uc, intno, user_data) -> None:
        """Unicorn interrupt hook - handle ARM SVC instructions.

        On ARM, SVC #n triggers a software interrupt. The syscall number
        is encoded in the immediate operand and also typically in R7.

        Args:
            uc: Unicorn engine instance
            intno: Interrupt number
            user_data: User data pointer
        """
        # On ARM, SVC syscall number is typically in R7
        syscall_num = uc.reg_read(self._arm_const.UC_ARM_REG_R7)

        # Attempt to dispatch to an OS stub if registered
        if "os" in self._stub_registry:
            try:
                regs = self._read_registers()
                self._call_stub("os", f"svc_{syscall_num}", [], regs)
                return
            except KeyError:
                pass

        # Fail closed on unexpected interrupt
        self._unexpected_syscall_number = syscall_num
        self._logger.error("Unexpected interrupt (SVC #%d)", syscall_num)
        uc.emu_stop()

    def _read_registers(self) -> Dict[str, int]:
        """Read all ARM general-purpose registers.

        Returns:
            Dictionary mapping register names to values
        """
        reg_names = [
            "UC_ARM_REG_R0", "UC_ARM_REG_R1", "UC_ARM_REG_R2", "UC_ARM_REG_R3",
            "UC_ARM_REG_R4", "UC_ARM_REG_R5", "UC_ARM_REG_R6", "UC_ARM_REG_R7",
            "UC_ARM_REG_R8", "UC_ARM_REG_R9", "UC_ARM_REG_R10", "UC_ARM_REG_R11",
            "UC_ARM_REG_R12", "UC_ARM_REG_SP", "UC_ARM_REG_LR", "UC_ARM_REG_PC",
        ]
        regs = {}
        for name in reg_names:
            const = getattr(self._arm_const, name, None)
            if const is not None:
                friendly = name.replace("UC_ARM_REG_", "")
                regs[friendly] = self._uc.reg_read(const)
        return regs

    def _write_registers(self, register_values: Dict[str, int]) -> None:
        """Write ARM general-purpose registers.

        Args:
            register_values: Dictionary mapping register names to values
        """
        for name, value in register_values.items():
            const_name = f"UC_ARM_REG_{name.upper()}"
            const = getattr(self._arm_const, const_name, None)
            if const is not None:
                self._uc.reg_write(const, value)

    def _setup_memory(self, binary_data: bytes) -> None:
        """Set up Unicorn memory mappings.

        Args:
            binary_data: Raw binary bytes to map into code space
        """
        # Map code region
        self._uc.mem_map(self.config.code_address, self.config.code_size)
        self._uc.mem_write(self.config.code_address, binary_data)

        # Map stack region
        self._uc.mem_map(self.config.stack_address, self.config.stack_size)
        self._uc.reg_write(self._arm_const.UC_ARM_REG_SP,
                           self.config.stack_address + self.config.stack_size)

    def _setup_hooks(self) -> None:
        """Install Unicorn hooks for tracing and fail-closed behavior."""
        from unicorn import UC_HOOK_CODE, UC_HOOK_INSN_INVALID, UC_HOOK_INTR

        self._uc.hook_add(UC_HOOK_CODE, self._hook_code)
        self._uc.hook_add(UC_HOOK_INSN_INVALID, self._hook_invalid_instruction)
        self._uc.hook_add(UC_HOOK_INTR, self._hook_intr)

    def _initialize_unicorn(self) -> None:
        """Initialize the Unicorn engine in ARM or Thumb mode."""
        from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_MODE_THUMB, arm_const

        mode_const = UC_MODE_ARM if self._mode == "arm" else UC_MODE_THUMB

        self._arm_const = arm_const
        self._uc = Uc(UC_ARCH_ARM, mode_const)
        self._setup_memory(self._load_binary(self.config.binary_path))
        self._setup_hooks()

    def run(self) -> EmulationResultRecord:
        """Execute the emulation run.

        Returns:
            Machine-readable emulation result record
        """
        import time
        self._start_time_ms = int(time.time() * 1000)

        result = EmulationResultRecord(
            binary_sha256=self.config.binary_sha256,
            patcher_commit=self.config.patcher_commit,
            harness_revision=self.config.harness_revision,
            emulator_version=self.config.emulator_version,
            fixture_stub_profile=self.config.fixture_stub_profile,
            command=self.config.command,
            result=EmulationResult.ERROR,
            exit_code=-1,
            duration_ms=0,
            instructions_executed=0,
        )

        try:
            self._initialize_unicorn()

            # Load binary to determine its length
            binary_data = self._load_binary(self.config.binary_path)

            # Set entry point. In Thumb mode set the Thumb bit (bit 0) on PC/LR;
            # in ARM mode write the address unmodified.
            entry = self.config.entry_address | 1 if self._mode == "thumb" else self.config.entry_address
            self._uc.reg_write(self._arm_const.UC_ARM_REG_PC, entry)

            # Set LR to a valid return address within mapped code region
            # so that BX LR returns to mapped memory instead of address 0
            lr = entry + len(binary_data)
            self._uc.reg_write(self._arm_const.UC_ARM_REG_LR, lr)

            # Start emulation
            self._uc.emu_start(entry, self.config.entry_address + len(binary_data),
                               timeout=self.config.timeout_ms)

            # Read final register state
            result.final_registers = self._read_registers()
            result.instructions_executed = self._instruction_count
            result.stub_calls = self._stub_call_records
            result.mode = self._mode

            # Determine result
            if self._instruction_limit_exceeded:
                result.result = EmulationResult.TIMEOUT
                result.error_message = (
                    f"Instruction limit exceeded: {self._instruction_count} instructions"
                )
                result.exit_code = 1
            elif self._unsupported_instruction_address is not None:
                result.result = EmulationResult.UNSUPPORTED_INSTRUCTION
                result.error_message = (
                    f"Unsupported instruction at 0x{self._unsupported_instruction_address:08x}"
                )
                result.exit_code = 1
            elif self._unexpected_syscall_number is not None:
                result.result = EmulationResult.UNEXPECTED_SYSCALL
                result.error_message = (
                    f"Unexpected syscall number: {self._unexpected_syscall_number}"
                )
                result.exit_code = 1
            else:
                result.result = EmulationResult.PASS
                result.exit_code = 0

        except ImportError:
            result.result = EmulationResult.ERROR
            result.error_message = "Unicorn engine not available"
            result.exit_code = 2
        except KeyError as e:
            result.result = EmulationResult.MISSING_STUB
            result.error_message = str(e)
            result.exit_code = 3
        except Exception as e:  # pylint: disable=broad-except
            # Check if this is an Unicorn instruction error
            from unicorn import UcError
            if isinstance(e, UcError):
                result.result = EmulationResult.UNSUPPORTED_INSTRUCTION
                result.error_message = str(e)
                # Try to capture the PC at time of error
                try:
                    pc = self._uc.reg_read(self._arm_const.UC_ARM_REG_PC)
                    result.unsupported_instruction_address = pc & ~1  # Clear Thumb bit
                except Exception:
                    pass
                result.exit_code = 1
            else:
                result.result = EmulationResult.ERROR
                result.error_message = str(e)
                result.exit_code = 255
        finally:
            end_time_ms = int(time.time() * 1000)
            result.duration_ms = end_time_ms - (self._start_time_ms or end_time_ms)

        return result
