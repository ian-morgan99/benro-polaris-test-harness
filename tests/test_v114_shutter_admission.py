"""Synthetic scheduling and native IPC tests for the v114 Stage-2 ARM path.

Run with POLARIS_V114_STAGE2 pointing at the install-manifest-matched
pgphoto.stage2ondisk. The private ELF is not a test asset in this checkout.
Physical reference: benro-polaris-firmware-patcher#235, code 261 index 3.
The preview schedule and gphoto responses are synthetic. The native dispatcher,
indexed setter and reply formatter come from the pinned ELF. Stubs supply libc
calls, gphoto widgets and the boundaries for IPC send and setting refresh.
"""

import os
import re
from pathlib import Path
from struct import pack, unpack

import pytest
from unicorn import UC_PROT_EXEC, UC_PROT_READ
from unicorn.arm_const import (
    UC_ARM_REG_LR,
    UC_ARM_REG_PC,
    UC_ARM_REG_R0,
    UC_ARM_REG_R1,
    UC_ARM_REG_R2,
    UC_ARM_REG_R3,
    UC_ARM_REG_SP,
)

from polaris_harness.arm_emulator.dynamic_loader import DynamicLoader

V114_INSTALL_SHA256 = "f167e66864479060af82f012891a9df5145d14cbcb735b66a0d2317a22a082ec"
CAMERA_CONTROL_HANDLER = 0x100714
UPDATE_LIVE_PREVIEW = 0x102B5C
GP_PARAMS = 0x3787F8
CAMERA_STATUS = GP_PARAMS + 0xAB0
VIEWFINDER_ENABLED = GP_PARAMS + 0xAB4
CAMERA_MESSAGE_DISPATCH = 0x16DAC
SHUTTER_SET_COMMAND = 261
BEFORE_SHUTTER_CLAIMS_BUSY = 0x100558
SP_CAMERA = 0x3787DC

# Dummy allocations in the dynamic loader's mapped heap. Two separate stack
# frames share the same ARM memory and firmware globals.
ARGUMENT = 0x1FE01000
DUPLICATE = 0x1FE01100
CAMERA = 0x1FE02000
FILE = 0x1FE03000
PREVIEW_DATA = 0x1FE04000
CAMERA_SERVICE = 0x1FE05000
WIDGET = 0x1FE06000
WIDGET_LABEL = 0x1FE06100
WIDGET_CHOICE = 0x1FE06200
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
        self.preview_cleanup_statuses = []
        self.setter_result = 0
        self.yield_after_sleep = None
        self.pause_before_busy_claim = False
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
        self._register_boundaries({
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
        })

    def _register_boundaries(self, boundaries):
        for address, handler in boundaries.items():
            self.register_stub(handler.__name__, handler)
            self.register_stub_entry(handler.__name__, address, model="target")

    def _hook_code(self, uc, address, size, user_data):
        if address == RETURN_PC:
            uc.emu_stop()
            return
        if address == BEFORE_SHUTTER_CLAIMS_BUSY and self.pause_before_busy_claim:
            self.pause_before_busy_claim = False
            self.yield_to = "before_busy_claim"
            uc.emu_stop()
            return
        super()._hook_code(uc, address, size, user_data)
        if self.yield_to is not None:
            uc.emu_stop()

    def _write_word(self, address, value):
        self._uc.mem_write(address, pack("<I", value))

    def status(self):
        return self._read_word(CAMERA_STATUS)

    def _read_word(self, address):
        return unpack("<I", self._uc.mem_read(address, 4))[0]

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
        assert args[0] == FILE
        self.preview_cleanups.append(FILE)
        self.preview_cleanup_statuses.append(self.status())
        return 0

    def invoke(self, entry, stack_top, r0, r1=0, r2=0, r3=0):
        uc = self._uc
        uc.reg_write(UC_ARM_REG_SP, stack_top)
        uc.reg_write(UC_ARM_REG_LR, RETURN_PC)
        uc.reg_write(UC_ARM_REG_R0, r0)
        uc.reg_write(UC_ARM_REG_R1, r1)
        uc.reg_write(UC_ARM_REG_R2, r2)
        uc.reg_write(UC_ARM_REG_R3, r3)
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
    assert fixture.preview_cleanup_statuses == [1]

    if expected_ret:
        assert not fixture.dispatched
        assert fixture.shutter() == 0
        assert fixture.dispatched == [(b"shutterspeed", b"3")]
        assert fixture.status() == 0


