"""Fixture-free tests for ARM fixture loading and memory seeding.

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

The ARM coverage gate measures `arm_emulator` but most of its exercised paths
need the gitignored region binaries, so on a runner the same commit reports 78%
against an 80% threshold (issue #26). The uncovered bulk is validation and
environment-seeding logic that needs no firmware at all - it just needs
synthetic inputs.

These tests build throwaway fixture directories in tmp_path. They target the
fail-closed paths specifically, because those are what prevent a run from
reporting results against an environment it does not actually have: a wrong
source binary, a patch outside any mapping, a short read. A bug there produces a
green-looking run over zero-filled globals, which is the failure mode the whole
provenance design exists to avoid.
"""

import hashlib
import json
from pathlib import Path

import pytest

from polaris_harness.arm_emulator.emulator import (
    ARMEmulator,
    DataRegion,
    EmulationConfig,
    MemoryPatch,
    PAGE_SIZE,
)
from polaris_harness.arm_emulator.fixtures import FixtureManager


def _write_fixture(
    fixtures_dir: Path, name: str, payload: bytes, sidecar: dict | None = None,
) -> Path:
    """Create a fixture binary and optional sidecar in a throwaway directory.

    Args:
        fixtures_dir: Directory to write into
        name: Fixture stem
        payload: Bytes for the .bin file
        sidecar: Optional provenance dict written as <name>.json

    Returns:
        Path to the created binary
    """
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    blob = fixtures_dir / f"{name}.bin"
    blob.write_bytes(payload)
    if sidecar is not None:
        (fixtures_dir / f"{name}.json").write_text(json.dumps(sidecar))
    return blob


def _sha256(data: bytes) -> str:
    """Hex SHA-256 of a byte string."""
    return hashlib.sha256(data).hexdigest()


class TestDiscoveryWithoutFirmware:
    """Fixture discovery must work and fail closed using only local files."""

    def test_missing_directory_yields_no_fixtures(self, tmp_path):
        """A nonexistent fixtures dir is empty, not an error.

        Callers distinguish "no fixtures configured" from "fixtures broken";
        raising here would make a fresh clone look like a corrupt checkout.
        """
        manager = FixtureManager(tmp_path / "absent")
        assert manager.list_fixtures() == {}

    def test_binary_without_sidecar_is_discovered(self, tmp_path):
        """A bare .bin is still a fixture, with no execution parameters."""
        payload = b"\x00\x00\xa0\xe1"
        _write_fixture(tmp_path, "bare", payload)

        fixture = FixtureManager(tmp_path).get_fixture("bare")

        assert fixture is not None
        assert fixture.sha256 == _sha256(payload)
        assert fixture.size_bytes == len(payload)
        assert fixture.mode is None
        assert fixture.data_regions == []

    def test_malformed_sidecar_does_not_raise(self, tmp_path):
        """Unparseable sidecar JSON must not crash discovery.

        A corrupt sidecar alongside a valid binary should still surface the
        binary; the missing execution parameters make it unusable for running,
        which callers report as a skip rather than a load failure.
        """
        _write_fixture(tmp_path, "corrupt", b"\x00" * 8)
        (tmp_path / "corrupt.json").write_text("{not json at all")

        fixture = FixtureManager(tmp_path).get_fixture("corrupt")

        assert fixture is not None
        assert fixture.mode is None

    def test_non_dict_sidecar_entries_are_dropped(self, tmp_path):
        """Garbage entries in provenance lists must be filtered, not trusted.

        A stub entry that is a string rather than an object would otherwise reach
        registration and fail there with an unhelpful error. Non-string alias
        values are dropped for the same reason: an assertion looking up a symbol
        name would receive an int and silently never match.

        Note the key filter on boundary_aliases is unreachable through parsed
        JSON, since JSON object keys are always strings - it only guards fixtures
        built programmatically.
        """
        _write_fixture(tmp_path, "mixed", b"\x00" * 8, {
            "provenance": {
                "stub_entries": [{"address": "0x100", "stub_name": "s"}, "junk", 5],
                "boundary_aliases": {"stop": "stopCapture", "k": 7, "n": None},
            }
        })

        fixture = FixtureManager(tmp_path).get_fixture("mixed")

        assert len(fixture.stub_entries) == 1
        assert fixture.stub_entries[0]["stub_name"] == "s"
        assert fixture.boundary_aliases == {"stop": "stopCapture"}

    def test_verify_fixture_unknown_name_is_false(self, tmp_path):
        """Verifying an absent fixture is False, not KeyError.

        Used by callers deciding whether to skip; raising would turn a missing
        private artifact into a crash.
        """
        manager = FixtureManager(tmp_path)
        assert manager.verify_fixture("nope", "0" * 64) is False

    def test_verify_fixture_matches_recorded_digest(self, tmp_path):
        """A correct digest verifies True and a wrong one False."""
        payload = b"\x01\x02\x03\x04"
        _write_fixture(tmp_path, "ok", payload)
        manager = FixtureManager(tmp_path)

        assert manager.verify_fixture("ok", _sha256(payload)) is True
        assert manager.verify_fixture("ok", "f" * 64) is False


