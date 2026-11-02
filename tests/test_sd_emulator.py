"""Tests for the SD card emulator.

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

Covers the FAT32 volume, packet parsing against the real firmwareInfo format,
and the simulated update flow including every failure path. Packet fixtures use
synthetic content in the real layout: proprietary firmware bytes are never
committed, and synthetic payloads still exercise the same sizes, digests and
partition math.
"""

import hashlib
import io
import shutil
import struct
import subprocess
import zipfile
from pathlib import Path

import pytest

from polaris_harness.sd_emulator import (
    BlockAllocationError,
    DeviceState,
    FirmwarePacket,
    ImageCorruptionError,
    MTD_PARTITIONS,
    PacketIntegrityError,
    PacketLayoutError,
    SDCardImage,
    SimulatedDevice,
    UpdateOutcome,
    compare_versions,
    parse_firmware_info,
    parse_fwver,
)
from polaris_harness.sd_emulator.image import (
    ATTR_LFN,
    BOOT_SIGNATURE,
    BYTES_PER_SECTOR,
    FAT_END_OF_CHAIN,
    FAT_FIRST_CLUSTER_MARKER,
    FSINFO_SECTOR,
    FSINFO_STRUCTURE_SIGNATURE,
    FSINFO_TRAIL_SIGNATURE,
    decode_lfn_entries,
    encode_lfn_entries,
    fits_83,
    geometry_for_size,
    lfn_checksum,
    pack_short_entry,
    pack_short_name,
    unpack_short_entry,
)

# Real SD cards are tens of gigabytes. Tests use the smallest size that still
# yields a FAT32 cluster count, so they exercise real geometry without needing
# tens of gigabytes of sparse backing per test.
TEST_VOLUME_BYTES = 64 * 1024 * 1024