def test_preview_can_enter_between_idle_check_and_shutter_claim(v114_binary):
    fixture = ShutterFixture(v114_binary)
    fixture.pause_before_busy_claim = True
    shutter = fixture.shutter()
    assert fixture.yield_to == "before_busy_claim"
    assert fixture.status() == 0 and not fixture.dispatched

    preview = fixture.preview()
    assert fixture.yield_to == "hold_preview" and fixture.status() == 1

    # Resume after the shutter's successful idle check. The two callers now
    # share the flag, and completing the setter clears it while preview is held.
    assert fixture.resume(shutter) == 0
    assert fixture.dispatched == [(b"shutterspeed", b"3")]
    assert fixture.status() == 0 and not fixture.preview_cleanups
    assert not fixture.sleeps

    assert fixture.resume(preview) == GP_ERROR_IO
    assert fixture.preview_cleanup_statuses == [0]


class NativeShutterFixture(ShutterFixture):
    """Run from the decoded native IPC message to the outgoing IPC reply."""

    def _register_sidecar_stub_entries(self):
        super()._register_sidecar_stub_entries()
        self._write_word(SP_CAMERA, CAMERA_SERVICE)
        # Ready state and the run-time config table's shutter name, indexed by
        # configType 3. The wrapper and getConfigName read these real offsets.
        self._write_word(CAMERA_SERVICE + 0xD0, 2)
        self._uc.mem_write(GP_PARAMS + 0xBCC + 3 * 64, b"shutterspeed\0")
        self.widget_reads = []
        self.camera_writes = []
        self.widget_frees = []
        self.refreshes = []
        self.replies = []
        self.choice_count = 4
        self._register_boundaries({
            0x14DE0: self._atoi,
            0x1505C: self._strcpy,
            0x154F4: self._strtok_r,
            0x15464: self._sprintf_result,
            0x165A4: self._send_ipc,
            0x14F0C: self._snprintf_pair,
            0x15554: self._sscanf_int,
            0x15668: self._dgettext,
            0x35610: self._context_error,
            0x1ECE94: self._log_print,
            0x1DF94: self._update_capture_info,
            0x2A600: self._get_single_config,
            0x2C52C: self._set_single_config,
            0x4290C: self._widget_type,
            0x429A4: self._widget_label,
            0x43BEC: self._count_choices,
            0x43CD4: self._get_choice,
            0x42A3C: self._set_widget_value,
            0x421C0: self._widget_free,
        })
        # Execute the real indexed parser, bounds checks, and callback chain.
        del self._stub_entries[0xF4018]

    def set_shutter_index(self, index):
        payload = b"s:" + index + b";\0"
        assert len(payload) <= 8
        payload_words = unpack("<II", payload.ljust(8, b"\0"))
        # The dispatcher takes the header and first eight payload bytes in r0-r3.
        # Longer commands also need the stack tail and are outside this fixture.
        self.invoke(
            CAMERA_MESSAGE_DISPATCH, 0x2000F000, 2, SHUTTER_SET_COMMAND,
            *payload_words,
        )

    def _atoi(self, _, args, _regs):
        match = re.match(rb"\s*[+-]?\d+", self._c_string(args[0]))
        return int(match[0]) & 0xFFFFFFFF if match else 0

    def _strcpy(self, _, args, _regs):
        self._uc.mem_write(args[0], self._c_string(args[1]) + b"\0")
        return args[0]

    def _strtok_r(self, _, args, _regs):
        start, delim, save, _ = args
        delimiter = self._c_string(delim)
        assert start and delimiter in (b";", b":")
        text = self._c_string(start)
        first = start + len(text) - len(text.lstrip(delimiter))
        token = self._c_string(first)
        if not token:
            self._write_word(save, first)
            return 0
        offset = token.find(delimiter)
        if offset < 0:
            self._write_word(save, first + len(token))
        else:
            self._uc.mem_write(first + offset, b"\0")
            self._write_word(save, first + offset + 1)
        return first

    def _sprintf_result(self, _, args, _regs):
        target, fmt, payload, result = args
        assert self._c_string(fmt) == b"%sret:%d;"
        signed = unpack("<i", pack("<I", result))[0]
        text = self._c_string(payload) + b"ret:%d;" % signed
        self._uc.mem_write(target, text + b"\0")
        return len(text)

    def _send_ipc(self, _, args, _regs):
        self.replies.append((args[0], self._c_string(args[1])))
        return 0

    def preview_dispatch_enabled(self):
        return self._read_word(CAMERA_SERVICE + 0xB4)

    def enable_preview_dispatch(self):
        self._write_word(CAMERA_SERVICE + 0xB4, 1)

    def _usleep(self, name, args, regs):
        assert self.preview_dispatch_enabled() == 0
        return super()._usleep(name, args, regs)

    def _snprintf_pair(self, _, args, regs):
        target, size, fmt, name = args
        assert self._c_string(fmt) == b"%s=%s"
        value = self._read_word(regs["SP"])
        text = self._c_string(name) + b"=" + self._c_string(value)
        assert len(text) < size
        self._uc.mem_write(target, text + b"\0")
        return len(text)

    def _sscanf_int(self, _, args, _regs):
        assert self._c_string(args[1]) == b"%d"
        match = re.match(rb"\s*[+-]?\d+", self._c_string(args[0]))
        if match is None:
            return 0
        self._write_word(args[2], int(match[0]) & 0xFFFFFFFF)
        return 1

    def _dgettext(self, _, args, _regs):
        return args[1]

    def _context_error(self, _, _args, _regs):
        return 0

    def _log_print(self, _, args, _regs):
        assert args[3] == 0x2F2
        return 0

    def _update_capture_info(self, _, args, _regs):
        assert args[:2] == [3, GP_PARAMS + 0x78]
        self.refreshes.append(args[0])
        return 0

    def _get_single_config(self, _, args, _regs):
        assert args[0] == CAMERA and self.status() == 1
        assert self.preview_dispatch_enabled() == 0
        self.widget_reads.append(self._c_string(args[1]))
        self._write_word(args[2], WIDGET)
        return 0

    def _widget_type(self, _, args, _regs):
        assert args[0] == WIDGET
        self._write_word(args[1], 6)  # GP_WIDGET_MENU
        return 0

    def _widget_label(self, _, args, _regs):
        assert args[0] == WIDGET
        self._uc.mem_write(WIDGET_LABEL, b"Shutter Speed\0")
        self._write_word(args[1], WIDGET_LABEL)
        return 0

    def _count_choices(self, _, args, _regs):
        assert args[0] == WIDGET
        return self.choice_count

    def _get_choice(self, _, args, _regs):
        assert args[:2] == [WIDGET, 3]
        self._uc.mem_write(WIDGET_CHOICE, b"15\0")
        self._write_word(args[2], WIDGET_CHOICE)
        return 0

    def _set_widget_value(self, _, args, _regs):
        assert args[:2] == [WIDGET, WIDGET_CHOICE]
        return 0

    def _set_single_config(self, _, args, _regs):
        assert args[0] == CAMERA and args[2] == WIDGET and self.status() == 1
        self.camera_writes.append(self._c_string(args[1]))
        return self.setter_result & 0xFFFFFFFF

    def _widget_free(self, _, args, _regs):
        assert args[0] == WIDGET
        self.widget_frees.append(WIDGET)
        return 0


