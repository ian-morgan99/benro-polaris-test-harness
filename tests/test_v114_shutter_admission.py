"""Synthetic scheduling test for the v114 Stage-2 ARM admission path.

Run with POLARIS_V114_STAGE2 pointing at the install-manifest-matched
pgphoto.stage2ondisk. The private ELF is not a test asset in this checkout.
Physical reference: benro-polaris-firmware-patcher#235, code 261 index 3.
The preview schedule and gphoto responses are synthetic.
"""

import os
from pathlib import Path
from struct import pack, unpack

import pytest
from unicorn import UC_PROT_EXEC, UC_PROT_READ
from unicorn.arm_const import (
    UC_ARM_REG_LR,
    UC_ARM_REG_PC,
    UC_ARM_REG_R0,
    UC_ARM_REG_R1,
    UC_ARM_REG_SP,
)

from polaris_harness.arm_emulator.dynamic_loader import DynamicLoader

V114_INSTALL_SHA256 = "f167e66864479060af82f012891a9df5145d14cbcb735b66a0d2317a22a082ec"
CAMERA_CONTROL_HANDLER = 0x100714
UPDATE_LIVE_PREVIEW = 0x102B5C
GP_PARAMS = 0x3787F8
CAMERA_STATUS = GP_PARAMS + 0xAB0
VIEWFINDER_ENABLED = GP_PARAMS + 0xAB4

# Dummy allocations in the dynamic loader's mapped heap. Two separate stack
# frames share the same ARM memory and firmware globals.
ARGUMENT = 0x1FE01000
DUPLICATE = 0x1FE01100
CAMERA = 0x1FE02000
FILE = 0x1FE03000
PREVIEW_DATA = 0x1FE04000
RETURN_PC = 0x60000000
GP_ERROR_IO = -7
IDLE_DIAGNOSTIC = b"--- waitCameraIdle busy  "


@pytest.fixture
def v114_binary():
    path = os.environ.get("POLARIS_V114_STAGE2")
    if not path:
        pytest.skip("set POLARIS_V114_STAGE2 to the private v114 install artifact")
    return Path(path)


class ShutterFixture(DynamicLoader):
    """Execute the real wait, status changes and indexed-command dispatch."""

    def __init__(self, path):
        super().__init__(path, max_instructions=40_000, timeout_ms=1000)
        assert self.binary_sha256 == V114_INSTALL_SHA256, (
            f"Expected v114 install SHA-256 {V114_INSTALL_SHA256}, "
            f"got {self.binary_sha256}"
        )
        self.entry_address = RETURN_PC
        self.sleeps = []
        self.diagnostics = []
        self.dispatched = []
        self.preview_calls = []
        self.preview_cleanups = []
        self.setter_result = 0
        self.yield_after_sleep = None
        self.yield_to = None
        result = self.run()
        assert result.result == "pass", result.error_message
        assert result.relocations_resolved > 0

    def _register_sidecar_stub_entries(self):
        uc = self._uc
        # Stop loader startup at a fixture return address. Every actual
        # function invocation below must independently return or yield.
        uc.mem_map(RETURN_PC, 0x1000, UC_PROT_READ | UC_PROT_EXEC)
        uc.mem_write(RETURN_PC, pack("<I", 0xE1A00000))  # ARM nop
        self._write_word(GP_PARAMS, CAMERA)
        uc.mem_write(VIEWFINDER_ENABLED, b"\x01")
        uc.mem_write(ARGUMENT, b"shutterspeed=3\0")
        boundaries = {
            0x14C54: self._free,
            0x14D80: self._puts,
            0x14E7C: self._memset,
            0x1541C: self._usleep,
            0x15698: self._strchr,
            0x156E0: self._strdup,
            0xFDBA8: self._not_fuji,
            0xF4018: self._set_config_index,
            0x35B84: self._file_new,
            0x2F218: self._capture_preview,
            0x360D8: self._file_unref,
        }
        for address, handler in boundaries.items():
            self.register_stub(handler.__name__, handler)
            self.register_stub_entry(handler.__name__, address, model="target")

    def _hook_code(self, uc, address, size, user_data):
        if address == RETURN_PC:
            uc.emu_stop()
            return
        super()._hook_code(uc, address, size, user_data)
        if self.yield_to is not None:
            uc.emu_stop()

    def _write_word(self, address, value):
        self._uc.mem_write(address, pack("<I", value))

    def status(self):
        return unpack("<I", self._uc.mem_read(CAMERA_STATUS, 4))[0]

    def _c_string(self, address):
        data = self._uc.mem_read(address, 128)
        return bytes(data[:data.index(0)])

    def _memset(self, _, args, _regs):
        self._uc.mem_write(args[0], bytes([args[1]]) * args[2])
        return args[0]

    def _puts(self, _, args, _regs):
        self.diagnostics.append(self._c_string(args[0]))
        return 0

    def _usleep(self, _, args, _regs):
        self.sleeps.append(args[0])
        if len(self.sleeps) == self.yield_after_sleep:
            self.yield_to = "release_preview"
        return 0

    def _strchr(self, _, args, _regs):
        offset = (self._c_string(args[0]) + b"\0").find(bytes([args[1]]))
        return args[0] + offset if offset >= 0 else 0

    def _strdup(self, _, args, _regs):
        self._uc.mem_write(DUPLICATE, self._c_string(args[0]) + b"\0")
        return DUPLICATE

    def _free(self, _, args, _regs):
        assert args[0] == DUPLICATE
        return 0

    def _not_fuji(self, _, args, _regs):
        assert args[0] == 5
        return 0

    def _set_config_index(self, _, args, _regs):
        assert args[0] == GP_PARAMS and self.status() == 1
        self.dispatched.append((self._c_string(args[1]), self._c_string(args[2])))
        return self.setter_result & 0xFFFFFFFF

    def _file_new(self, _, args, _regs):
        self._write_word(args[0], FILE)
        return 0

    def _capture_preview(self, _, args, _regs):
        assert args[:2] == [CAMERA, FILE] and self.status() == 1
        self.preview_calls.append(args[:2])
        self.yield_to = "hold_preview"
        return GP_ERROR_IO & 0xFFFFFFFF

    def _file_unref(self, _, args, _regs):
        assert args[0] == FILE and self.status() == 1
        self.preview_cleanups.append(FILE)
        return 0

    def invoke(self, entry, stack_top, r0, r1=0):
        uc = self._uc
        uc.reg_write(UC_ARM_REG_SP, stack_top)
        uc.reg_write(UC_ARM_REG_LR, RETURN_PC)
        uc.reg_write(UC_ARM_REG_R0, r0)
        uc.reg_write(UC_ARM_REG_R1, r1)
        return self.execute(entry)

    def execute(self, entry):
        uc = self._uc
        self.yield_to = None
        uc.emu_start(entry, RETURN_PC, timeout=self.timeout_ms * 1000)
        assert not self._instruction_limit_exceeded
        if self.yield_to is not None:
            return uc.context_save()
        assert uc.reg_read(UC_ARM_REG_PC) == RETURN_PC
        return unpack("<i", pack("<I", uc.reg_read(UC_ARM_REG_R0)))[0]

    def resume(self, context):
        self._uc.context_restore(context)
        return self.execute(self._uc.reg_read(UC_ARM_REG_PC))

    def shutter(self):
        return self.invoke(CAMERA_CONTROL_HANDLER, 0x2000F000, 0x1F, ARGUMENT)

    def preview(self):
        return self.invoke(UPDATE_LIVE_PREVIEW, 0x20008000, PREVIEW_DATA)


