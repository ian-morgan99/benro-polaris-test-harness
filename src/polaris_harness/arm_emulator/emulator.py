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
import struct
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Unicorn's mem_map requires page alignment.
PAGE_SIZE = 0x1000


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
class DataRegion:
    """A mapped memory region seeded with real bytes before the run.

    Extracted code regions resolve globals through PC-relative literal pools, so
    they expect the data those pointers refer to to exist. Leaving such ranges
    zero-filled makes a `ldr rN,[rA,rB]` + `ldr rN,[rN]` chain dereference 0 and
    fault immediately, which looks like missing behaviour but is only a missing
    environment.

    Attributes:
        address: Page-aligned start address to map
        size: Page-aligned size to map
        source_path: File to copy bytes from (e.g. the retained source ELF)
        source_offset: Offset within that file to start copying
        dest_offset: Offset within the mapped region to write at
        fill: Byte used to fill the region before any source bytes are applied
    """

    address: int
    size: int
    source_path: Optional[Path] = None
    source_offset: int = 0
    dest_offset: int = 0
    fill: int = 0

    def validate(self) -> None:
        """Fail closed on an unusable region description."""
        if self.address % PAGE_SIZE != 0:
            raise ValueError(
                f"DataRegion address {hex(self.address)} is not "
                f"{PAGE_SIZE}-byte page aligned; Unicorn cannot map it."
            )
        if self.size <= 0 or self.size % PAGE_SIZE != 0:
            raise ValueError(
                f"DataRegion size {hex(self.size)} must be a positive multiple "
                f"of {PAGE_SIZE}."
            )
        if self.dest_offset < 0 or self.dest_offset >= self.size:
            raise ValueError(
                f"DataRegion dest_offset {self.dest_offset} is outside the "
                f"{self.size}-byte region at {hex(self.address)}."
            )
        if self.source_path is not None and self.source_offset < 0:
            raise ValueError(
                f"DataRegion source_offset {self.source_offset} must be >= 0."
            )


@dataclass
class MemoryPatch:
    """Bytes written into an already-mapped range before the run starts.

    Differs from DataRegion, which maps a new range. A region extracted from an
    ELF often has its code mapping already cover the globals it dereferences -
    they are simply zero-filled because they were not part of the extracted
    window. Patching those words in place reproduces the values the function was
    linked against without changing the artifact itself.

    Attributes:
        address: Absolute address to write at
        source_path: File to read bytes from (the retained source ELF)
        source_offset: Offset within that file to read from
        size: Number of bytes to copy
    """

    address: int
    source_path: Path
    source_offset: int
    size: int

    def validate(self) -> None:
        """Fail closed on an unusable patch description."""
        if self.size <= 0:
            raise ValueError(
                f"MemoryPatch size {self.size} must be positive."
            )
        if self.source_offset < 0:
            raise ValueError(
                f"MemoryPatch source_offset {self.source_offset} must be >= 0."
            )


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
    # Absolute address at which the artifact belongs in the original process
    # image. When set, the blob is written here instead of at code_address,
    # while code_address/code_size still define the mapped page range.
    #
    # This matters for regions lifted out of an ELF: ARM code commonly resolves
    # globals with `ldr rN, [pc, #imm]` followed by `add rN, pc, rN`, so the
    # computed address depends on where the code actually sits. Loading such a
    # region at a relocated base makes those loads point at unrelated memory and
    # the run faults on its first data access. Set load_address to the symbol's
    # original vaddr and make code_address/code_size cover it.
    load_address: Optional[int] = None
    # Extra mapped regions seeded with real bytes before execution. A region
    # extracted from an ELF dereferences globals that are not part of the
    # extracted window; without these the run faults on its first pointer chase
    # and the boundary call sites beyond it can never be observed.
    data_regions: List[DataRegion] = field(default_factory=list)
    # Bytes copied from the retained source binary into the existing mapping,
    # used to give zero-filled globals their real contents.
    memory_patches: List[MemoryPatch] = field(default_factory=list)
    # Addresses at which to capture a register snapshot during the run. Used to
    # diagnose where a run diverges, e.g. which pointer a faulting load derived
    # its address from.
    watch_addresses: List[int] = field(default_factory=list)
    # AAPCS argument values for the entry function, written to R0-R3 then pushed
    # on the stack in order. A region extracted mid-binary is entered with no
    # caller frame at all, so anything it reads from its arguments is garbage.
    entry_arguments: List[int] = field(default_factory=list)
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
    # Register snapshots taken at config.watch_addresses, in execution order.
    watch_captures: List[Dict[str, Any]] = field(default_factory=list)