class TestCoerceInt:
    """Sidecar values arrive as hex strings and must not be silently misread."""

    @pytest.mark.parametrize("value,expected", [
        ("0x101aac", 0x101aac),
        ("65536", 65536),
        (4096, 4096),
        (None, None),
        ("not a number", None),
        (True, None),          # bool is an int subclass; accepting it would let
        (False, None),         # "enabled: false" read as address 0
        ([1], None),           # wrong type must not become 0
        ({"a": 1}, None),
    ])
    def test_coerce_int(self, value, expected):
        """Hex, decimal and int forms coerce; unusable forms return None."""
        assert FixtureManager._coerce_int(value) == expected


class TestSourceBinaryResolution:
    """Global seeding reads the retained source binary; wrong bytes must abort.

    These are the paths that decide whether a run describes the artifact under
    test. A silent fallback to zero-filled globals reproduces exactly the fault
    the patches exist to remove, and the run would then look valid.
    """

    def _fixture_needing_source(self, tmp_path) -> Path:
        """Fixture declaring a memory patch, so it requires the source binary."""
        _write_fixture(tmp_path, "needs", b"\x00" * 8, {
            "provenance": {
                "mode": "arm",
                "source_binary_name": "pgphoto.stage2ondisk",
                "memory_patches": [{
                    "address": "0x1000", "size": "0x10",
                    "source_offset": "0x0",
                }],
            }
        })
        return tmp_path

    def test_missing_root_raises_with_actionable_message(self, tmp_path, monkeypatch):
        """No POLARIS_FIRMWARE_ROOT must say what to set it to."""
        monkeypatch.delenv("POLARIS_FIRMWARE_ROOT", raising=False)
        manager = FixtureManager(self._fixture_needing_source(tmp_path))

        with pytest.raises(ValueError, match="POLARIS_FIRMWARE_ROOT"):
            manager.to_emulation_config(
                "needs", patcher_commit="t", harness_revision="0.1.0",
                emulator_version="u", stub_profile="lifecycle", command="c",
            )

    def test_root_without_binary_raises(self, tmp_path, monkeypatch):
        """A root that lacks the named binary must name the path it looked for."""
        empty = tmp_path / "root"
        empty.mkdir()
        monkeypatch.setenv("POLARIS_FIRMWARE_ROOT", str(empty))
        manager = FixtureManager(self._fixture_needing_source(tmp_path))

        with pytest.raises(ValueError, match="does not exist"):
            manager.to_emulation_config(
                "needs", patcher_commit="t", harness_revision="0.1.0",
                emulator_version="u", stub_profile="lifecycle", command="c",
            )

    def test_hash_mismatch_raises(self, tmp_path, monkeypatch):
        """A source binary that differs from the recorded hash must abort.

        Seeding globals from a different build produces results that do not
        describe this artifact, which is worse than not running at all.
        """
        root = tmp_path / "root"
        root.mkdir()
        (root / "pgphoto.stage2ondisk").write_bytes(b"totally different bytes")
        monkeypatch.setenv("POLARIS_FIRMWARE_ROOT", str(root))

        _write_fixture(tmp_path, "hash", b"\x00" * 8, {
            "provenance": {
                "mode": "arm",
                "source_binary_sha256": "a" * 64,
                "memory_patches": [{
                    "address": "0x1000", "size": "0x10", "source_offset": "0x0",
                }],
            }
        })
        manager = FixtureManager(tmp_path)

        with pytest.raises(ValueError, match="SHA-256"):
            manager.to_emulation_config(
                "hash", patcher_commit="t", harness_revision="0.1.0",
                emulator_version="u", stub_profile="lifecycle", command="c",
            )

    def test_source_binary_available_is_non_raising(self, tmp_path, monkeypatch):
        """The availability probe reports the reason instead of raising.

        Callers need to skip with an explanation; the raising variant is for
        execution paths that genuinely cannot proceed.
        """
        monkeypatch.delenv("POLARIS_FIRMWARE_ROOT", raising=False)
        manager = FixtureManager(self._fixture_needing_source(tmp_path))

        available, reason = manager.source_binary_available("needs")
        assert available is False
        assert "POLARIS_FIRMWARE_ROOT" in reason

    def test_fixture_without_patches_needs_no_source(self, tmp_path, monkeypatch):
        """A fixture with no seeding declares itself available regardless of env.

        Otherwise a plain region would be skipped merely because the private
        firmware root is unset.
        """
        monkeypatch.delenv("POLARIS_FIRMWARE_ROOT", raising=False)
        _write_fixture(tmp_path, "plain", b"\x00" * 8, {"provenance": {"mode": "arm"}})

        available, reason = FixtureManager(tmp_path).source_binary_available("plain")
        assert available is True
        assert reason == ""

    def test_unknown_fixture_reports_not_found(self, tmp_path):
        """Probing an absent fixture says so rather than claiming it is usable."""
        available, reason = FixtureManager(tmp_path).source_binary_available("ghost")
        assert available is False
        assert "not found" in reason