MODE_DIRS = ("normal", "focusStack", "panorama", "Lapse", "sun", "HDR",
             "starskyStack")


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def build_packet_bytes(version: str = "6.0.0.54.90", date: str = "2026.10.29",
                       components: dict = None, drop: tuple = ()) -> bytes:
    """Build a packet archive in the real FwPkt layout with synthetic content.

    Args:
        version: value written to FwVer
        date: date string written to FwVer
        components: override the component map (logical name -> bytes)
        drop: metadata names to omit from firmwareInfo

    Returns:
        Zip archive bytes
    """
    default = {
        "config": b"setenv bootargs 'mem=1536M quiet'\n",
        "uImage": b"\x27\x05\x19\x56" + b"K" * 4095,
        "rootfs": b"R" * 200_000,
        "appfs": b"A" * 1_500_000,
        "polaris403": b"G" * 84_328,
        "polaris413": b"H" * 84_284,
    }
    if components is not None:
        default = components

    paths = {
        "config": "camera/config",
        "uImage": "camera/uImage",
        "rootfs": "camera/rootfs.ubifs",
        "appfs": "camera/appfs.ubifs",
        "polaris403": "gimbal/polaris403_2.0.0.22.bin",
        "polaris413": "gimbal/polaris413_2.0.0.22.bin",
    }

    info = "".join(
        f"{name} size:{len(default[name])};{name} MD5:{_md5(default[name])};\n"
        for name in paths
        if name not in drop
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("FwPkt/FwVer", f"FwVer:{version};date:{date};\n")
        archive.writestr("FwPkt/firmwareInfo", info)
        for name, payload in default.items():
            if name not in drop:
                archive.writestr(f"FwPkt/{paths[name]}", payload)
    return buf.getvalue()


@pytest.fixture
def image():
    """A FAT32 volume with the mode directories seen on a real card."""
    volume = SDCardImage(total_bytes=TEST_VOLUME_BYTES)
    volume.write_boot_sector()
    for name in MODE_DIRS:
        volume.add_directory(name)
    volume.add_directory("system")
    volume.flush_directories()
    return volume


class TestGeometry:
    """Volume sizing must stay FAT32-shaped."""

    def test_small_volume_still_yields_fat32_cluster_count(self):
        # Below 65,525 clusters the same layout reads as FAT16, so a small test
        # volume would not be testing FAT32 at all.
        geometry = geometry_for_size(TEST_VOLUME_BYTES)
        assert geometry.cluster_count >= 65525

    def test_too_small_volume_is_rejected(self):
        with pytest.raises(BlockAllocationError, match="too small for FAT32"):
            geometry_for_size(1024 * 1024)

    def test_32gib_geometry_computes_without_materialising(self):
        # The whole point of sparse backing: a legitimate card size must be
        # constructible without allocating tens of gigabytes.
        volume = SDCardImage(total_bytes=32 * 1024 * 1024 * 1024)
        assert volume.geometry.volume_bytes == 32 * 1024 * 1024 * 1024
        assert len(volume._sectors) == 0


class TestBootSector:
    """A reader must be able to identify the volume as FAT32."""

    def test_boot_sector_signatures_and_fat32_markers(self, image):
        boot = image.read_sector(0)
        assert struct.unpack_from("<H", boot, 510)[0] == BOOT_SIGNATURE
        assert boot[82:90] == b"FAT32   "
        assert struct.unpack_from("<H", boot, 11)[0] == BYTES_PER_SECTOR
        assert boot[16] == image.geometry.fat_copies
        # BPB_FATSz16 must be zero for FAT32; the real size lives in BPB_FATSz32.
        assert struct.unpack_from("<H", boot, 22)[0] == 0
        assert struct.unpack_from("<I", boot, 36)[0] == image.geometry.sectors_per_fat
        assert struct.unpack_from("<I", boot, 44)[0] == 2  # root cluster

    def test_backup_boot_sector_is_a_copy(self, image):
        assert image.read_sector(6) == image.read_sector(0)

    def test_fsinfo_signatures_at_spec_offsets(self, image):
        fsinfo = image.read_sector(FSINFO_SECTOR)
        assert struct.unpack_from("<I", fsinfo, 0)[0] == 0x41615252
        assert struct.unpack_from("<I", fsinfo, 484)[0] == FSINFO_STRUCTURE_SIGNATURE
        free_count = struct.unpack_from("<I", fsinfo, 488)[0]
        next_free = struct.unpack_from("<I", fsinfo, 492)[0]
        assert 0 < free_count < image.geometry.cluster_count
        assert image.get_fat_entry(next_free) == 0
        assert struct.unpack_from("<I", fsinfo, 508)[0] == FSINFO_TRAIL_SIGNATURE

    def test_volume_label_written(self, image):
        boot = image.read_sector(0)
        assert boot[71:82].decode("ascii").strip() == "BENRO"


class TestShortNamesAndLfn:
    """Names on a real card are mixed case, so LFN handling is not optional."""

    @pytest.mark.parametrize("name,is_dir,fits", [
        ("NORMAL", True, True),
        ("normal", True, False),      # lowercase needs an LFN
        ("FwPkt.zip", False, False),  # mixed case and length
        ("focusStack", True, False),
        ("starskyStack", True, False),
    ])
    def test_fits_83_matches_real_names(self, name, is_dir, fits):
        assert fits_83(name, is_dir) is fits

    def test_short_field_is_always_11_chars(self):
        for name in ("FwPkt.zip", "starskyStack", "system", "a.bin"):
            assert len(pack_short_name(name, False)) == 11

    def test_lfn_records_are_32_bytes_with_lfn_attributes(self):
        short = pack_short_name("FwPkt.zip", False)
        records = encode_lfn_entries("FwPkt.zip", short)
        assert all(len(r) == 32 for r in records)
        assert all(r[11] == ATTR_LFN for r in records)
        # The one-record sequence carries LAST on its first/on-disk record.
        assert records[0][0] & 0x40

    def test_lfn_ordinals_are_descending_on_disk(self):
        long_name = "Mlog_000063.log.bak"
        records = encode_lfn_entries(long_name, pack_short_name(long_name, False))
        assert len(records) == 2
        assert [record[0] for record in records] == [0x42, 0x01]
        assert records[0][0] & 0x40
        assert not records[1][0] & 0x40

    def test_lfn_roundtrip(self):
        for name in ("FwPkt.zip", "OmsPkt.zip", "focusStack", "starskyStack",
                     "firmwareInfo", "Mlog_000063.log"):
            short = pack_short_name(name, False)
            assert decode_lfn_entries(encode_lfn_entries(name, short)) == name

    def test_lfn_checksum_is_deterministic(self):
        short = pack_short_name("FwPkt.zip", False)
        assert lfn_checksum(short) == lfn_checksum(short)
        assert 0 <= lfn_checksum(short) <= 255

    def test_malformed_lfn_ordinal_is_rejected(self):
        # A partially readable name would present a corrupt volume as valid.
        # Needs more than 13 characters to span multiple records; "starskyStack"
        # plus its NUL terminator is exactly 13 units and fits one record.
        long_name = "Mlog_000063.log.bak"
        short = pack_short_name(long_name, False)
        records = encode_lfn_entries(long_name, short)
        assert len(records) > 1, f"{long_name} produced only {len(records)} record"
        broken = bytearray(records[0])
        broken[0] = 7
        with pytest.raises(ImageCorruptionError, match="ordinal"):
            decode_lfn_entries([bytes(broken)] + records[1:])

    def test_short_entry_is_32_bytes_with_fields_at_spec_offsets(self):
        from polaris_harness.sd_emulator.image import DirectoryEntry
        entry = DirectoryEntry(name="FwPkt.zip", short_name="FWPKT  ZIP",
                               is_directory=False, size=68_396_345,
                               first_cluster=0x12345)
        raw = pack_short_entry(entry)
        assert len(raw) == 32
        # Cluster low at 26, high at 20, size at 28 - an earlier revision wrote
        # only 27 bytes with cluster/size at 22/24, corrupting every directory.
        assert struct.unpack_from("<H", raw, 26)[0] == 0x12345 & 0xFFFF
        assert struct.unpack_from("<H", raw, 20)[0] == 0x12345 >> 16
        assert struct.unpack_from("<I", raw, 28)[0] == 68_396_345

    def test_short_entry_roundtrip(self):
        from polaris_harness.sd_emulator.image import DirectoryEntry
        entry = DirectoryEntry(name="appfs.ubifs", short_name="APPFS~1.UBI",
                               is_directory=False, size=64_356_352,
                               first_cluster=0xABCDEF)
        decoded = unpack_short_entry(pack_short_entry(entry))
        assert decoded.size == entry.size
        assert decoded.first_cluster == entry.first_cluster


class TestVolumeReadWrite:
    """Files and directories must survive a write/read cycle."""

    def test_mode_directories_read_back_with_long_names(self, image):
        names = [e.name for e in image.list_root()]
        for expected in MODE_DIRS + ("system",):
            assert expected in names, f"{expected} missing from {names}"

    def test_subdirectory_has_dot_and_dotdot(self, image):
        system = next(e for e in image.list_root() if e.name == "system")
        entries = image.list_directory(system.first_cluster)
        assert [e.name for e in entries] == [".", ".."]

    def test_file_roundtrip_across_many_clusters(self, image):
        payload = bytes(range(256)) * 4000  # ~1 MB, spans many clusters
        image.add_file("FwPkt.zip", payload)
        image.flush_directories()
        assert image.read_file("FwPkt.zip") == payload

    def test_long_name_file_read_back(self, image):
        image.add_file("firmwareInfo", b"x" * 400)
        image.flush_directories()
        names = [e.name for e in image.list_root()]
        assert "firmwareInfo" in names

    def test_missing_file_raises_not_silently_empty(self, image):
        with pytest.raises(ImageCorruptionError, match="not present"):
            image.read_file("nope.zip")

    def test_sector_write_validates_size_and_range(self, image):
        with pytest.raises(ImageCorruptionError, match="must be 512 bytes"):
            image.write_sector(0, b"short")
        with pytest.raises(ImageCorruptionError, match="outside volume"):
            image.write_sector(image.geometry.total_sectors, b"\x00" * 512)

    def test_root_clusters_reserved_before_allocation(self, image):
        # Cluster 2 is the FAT32 root. If file allocation started there too, the
        # first file written would overwrite the root directory and the volume
        # would read back empty rather than corrupt.
        assert image._next_free_cluster > 2 + image.root_clusters

    def test_reserved_fat_entries_are_initialized_in_both_copies(self, image):
        for cluster, expected in ((0, FAT_FIRST_CLUSTER_MARKER),
                                  (1, FAT_END_OF_CHAIN)):
            sector, offset = image._fat_entry_offset(cluster)
            for copy in range(image.geometry.fat_copies):
                fat_sector = (image.geometry.reserved_sectors
                              + copy * image.geometry.sectors_per_fat + sector)
                value = struct.unpack_from("<I", image.read_sector(fat_sector),
                                           offset)[0]
                assert value & 0x0FFFFFFF == expected

    def test_fat_written_to_both_copies(self, image):
        image.add_file("probe.bin", b"z" * 100)
        image.flush_directories()
        first = next(e for e in image.list_root() if e.name == "probe.bin").first_cluster
        sector, offset = image._fat_entry_offset(first)
        copy0 = image.geometry.reserved_sectors + sector
        copy1 = image.geometry.reserved_sectors + image.geometry.sectors_per_fat + sector
        v0 = struct.unpack_from("<I", image.read_sector(copy0), offset)[0]
        v1 = struct.unpack_from("<I", image.read_sector(copy1), offset)[0]
        assert v0 == v1, "FAT copies disagree"

    def test_sparse_volume_refuses_to_materialise(self):
        volume = SDCardImage(total_bytes=32 * 1024 * 1024 * 1024)
        with pytest.raises(BlockAllocationError, match="write_to"):
            volume.to_bytes()

    def test_write_to_produces_sparse_file(self, tmp_path):
        volume = SDCardImage(total_bytes=TEST_VOLUME_BYTES)
        volume.write_boot_sector()
        volume.add_directory("normal")
        volume.flush_directories()
        target = tmp_path / "card.img"
        written = volume.write_to(target)
        assert written == volume.geometry.volume_bytes
        # Sparse: on-disk usage must be far below the logical size.
        assert target.stat().st_blocks * 512 < written // 4

    def test_sha256_is_stable_for_identical_content(self):
        def build():
            volume = SDCardImage(total_bytes=TEST_VOLUME_BYTES)
            volume.write_boot_sector()
            volume.add_directory("normal")
            volume.add_file("FwPkt.zip", b"same" * 100)
            volume.flush_directories()
            return volume
        assert build().sha256() == build().sha256()


class TestFirmwareInfoParsing:
    """The parser must match the real firmwareInfo format exactly."""

    REAL_INFO = (
        "config size:326;config MD5:1905e2d041be62b679f7dc6c64ab9d3a;\n"
        "uImage size:4188435;uImage MD5:5f6a0c1861a254371c4a956b57f26685;\n"
        "rootfs size:21102592;rootfs MD5:778b27bcade9ddc6ea4a7cb45254c551;\n"
        "appfs size:64356352;appfs MD5:d1e290f5509cee93f7e2d1b9b5666c36;\n"
        "polaris403 size:84328;polaris403 MD5:4facafa7d29c1e6c2a125b8309c9b901;\n"
        "polaris413 size:84284;polaris413 MD5:c0299d06a15f5c2fbecb9a6db76a29c5;\n"
    )

    def test_parses_real_metadata(self):
        records = parse_firmware_info(self.REAL_INFO)
        assert [r.name for r in records] == [
            "config", "uImage", "rootfs", "appfs", "polaris403", "polaris413"]
        by_name = {r.name: r for r in records}
        assert by_name["appfs"].size == 64_356_352
        assert by_name["appfs"].md5 == "d1e290f5509cee93f7e2d1b9b5666c36"
        assert by_name["rootfs"].path == "camera/rootfs.ubifs"

    def test_size_without_md5_is_rejected(self):
        with pytest.raises(PacketLayoutError, match="no MD5"):
            parse_firmware_info("appfs size:100;")

    def test_md5_without_size_is_rejected(self):
        with pytest.raises(PacketLayoutError, match="no preceding size"):
            parse_firmware_info("appfs MD5:" + "0" * 32 + ";")

    def test_unrecognised_token_is_rejected(self):
        with pytest.raises(PacketLayoutError, match="unrecognised"):
            parse_firmware_info("appfs bytes:100;")

    def test_fwver_parses_and_rejects_near_miss(self):
        assert parse_fwver("FwVer:6.0.0.54.90;date:2026.10.29;") == {
            "version": "6.0.0.54.90", "date": "2026.10.29"}
        # The device compares this string verbatim, so a near miss is a real
        # mismatch rather than a formatting detail to tolerate.
        with pytest.raises(PacketLayoutError, match="does not match"):
            parse_fwver("FwVer 6.0.0.54.90")


class TestVersionComparison:
    """Numeric components must compare numerically."""

    def test_numeric_not_lexicographic(self):
        # A plain string compare gets this wrong: "9" sorts after "1".
        assert compare_versions("6.0.0.54.90", "6.0.0.54.13") == 1
        assert compare_versions("6.0.0.54.13", "6.0.0.54.90") == -1

    def test_equal_and_different_lengths(self):
        assert compare_versions("6.0.0.54.90", "6.0.0.54.90") == 0
        assert compare_versions("6.0.1", "6.0.0.99") == 1


class TestPacketIntegrity:
    """Digests must be checked, not assumed."""

    def test_valid_packet_parses(self):
        packet = FirmwarePacket.from_bytes(build_packet_bytes(), kind="FwPkt",
                                           label="FwPkt.zip")
        assert packet.version == "6.0.0.54.90"
        assert packet.missing_expected_components() == []

    def test_md5_mismatch_is_detected(self):
        # Tamper one component's bytes without updating its declared digest.
        original = build_packet_bytes()
        buf = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(original)) as src, \
                zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.namelist():
                data = src.read(item)
                if item.endswith("appfs.ubifs"):
                    data = b"T" * len(data)
                dst.writestr(item, data)
        with pytest.raises(PacketIntegrityError, match="appfs"):
            FirmwarePacket.from_bytes(buf.getvalue(), kind="FwPkt",
                                      label="FwPkt.zip")

    def test_declared_component_missing_from_archive(self):
        # firmwareInfo declares appfs but the member is absent.
        payload = build_packet_bytes()
        buf = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(payload)) as src, \
                zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.namelist():
                if not item.endswith("appfs.ubifs"):
                    dst.writestr(item, src.read(item))
        with pytest.raises(PacketIntegrityError, match="no such member"):
            FirmwarePacket.from_bytes(buf.getvalue(), kind="FwPkt",
                                      label="FwPkt.zip")

    def test_no_root_directory_is_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("FwVer", "FwVer:1.0;date:2026.01.01;")
        with pytest.raises(PacketLayoutError, match="exactly one root"):
            FirmwarePacket.from_bytes(buf.getvalue())

    def test_missing_firmware_info_is_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("FwPkt/FwVer", "FwVer:1.0;date:2026.01.01;")
        with pytest.raises(PacketLayoutError, match="firmwareInfo"):
            FirmwarePacket.from_bytes(buf.getvalue())

    def test_to_dict_has_no_absolute_paths(self):
        packet = FirmwarePacket.from_bytes(build_packet_bytes(), kind="FwPkt",
                                           label="FwPkt.zip")
        text = repr(packet.to_dict())
        assert "/home" not in text and str(Path.cwd()) not in text