class ARMEmulator:
    """
    ARM (Thumb) user-space emulator using Unicorn.

    Executes ARM binaries in an isolated user-space environment with explicit
    ABI stubs for external boundaries (camera, libgphoto, OS, timer, callbacks).

    **Each ARMEmulator instance is a clean, isolated emulation environment.**
    Create a new instance for every test case or use case — do not reuse a
    single emulator across multiple scenarios. All state (CPU registers,
    memory, instruction count, stub call records, hook state) is reset on
    construction and never shared between instances.

    Example:

        from polaris_harness.arm_emulator import ARMEmulator, EmulationConfig

        # Fresh instance for test case 1
        emu1 = ARMEmulator(EmulationConfig(...))
        result1 = emu1.run()

        # Fresh instance for test case 2 — no state leaks from emu1
        emu2 = ARMEmulator(EmulationConfig(...))
        result2 = emu2.run()
    """

    PAGE_SIZE = PAGE_SIZE

    def __init__(self, config: EmulationConfig):
        self.config = config
        self._uc: Any = None
        self._arm_const: Any = None
        self._stub_registry: Dict[str, Callable] = {}
        self._stub_call_records: List[StubCallRecord] = []
        # address -> {"stub_name", "function_name", "abi_shape", "model"}
        # Keyed by address so one stub can expose several distinct entry points
        # (e.g. camera.stop and camera.close) with different identities.
        self._stub_entries: Dict[int, Dict[str, Any]] = {}
        self._stub_abi_shapes: Dict[str, Dict[str, Any]] = {}
        self._instruction_count = 0
        self._max_instructions = config.max_instructions
        self._instruction_limit_exceeded = False
        self._start_time_ms: Optional[int] = None
        self._unsupported_instruction_address: Optional[int] = None
        self._unexpected_syscall_number: Optional[int] = None
        # Register snapshots taken at config.watch_addresses, in execution order.
        self._watch_captures: List[Dict[str, Any]] = []
        self._logger = logging.getLogger(f"{__name__}.ARMEmulator")

        # Validate instruction mode (fail closed on unknown values)
        if config.mode not in ("arm", "thumb"):
            raise ValueError(
                f"Invalid instruction mode: {config.mode!r}. "
                f"Expected 'arm' or 'thumb'."
            )
        self._mode = config.mode

        # Validate the memory layout (fail closed, issue #15: "fail closed on
        # unknown binary hashes/layouts"). Two classes of misconfiguration are
        # rejected here because both produce a *plausible-looking* result rather
        # than an obvious error:
        #
        # 1. Non-page-aligned map addresses. Unicorn's mem_map requires page
        #    alignment, so an absolute symbol address such as 0x101aac cannot be
        #    mapped directly and the run dies at address 0 with zero progress.
        # 2. An entry point outside the written blob. The blob is written at
        #    code_address; if entry_address lies beyond it, execution runs on
        #    zero-filled pages and reports PASS having never touched the real
        #    function. That is the worst possible outcome for a qualification
        #    harness: a green result from an artifact that was never executed.
        for field_name, value in (
            ("code_address", config.code_address),
            ("stack_address", config.stack_address),
        ):
            if value % self.PAGE_SIZE != 0:
                raise ValueError(
                    f"{field_name} {hex(value)} is not {self.PAGE_SIZE}-byte "
                    f"page aligned; Unicorn cannot map it. Load the region at a "
                    f"page-aligned address and set entry_address accordingly."
                )
        for field_name, value in (
            ("code_size", config.code_size),
            ("stack_size", config.stack_size),
        ):
            if value <= 0 or value % self.PAGE_SIZE != 0:
                raise ValueError(
                    f"{field_name} {hex(value)} must be a positive multiple of "
                    f"{self.PAGE_SIZE}."
                )
        if not (
            config.code_address
            <= config.entry_address
            < config.code_address + config.code_size
        ):
            raise ValueError(
                f"entry_address {hex(config.entry_address)} is outside the code "
                f"region [{hex(config.code_address)}, "
                f"{hex(config.code_address + config.code_size)}). Execution would "
                f"begin on unmapped or zero-filled memory and could report PASS "
                f"without running the artifact."
            )

        # The mapped region may be larger than the artifact, and with
        # load_address set the artifact need not start at code_address at all.
        # Entry must still land on real bytes: Unicorn happily decodes zeros, so
        # a run that never touches the artifact can finish cleanly and report
        # PASS.
        try:
            blob_length = self._binary_length(config.binary_path)
        except OSError as exc:
            raise ValueError(
                f"Cannot read binary {config.binary_path}: {exc}"
            ) from exc

        load_address = (
            config.load_address
            if config.load_address is not None
            else config.code_address
        )
        # Validate placement before the entry point: an invalid load address is
        # the more fundamental error, and reporting it first keeps diagnostics
        # actionable.
        load_address = (
            config.load_address
            if config.load_address is not None
            else config.code_address
        )
        if not (
            config.code_address
            <= load_address
            <= config.code_address + config.code_size
        ):
            raise ValueError(
                f"load_address {hex(load_address)} is outside the mapped region "
                f"[{hex(config.code_address)}, "
                f"{hex(config.code_address + config.code_size)})."
            )
        if blob_length > config.code_size:
            raise ValueError(
                f"binary is {blob_length} bytes but code_size is "
                f"{config.code_size}; the artifact would be truncated."
            )
        if load_address + blob_length > config.code_address + config.code_size:
            raise ValueError(
                f"artifact at load_address {hex(load_address)} plus "
                f"{blob_length} bytes exceeds the mapped region ending at "
                f"{hex(config.code_address + config.code_size)}."
            )
        if not (
            load_address <= config.entry_address < load_address + blob_length
        ):
            raise ValueError(
                f"entry_address {hex(config.entry_address)} lies outside the "
                f"{blob_length}-byte artifact loaded at {hex(load_address)} "
                f"(ends at {hex(load_address + blob_length)}). Execution would "
                f"run zero-filled pages and could report PASS without ever "
                f"executing the artifact. Set entry_address to the load address, "
                f"or set load_address to the region's original ELF vaddr."
            )

    def register_stub(self, name: str, handler: Callable) -> None:
        """Register an ABI stub handler.

        Args:
            name: Stub identifier (e.g. "camera", "libgphoto", "os", "timer", "callback")
            handler: Callable that handles the stub invocation
        """
        self._stub_registry[name] = handler
        self._logger.debug("Registered stub: %s", name)

    def apply_stub_profile(self, profile: Any) -> None:
        """Apply a StubProfile: register its handlers and per-boundary ABI shapes.

        The profile's ``abi_shape`` maps stub name -> {"arg_count": N,
        "pointer_args": [indices], "deref_bytes": {index: byte_count}}. Without
        this the marshaling layer cannot read arguments past R3 or follow
        pointer arguments into emulated memory.

        Args:
            profile: A StubProfile instance
        """
        for attr, stub_name in (
            ("camera_stub", "camera"),
            ("libgphoto_stub", "libgphoto"),
            ("os_stub", "os"),
            ("timer_stub", "timer"),
            ("callback_stub", "callback"),
        ):
            handler = getattr(profile, attr, None)
            if handler is not None:
                self.register_stub(stub_name, handler)

        for stub_name, shape in (getattr(profile, "abi_shape", {}) or {}).items():
            self._stub_abi_shapes[stub_name] = dict(shape)
            self._logger.debug("ABI shape for %s: %s", stub_name, shape)

    def set_stub_abi_shape(self, stub_name: str, abi_shape: Dict[str, Any]) -> None:
        """Declare the ABI shape for one stub boundary.

        Args:
            stub_name: Stub identifier
            abi_shape: {"arg_count": int, "pointer_args": [int], "deref_bytes": {int: int}}
        """
        self._stub_abi_shapes[stub_name] = dict(abi_shape)

    def register_stub_entry(self, stub_name: str, address: int,
                            function_name: Optional[str] = None,
                            abi_shape: Optional[Dict[str, Any]] = None,
                            model: str = "call-site") -> None:
        """Register a stub entry address for AAPCS marshaling.

        Two interception models are supported and must be chosen explicitly:

        ``model="call-site"`` (default)
            ``address`` is the **branch instruction that calls the stub** (the
            ``BL``/``BLX`` itself). The emulator dispatches the stub there and
            resumes at the next instruction, which is exactly where AAPCS says
            control returns. Use this when the fixture places a call in the code
            region.

        ``model="target"``
            ``address`` is the **callee** - the PLT-style stub body the branch
            jumps to. This is how real position-independent binaries resolve
            external calls. The emulator dispatches the stub and returns to the
            caller by writing ``LR`` back to ``PC``, honouring ARM/Thumb
            interworking. Registering a target under the old call-site behaviour
            would fall through into the instruction after the stub body.

        Args:
            stub_name: Stub identifier (must match a registered stub)
            address: Address to intercept, per the chosen model
            function_name: Semantic identity recorded for calls at this entry
                (e.g. ``"stop"``, ``"close"``, ``"cancel"``). Defaults to
                ``"aapcs_call"`` for backwards compatibility; lifecycle
                assertions need a real name.
            abi_shape: Per-entry ABI shape, overriding the profile-level shape
            model: ``"call-site"`` or ``"target"``

        Raises:
            KeyError: If the stub is not registered
            ValueError: If ``model`` is unknown (fail closed)
        """
        if stub_name not in self._stub_registry:
            raise KeyError(f"Cannot register entry for unregistered stub: {stub_name}")
        if model not in ("call-site", "target"):
            raise ValueError(
                f"Invalid stub entry model: {model!r}. "
                f"Expected 'call-site' or 'target'."
            )
        self._stub_entries[address] = {
            "stub_name": stub_name,
            "function_name": function_name or "aapcs_call",
            "abi_shape": dict(abi_shape) if abi_shape else None,
            "model": model,
        }
        self._logger.debug("Registered stub entry: %s.%s @ 0x%08x (%s)",
                           stub_name, function_name or "aapcs_call", address, model)

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

    @staticmethod
    def _binary_length(binary_path: Path) -> int:
        """Return the artifact size without reading its contents.

        Used by construction-time layout validation so a bad layout is rejected
        before any emulation state is created.

        Args:
            binary_path: Path to the binary file

        Returns:
            Size of the file in bytes

        Raises:
            OSError: If the file cannot be stat'd
        """
        return Path(binary_path).stat().st_size

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

        # AAPCS stub marshaling: if we've reached a stub entry address, marshal
        # arguments per the declared ABI shape, dispatch the stub, write the
        # return value to R0, and resume according to the entry's model.
        entry = self._stub_entries.get(address)
        if entry is not None:
            self._marshal_stub_call(uc, entry["stub_name"], entry)
            if entry["model"] == "target":
                # The intercepted address is the callee (PLT-style stub body).
                # Return to the caller via LR, preserving bit 0 so ARM/Thumb
                # interworking is honoured.
                lr = uc.reg_read(self._arm_const.UC_ARM_REG_LR)
                uc.reg_write(self._arm_const.UC_ARM_REG_PC, lr)
            else:
                # The intercepted address is the BL/BLX itself: resume at the
                # following instruction, which is where AAPCS says it returns.
                uc.reg_write(self._arm_const.UC_ARM_REG_PC, address + size)
            return

    def _resolve_abi_shape(self, stub_name: str,
                           entry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Resolve the ABI shape for a stub call.

        Precedence: per-entry override, then profile-level shape for the stub,
        then the four-register default. Never silently invents a shape beyond
        the documented AAPCS default.

        Args:
            stub_name: Stub identifier
            entry: Stub entry descriptor, if any

        Returns:
            Resolved ABI shape dictionary
        """
        if entry is not None and entry.get("abi_shape"):
            return dict(entry["abi_shape"])
        profile_shape = self._stub_abi_shapes.get(stub_name)
        if profile_shape:
            return dict(profile_shape)
        return {"arg_count": 4, "pointer_args": [], "deref_bytes": {}}

    def _marshal_stub_call(self, uc, stub_name: str,
                           entry: Optional[Dict[str, Any]] = None) -> None:
        """Marshal AAPCS arguments and call a stub.

        Reads R0-R3 (plus stack words past R3 per ABI shape), dereferences any
        declared pointer arguments, calls the stub with its semantic function
        name, and writes the return value back to R0.

        Args:
            uc: Unicorn engine instance
            stub_name: Stub identifier
            entry: Stub entry descriptor from ``self._stub_entries``

        Raises:
            KeyError: If stub is not registered (fail closed)
            ValueError: If required arguments cannot be read (fail closed)
        """
        if stub_name not in self._stub_registry:
            raise KeyError(f"Missing stub: {stub_name}")

        function_name = entry["function_name"] if entry else "aapcs_call"

        # Read caller-saved argument registers R0-R3
        arg_regs = ["R0", "R1", "R2", "R3"]
        arguments = []
        for reg in arg_regs:
            const = getattr(self._arm_const, f"UC_ARM_REG_{reg}", None)
            if const is not None:
                arguments.append(uc.reg_read(const))

        abi_shape = self._resolve_abi_shape(stub_name, entry)

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

        # Call the stub under its semantic identity so lifecycle assertions can
        # distinguish stop/close/cancel/timer/callback calls.
        handler = self._stub_registry[stub_name]
        return_value = handler(function_name, arguments, register_snapshot)

        # Write return value back to R0
        uc.reg_write(self._arm_const.UC_ARM_REG_R0, return_value)

        # Record the stub call
        record = StubCallRecord(
            stub_name=stub_name,
            function_name=function_name,
            arguments=list(arguments),
            return_value=return_value,
            timestamp_ms=int(time.time() * 1000) - (self._start_time_ms or 0),
            register_snapshot=register_snapshot,
        )
        self._stub_call_records.append(record)
        self._logger.debug("AAPCS stub call: %s.%s(%s) -> %s",
                           stub_name, function_name, arguments, return_value)

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

    def _hook_watchpoint(self, uc, address: int, size: int,
                         user_data) -> None:
        """Capture a register snapshot immediately before a watched instruction.

        Recording state *before* the instruction executes is what makes a
        faulting load diagnosable: the registers show where the bad pointer was
        derived from, rather than only that a fault happened.

        Args:
            uc: Unicorn engine instance
            address: Watched address
            size: Instruction size
            user_data: User data pointer
        """
        self._watch_captures.append({
            "address": address,
            "registers": self._read_registers(),
        })

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

        The artifact is written at ``load_address`` when one is configured, so a
        region taken from an ELF executes at the address its PC-relative
        references were assembled against. Otherwise it is written at
        ``code_address``.

        Args:
            binary_data: Raw binary bytes to map into code space
        """
        # Map code region
        self._uc.mem_map(self.config.code_address, self.config.code_size)
        load_address = (
            self.config.load_address
            if self.config.load_address is not None
            else self.config.code_address
        )
        self._uc.mem_write(load_address, binary_data)

        # Map stack region
        self._uc.mem_map(self.config.stack_address, self.config.stack_size)
        self._uc.reg_write(self._arm_const.UC_ARM_REG_SP,
                           self.config.stack_address + self.config.stack_size)

        # Map and seed any extra data regions the artifact dereferences.
        self._setup_data_regions()

        # Give zero-filled globals their real contents inside the existing map.
        self._apply_memory_patches()

    def _mapped_ranges(self) -> List[tuple]:
        """Ranges this emulator has mapped, for patch bounds checks."""
        return [
            (self.config.code_address,
             self.config.code_address + self.config.code_size),
            (self.config.stack_address,
             self.config.stack_address + self.config.stack_size),
        ] + [
            (region.address, region.address + region.size)
            for region in self.config.data_regions
        ]

    def _apply_memory_patches(self) -> None:
        """Copy bytes from the retained source binary into mapped memory.

        Used to restore global contents that were outside the extracted window.
        A patch must land entirely inside a mapping: writing to an unmapped
        address would fault, and a patch that straddles a boundary would be
        partially applied and produce a run that looks valid but is not.
        """
        ranges = self._mapped_ranges()
        for index, patch in enumerate(self.config.memory_patches):
            patch.validate()
            end = patch.address + patch.size
            if not any(start <= patch.address and end <= stop
                       for start, stop in ranges):
                covered = ", ".join(
                    f"[{hex(s)}, {hex(e)})" for s, e in ranges)
                raise ValueError(
                    f"memory_patches[{index}] [{hex(patch.address)}, "
                    f"{hex(end)}) is not fully inside a mapped range ({covered})."
                )

            source = Path(patch.source_path)
            if not source.exists():
                raise ValueError(
                    f"memory_patches[{index}] source {source} does not exist. "
                    f"Global contents must be copied from the retained source "
                    f"binary; leaving them zero-filled reproduces the fault this "
                    f"patch is meant to remove."
                )
            with open(source, "rb") as handle:
                handle.seek(patch.source_offset)
                payload = handle.read(patch.size)
            if len(payload) != patch.size:
                raise ValueError(
                    f"memory_patches[{index}] read {len(payload)} bytes from "
                    f"{source} at offset {patch.source_offset}, expected "
                    f"{patch.size}."
                )
            self._uc.mem_write(patch.address, payload)

    def _setup_data_regions(self) -> None:
        """Map configured data regions and seed them with real bytes.

        Overlaps are rejected rather than silently resolved: a data region that
        covers the code mapping would overwrite the artifact, and one covering
        the stack would corrupt the frame. Both produce runs whose results look
        plausible but describe an environment that never existed.
        """
        load_address = (
            self.config.load_address
            if self.config.load_address is not None
            else self.config.code_address
        )
        code_end = self.config.code_address + self.config.code_size
        stack_end = self.config.stack_address + self.config.stack_size
        # The code mapping is deliberately wider than the artifact: it spans the
        # globals a region dereferences. So the thing that must not be clobbered
        # is the artifact's own bytes, not every address inside the mapping.
        artifact_start = load_address
        artifact_end = load_address + self._binary_length(self.config.binary_path)

        # Ranges mapped so far, extended as data regions are added so a later
        # region can be checked against an earlier one.
        mapped: List[tuple] = [
            (self.config.code_address, code_end),
            (self.config.stack_address, stack_end),
        ]

        for index, region in enumerate(self.config.data_regions):
            region.validate()
            end = region.address + region.size
            if region.address < artifact_end and end > artifact_start:
                raise ValueError(
                    f"data_regions[{index}] [{hex(region.address)}, {hex(end)}) "
                    f"overlaps the artifact "
                    f"[{hex(artifact_start)}, {hex(artifact_end)}); it would "
                    f"overwrite the code under test."
                )
            if region.address < stack_end and end > self.config.stack_address:
                raise ValueError(
                    f"data_regions[{index}] [{hex(region.address)}, {hex(end)}) "
                    f"overlaps the stack mapping "
                    f"[{hex(self.config.stack_address)}, {hex(stack_end)})."
                )

            # The code mapping usually already spans these addresses, so only
            # map when the range is genuinely new. Re-mapping an existing page
            # makes Unicorn fail with an out-of-memory error that says nothing
            # about the real cause.
            already_mapped = any(
                start <= region.address and end <= stop
                for start, stop in mapped
            )
            if not already_mapped:
                straddling = [
                    (start, stop) for start, stop in mapped
                    if start < end and stop > region.address
                ]
                if straddling:
                    spans = ", ".join(
                        f"[{hex(s)}, {hex(e)})" for s, e in straddling)
                    raise ValueError(
                        f"data_regions[{index}] [{hex(region.address)}, "
                        f"{hex(end)}) partially overlaps an existing mapping "
                        f"({spans}). Unicorn cannot map a range that straddles "
                        f"a mapped page. Widen code_address/code_size to cover "
                        f"[{hex(min(region.address, straddling[0][0]))}, "
                        f"{hex(max(end, straddling[0][1]))}) so the whole range "
                        f"is one mapping."
                    )
                self._uc.mem_map(region.address, region.size)
                mapped.append((region.address, end))
            if region.fill:
                self._uc.mem_write(region.address, bytes([region.fill]) * region.size)
            if region.source_path is None:
                continue

            source = Path(region.source_path)
            if not source.exists():
                raise ValueError(
                    f"data_regions[{index}] source {source} does not exist. "
                    f"Seed bytes must come from the retained source binary; do "
                    f"not substitute zeros, that reproduces the fault this "
                    f"region is meant to remove."
                )
            with open(source, "rb") as handle:
                handle.seek(region.source_offset)
                available = region.size - region.dest_offset
                payload = handle.read(available)
            if not payload:
                raise ValueError(
                    f"data_regions[{index}] read 0 bytes from {source} at offset "
                    f"{region.source_offset}."
                )
            self._uc.mem_write(region.address + region.dest_offset, payload)

    def _setup_entry_arguments(self) -> None:
        """Write AAPCS arguments for the entry function.

        A region extracted from the middle of a binary is entered with no caller
        frame: R0-R3 hold whatever the previous run left and the stack holds
        zeros. Anything the function reads from its parameters is therefore
        garbage, and control flow that branches on them is meaningless.

        Arguments beyond R0-R3 are pushed in order so SP-4 holds the fifth
        argument, matching AAPCS.
        """
        values = list(self.config.entry_arguments)
        if not values:
            return

        arg_regs = ["UC_ARM_REG_R0", "UC_ARM_REG_R1", "UC_ARM_REG_R2",
                    "UC_ARM_REG_R3"]
        for reg_name, value in zip(arg_regs, values):
            const = getattr(self._arm_const, reg_name, None)
            if const is not None:
                self._uc.reg_write(const, value & 0xFFFFFFFF)

        extra = values[len(arg_regs):]
        if not extra:
            return
        sp = self._uc.reg_read(self._arm_const.UC_ARM_REG_SP)
        needed = 4 * len(extra)
        if sp - needed < self.config.stack_address:
            raise ValueError(
                f"stack cannot hold {len(extra)} spilled argument(s): SP "
                f"{hex(sp)} would fall below the stack base "
                f"{hex(self.config.stack_address)}. Increase stack_size."
            )
        for offset, value in enumerate(extra):
            self._uc.mem_write(sp - (offset + 1) * 4,
                               struct.pack("<I", value & 0xFFFFFFFF))
        self._uc.reg_write(self._arm_const.UC_ARM_REG_SP, sp - needed)

    def _setup_hooks(self) -> None:
        """Install Unicorn hooks for tracing and fail-closed behavior."""
        from unicorn import UC_HOOK_CODE, UC_HOOK_INSN_INVALID, UC_HOOK_INTR

        self._uc.hook_add(UC_HOOK_CODE, self._hook_code)
        self._uc.hook_add(UC_HOOK_INSN_INVALID, self._hook_invalid_instruction)
        self._uc.hook_add(UC_HOOK_INTR, self._hook_intr)

        # Per-address register captures, installed only when requested.
        if self.config.watch_addresses:
            watched = set(self.config.watch_addresses)
            for address in sorted(watched):
                self._uc.hook_add(
                    UC_HOOK_CODE, self._hook_watchpoint,
                    begin=address, end=address + 1,
                )

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

        # Provenance fields are set at construction, not only on the success
        # path: a failing run must report the mode it actually ran in and how
        # many instructions it actually executed (issue #21). The dataclass
        # defaults would otherwise silently claim "thumb"/0 on an ARM run.
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
            mode=self._mode,
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

            # Supply AAPCS arguments so the function sees a caller frame rather
            # than whatever the previous run left in R0-R3.
            self._setup_entry_arguments()

            # Start emulation
            self._uc.emu_start(entry, self.config.entry_address + len(binary_data),
                               timeout=self.config.timeout_ms)

            # Read final register state
            result.final_registers = self._read_registers()
            result.stub_calls = self._stub_call_records

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

            # Record execution facts on every path, including failures. Without
            # this a faulting run reports instructions_executed=0 and empty
            # stub_calls/registers even though the engine did work, which makes
            # the failing record - the one a gate must trust - misleading.
            result.mode = self._mode
            result.instructions_executed = self._instruction_count
            if not result.stub_calls:
                result.stub_calls = list(self._stub_call_records)
            # Captures are recorded even when the run faults: the point of a
            # watchpoint is to explain a failure, so dropping them on the
            # failure path would defeat it.
            if not result.watch_captures:
                result.watch_captures = list(self._watch_captures)
            if not result.final_registers and self._uc is not None:
                # Best effort: registers may be unreadable if the engine died
                # before any instruction ran.
                try:
                    result.final_registers = self._read_registers()
                except Exception:  # pylint: disable=broad-except
                    pass

        return result