class TestSidecarEnvironmentBuilding:
    """Malformed region/patch declarations must be rejected before the run."""

    def test_data_region_missing_address_raises(self, tmp_path):
        """A data region without an address cannot be mapped; refuse to guess."""
        _write_fixture(tmp_path, "dr", b"\x00" * 8, {
            "provenance": {"mode": "arm", "data_regions": [{"size": "0x1000"}]}
        })
        manager = FixtureManager(tmp_path)

        with pytest.raises(ValueError, match="missing address/size"):
            manager.to_emulation_config(
                "dr", patcher_commit="t", harness_revision="0.1.0",
                emulator_version="u", stub_profile="lifecycle", command="c",
            )

    def test_memory_patch_missing_address_raises(self, tmp_path, monkeypatch):
        """A patch without an address would be written somewhere arbitrary.

        The source binary is resolved before the fields are validated, so a
        resolvable source is provided to reach the field check itself rather than
        the earlier environment error.
        """
        root = tmp_path / "root"
        root.mkdir()
        (root / "pgphoto.stage2ondisk").write_bytes(b"\x00" * 64)
        monkeypatch.setenv("POLARIS_FIRMWARE_ROOT", str(root))

        _write_fixture(tmp_path, "mp", b"\x00" * 8, {
            "provenance": {"mode": "arm", "memory_patches": [{"size": "0x10",
                                                              "source_offset": "0x0"}]}
        })
        manager = FixtureManager(tmp_path)

        with pytest.raises(ValueError, match="missing address/size"):
            manager.to_emulation_config(
                "mp", patcher_commit="t", harness_revision="0.1.0",
                emulator_version="u", stub_profile="lifecycle", command="c",
            )

    def test_sidecar_environment_can_be_disabled(self, tmp_path, monkeypatch):
        """Disabling seeding must not attempt to resolve the source binary.

        Lets a caller reproduce the unseeded run deliberately; if it still
        resolved the source it would raise and the option would be unusable.
        """
        monkeypatch.delenv("POLARIS_FIRMWARE_ROOT", raising=False)
        _write_fixture(tmp_path, "opt", b"\x00" * 8, {
            "provenance": {
                "mode": "arm",
                "memory_patches": [{
                    "address": "0x1000", "size": "0x10", "source_offset": "0x0",
                }],
            }
        })
        manager = FixtureManager(tmp_path)

        config = manager.to_emulation_config(
            "opt", patcher_commit="t", harness_revision="0.1.0",
            emulator_version="u", stub_profile="lifecycle", command="c",
            apply_sidecar_environment=False,
        )
        assert config.memory_patches == []

    def test_entry_arguments_come_from_sidecar(self, tmp_path):
        """Declared arguments reach the config so the entry frame is real."""
        _write_fixture(tmp_path, "args", b"\x00" * 8, {
            "provenance": {"mode": "arm", "entry_arguments": ["0x1", 2, "bad"]}
        })

        config = FixtureManager(tmp_path).to_emulation_config(
            "args", patcher_commit="t", harness_revision="0.1.0",
            emulator_version="u", stub_profile="lifecycle", command="c",
        )
        assert config.entry_arguments == [1, 2]