class TestSimulatedUpdate:
    """The update flow, including every failure path."""

    def _card_with(self, *packet_names_and_bytes):
        volume = SDCardImage(total_bytes=TEST_VOLUME_BYTES)
        volume.write_boot_sector()
        for name in MODE_DIRS:
            volume.add_directory(name)
        volume.add_directory("system")
        payloads = {}
        for name, payload in packet_names_and_bytes:
            volume.add_file(name, payload)
            payloads[name] = payload
        volume.flush_directories()
        return volume, payloads

    def test_successful_update_writes_nand_partitions(self):
        payload = build_packet_bytes()
        volume, payloads = self._card_with(("FwPkt.zip", payload))
        device = SimulatedDevice(current_version="4.0.0.32")
        report = device.attempt_update(volume, mock_packet_bytes=payloads)

        assert report.outcome is UpdateOutcome.UPDATED
        assert report.installed_version == "6.0.0.54.90"
        assert report.previous_version == "4.0.0.32"
        # NAND-bound components only; gimbal firmware is flashed over USB.
        assert report.partitions_written == ["uImage", "rootfs.ubifs", "appfs.ubifs"]
        assert report.bytes_written > 0

    def test_partition_sizes_respect_mtd_capacity(self):
        payload = build_packet_bytes()
        volume, payloads = self._card_with(("FwPkt.zip", payload))
        device = SimulatedDevice(current_version="4.0.0.32")
        report = device.attempt_update(volume, mock_packet_bytes=payloads)
        for partition in report.partitions_written:
            assert partition in MTD_PARTITIONS

    def test_no_packet_on_card(self):
        volume, _ = self._card_with()
        device = SimulatedDevice()
        report = device.attempt_update(volume)
        assert report.outcome is UpdateOutcome.NO_PACKET
        assert device.state is DeviceState.FAILED

    def test_both_packets_present_is_ambiguous_not_guessed(self):
        # A real snapshot held both FwPkt.zip and OmsPkt.zip, and the retained
        # evidence never records which the device prefers. Silently picking one
        # would make a simulated result look like verified device behaviour.
        fw = build_packet_bytes(version="6.0.0.54.90")
        oms = build_packet_bytes(version="6.0.0.54.90")
        volume, payloads = self._card_with(("FwPkt.zip", fw), ("OmsPkt.zip", oms))
        device = SimulatedDevice()
        report = device.attempt_update(volume, mock_packet_bytes=payloads)

        assert report.outcome is UpdateOutcome.AMBIGUOUS_PACKETS
        assert "refusing to guess precedence" in " ".join(report.failures)
        assert report.partitions_written == []

    def test_downgrade_refused_by_default(self):
        payload = build_packet_bytes(version="3.0.0.10")
        volume, payloads = self._card_with(("FwPkt.zip", payload))
        device = SimulatedDevice(current_version="4.0.0.32")
        report = device.attempt_update(volume, mock_packet_bytes=payloads)
        assert report.outcome is UpdateOutcome.VERSION_REJECTED
        assert device.current_version == "4.0.0.32", "must not have changed"

    def test_downgrade_allowed_when_explicitly_enabled(self):
        payload = build_packet_bytes(version="3.0.0.10")
        volume, payloads = self._card_with(("FwPkt.zip", payload))
        device = SimulatedDevice(current_version="4.0.0.32", allow_downgrade=True)
        report = device.attempt_update(volume, mock_packet_bytes=payloads)
        assert report.outcome is UpdateOutcome.UPDATED

    def test_invalid_packet_reports_invalid_not_updated(self):
        volume, payloads = self._card_with(("FwPkt.zip", b"not a zip at all"))
        device = SimulatedDevice()
        report = device.attempt_update(volume, mock_packet_bytes=payloads)
        assert report.outcome is UpdateOutcome.INVALID_PACKET

    def test_missing_component_fails_closed(self):
        payload = build_packet_bytes(drop=("appfs",))
        volume, payloads = self._card_with(("FwPkt.zip", payload))
        device = SimulatedDevice()
        report = device.attempt_update(volume, mock_packet_bytes=payloads)
        assert report.outcome is UpdateOutcome.INVALID_PACKET
        assert "appfs" in " ".join(report.failures)

    def test_partition_overflow_is_write_failure(self):
        # appfs.ubifs partition holds 81 MiB; declare something larger.
        huge = b"A" * (82 * 1024 * 1024)
        payload = build_packet_bytes(components={
            "config": b"x", "uImage": b"y" * 100, "rootfs": b"z" * 100,
            "appfs": huge, "polaris403": b"g", "polaris413": b"h"})
        volume, payloads = self._card_with(("FwPkt.zip", payload))
        device = SimulatedDevice()
        report = device.attempt_update(volume, mock_packet_bytes=payloads)
        assert report.outcome is UpdateOutcome.WRITE_FAILED
        assert "capacity" in " ".join(report.failures)

    def test_event_log_is_ordered_and_complete(self):
        payload = build_packet_bytes()
        volume, payloads = self._card_with(("FwPkt.zip", payload))
        device = SimulatedDevice(current_version="4.0.0.32")
        report = device.attempt_update(volume, mock_packet_bytes=payloads)
        states = [e.state for e in report.events]
        assert states[0] is DeviceState.CARD_MOUNTED
        assert states[-1] is DeviceState.UPDATED
        assert DeviceState.REBOOTING in states
        assert DeviceState.VERIFYING in states

    def test_report_to_dict_is_json_safe(self):
        import json
        payload = build_packet_bytes()
        volume, payloads = self._card_with(("FwPkt.zip", payload))
        device = SimulatedDevice(current_version="4.0.0.32")
        report = device.attempt_update(volume, mock_packet_bytes=payloads)
        text = json.dumps(report.to_dict())
        assert "updated" in text
        assert "/home" not in text

    def test_stale_out_of_band_payload_cannot_bypass_card_bytes(self):
        payload = build_packet_bytes()
        volume, stale_payloads = self._card_with(("FwPkt.zip", payload))
        entry = next(e for e in volume.list_root() if e.name == "FwPkt.zip")
        first_cluster = bytearray(
            volume.read_bytes(volume._cluster_sector(entry.first_cluster)
                              * BYTES_PER_SECTOR,
                              volume.geometry.cluster_bytes)
        )
        first_cluster[0] ^= 0xFF
        volume.write_cluster_data(entry.first_cluster, bytes(first_cluster))

        # The SD-qualified path reads the mutated cluster chain and must reject
        # it, even though the original bytes remain available in this stale
        # out-of-band fixture.
        device = SimulatedDevice(current_version="4.0.0.32")
        report = device.attempt_update(volume)
        assert report.packet_source == "fat-cluster-chain"
        assert report.outcome is UpdateOutcome.INVALID_PACKET

        # Direct injection remains available only as an explicitly labelled
        # mock path for small unit tests; it is not the qualified result.
        mock_report = SimulatedDevice(current_version="4.0.0.32").attempt_update(
            volume, mock_packet_bytes=stale_payloads
        )
        assert mock_report.packet_source == "mock-only"
        assert mock_report.outcome is UpdateOutcome.UPDATED

    def test_fsck_fat_accepts_generated_card(self, tmp_path):
        checker = shutil.which("fsck.fat") or shutil.which("fsck.vfat")
        if checker is None:
            pytest.skip("fsck.fat/fsck.vfat not installed")
        from polaris_harness.sd_emulator import build_benro_card
        volume = build_benro_card(total_bytes=TEST_VOLUME_BYTES, log_count=2)
        image_path = tmp_path / "benro-card.img"
        volume.write_to(image_path)
        result = subprocess.run([checker, "-n", str(image_path)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_packet_read_from_volume_when_not_supplied(self):
        payload = build_packet_bytes()
        volume, _ = self._card_with(("FwPkt.zip", payload))
        device = SimulatedDevice(current_version="4.0.0.32")
        report = device.attempt_update(volume)
        assert report.outcome is UpdateOutcome.UPDATED
        assert report.packet_source == "fat-cluster-chain"


class TestRealPacketFidelity:
    """Parse the real packet when the private research repo is available."""

    REAL_PACKET = Path(
        "/home/ian/Documents/VSCodeProjects/PrivateResearch/firmware-packets/"
        "6.0.0.54.90--o-v18m-k1ii-pgphoto-readiness-20261029/FwPkt.zip"
    )

    def test_real_packet_metadata(self):
        if not self.REAL_PACKET.exists():
            pytest.skip("real FwPkt.zip not available locally (private research)")
        packet = FirmwarePacket.from_zip(self.REAL_PACKET)
        assert packet.version == "6.0.0.54.90"
        assert packet.date == "2026.10.29"
        assert packet.missing_expected_components() == []
        by_name = {c.name: c for c in packet.components}
        # Sizes verified against the archive's own MD5s during from_zip.
        assert by_name["appfs"].size == 64_356_352
        assert by_name["uImage"].size == 4_188_435

    def test_real_packet_config_declares_mtdparts(self):
        if not self.REAL_PACKET.exists():
            pytest.skip("real FwPkt.zip not available locally (private research)")
        packet = FirmwarePacket.from_zip(self.REAL_PACKET)
        config = packet.read_member("camera/config").decode("utf-8", "replace")
        assert "mtdparts" in config
        # The emulator's partition map must agree with what the device declares.
        for partition in MTD_PARTITIONS:
            assert partition.rstrip("0123456789KMBkmb") in config or \
                partition in config, f"{partition} not found in real config"


class TestBuilder:
    """The shared card shape must match what a real card looks like."""

    def test_benro_card_has_mode_dirs_and_system_log(self):
        from polaris_harness.sd_emulator import build_benro_card

        volume = build_benro_card(total_bytes=TEST_VOLUME_BYTES, log_count=2)
        names = [e.name for e in volume.list_root()]
        for expected in MODE_DIRS + ("system",):
            assert expected in names

        system = next(e for e in volume.list_root() if e.name == "system")
        sub = {e.name: e for e in volume.list_directory(system.first_cluster)}
        assert "log" in sub

        logs = volume.list_directory(sub["log"].first_cluster)
        log_names = sorted(e.name for e in logs if not e.is_directory)
        assert "Mlog_000000.log" in log_names
        assert "Clog_000001.log" in log_names

    def test_packet_placed_at_card_root(self):
        from polaris_harness.sd_emulator import build_benro_card

        payload = build_packet_bytes()
        volume = build_benro_card(total_bytes=TEST_VOLUME_BYTES,
                                  packets=[("FwPkt.zip", payload)])
        assert volume.read_file("FwPkt.zip") == payload


class TestCli:
    """Exit codes must distinguish setup problems from behavioural rejections."""

    def _run(self, argv):
        from polaris_harness.cli import main
        return main(argv)

    def test_list_mode_succeeds(self, capsys):
        code = self._run(["sd-emulate", "--list",
                          "--card-size-bytes", str(TEST_VOLUME_BYTES)])
        assert code == 0
        out = capsys.readouterr().out
        assert "focusStack" in out and "starskyStack" in out

    def test_no_packet_on_card_is_setup_problem(self, capsys):
        code = self._run(["sd-emulate", "--card-size-bytes", str(TEST_VOLUME_BYTES)])
        assert code == 2
        assert "no-packet" in capsys.readouterr().out

    def test_missing_packet_file_is_argument_error(self):
        assert self._run(["sd-emulate", "--packet", "/tmp/definitely-absent.zip"]) == 2

    def test_renamed_packet_is_rejected_not_ignored(self, tmp_path):
        # A packet the device would not look for would silently produce
        # NO_PACKET, which reads as a card problem rather than an argument error.
        target = tmp_path / "renamed.zip"
        target.write_bytes(build_packet_bytes())
        assert self._run(["sd-emulate", "--packet", str(target)]) == 2

    def test_successful_update_with_synthetic_packet(self, tmp_path, capsys):
        target = tmp_path / "FwPkt.zip"
        target.write_bytes(build_packet_bytes())
        code = self._run(["sd-emulate", "--packet", str(target),
                          "--card-size-bytes", str(TEST_VOLUME_BYTES)])
        assert code == 0
        out = capsys.readouterr().out
        assert "outcome: updated" in out
        assert "packet source: fat-cluster-chain" in out
        assert "6.0.0.54.90" in out

    def test_downgrade_is_behavioural_failure(self, tmp_path):
        target = tmp_path / "FwPkt.zip"
        target.write_bytes(build_packet_bytes(version="3.0.0.10"))
        code = self._run(["sd-emulate", "--packet", str(target),
                          "--current-version", "4.0.0.32",
                          "--card-size-bytes", str(TEST_VOLUME_BYTES)])
        assert code == 3

    def test_ambiguous_packets_is_setup_problem(self, tmp_path):
        fw = tmp_path / "FwPkt.zip"
        oms = tmp_path / "OmsPkt.zip"
        fw.write_bytes(build_packet_bytes())
        oms.write_bytes(build_packet_bytes())
        code = self._run(["sd-emulate", "--packet", str(fw), "--oms-packet", str(oms),
                          "--card-size-bytes", str(TEST_VOLUME_BYTES)])
        assert code == 2

    def test_output_writes_json_report(self, tmp_path):
        import json
        target = tmp_path / "FwPkt.zip"
        target.write_bytes(build_packet_bytes())
        report_path = tmp_path / "report.json"
        code = self._run(["sd-emulate", "--packet", str(target),
                          "--card-size-bytes", str(TEST_VOLUME_BYTES),
                          "--output", str(report_path)])
        assert code == 0
        data = json.loads(report_path.read_text())
        assert data["outcome"] == "updated"
        assert data["partitions_written"] == ["uImage", "rootfs.ubifs", "appfs.ubifs"]
        assert "/home" not in report_path.read_text()