def test_idle_and_downstream_refusal(v114_binary):
    fixture = ShutterFixture(v114_binary)
    assert fixture.shutter() == 0
    assert fixture.dispatched == [(b"shutterspeed", b"3")]
    assert not fixture.sleeps and fixture.status() == 0

    fixture.setter_result = GP_ERROR_IO
    assert fixture.shutter() == GP_ERROR_IO
    assert fixture.dispatched == [(b"shutterspeed", b"3")] * 2
    assert not fixture.sleeps and not fixture.diagnostics and fixture.status() == 0


def test_absent_camera_rejects_without_polling(v114_binary):
    fixture = ShutterFixture(v114_binary)
    fixture._write_word(GP_PARAMS, 0)
    assert fixture.shutter() == -2110
    assert not fixture.sleeps and not fixture.dispatched
    assert fixture.diagnostics == [IDLE_DIAGNOSTIC]


@pytest.mark.parametrize("release_after", [499, 500, None], ids=["998ms", "1000ms", "held"])
def test_real_preview_contends_with_shutter(v114_binary, release_after):
    fixture = ShutterFixture(v114_binary)
    preview = fixture.preview()
    assert fixture.yield_to == "hold_preview" and fixture.status() == 1
    assert not fixture.sleeps

    fixture.yield_after_sleep = release_after
    shutter = fixture.shutter()
    if release_after is not None:
        assert fixture.yield_to == "release_preview"
        assert not fixture.dispatched
        assert fixture.resume(preview) == GP_ERROR_IO
        assert fixture.status() == 0
        shutter = fixture.resume(shutter)

    expected_ret = 0 if release_after == 499 else -2110
    assert shutter == expected_ret
    assert fixture.sleeps == [2000] * (release_after or 500)
    assert fixture.diagnostics == ([IDLE_DIAGNOSTIC] if expected_ret else [])
    assert fixture.dispatched == ([] if expected_ret else [(b"shutterspeed", b"3")])

    if release_after is None:
        assert fixture.status() == 1
        assert fixture.resume(preview) == GP_ERROR_IO
    assert fixture.preview_calls == [[CAMERA, FILE]]
    assert fixture.preview_cleanups == [FILE] and fixture.status() == 0

    if expected_ret:
        assert not fixture.dispatched
        assert fixture.shutter() == 0
        assert fixture.dispatched == [(b"shutterspeed", b"3")]
        assert fixture.status() == 0
