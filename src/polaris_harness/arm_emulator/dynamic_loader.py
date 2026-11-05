"""
Dynamic ELF loader for original Polaris binaries under Unicorn.

Maps all ELF program segments, resolves dynamic relocations against retained
shared libraries, and executes the binary's entry point with a minimal
userspace environment. This is the first step toward running pgphoto.stage2ondisk
end-to-end under emulation (issue #27, WBS 2).

Design constraints:
- Read-only: never mutates input binaries or libraries.
- Fail closed: unresolved relocations, missing symbols, or unsupported
  relocations abort the run with a machine-readable error.
- Every run bound to binary SHA-256, library hashes, entry address, and
  full machine-readable result.
- Synthetic libraries are clearly marked as assumptions, not evidence.
"""

import hashlib
import logging
import struct
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_ARM
from unicorn import UC_PROT_READ, UC_PROT_WRITE, UC_PROT_EXEC
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_INVALID, UC_HOOK_INSN_INVALID, UC_HOOK_INTR
from unicorn.arm_const import (
    UC_ARM_REG_PC,
    UC_ARM_REG_LR,
    UC_ARM_REG_SP,
    UC_ARM_REG_R0,
    UC_ARM_REG_R1,
    UC_ARM_REG_R2,
    UC_ARM_REG_R3,
    UC_ARM_REG_R4,
    UC_ARM_REG_R5,
    UC_ARM_REG_R6,
    UC_ARM_REG_R7,
    UC_ARM_REG_R8,
    UC_ARM_REG_R9,
    UC_ARM_REG_R10,
    UC_ARM_REG_R11,
    UC_ARM_REG_R12,
    UC_ARM_REG_FPEXC,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 0x1000

# ELF constants
PT_NULL = 0
PT_LOAD = 1
PT_DYNAMIC = 2
DT_NULL = 0
DT_NEEDED = 1
DT_PLTRELSZ = 2
DT_STRTAB = 5
DT_SYMTAB = 6
DT_REL = 17
DT_RELSZ = 18
DT_JMPREL = 23

# ARM relocation types
R_ARM_ABS32 = 2
R_ARM_GLOB_DAT = 21
R_ARM_JUMP_SLOT = 22
R_ARM_RELATIVE = 23
R_ARM_COPY = 20

RELOC_TYPE_NAMES = {
    R_ARM_ABS32: "R_ARM_ABS32",
    R_ARM_GLOB_DAT: "R_ARM_GLOB_DAT",
    R_ARM_JUMP_SLOT: "R_ARM_JUMP_SLOT",
    R_ARM_RELATIVE: "R_ARM_RELATIVE",
    R_ARM_COPY: "R_ARM_COPY",
}

# Synthetic symbol stubs for system libraries we don't have retained.
# These are clearly marked as assumptions, not evidence. Each maps a symbol
# name to a synthetic address in a reserved range (0x50000000+).
_SYNTHETIC_SYMBOLS = {
    # libc.so.6
    "signal": 0x50001000,
    "pthread_cond_init": 0x50001004,
    "chdir": 0x50001008,
    "trunc": 0x5000100c,
    "_IO_getc": 0x50001010,
    "strndup": 0x50001014,
    "pthread_mutex_lock": 0x50001018,
    "localtime_r": 0x5000101c,
    "deflateInit2_": 0x50001020,
    "pthread_mutex_init": 0x50001024,
    "shmctl": 0x50001028,
    "lt_dlsym": 0x5000102c,
    "round": 0x50001030,
    "rand": 0x50001034,
    "pthread_detach": 0x50001038,
    "sqrt": 0x5000103c,
    "popen": 0x50001040,
    "ftruncate64": 0x50001044,
    "log": 0x50001048,
    "dlerror": 0x5000104c,
    "gettimeofday": 0x50001050,
    "pthread_cond_destroy": 0x50001054,
    "getsockopt": 0x50001058,
    "strcasecmp": 0x5000105c,
    "utime": 0x50001060,
    "msgget": 0x50001064,
    "freeaddrinfo": 0x50001068,
    "strtol": 0x5000106c,
    "free": 0x50001070,
    "getopt_long_only": 0x50001074,
    "getaddrinfo": 0x50001078,
    "fseek": 0x5000107c,
    "bind_textdomain_codeset": 0x50001080,
    "__xstat64": 0x50001084,
    "qsort": 0x50001088,
    "strncmp": 0x5000108c,
    "pthread_key_delete": 0x50001090,
    "__pthread_unwind_next": 0x50001094,
    "pthread_join": 0x50001098,
    "fgetc": 0x5000109c,
    "asctime": 0x500010a0,
    "deflate": 0x500010a4,
    "closedir": 0x500010a8,
    "inet_ntop": 0x500010ac,
    "inet_aton": 0x500010b0,
    "exit": 0x500010b4,
    "printf": 0x500010b8,
    "strerror": 0x500010bc,
    "pthread_setschedparam": 0x500010c0,
    "stpcpy": 0x500010c4,
    "pthread_cond_timedwait": 0x500010c8,
    "pthread_cond_signal": 0x500010cc,
    "atol": 0x500010d0,
    "feof": 0x500010d4,
    "exp2": 0x500010d0,
    "puts": 0x500010d4,
    "pthread_equal": 0x500010d8,
    "strtok": 0x500010dc,
    "perror": 0x500010e0,
    "optind": 0x500010e4,
    "optarg": 0x500010e8,
    "stdin": 0x500010ec,
    "stdout": 0x500010f0,
    "stderr": 0x500010f4,
    # libm.so.6
    "exp": 0x50002000,
    "pow": 0x50002004,
    "floor": 0x50002008,
    "ceil": 0x5000200c,
    "fabs": 0x50002010,
    # libpthread.so.0
    "pthread_create": 0x50003000,
    "pthread_exit": 0x50003004,
    "pthread_self": 0x50003008,
    "pthread_cancel": 0x5000300c,
    "pthread_once": 0x50003010,
    # libdl.so.2
    "dlopen": 0x50004000,
    "dlsym": 0x50004004,
    "dlclose": 0x50004008,
    # librt.so.1
    "clock_gettime": 0x50005000,
    "timer_create": 0x50005004,
    # libgcc_s.so.1
    "__aeabi_idiv": 0x50006000,
    "__aeabi_uidiv": 0x50006004,
    # libstdc++.so.6
    "__cxa_atexit": 0x50007000,
    "__cxa_finalize": 0x50007004,
    # Additional unresolved symbols from pgphoto.stage2ondisk
    "__ctype_toupper_loc": 0x50008000,
    "__errno_location": 0x50008004,
    "__lxstat64": 0x50008008,
    "__pthread_unregister_cancel": 0x5000800c,
    "__tls_get_addr": 0x50008010,
    "__xpg_basename": 0x50008014,
    "_exit": 0x50008018,
    "_setjmp": 0x5000801c,
    "abort": 0x50008020,
    "accept": 0x50008024,
    "access": 0x50008028,
    "bindtextdomain": 0x5000802c,
    "bsearch": 0x50008030,
    "chmod": 0x50008034,
    "closelog": 0x50008038,
    "crc32": 0x5000803c,
    "execve": 0x50008040,
    "fclose": 0x50008044,
    "ferror": 0x50008048,
    "fgets": 0x5000804c,
    "fopen": 0x50008050,
    "fork": 0x50008054,
    "fprintf": 0x50008058,
    "fputc": 0x5000805c,
    "fread": 0x50008060,
    "ftell": 0x50008064,
    "gai_strerror": 0x50008068,
    "getenv": 0x5000806c,
    "gzclose": 0x50008070,
    "gzdirect": 0x50008074,
    "gzdopen": 0x50008078,
    "gzopen64": 0x5000807c,
    "gzread": 0x50008080,
    "gzwrite": 0x50008084,
    "htons": 0x50008088,
    "iconv_open": 0x5000808c,
    "inflate": 0x50008090,
    "inflateEnd": 0x50008094,
    "inflateInit2_": 0x50008098,
    "inflateReset": 0x5000809c,
    "isatty": 0x500080a0,
    "log10": 0x500080a4,
    "lt_dladdsearchdir": 0x500080a8,
    "lt_dlclose": 0x500080ac,
    "lt_dlerror": 0x500080b0,
    "lt_dlexit": 0x500080b4,
    "lt_dlforeachfile": 0x500080b8,
    "lt_dlopenext": 0x500080bc,
    "lzma_auto_decoder": 0x500080c0,
    "lzma_code": 0x500080c4,
    "lzma_properties_decode": 0x500080c8,
    "memcmp": 0x500080cc,
    "memcpy": 0x500080d0,
    "mkdir": 0x500080d4,
    "mkstemp64": 0x500080d8,
    "msgctl": 0x500080dc,
    "msgrcv": 0x500080e0,
    "msgsnd": 0x500080e4,
    "ntohs": 0x500080e8,
    "open64": 0x500080ec,
    "poll": 0x500080f0,
    "prctl": 0x500080f4,
    "pthread_cond_wait": 0x500080f8,
    "pthread_getspecific": 0x500080fc,
    "pthread_key_create": 0x50008100,
    "pthread_mutex_destroy": 0x50008104,
    "pthread_setspecific": 0x50008108,
    "pthread_testcancel": 0x5000810c,
    "putenv": 0x50008110,
    "rand_r": 0x50008114,
    "readdir64": 0x50008118,
    "realloc": 0x5000811c,
    "recv": 0x50008120,
    "remove": 0x50008124,
    "rename": 0x50008128,
    "rewind": 0x5000812c,
    "rmdir": 0x50008130,
    "sched_get_priority_max": 0x50008134,
    "sched_get_priority_min": 0x50008138,
    "select": 0x5000813c,
    "semget": 0x50008140,
    "setlocale": 0x50008144,
    "setsockopt": 0x50008148,
    "shmat": 0x5000814c,
    "shmdt": 0x50008150,
    "snprintf": 0x50008154,
    "sprintf": 0x50008158,
    "srand": 0x5000815c,
    "statfs64": 0x50008160,
    "strcasestr": 0x50008164,
    "strcpy": 0x50008168,
    "strlen": 0x5000816c,
    "strrchr": 0x50008170,
    "strspn": 0x50008174,
    "strstr": 0x50008178,
    "strtok_r": 0x5000817c,
    "strtoul": 0x50008180,
    "syslog": 0x50008184,
    "tzset": 0x50008188,
    "umask": 0x5000818c,
    "unlink": 0x50008190,
    "usleep": 0x50008194,
    "vfprintf": 0x50008198,
    "waitpid": 0x5000819c,
}

# Map register names to their Unicorn constants for AAPCS argument marshaling.
_REG_CONSTS = {
    "R0": UC_ARM_REG_R0, "R1": UC_ARM_REG_R1, "R2": UC_ARM_REG_R2,
    "R3": UC_ARM_REG_R3, "R4": UC_ARM_REG_R4, "R5": UC_ARM_REG_R5,
    "R6": UC_ARM_REG_R6, "R7": UC_ARM_REG_R7, "R8": UC_ARM_REG_R8,
    "R9": UC_ARM_REG_R9, "R10": UC_ARM_REG_R10, "R11": UC_ARM_REG_R11,
    "R12": UC_ARM_REG_R12, "SP": UC_ARM_REG_SP, "LR": UC_ARM_REG_LR,
    "PC": UC_ARM_REG_PC,
}


class LoadResult(Enum):
    """Loader/emulation outcome."""
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    TIMEOUT = "timeout"
    UNSUPPORTED_RELOCATION = "unsupported_relocation"
    UNRESOLVED_SYMBOL = "unresolved_symbol"
    MISSING_LIBRARY = "missing_library"
    MEMORY_FAULT = "memory_fault"
    UNSUPPORTED_INSTRUCTION = "unsupported_instruction"


@dataclass
class LibraryRef:
    """A shared library needed by the binary."""
    name: str
    path: Optional[Path] = None
    sha256: Optional[str] = None
    soname: Optional[str] = None
    synthetic: bool = False
    exports: Dict[str, int] = field(default_factory=dict)
    base_address: Optional[int] = None
    size: int = 0


@dataclass
class RelocEntry:
    """A single dynamic relocation."""
    offset: int
    info: int
    type: str
    symbol_index: int
    symbol_name: str
    addend: int = 0


@dataclass
class LoadRecord:
    """Machine-readable record of a load+run attempt."""
    binary_sha256: str
    binary_path: str
    entry_address: int
    libraries: List[Dict[str, Any]] = field(default_factory=list)
    relocations_resolved: int = 0
    relocations_unresolved: int = 0
    instructions_executed: int = 0
    result: str = LoadResult.ERROR.value
    error_message: Optional[str] = None
    unsupported_relocation_offset: Optional[int] = None
    unresolved_symbol: Optional[str] = None
    missing_library: Optional[str] = None
    exit_code: int = -1
    duration_ms: int = 0


def _align_up(value: int, alignment: int = PAGE_SIZE) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def _compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_elf_header(data: bytes) -> dict:
    """Parse minimal ELF32 header fields."""
    if len(data) < 52:
        raise ValueError("ELF too small for header")
    if data[:4] != b"\x7fELF":
        raise ValueError("Not an ELF file")
    ei_class = data[4]
    if ei_class != 1:
        raise ValueError(f"Expected ELF32, got class {ei_class}")
    ei_data = data[5]
    if ei_data != 1:
        raise ValueError(f"Expected little-endian, got {ei_data}")
    e_type = struct.unpack_from("<H", data, 16)[0]
    e_machine = struct.unpack_from("<H", data, 18)[0]
    e_entry = struct.unpack_from("<I", data, 24)[0]
    e_phoff = struct.unpack_from("<I", data, 28)[0]
    e_phentsize = struct.unpack_from("<H", data, 42)[0]
    e_phnum = struct.unpack_from("<H", data, 44)[0]
    return {
        "class": ei_class,
        "data": ei_data,
        "type": e_type,
        "machine": e_machine,
        "entry": e_entry,
        "phoff": e_phoff,
        "phentsize": e_phentsize,
        "phnum": e_phnum,
    }


def _read_program_headers(data: bytes, header: dict) -> List[dict]:
    """Parse ELF32 program headers."""
    phdrs = []
    for i in range(header["phnum"]):
        off = header["phoff"] + i * header["phentsize"]
        p_type = struct.unpack_from("<I", data, off)[0]
        p_offset = struct.unpack_from("<I", data, off + 4)[0]
        p_vaddr = struct.unpack_from("<I", data, off + 8)[0]
        p_paddr = struct.unpack_from("<I", data, off + 12)[0]
        p_filesz = struct.unpack_from("<I", data, off + 16)[0]
        p_memsz = struct.unpack_from("<I", data, off + 20)[0]
        p_flags = struct.unpack_from("<I", data, off + 24)[0]
        p_align = struct.unpack_from("<I", data, off + 28)[0]
        phdrs.append({
            "type": p_type,
            "offset": p_offset,
            "vaddr": p_vaddr,
            "paddr": p_paddr,
            "filesz": p_filesz,
            "memsz": p_memsz,
            "flags": p_flags,
            "align": p_align,
        })
    return phdrs


def _read_dynamic_entries(data: bytes, phdrs: List[dict]) -> List[dict]:
    """Find and parse the PT_DYNAMIC segment entries."""
    dyn_seg = None
    for p in phdrs:
        if p["type"] == PT_DYNAMIC:
            dyn_seg = p
            break
    if dyn_seg is None:
        return []

    entries = []
    off = dyn_seg["offset"]
    while off + 8 <= len(data):
        tag = struct.unpack_from("<I", data, off)[0]
        val = struct.unpack_from("<I", data, off + 4)[0]
        entries.append({"tag": tag, "val": val})
        off += 8
        if tag == DT_NULL:
            break
    return entries


def _read_string(data: bytes, strtab_offset: int, str_index: int) -> str:
    """Read a null-terminated string from the string table."""
    off = strtab_offset + str_index
    end = data.find(b"\x00", off)
    if end == -1:
        return data[off:].decode("utf-8", errors="replace")
    return data[off:end].decode("utf-8", errors="replace")


def _parse_symbol_table(data: bytes, phdrs: List[dict], dyn_entries: List[dict]) -> List[tuple]:
    """Parse .dynsym and .dynstr to build ordered list of (index, name, value).

    Returns a list indexed by symbol table index.
    """
    symtab_off = strtab_off = syment = None
    for e in dyn_entries:
        if e["tag"] == DT_SYMTAB:
            symtab_off = e["val"]
        elif e["tag"] == DT_STRTAB:
            strtab_off = e["val"]
        elif e["tag"] == 11:  # DT_SYMENT
            syment = e["val"]

    if symtab_off is None or strtab_off is None or syment is None:
        return []

    # Convert virtual addresses to file offsets
    symtab_file = _vaddr_to_file_offset(data, phdrs, symtab_off)
    strtab_file = _vaddr_to_file_offset(data, phdrs, strtab_off)
    if symtab_file is None or strtab_file is None:
        return []

    symbols = []
    off = symtab_file
    index = 0
    while off + syment <= len(data):
        st_name = struct.unpack_from("<I", data, off)[0]
        st_value = struct.unpack_from("<I", data, off + 4)[0]
        st_shndx = struct.unpack_from("<H", data, off + 14)[0]

        name = ""
        if st_name != 0:
            name = _read_string(data, strtab_file, st_name)

        symbols.append((index, name, st_value, st_shndx))
        off += syment
        index += 1

    return symbols


def _vaddr_to_file_offset(data: bytes, phdrs: List[dict], vaddr: int) -> Optional[int]:
    """Convert a virtual address to a file offset using PT_LOAD segments."""
    for p in phdrs:
        if p["type"] != PT_LOAD:
            continue
        if p["vaddr"] <= vaddr < p["vaddr"] + p["memsz"]:
            return p["offset"] + (vaddr - p["vaddr"])
    return None


def _parse_relocations(data: bytes, phdrs: List[dict], dyn_entries: List[dict],
                       index_to_name: Dict[int, str]) -> List[RelocEntry]:
    """Parse .rel.plt and .rel.dyn relocation entries."""
    relocs = []

    jmprel_off = pltrelsz = rel_off = relsz = None
    for e in dyn_entries:
        if e["tag"] == 23:  # DT_JMPREL
            jmprel_off = e["val"]
        elif e["tag"] == 2:  # DT_PLTRELSZ
            pltrelsz = e["val"]
        elif e["tag"] == DT_REL:
            rel_off = e["val"]
        elif e["tag"] == DT_RELSZ:
            relsz = e["val"]

    def parse_rel_table(vaddr: int, size: int) -> None:
        if vaddr is None or size is None:
            return
        file_off = _vaddr_to_file_offset(data, phdrs, vaddr)
        if file_off is None:
            return
        num = size // 8
        for i in range(num):
            entry_off = file_off + i * 8
            if entry_off + 8 > len(data):
                break
            r_offset = struct.unpack_from("<I", data, entry_off)[0]
            r_info = struct.unpack_from("<I", data, entry_off + 4)[0]
            sym_idx = r_info >> 8
            reloc_type = r_info & 0xFF
            type_name = RELOC_TYPE_NAMES.get(reloc_type, f"R_ARM_{reloc_type}")
            symbol_name = index_to_name.get(sym_idx, f"sym_{sym_idx}")
            relocs.append(RelocEntry(
                offset=r_offset,
                info=r_info,
                type=type_name,
                symbol_index=sym_idx,
                symbol_name=symbol_name,
            ))

    parse_rel_table(jmprel_off, pltrelsz)
    parse_rel_table(rel_off, relsz)

    return relocs


class DynamicLoader:
    """
    Loads an ELF32 ARM executable into Unicorn memory, resolves dynamic
    relocations, and runs from its entry point.
    """

    def __init__(
        self,
        binary_path: Path,
        libraries: Optional[List[LibraryRef]] = None,
        entry_arguments: Optional[List[int]] = None,
        stack_address: int = 0x20000000,
        stack_size: int = 0x10000,
        max_instructions: int = 100000,
        timeout_ms: int = 10000,
        enable_fpu: bool = True,
    ):
        self.binary_path = Path(binary_path)
        self.binary_data = self.binary_path.read_bytes()
        self.binary_sha256 = _compute_sha256(self.binary_path)
        self.libraries = libraries or []
        self.entry_arguments = entry_arguments or []
        self.stack_address = stack_address
        self.stack_size = stack_size
        self.max_instructions = max_instructions
        self.timeout_ms = timeout_ms
        self.enable_fpu = enable_fpu

        self.header = _read_elf_header(self.binary_data)
        self.phdrs = _read_program_headers(self.binary_data, self.header)
        self.entry_address = self.header["entry"]

        self._uc: Optional[Uc] = None
        self._instruction_count = 0
        self._instruction_limit_exceeded = False
        self._unsupported_instruction_address: Optional[int] = None
        self._fault_access_address: Optional[int] = None
        self._fault_access_type: Optional[str] = None
        self._unexpected_syscall_number: Optional[int] = None
        self._start_time_ms: Optional[int] = None
        self._stub_registry: Dict[str, Callable] = {}
        self._stub_entries: Dict[int, dict] = {}
        self._symbol_map: Dict[str, int] = {}

        self.record = LoadRecord(
            binary_sha256=self.binary_sha256,
            binary_path=str(self.binary_path),
            entry_address=self.entry_address,
        )

    # ------------------------------------------------------------------
    # Memory setup
    # ------------------------------------------------------------------
    def _setup_memory(self) -> None:
        """Map all PT_LOAD segments and the stack."""
        for p in self.phdrs:
            if p["type"] != PT_LOAD:
                continue
            vaddr = p["vaddr"]
            memsz = _align_up(p["memsz"])
            filesz = p["filesz"]
            flags = p["flags"]

            # Unicorn requires page-aligned mappings. Align the vaddr down and
            # extend the size to cover the original range.
            map_addr = vaddr & ~(PAGE_SIZE - 1)
            map_size = _align_up((vaddr + memsz) - map_addr)

            perm = 0
            if flags & 0x4:  # PF_R
                perm |= UC_PROT_READ
            if flags & 0x2:  # PF_W
                perm |= UC_PROT_WRITE
            if flags & 0x1:  # PF_X
                perm |= UC_PROT_EXEC

            self._uc.mem_map(map_addr, map_size, perm)
            if filesz > 0:
                file_off = vaddr - map_addr
                self._uc.mem_write(map_addr + file_off,
                                   self.binary_data[p["offset"]:p["offset"] + filesz])

        # Map stack
        self._uc.mem_map(self.stack_address, self.stack_size,
                         UC_PROT_READ | UC_PROT_WRITE)

        # Push argc, argv
        stack_top = self.stack_address + self.stack_size
        for arg in reversed(self.entry_arguments):
            stack_top -= 4
            self._uc.mem_write(stack_top, struct.pack("<I", arg))
        stack_top -= 4
        self._uc.mem_write(stack_top, struct.pack("<I", 0))  # argv[argc] = NULL
        argc = len(self.entry_arguments)
        stack_top -= 4
        self._uc.mem_write(stack_top, struct.pack("<I", argc))  # argc

        self._uc.reg_write(UC_ARM_REG_SP, stack_top)
        self._uc.reg_write(UC_ARM_REG_LR, 0)

    # ------------------------------------------------------------------
    # Relocation resolution
    # ------------------------------------------------------------------
    def _apply_relocations(self, relocs: List[RelocEntry]) -> None:
        """Apply relocations to emulated memory."""
        for reloc in relocs:
            if reloc.type == "R_ARM_RELATIVE":
                self._uc.mem_write(reloc.offset, struct.pack("<I", reloc.addend))
                self.record.relocations_resolved += 1
                continue

            if reloc.type in ("R_ARM_JUMP_SLOT", "R_ARM_GLOB_DAT", "R_ARM_ABS32", "R_ARM_COPY"):
                addr = self._resolve_symbol(reloc.symbol_name)
                if addr is None:
                    self.record.relocations_unresolved += 1
                    self.record.unresolved_symbol = reloc.symbol_name
                    raise ValueError(
                        f"Unresolved symbol {reloc.symbol_name} at "
                        f"0x{reloc.offset:08x} ({reloc.type})"
                    )
                self._uc.mem_write(reloc.offset, struct.pack("<I", addr))
                self.record.relocations_resolved += 1
                continue

            self.record.relocations_unresolved += 1
            self.record.unsupported_relocation_offset = reloc.offset
            raise ValueError(
                f"Unsupported relocation type {reloc.type} at "
                f"0x{reloc.offset:08x} for symbol {reloc.symbol_name}"
            )

    def _resolve_symbol(self, symbol_name: str) -> Optional[int]:
        """Look up a symbol in the symbol map."""
        if symbol_name in self._symbol_map:
            return self._symbol_map[symbol_name]
        for lib in self.libraries:
            if symbol_name in lib.exports:
                addr = lib.exports[symbol_name]
                self._symbol_map[symbol_name] = addr
                return addr
        return None

    # ------------------------------------------------------------------
    # Unicorn hooks
    # ------------------------------------------------------------------
    def _hook_code(self, uc, address: int, size: int, user_data) -> None:
        self._instruction_count += 1
        if self._instruction_count > self.max_instructions:
            self._instruction_limit_exceeded = True
            uc.emu_stop()

        entry = self._stub_entries.get(address)
        if entry is not None:
            self._marshal_stub_call(uc, entry["stub_name"], entry)
            if entry["model"] == "target":
                lr = uc.reg_read(UC_ARM_REG_LR)
                uc.reg_write(UC_ARM_REG_PC, lr)
            else:
                uc.reg_write(UC_ARM_REG_PC, address + size)
            return

    def _marshal_stub_call(self, uc, stub_name: str, entry: dict) -> None:
        if stub_name not in self._stub_registry:
            raise KeyError(f"Missing stub: {stub_name}")
        function_name = entry["function_name"]
        arguments = []
        for reg in ["R0", "R1", "R2", "R3"]:
            const = _REG_CONSTS[reg]
            arguments.append(uc.reg_read(const))
        handler = self._stub_registry[stub_name]
        register_snapshot = self._read_registers(uc)
        return_value = handler(function_name, arguments, register_snapshot)
        uc.reg_write(UC_ARM_REG_R0, return_value)

    def _hook_mem_invalid(self, uc, access, address: int, size: int,
                          value: int, user_data) -> bool:
        self._fault_access_address = address
        self._fault_access_type = f"access({access})"
        logger.error("Faulting memory %s at 0x%08x", self._fault_access_type, address)
        return False

    def _hook_invalid_instruction(self, uc, address: int, size: int,
                                  user_data) -> None:
        self._unsupported_instruction_address = address
        logger.error("Unsupported instruction at 0x%08x", address)
        uc.emu_stop()

    def _hook_syscall(self, uc, user_data) -> None:
        syscall_num = uc.reg_read(UC_ARM_REG_R7)
        self._unexpected_syscall_number = syscall_num
        if "os" in self._stub_registry:
            try:
                regs = self._read_registers(uc)
                self._call_stub("os", f"syscall_{syscall_num}", [], regs, uc)
                return
            except KeyError:
                pass
        logger.error("Unexpected syscall number: %d", syscall_num)
        uc.emu_stop()

    def _read_registers(self, uc) -> Dict[str, int]:
        regs = {}
        for name, const in _REG_CONSTS.items():
            regs[name] = uc.reg_read(const)
        return regs

    def _call_stub(self, stub_name: str, function_name: str,
                   arguments: List[Any], register_snapshot: Dict[str, int],
                   uc) -> Any:
        if stub_name not in self._stub_registry:
            raise KeyError(f"Missing stub: {stub_name}")
        handler = self._stub_registry[stub_name]
        return_value = handler(function_name, arguments, register_snapshot)
        uc.reg_write(UC_ARM_REG_R0, return_value)
        return return_value

    def register_stub(self, name: str, handler: Callable) -> None:
        self._stub_registry[name] = handler

    def register_stub_entry(self, stub_name: str, address: int,
                            function_name: Optional[str] = None,
                            model: str = "call-site") -> None:
        if stub_name not in self._stub_registry:
            raise KeyError(f"Cannot register entry for unregistered stub: {stub_name}")
        if model not in ("call-site", "target"):
            raise ValueError(f"Invalid stub entry model: {model!r}")
        self._stub_entries[address] = {
            "stub_name": stub_name,
            "function_name": function_name or "aapcs_call",
            "model": model,
        }

    def _enable_fpu(self) -> None:
        """Set FPEXC.EN so VFP/NEON instructions are accepted."""
        try:
            fpexc = self._uc.reg_read(UC_ARM_REG_FPEXC)
            self._uc.reg_write(UC_ARM_REG_FPEXC, fpexc | (1 << 30))
        except UcError:
            pass

    # ------------------------------------------------------------------
    # Main run
    # ------------------------------------------------------------------
    def run(self) -> LoadRecord:
        """Load the binary, resolve relocations, and execute from entry."""
        self._start_time_ms = int(time.time() * 1000)

        try:
            self._uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        except ImportError as exc:
            self.record.result = LoadResult.ERROR.value
            self.record.error_message = f"Unicorn not available: {exc}"
            return self.record

        if self.enable_fpu:
            self._enable_fpu()

        self._setup_memory()

        # Parse dynamic section and relocations
        dyn_entries = _read_dynamic_entries(self.binary_data, self.phdrs)
        symbol_list = _parse_symbol_table(self.binary_data, self.phdrs, dyn_entries)
        # Build name -> value map for defined symbols
        self._symbol_map = {}
        for idx, name, value, shndx in symbol_list:
            if name and shndx != 0:
                self._symbol_map[name] = value
        # Build index -> name for relocation resolution
        index_to_name = {idx: name for idx, name, value, shndx in symbol_list}
        relocs = _parse_relocations(self.binary_data, self.phdrs, dyn_entries, index_to_name)

        # Add library exports to symbol map
        for lib in self.libraries:
            for name, addr in lib.exports.items():
                self._symbol_map[name] = addr

        # Add synthetic system library stubs (clearly marked as assumptions)
        self._symbol_map.update(_SYNTHETIC_SYMBOLS)

        try:
            self._apply_relocations(relocs)
        except ValueError as exc:
            self.record.result = LoadResult.ERROR.value
            self.record.error_message = str(exc)
            return self.record

        # Register hooks
        self._uc.hook_add(UC_HOOK_CODE, self._hook_code)
        self._uc.hook_add(UC_HOOK_MEM_INVALID, self._hook_mem_invalid)
        self._uc.hook_add(UC_HOOK_INSN_INVALID, self._hook_invalid_instruction)
        self._uc.hook_add(UC_HOOK_INTR, self._hook_syscall)

        # Set entry point
        self._uc.reg_write(UC_ARM_REG_PC, self.entry_address)

        # Run
        try:
            self._uc.emu_start(self.entry_address, 0,
                               count=self.max_instructions)
        except UcError:
            pass

        # Finalize record
        self.record.instructions_executed = self._instruction_count
        if self._instruction_limit_exceeded:
            self.record.result = LoadResult.TIMEOUT.value
            self.record.error_message = "Instruction limit exceeded"
        elif self._unsupported_instruction_address is not None:
            self.record.result = LoadResult.UNSUPPORTED_INSTRUCTION.value
            self.record.error_message = (
                f"Unsupported instruction at "
                f"0x{self._unsupported_instruction_address:08x}"
            )
        elif self._fault_access_address is not None:
            self.record.result = LoadResult.MEMORY_FAULT.value
            self.record.error_message = (
                f"Memory fault at 0x{self._fault_access_address:08x} "
                f"({self._fault_access_type})"
            )
        elif self._unexpected_syscall_number is not None:
            self.record.result = LoadResult.ERROR.value
            self.record.error_message = (
                f"Unexpected syscall {self._unexpected_syscall_number}"
            )
        else:
            self.record.result = LoadResult.PASS.value
            self.record.exit_code = 0

        return self.record


def load_and_run_pgphoto(
    binary_path: Path,
    library_dir: Path,
    max_instructions: int = 100000,
    timeout_ms: int = 10000,
) -> LoadRecord:
    """
    Convenience function: load pgphoto.stage2ondisk with its retained
    libraries and run from entry.

    Libraries in library_dir are mapped as retained binaries. Missing system
    libraries are recorded as synthetic stubs.
    """
    libraries = []

    retained = {
        "libpolaris_stage2.so": library_dir / "ondisk" / "libpolaris_stage2.so",
        "libgphoto2_port.so.12": library_dir / "ondisk" / "libgphoto2_port.so.12",
        "libgphoto2.so.6": library_dir / "ondisk" / "libgphoto2.so.6",
        "ptp2.so": library_dir / "ondisk" / "libgphoto2" / "2.5.34" / "ptp2.so",
        "pentax.so": library_dir / "ondisk" / "libgphoto2" / "2.5.34" / "pentax.so",
        "usb1.so": library_dir / "ondisk" / "libgphoto2_port" / "0.12.2" / "usb1.so",
        "usbscsi.so": library_dir / "ondisk" / "libgphoto2_port" / "0.12.2" / "usbscsi.so",
    }

    for name, path in retained.items():
        if path.exists():
            sha = _compute_sha256(path)
            libraries.append(LibraryRef(
                name=name,
                path=path,
                sha256=sha,
                synthetic=False,
            ))
        else:
            libraries.append(LibraryRef(
                name=name,
                path=None,
                synthetic=True,
            ))

    system_libs = [
        "librt.so.1", "libdl.so.2", "libpthread.so.0", "libtimers.so",
        "libuproc.so", "libsecurec.so", "libm.so.6", "libstdc++.so.6",
        "libc.so.6", "libltdl.so.7", "liblzma.so.5", "libz.so.1",
        "libgomp.so.1", "libgcc_s.so.1", "ld-linux.so.3",
    ]
    for name in system_libs:
        libraries.append(LibraryRef(
            name=name,
            path=None,
            synthetic=True,
        ))

    loader = DynamicLoader(
        binary_path=binary_path,
        libraries=libraries,
        max_instructions=max_instructions,
        timeout_ms=timeout_ms,
        enable_fpu=True,
    )
    return loader.run()