class TestRegionAndPatchValidation:
    """Dataclasses must reject descriptions Unicorn cannot act on."""

    def test_data_region_rejects_unaligned_address(self):
        """Unicorn maps whole pages, so an unaligned address is unusable."""
        with pytest.raises(ValueError, match="page aligned"):
            DataRegion(address=0x1001, size=PAGE_SIZE).validate()

    def test_data_region_rejects_bad_size(self):
        """A zero or non-multiple size cannot be mapped."""
        with pytest.raises(ValueError, match="positive multiple"):
            DataRegion(address=0x1000, size=0).validate()
        with pytest.raises(ValueError, match="positive multiple"):
            DataRegion(address=0x1000, size=PAGE_SIZE + 1).validate()

    def test_data_region_rejects_out_of_range_dest_offset(self):
        """Writing past the region end would touch unmapped memory."""
        with pytest.raises(ValueError, match="dest_offset"):
            DataRegion(address=0x1000, size=PAGE_SIZE, dest_offset=PAGE_SIZE).validate()

    def test_data_region_rejects_negative_source_offset(self):
        """A negative file offset is meaningless and must not seek silently."""
        with pytest.raises(ValueError, match="source_offset"):
            DataRegion(
                address=0x1000, size=PAGE_SIZE,
                source_path=Path("/dev/null"), source_offset=-1,
            ).validate()

    def test_data_region_accepts_valid_description(self):
        """A well-formed region validates without raising."""
        DataRegion(
            address=0x1000, size=PAGE_SIZE * 2, dest_offset=4,
        ).validate()

    def test_memory_patch_rejects_non_positive_size(self):
        """A zero-size patch would write nothing while claiming to seed a global."""
        with pytest.raises(ValueError, match="must be positive"):
            MemoryPatch(address=0x1000, source_path=Path("/dev/null"),
                        source_offset=0, size=0).validate()

    def test_memory_patch_rejects_negative_source_offset(self):
        """A negative offset must not silently seek to an arbitrary position."""
        with pytest.raises(ValueError, match="source_offset"):
            MemoryPatch(address=0x1000, source_path=Path("/dev/null"),
                        source_offset=-4, size=4).validate()