@pytest.mark.parametrize(
    ("index", "choices", "camera_ret", "expected", "writes"),
    [
        (b"3", 4, 0, 0, 1),
        (b"3", 4, GP_ERROR_IO, GP_ERROR_IO, 1),
        (b"3", 0, 0, -2, 0),
        (b"4", 4, 0, -2, 0),
        (b"-1", 4, 0, -2, 0),
        (b"x", 4, 0, -2, 0),
    ],
    ids=["valid", "camera-refusal", "empty-domain", "past-end", "negative", "text"],
)
def test_native_shutter_dispatch_checks_widget_domain(
    v114_binary, index, choices, camera_ret, expected, writes
):
    fixture = NativeShutterFixture(v114_binary)
    fixture.choice_count = choices
    fixture.setter_result = camera_ret
    assert fixture.preview_dispatch_enabled() == 0
    fixture.set_shutter_index(index)
    assert fixture.replies == [(261, b"s:" + index + b";ret:%d;" % expected)]
    assert fixture.widget_reads == [b"shutterspeed"]
    assert fixture.camera_writes == [b"shutterspeed"] * writes
    assert fixture.widget_frees == [WIDGET]
    assert fixture.refreshes == ([3] if expected == 0 else [])
    assert fixture.status() == 0
    assert fixture.preview_dispatch_enabled() == 1


def test_native_shutter_masks_dispatch_without_draining_inflight_preview(v114_binary):
    fixture = NativeShutterFixture(v114_binary)
    fixture.enable_preview_dispatch()
    preview = fixture.preview()
    assert fixture.status() == 1 and fixture.preview_dispatch_enabled() == 1

    fixture.set_shutter_index(b"3")
    assert fixture.replies == [(261, b"s:3;ret:-2110;")]
    assert not fixture.widget_reads and not fixture.camera_writes
    assert fixture.sleeps == [2000] * 500
    assert fixture.preview_dispatch_enabled() == 1
    assert fixture.status() == 1 and not fixture.preview_cleanups

    assert fixture.resume(preview) == GP_ERROR_IO
    assert fixture.status() == 0
    fixture.set_shutter_index(b"3")
    assert fixture.replies == [(261, b"s:3;ret:-2110;"), (261, b"s:3;ret:0;")]
    assert fixture.camera_writes == [b"shutterspeed"]