def _config(tmp_path: Path, code: bytes, **overrides) -> EmulationConfig:
    """Minimal config executing ``code`` at 0x1000."""
    blob = tmp_path / "probe.bin"
    blob.write_bytes(code)
    defaults = dict(
        binary_path=blob,
        binary_sha256=_sha256(code),
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


NOP = bytes.fromhex("0000a0e1")


def _initialized(tmp_path: Path, code: bytes, **overrides) -> ARMEmulator:
    """Build an emulator with Unicorn set up but no instructions executed.

    Memory patches and data regions are applied during _initialize_unicorn, so
    inspecting their effect needs the engine without a run. run() is avoided
    because these tests are about environment construction, not behaviour.
    """
    emulator = ARMEmulator(_config(tmp_path, code, **overrides))
    emulator._initialize_unicorn()
    return emulator


class TestMemoryPatchApplication:
    """Patches must land inside a mapping and read the bytes they claim to."""

    def test_patch_outside_mapping_raises(self, tmp_path):
        """A patch outside any mapped range must abort, not partially apply.

        A partially applied environment produces a run that looks valid but is
        not, which is precisely what these guards exist to prevent.
        """
        source = tmp_path / "src.bin"
        source.write_bytes(b"\x11" * 64)
        config = _config(tmp_path, NOP, memory_patches=[
            MemoryPatch(address=0x900000, source_path=source,
                        source_offset=0, size=4),
        ])

        with pytest.raises(ValueError, match="not fully inside a mapped range"):
            _initialized(tmp_path, NOP, memory_patches=config.memory_patches)

    def test_patch_with_missing_source_raises(self, tmp_path):
        """A missing source must not leave globals zero-filled.

        Zero-filled globals reproduce the fault the patch exists to remove, and
        the run would then be reported as firmware behaviour.
        """
        with pytest.raises(ValueError, match="does not exist"):
            _initialized(tmp_path, NOP, memory_patches=[
                MemoryPatch(address=0x1000, source_path=tmp_path / "absent.bin",
                            source_offset=0, size=4),
            ])

    def test_short_read_raises(self, tmp_path):
        """Reading fewer bytes than declared must abort rather than pad.

        A short read means the offset/size pair does not describe the artifact,
        so the seeded globals would be partly real and partly whatever Unicorn
        zero-filled - indistinguishable from genuine firmware state.
        """
        source = tmp_path / "src.bin"
        source.write_bytes(b"\x22" * 4)

        with pytest.raises(ValueError, match="expected"):
            _initialized(tmp_path, NOP, memory_patches=[
                MemoryPatch(address=0x1000, source_path=source,
                            source_offset=0, size=64),
            ])

    def test_valid_patch_lands_in_memory(self, tmp_path):
        """A well-formed patch must actually write its bytes.

        Without this, every guard above could pass while the write silently did
        nothing and globals stayed zero-filled.
        """
        source = tmp_path / "src.bin"
        payload = bytes.fromhex("78563412")
        source.write_bytes(payload)

        emulator = _initialized(tmp_path, NOP, memory_patches=[
            MemoryPatch(address=0x1000, source_path=source,
                        source_offset=0, size=4),
        ])
        assert bytes(emulator._uc.mem_read(0x1000, 4)) == payload


class TestDataRegionApplication:
    """Extra mappings must be created and seeded from the declared source."""

    def test_data_region_is_mapped_and_seeded(self, tmp_path):
        """A declared region must exist in memory with its real bytes."""
        source = tmp_path / "src.bin"
        payload = bytes.fromhex("aabbccddeeff0011")
        source.write_bytes(payload)

        emulator = _initialized(tmp_path, NOP, data_regions=[
            DataRegion(address=0x300000, size=PAGE_SIZE,
                       source_path=source, source_offset=0, dest_offset=0),
        ])
        assert bytes(emulator._uc.mem_read(0x300000, 8)) == payload

    def test_data_region_dest_offset_is_honoured(self, tmp_path):
        """Bytes must land at address+dest_offset, not the region start.

        Real sidecars use dest_offset to place a section inside a larger mapping;
        ignoring it would seed the wrong addresses while looking successful.
        """
        source = tmp_path / "src.bin"
        payload = bytes.fromhex("deadbeef")
        source.write_bytes(payload)

        emulator = _initialized(tmp_path, NOP, data_regions=[
            DataRegion(address=0x300000, size=PAGE_SIZE,
                       source_path=source, source_offset=0, dest_offset=0x40),
        ])
        assert bytes(emulator._uc.mem_read(0x300000 + 0x40, 4)) == payload
        assert bytes(emulator._uc.mem_read(0x300000, 4)) == b"\x00\x00\x00\x00"

    def test_data_region_fill_is_applied(self, tmp_path):
        """A declared fill byte must be present where no source bytes land.

        Fill distinguishes "provided but zero" from "absent"; ignoring it makes
        a synthetic region indistinguishable from an unmapped one.
        """
        emulator = _initialized(tmp_path, NOP, data_regions=[
            DataRegion(address=0x300000, size=PAGE_SIZE, fill=0x5a),
        ])
        assert bytes(emulator._uc.mem_read(0x300000, 4)) == b"\x5a\x5a\x5a\x5a"

    def test_data_region_empty_source_raises(self, tmp_path):
        """A source yielding no bytes must abort rather than seed nothing.

        A region declared with a source but seeded from an empty file would stay
        zero-filled while reporting success, which is the exact condition the
        region exists to remove.
        """
        source = tmp_path / "src.bin"
        source.write_bytes(b"")

        with pytest.raises(ValueError, match="read 0 bytes"):
            _initialized(tmp_path, NOP, data_regions=[
                DataRegion(address=0x300000, size=PAGE_SIZE,
                           source_path=source, source_offset=0, dest_offset=0),
            ])

    def test_data_region_missing_source_raises(self, tmp_path):
        """A declared source that does not exist must abort, not substitute zeros."""
        with pytest.raises(ValueError, match="does not exist"):
            _initialized(tmp_path, NOP, data_regions=[
                DataRegion(address=0x300000, size=PAGE_SIZE,
                           source_path=tmp_path / "absent.bin"),
            ])

    def test_data_region_partial_read_is_not_rejected(self, tmp_path):
        """A source shorter than the region seeds what it has and continues.

        Recorded deliberately rather than asserted as an error: unlike
        memory_patches, which compares the byte count and aborts, data_regions
        only rejects a zero-byte read. A 2-byte source into a 4096-byte region
        leaves the remainder at fill/zero with no diagnostic. This documents the
        asymmetry so it is not mistaken for the stricter patch behaviour.
        """
        source = tmp_path / "src.bin"
        source.write_bytes(b"\x33\x44")

        emulator = _initialized(tmp_path, NOP, data_regions=[
            DataRegion(address=0x300000, size=PAGE_SIZE,
                       source_path=source, source_offset=0, dest_offset=0),
        ])

        seeded = bytes(emulator._uc.mem_read(0x300000, 4))
        assert seeded[:2] == b"\x33\x44"
        assert seeded[2:] == b"\x00\x00", (
            "remainder is zero-filled silently; memory_patches would have raised"
        )

    def test_data_region_unaligned_address_raises(self, tmp_path):
        """An unaligned region cannot be mapped by Unicorn."""
        with pytest.raises(ValueError, match="page aligned"):
            _initialized(tmp_path, NOP, data_regions=[
                DataRegion(address=0x300001, size=PAGE_SIZE),
            ])


class TestEntryArguments:
    """A region entered mid-binary has no caller frame; arguments must be real."""

    def test_r0_to_r3_receive_values(self, tmp_path):
        """Declared arguments must reach the argument registers.

        Arguments are written during run(), before emu_start, so they are present
        even when the probe code itself faults immediately afterwards.
        """
        emulator = ARMEmulator(_config(tmp_path, NOP,
                                       entry_arguments=[0x11, 0x22, 0x33, 0x44]))
        emulator.run()

        for reg, expected in zip(("R0", "R1", "R2", "R3"), (0x11, 0x22, 0x33, 0x44)):
            const = getattr(emulator._arm_const, f"UC_ARM_REG_{reg}")
            assert emulator._uc.reg_read(const) == expected

    def test_extra_arguments_spill_onto_stack(self, tmp_path):
        """Arguments beyond R0-R3 must be pushed in AAPCS order.

        Without spilling, a five-argument function reads garbage from its fifth
        parameter and branches on it, producing a run that looks meaningful.
        """
        emulator = ARMEmulator(_config(tmp_path, NOP,
                                       entry_arguments=[1, 2, 3, 4, 5, 6]))
        emulator.run()

        sp = emulator._uc.reg_read(emulator._arm_const.UC_ARM_REG_SP)
        # Stacked arguments ascend in address: SP holds the fifth, SP+4 the sixth.
        fifth = int.from_bytes(bytes(emulator._uc.mem_read(sp, 4)), "little")
        sixth = int.from_bytes(bytes(emulator._uc.mem_read(sp + 4, 4)), "little")
        assert (fifth, sixth) == (5, 6), (
            f"AAPCS puts argument 5 at SP and 6 at SP+4; got {fifth}, {sixth}"
        )

    def test_spill_beyond_stack_base_reports_failure(self, tmp_path):
        """Pushing past the stack base must not corrupt memory below the stack.

        run() catches setup errors and returns a fail-closed result rather than
        propagating, so the condition is asserted through the result. What matters
        is that SP was never moved below the stack base: silently writing there
        would fault later in a way that reads as firmware behaviour.

        The count must actually exceed the stack - 25 spilled arguments need only
        100 bytes of a 4096-byte stack and would never reach the guard.
        """
        config = _config(
            tmp_path, NOP,
            entry_arguments=list(range(1, 1101)),
            stack_size=PAGE_SIZE,
        )
        emulator = ARMEmulator(config)
        result = emulator.run()

        assert "stack cannot hold" in (result.error_message or ""), (
            f"expected the spill guard to fire, got {result.error_message!r}"
        )
        sp = emulator._uc.reg_read(emulator._arm_const.UC_ARM_REG_SP)
        assert sp >= config.stack_address, (
            f"SP {hex(sp)} moved below the stack base {hex(config.stack_address)}"
        )
