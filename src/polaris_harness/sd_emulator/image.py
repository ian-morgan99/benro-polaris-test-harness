"""Byte-backed FAT32 SD card volume.

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

A FAT32 volume built from retained SD-card evidence rather than guessed:

- Top level holds FwPkt.zip / OmsPkt.zip plus mode directories (normal,
  focusStack, panorama, Lapse, sun, HDR, starskyStack) and system/log/.
- Those names are mixed case and some exceed 8.3, so long-file-name entries are
  required for the volume to look like one a camera would accept.

Sectors are held in a sparse dict rather than one bytearray. A legitimate FAT32
card is tens of gigabytes; materialising that in RAM would make the suite
impossible to run, while an unrealistically small card would not exercise the
cluster-chain paths a real packet needs. Sparse backing gives both: a 32 GiB
volume costs only the sectors actually written.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

SECTOR_SIZE = 512
BYTES_PER_SECTOR = SECTOR_SIZE

# FAT32 cluster-chain sentinels (bits 28-31 reserved; values below are the ones
# implementations actually compare against).
FAT_FREE = 0x0FFFFFFF
FAT_END_OF_CHAIN = 0x0FFFFFFF
FAT_BAD_CLUSTER = 0x0FFFFFF7
FAT_FIRST_CLUSTER_MARKER = 0x0FFFFFF8

BOOT_SIGNATURE = 0xAA55
FSINFO_LEAD_SIGNATURE = 0x41615252
FSINFO_STRUCTURE_SIGNATURE = 0x41615252
FSINFO_TRAIL_SIGNATURE = 0xAA55
BACKUP_BOOT_SECTOR = 6
FSINFO_SECTOR = 1

ATTR_READ_ONLY = 0x01
ATTR_HIDDEN = 0x02
ATTR_SYSTEM = 0x04
ATTR_VOLUME_ID = 0x08
ATTR_DIRECTORY = 0x10
ATTR_ARCHIVE = 0x20
ATTR_LFN = ATTR_READ_ONLY | ATTR_HIDDEN | ATTR_SYSTEM | ATTR_VOLUME_ID

# Case flags stored in the NTimeTenth field of the first LFN entry.
CASE_LOWER_NAME = 0x01
CASE_LOWER_EXT = 0x08


class ImageCorruptionError(Exception):
    """The volume's own structures are inconsistent - never guess a repair."""


class BlockAllocationError(Exception):
    """The volume is full, or a request exceeds what the geometry can hold."""


@dataclass(frozen=True)
class Geometry:
    """Derived FAT32 layout numbers for one volume.

    Attributes:
        total_sectors: whole sectors on the volume
        sectors_per_cluster: clusters are this many sectors
        reserved_sectors: sectors before the first FAT
        fat_copies: number of FAT copies (2 in practice)
        sectors_per_fat: size of one FAT in sectors
        data_start_sector: sector of cluster 2
        cluster_count: usable data clusters
    """

    total_sectors: int
    sectors_per_cluster: int
    reserved_sectors: int
    fat_copies: int
    sectors_per_fat: int

    @property
    def data_start_sector(self) -> int:
        return self.reserved_sectors + self.fat_copies * self.sectors_per_fat

    @property
    def cluster_count(self) -> int:
        data_sectors = self.total_sectors - self.data_start_sector
        return data_sectors // self.sectors_per_cluster

    @property
    def volume_bytes(self) -> int:
        return self.total_sectors * BYTES_PER_SECTOR

    @property
    def cluster_bytes(self) -> int:
        return self.sectors_per_cluster * BYTES_PER_SECTOR


def geometry_for_size(total_bytes: int, sectors_per_cluster: int = 8) -> Geometry:
    """Compute a FAT32 geometry that yields at least 65,525 clusters.

    Below 65,525 clusters the same layout is read as FAT16, so a small test
    volume must still be shaped like FAT32 or the emulator would not be testing
    what it claims to.

    Args:
        total_bytes: requested volume size
        sectors_per_cluster: cluster size in sectors

    Returns:
        Geometry with FAT sizes grown until the cluster count is FAT32-sized

    Raises:
        BlockAllocationError: if even a maximal FAT cannot reach FAT32 sizing
    """
    total_sectors = total_bytes // BYTES_PER_SECTOR
    if total_sectors < 65536:
        raise BlockAllocationError(
            f"volume too small for FAT32: {total_sectors} sectors; need >= 65536 "
            f"({65536 * BYTES_PER_SECTOR // (1024 * 1024)} MiB minimum)"
        )

    reserved = 32
    fat_copies = 2
    # Grow the FAT until clusters fit in 32 bits and count >= 65525.
    sectors_per_fat = max(1, (total_sectors // sectors_per_cluster) * 4 // BYTES_PER_SECTOR + 1)
    while True:
        data_sectors = total_sectors - reserved - fat_copies * sectors_per_fat
        clusters = data_sectors // sectors_per_cluster
        if clusters >= 65525:
            return Geometry(
                total_sectors=total_sectors,
                sectors_per_cluster=sectors_per_cluster,
                reserved_sectors=reserved,
                fat_copies=fat_copies,
                sectors_per_fat=sectors_per_fat,
            )
        # Not enough clusters: shrink the cluster size if possible, else grow FAT.
        if sectors_per_cluster > 1:
            sectors_per_cluster -= 1
            sectors_per_fat = max(
                1, (total_sectors // sectors_per_cluster) * 4 // BYTES_PER_SECTOR + 1
            )
        else:
            raise BlockAllocationError(
                f"cannot reach FAT32 cluster count with {total_sectors} sectors"
            )


@dataclass
class DirectoryEntry:
    """One decoded directory record.

    Attributes:
        name: display name, long form when present
        short_name: 8.3 name as stored
        is_directory: True for subdirectories
        size: file length in bytes
        first_cluster: start of the cluster chain
        attributes: raw FAT attribute byte
        mtime: modification time as (date, time) DOS fields
    """

    name: str
    short_name: str
    is_directory: bool
    size: int
    first_cluster: int
    attributes: int = ATTR_ARCHIVE
    mtime: Tuple[int, int] = (0x2601, 0)  # 2026-01-01, midnight

    @property
    def is_volume_label(self) -> bool:
        return bool(self.attributes & ATTR_VOLUME_ID)


def dos_time(hour: int, minute: int, second: int) -> int:
    """Encode a DOS time field."""
    return (hour << 11) | (minute << 5) | (second // 2)


def dos_date(year: int, month: int, day: int) -> int:
    """Encode a DOS date field (year offset from 1980)."""
    return ((max(1980, year) - 1980) << 9) | (month << 5) | day


def split_short_name(name: str) -> Tuple[str, str]:
    """Split a name into base and extension for 8.3 storage.

    Args:
        name: file or directory name

    Returns:
        (base, extension) with extension empty for directories/dotless names
    """
    if "." in name and not name.startswith("."):
        base, _, ext = name.rpartition(".")
        return base, ext
    return name, ""


def fits_83(name: str, is_directory: bool) -> bool:
    """Whether a name can be represented exactly by its 8.3 form.

    A name fits only when it is uppercase, has no more than one dot, and the
    base/extension lengths are within 8 and 3. Mixed case or long names need an
    LFN entry, which is why FwPkt.zip requires one.
    """
    base, ext = split_short_name(name)
    if len(base) > 8 or len(ext) > 3:
        return False
    if is_directory and ext:
        return False
    if name != name.upper():
        return False
    if name.count(".") > (0 if is_directory else 1):
        return False
    return all(ch.isalnum() or ch in "_-~!@#$%^&'()`{}." for ch in name)


def lfn_checksum(short_name: str) -> int:
    """Compute the FAT long-file-name checksum for an 8.3 name.

    The short name is padded to 11 characters (base then extension, space
    filled) and folded with a rotate-and-add. LFN entries carry this value so a
    reader can confirm they belong to the short entry that follows.

    Args:
        short_name: 8.3 name as stored on disk

    Returns:
        Checksum byte
    """
    padded = short_name.ljust(11, " ")[:11]
    checksum = 0
    for char in padded:
        checksum = ((checksum >> 1) + ((checksum & 1) << 7)) + ord(char)
        checksum &= 0xFF
    return checksum


def pack_short_name(name: str, is_directory: bool) -> str:
    """Build the 11-character 8.3 field value for a name.

    Names that do not fit 8.3 get a Windows-style collapsed form (first six
    characters plus a tilde sequence number), which is what a real formatter
    writes alongside the LFN entry.

    Args:
        name: file or directory name
        is_directory: True for directories

    Returns:
        Exactly 11 characters, space padded
    """
    base, ext = split_short_name(name)
    base = base.upper()
    ext = ext.upper()
    if is_directory:
        ext = ""
    if len(base) > 8:
        base = base[:6] + "~1"
    return f"{base:<8}{ext:<3}"


def encode_lfn_entries(long_name: str, short_field: str) -> List[bytes]:
    """Encode a long name as the LFN records that precede its short entry.

    Record layout (32 bytes):
      0  ordinal (bit 6 set on the last record in the sequence)
      1  five UTF-16LE characters
     11  attributes, always ATTR_LFN (0x0F)
     12  entry type, always 0
     13  checksum of the short field
     14  six UTF-16LE characters
     26  reserved, zero
     28  two UTF-16LE characters

    Args:
        long_name: name as it should be displayed
        short_field: 11-character short field the records belong to

    Returns:
        List of 32-byte records, ordered first-to-last
    """
    checksum = lfn_checksum(short_field)
    # UTF-16LE with a terminating NUL, padded to whole 13-char records.
    units = [ord(ch) for ch in long_name] + [0x0000]
    while len(units) % 13:
        units.append(0xFFFF)
    chunks = [units[i:i + 13] for i in range(0, len(units), 13)]

    records: List[bytes] = []
    total = len(chunks)
    for index, chunk in enumerate(chunks):
        padded = list(chunk) + [0xFFFF] * (13 - len(chunk))
        ordinal = index + 1
        if index == total - 1:
            ordinal |= 0x40  # last-in-sequence flag
        record = bytearray(32)
        record[0] = ordinal
        for slot, char_index in enumerate(range(0, 5)):
            struct.pack_into("<H", record, 1 + slot * 2, padded[char_index])
        record[11] = ATTR_LFN
        record[12] = 0x00
        record[13] = checksum
        for slot, char_index in enumerate(range(5, 11)):
            struct.pack_into("<H", record, 14 + slot * 2, padded[char_index])
        # bytes 26-27 stay zero (reserved)
        for slot, char_index in enumerate(range(11, 13)):
            struct.pack_into("<H", record, 28 + slot * 2, padded[char_index])
        assert len(record) == 32
        records.append(bytes(record))
    return records


def decode_lfn_entries(records: List[bytes]) -> str:
    """Recover a long name from its LFN records.

    Args:
        records: the records in first-to-last order

    Returns:
        The decoded name, truncated at the NUL terminator

    Raises:
        ImageCorruptionError: if a record is malformed or ordinals are out of
            sequence. A partially readable name would silently present a
            corrupted volume as a valid one.
    """
    units: List[int] = []
    for position, raw in enumerate(records):
        if len(raw) != 32:
            raise ImageCorruptionError("LFN record must be 32 bytes")
        if raw[11] != ATTR_LFN:
            raise ImageCorruptionError(
                f"LFN record {position} has attributes {raw[11]:#04x}, expected "
                f"{ATTR_LFN:#04x}"
            )
        ordinal = raw[0]
        if (ordinal & 0x3F) != position + 1:
            raise ImageCorruptionError(
                f"LFN record {position} has ordinal {ordinal & 0x3F}"
            )
        for offset in (1, 14, 28):
            span = 10 if offset == 1 else (12 if offset == 14 else 4)
            for i in range(0, span, 2):
                units.append(struct.unpack_from("<H", raw, offset + i)[0])
        if ordinal & 0x40:
            break

    # Trim padding and the NUL terminator.
    while units and units[-1] == 0xFFFF:
        units.pop()
    if 0x0000 in units:
        units = units[:units.index(0x0000)]
    return "".join(chr(u) for u in units)


def case_flags_for(name: str) -> int:
    """Case flags stored in the short entry's NTimeTenth byte.

    A reader uses these to restore the original casing without an LFN record,
    so writing them keeps the short entry self-describing for tools that ignore
    long names.

    Args:
        name: file or directory name

    Returns:
        CASE_LOWER_NAME and/or CASE_LOWER_EXT when those parts are lowercase
    """
    base, ext = split_short_name(name)
    flags = 0
    if base != base.upper():
        flags |= CASE_LOWER_NAME
    if ext and ext != ext.upper():
        return flags | CASE_LOWER_EXT
    return flags


def short_entry_case_flags(name: str, is_directory: bool) -> int:
    """Case flags for a name, empty for directories which have no extension.

    Args:
        name: file or directory name
        is_directory: True for directories

    Returns:
        Flags suitable for the NTimeTenth byte
    """
    if is_directory:
        base, _ = split_short_name(name)
        return CASE_LOWER_NAME if base != base.upper() else 0
    return case_flags_for(name)


def pack_short_entry(entry: DirectoryEntry) -> bytes:
    """Pack a DirectoryEntry into a 32-byte FAT directory record.

    Args:
        entry: the entry to encode

    Returns:
        Exactly 32 bytes
    """
    short = pack_short_name(entry.name, entry.is_directory)
    attrs = entry.attributes | (ATTR_DIRECTORY if entry.is_directory else 0)
    date, time = entry.mtime
    cluster = entry.first_cluster & 0xFFFFFFFF
    record = bytearray(32)
    # Offsets follow the FAT directory-entry structure exactly. An earlier
    # version packed only 27 bytes and put the cluster/size fields at 22/24,
    # which silently corrupted every directory written.
    record[0:11] = short.encode("ascii", "replace")[:11]
    record[11] = attrs
    record[12] = 0x00                                        # NTRes
    record[13] = short_entry_case_flags(entry.name, entry.is_directory)
    struct.pack_into("<H", record, 14, time)                 # creation time
    struct.pack_into("<H", record, 16, date)                 # creation date
    struct.pack_into("<H", record, 18, date)                 # last access date
    struct.pack_into("<H", record, 20, (cluster >> 16) & 0xFFFF)
    struct.pack_into("<H", record, 22, time)                 # mtime
    struct.pack_into("<H", record, 24, date)                 # mdate
    struct.pack_into("<H", record, 26, cluster & 0xFFFF)     # low cluster
    struct.pack_into("<I", record, 28, entry.size & 0xFFFFFFFF)
    return bytes(record)


def unpack_short_entry(raw: bytes) -> DirectoryEntry:
    """Decode a 32-byte FAT directory record.

    Args:
        raw: exactly 32 bytes

    Returns:
        Decoded entry

    Raises:
        ImageCorruptionError: if the record is not 32 bytes
    """
    if len(raw) != 32:
        raise ImageCorruptionError(f"directory record must be 32 bytes, got {len(raw)}")
    short = raw[:11].decode("ascii", "replace")
    attrs = raw[11]
    time_field = struct.unpack("<H", raw[14:16])[0]
    date_field = struct.unpack("<H", raw[16:18])[0]
    cluster_low = struct.unpack("<H", raw[26:28])[0]
    cluster_high = struct.unpack("<H", raw[20:22])[0]
    size = struct.unpack("<I", raw[28:32])[0]
    base, ext = short[:8].rstrip(), short[9:].rstrip()
    name = f"{base}.{ext}" if ext else base
    return DirectoryEntry(
        name=name,
        short_name=short.strip(),
        is_directory=bool(attrs & ATTR_DIRECTORY),
        size=size,
        first_cluster=(cluster_high << 16) | cluster_low,
        attributes=attrs,
        mtime=(date_field, time_field),
    )


class SDCardImage:
    """Sparse-backed FAT32 volume.

    Sectors are stored in a dict keyed by sector number, so a 32 GiB card costs
    only the sectors actually written. Materialising a real-sized FAT32 volume in
    RAM would make the suite impossible to run; shrinking the card to something
    tiny would stop exercising multi-cluster chains, which is what the 64 MB
    appfs.ubifs component needs. Sparse backing keeps both properties.

    The volume is built and read through this class only; nothing here mounts,
    formats, or writes to a real device.
    """

    def __init__(self, total_bytes: int = 32 * 1024 * 1024 * 1024,
                 volume_label: str = "BENRO",
                 sectors_per_cluster: int = 8) -> None:
        """Create an empty FAT32 volume of the requested size.

        Args:
            total_bytes: volume size; must be large enough for FAT32 sizing
            volume_label: label written to the root directory
            sectors_per_cluster: cluster size in sectors

        Raises:
            BlockAllocationError: if the size cannot yield a FAT32 cluster count
        """
        self.geometry = geometry_for_size(total_bytes, sectors_per_cluster)
        self.volume_label = volume_label[:11].upper()
        # sector number -> 512 bytes. Absent keys read as zeros.
        self._sectors: Dict[int, bytearray] = {}
        self._fat: List[int] = []
        self._root_entries: List[bytes] = []
        self._dir_clusters: Dict[int, List[bytes]] = {}
        # Reserve the root directory's clusters before anything else allocates.
        # Cluster 2 is the FAT32 root; if allocation started there too, the
        # first file written would overwrite the root directory and the volume
        # would read back as empty rather than corrupt.
        self.root_clusters = 4
        self._next_free_cluster = 2 + self.root_clusters
        self._boot_written = False

    # -- sparse sector access ------------------------------------------------

    def read_sector(self, number: int) -> bytes:
        """Read one sector, returning zeros for never-written ones."""
        sector = self._sectors.get(number)
        return bytes(sector) if sector is not None else bytes(BYTES_PER_SECTOR)

    def write_sector(self, number: int, data: bytes) -> None:
        """Write one sector.

        Args:
            number: sector index
            data: exactly 512 bytes

        Raises:
            ImageCorruptionError: on wrong size or out-of-range sector
        """
        if len(data) != BYTES_PER_SECTOR:
            raise ImageCorruptionError(
                f"sector must be {BYTES_PER_SECTOR} bytes, got {len(data)}"
            )
        if number < 0 or number >= self.geometry.total_sectors:
            raise ImageCorruptionError(
                f"sector {number} outside volume (0..{self.geometry.total_sectors - 1})"
            )
        self._sectors[number] = bytearray(data)

    def read_bytes(self, offset: int, length: int) -> bytes:
        """Read an arbitrary byte range across sector boundaries."""
        out = bytearray()
        for sector in range(offset // BYTES_PER_SECTOR,
                            (offset + length + BYTES_PER_SECTOR - 1) // BYTES_PER_SECTOR):
            start = max(0, offset - sector * BYTES_PER_SECTOR)
            stop = min(BYTES_PER_SECTOR, offset + length - sector * BYTES_PER_SECTOR)
            if stop <= start:
                continue
            out += self.read_sector(sector)[start:stop]
        return bytes(out[:length])

    def write_bytes(self, offset: int, data: bytes) -> None:
        """Write an arbitrary byte range across sector boundaries."""
        for index in range(0, len(data), BYTES_PER_SECTOR):
            chunk = data[index:index + BYTES_PER_SECTOR]
            position = offset + index
            sector = position // BYTES_PER_SECTOR
            inner = position % BYTES_PER_SECTOR
            buf = bytearray(self.read_sector(sector))
            buf[inner:inner + len(chunk)] = chunk
            self.write_sector(sector, bytes(buf))

    # -- FAT -----------------------------------------------------------------

    def _fat_entry_offset(self, cluster: int) -> Tuple[int, int]:
        """Locate a cluster's 4-byte FAT entry.

        Args:
            cluster: cluster number

        Returns:
            (sector number, offset within that sector)
        """
        byte = cluster * 4
        return byte // BYTES_PER_SECTOR, byte % BYTES_PER_SECTOR

    def get_fat_entry(self, cluster: int) -> int:
        """Read a FAT entry for one cluster."""
        sector, offset = self._fat_entry_offset(cluster)
        # FAT lives in the first FAT copy, inside the reserved+1.. region.
        fat_sector = self.geometry.reserved_sectors + sector
        raw = self.read_sector(fat_sector)
        value = struct.unpack_from("<I", raw, offset)[0]
        return int(value) & 0x0FFFFFFF

    def set_fat_entry(self, cluster: int, value: int) -> None:
        """Write a FAT entry to both FAT copies.

        Args:
            cluster: cluster number
            value: 28-bit value

        Raises:
            ImageCorruptionError: if the entry falls outside the FAT
        """
        sector, offset = self._fat_entry_offset(cluster)
        if sector >= self.geometry.sectors_per_fat:
            raise ImageCorruptionError(
                f"cluster {cluster} maps beyond the FAT ({self.geometry.sectors_per_fat} "
                f"sectors); volume geometry is inconsistent"
            )
        for copy in range(self.geometry.fat_copies):
            fat_sector = self.geometry.reserved_sectors + copy * self.geometry.sectors_per_fat + sector
            raw = bytearray(self.read_sector(fat_sector))
            struct.pack_into("<I", raw, offset, value & 0x0FFFFFFF)
            self.write_sector(fat_sector, bytes(raw))

    def _alloc_cluster(self) -> int:
        """Reserve the next free cluster.

        Returns:
            Cluster number

        Raises:
            BlockAllocationError: when the volume is full
        """
        cluster = self._next_free_cluster
        if cluster >= self.geometry.cluster_count + 2:
            raise BlockAllocationError(
                f"volume full: {self.geometry.cluster_count} clusters exhausted"
            )
        self._next_free_cluster = cluster + 1
        return cluster

    def _chain(self, first: int, count: int) -> List[int]:
        """Link `count` consecutive clusters starting at `first`.

        Args:
            first: starting cluster
            count: clusters to link

        Returns:
            The cluster numbers in order
        """
        clusters = [first + i for i in range(count)]
        if clusters[-1] >= self.geometry.cluster_count + 2:
            raise BlockAllocationError(
                f"chain of {count} from {first} exceeds {self.geometry.cluster_count} clusters"
            )
        for index, cluster in enumerate(clusters):
            value = FAT_END_OF_CHAIN if index == len(clusters) - 1 else clusters[index + 1]
            self.set_fat_entry(cluster, value)
        return clusters

    def _cluster_sector(self, cluster: int) -> int:
        """First sector of a cluster."""
        return self.geometry.data_start_sector + (cluster - 2) * self.geometry.sectors_per_cluster

    def write_cluster_data(self, cluster: int, data: bytes) -> None:
        """Write bytes into a cluster's sectors."""
        self.write_bytes(self._cluster_sector(cluster) * BYTES_PER_SECTOR, data)

    def read_cluster_chain(self, first: int, size: int) -> bytes:
        """Follow a cluster chain and return `size` bytes of file content.

        Args:
            first: first cluster of the chain
            size: bytes to read

        Returns:
            File contents

        Raises:
            ImageCorruptionError: on a cycle, or a chain shorter than `size`
        """
        out = bytearray()
        seen = set()
        cluster = first
        while cluster not in (FAT_END_OF_CHAIN, 0) and len(out) < size:
            if cluster in seen:
                raise ImageCorruptionError(
                    f"cluster chain from {first} revisits {cluster}; the FAT is corrupt"
                )
            seen.add(cluster)
            out += self.read_bytes(self._cluster_sector(cluster) * BYTES_PER_SECTOR,
                                   self.geometry.cluster_bytes)
            cluster = self.get_fat_entry(cluster)
        if len(out) < size:
            raise ImageCorruptionError(
                f"chain from {first} holds {len(out)} bytes but entry declares {size}"
            )
        return bytes(out[:size])

    # -- boot sector / FSINFO ------------------------------------------------

    def write_boot_sector(self) -> None:
        """Write the boot sector, FSINFO, and backup boot sector.

        Values are chosen so a reader can identify FAT32 unambiguously: cluster
        count above 65,524, a non-zero sectors-per-FAT, and the FAT32 ID string.
        """
        geometry = self.geometry
        sector = bytearray(BYTES_PER_SECTOR)
        sector[0:3] = b"\xEB\x58\x90"                 # jump to boot code
        sector[3:11] = b"MSWIN4.1"                    # OEM name
        struct.pack_into("<H", sector, 11, BYTES_PER_SECTOR)   # bytes/sector
        sector[13] = geometry.sectors_per_cluster
        struct.pack_into("<H", sector, 14, geometry.reserved_sectors)
        sector[16] = geometry.fat_copies
        # FAT16 fields stay zero; FAT32 uses the extended ones.
        struct.pack_into("<H", sector, 17, 0)         # root entry count
        struct.pack_into("<H", sector, 19, 0)         # total sectors (16-bit)
        sector[21] = 0xF8                             # media descriptor
        # BPB_FATSz16 stays zero for FAT32. On a 32 GiB card the FAT needs more
        # than 65,535 sectors, which cannot fit this field at all - that is
        # exactly why BPB_FATSz32 exists below. Writing the real value here
        # raised struct.error on any volume above ~16 GiB.
        struct.pack_into("<H", sector, 22, 0)
        struct.pack_into("<H", sector, 24, 32)        # sectors per track
        struct.pack_into("<H", sector, 26, 2)         # heads
        struct.pack_into("<I", sector, 28, 0)         # hidden sectors
        struct.pack_into("<I", sector, 32, geometry.total_sectors)
        struct.pack_into("<I", sector, 36, geometry.sectors_per_fat)
        struct.pack_into("<H", sector, 40, 0)         # FAT flags
        struct.pack_into("<H", sector, 42, 0)         # version
        struct.pack_into("<I", sector, 44, 2)         # root cluster
        struct.pack_into("<H", sector, 48, 1)         # FSINFO sector
        struct.pack_into("<H", sector, 50, BACKUP_BOOT_SECTOR)
        sector[66] = 0x80                             # drive number
        sector[67] = 0x00
        sector[68] = 0x29                             # boot signature present
        struct.pack_into("<I", sector, 69, 0x12345678)  # volume serial
        sector[71:82] = self.volume_label.ljust(11).encode("ascii")[:11]
        sector[82:90] = b"FAT32   "
        struct.pack_into("<H", sector, 510, BOOT_SIGNATURE)
        self.write_sector(0, bytes(sector))

        # Backup boot sector is a byte copy.
        self.write_sector(BACKUP_BOOT_SECTOR, bytes(sector))

        # FSINFO: free-cluster and next-free hints.
        # FSINFO layout: lead signature at 0, a second structure signature at
        # 488, free-cluster count at 496, next-free hint at 500, and the trail
        # signature as a 16-bit value at 510. Writing these at guessed offsets
        # produced struct errors and, worse, would present a sector a real
        # reader treats as invalid.
        fsinfo = bytearray(BYTES_PER_SECTOR)
        struct.pack_into("<I", fsinfo, 0, FSINFO_LEAD_SIGNATURE)
        struct.pack_into("<I", fsinfo, 488, FSINFO_STRUCTURE_SIGNATURE)
        used = self._next_free_cluster - 2
        struct.pack_into("<I", fsinfo, 496, max(0, geometry.cluster_count - used))
        struct.pack_into("<I", fsinfo, 500, self._next_free_cluster)
        struct.pack_into("<H", fsinfo, 510, FSINFO_TRAIL_SIGNATURE)
        self.write_sector(FSINFO_SECTOR, bytes(fsinfo))

        self._boot_written = True

    # -- directory writing ---------------------------------------------------

    def add_file(self, path: str, data: bytes,
                 parent_cluster: Optional[int] = None) -> DirectoryEntry:
        """Add a file, allocating clusters and writing LFN + short entries.

        Args:
            path: name within the root directory (single component)
            data: file contents
            parent_cluster: cluster of the containing directory; None for root

        Returns:
            The written entry

        Raises:
            BlockAllocationError: if there is no room for the data or entries
        """
        clusters_needed = max(1, (len(data) + self.geometry.cluster_bytes - 1)
                              // self.geometry.cluster_bytes)
        first = self._alloc_cluster()
        chain = self._chain(first, clusters_needed)

        # Write file content across the chain.
        for index, cluster in enumerate(chain):
            chunk = data[index * self.geometry.cluster_bytes:
                         (index + 1) * self.geometry.cluster_bytes]
            if chunk:
                self.write_cluster_data(cluster, chunk)

        entry = DirectoryEntry(
            name=path,
            short_name=pack_short_name(path, False),
            is_directory=False,
            size=len(data),
            first_cluster=first,
            attributes=ATTR_ARCHIVE,
        )
        records = self._entry_records(entry)
        self._append_records(records, parent_cluster)
        return entry

    def add_directory(self, name: str,
                      parent_cluster: Optional[int] = None) -> DirectoryEntry:
        """Add a subdirectory with . and .. entries.

        Args:
            name: directory name
            parent_cluster: cluster of the containing directory; None for root

        Returns:
            The written entry, whose first_cluster is the new directory's
        """
        cluster = self._alloc_cluster()
        self.set_fat_entry(cluster, FAT_END_OF_CHAIN)
        entry = DirectoryEntry(
            name=name,
            short_name=pack_short_name(name, True),
            is_directory=True,
            size=0,
            first_cluster=cluster,
            attributes=ATTR_DIRECTORY,
        )
        self._append_records(self._entry_records(entry), parent_cluster)

        # The new directory starts with . and .. pointing at itself and parent.
        dot = pack_short_entry(DirectoryEntry(
            name=".", short_name=".          ", is_directory=True, size=0,
            first_cluster=cluster, attributes=ATTR_DIRECTORY))
        parent_target = 2 if parent_cluster is None else parent_cluster
        dotdot = pack_short_entry(DirectoryEntry(
            name="..", short_name="..         ", is_directory=True, size=0,
            first_cluster=parent_target, attributes=ATTR_DIRECTORY))
        self._dir_clusters[cluster] = [dot, dotdot]
        return entry

    def _entry_records(self, entry: DirectoryEntry) -> List[bytes]:
        """Build the LFN records plus short record for one entry."""
        short_field = pack_short_name(entry.name, entry.is_directory)
        if fits_83(entry.name, entry.is_directory):
            return [pack_short_entry(entry)]
        return encode_lfn_entries(entry.name, short_field) + [pack_short_entry(entry)]

    def _append_records(self, records: List[bytes],
                        parent_cluster: Optional[int]) -> None:
        """Append directory records to the root or a subdirectory."""
        if parent_cluster is None:
            self._root_entries.extend(records)
        else:
            self._dir_clusters.setdefault(parent_cluster, []).extend(records)

    def flush_directories(self) -> None:
        """Write buffered directory records into their clusters.

        Must be called after all entries are added and before reading the volume
        back, so the FAT and directory sectors agree.
        """
        self._write_root()
        for cluster, records in self._dir_clusters.items():
            payload = b"".join(records)
            needed = max(1, (len(payload) + self.geometry.cluster_bytes - 1)
                         // self.geometry.cluster_bytes)
            # The directory already owns one cluster; extend the chain if needed.
            existing = self.get_fat_entry(cluster)
            if needed > 1:
                extra_first = self._alloc_cluster()
                self._chain(extra_first, needed - 1)
                self.set_fat_entry(cluster, extra_first)
            self.write_cluster_data(cluster, payload.ljust(
                needed * self.geometry.cluster_bytes, b"\x00"))
        self.write_boot_sector()

    def _write_root(self) -> None:
        """Write root-directory records starting at cluster 2."""
        if not self._root_entries:
            return
        payload = b"".join(self._root_entries)
        # Volume label occupies the first short entry of the root.
        label = pack_short_entry(DirectoryEntry(
            name=self.volume_label, short_name=self.volume_label.ljust(11)[:11],
            is_directory=False, size=0, first_cluster=0, attributes=ATTR_VOLUME_ID))
        payload = label + payload
        needed = max(1, (len(payload) + self.geometry.cluster_bytes - 1)
                     // self.geometry.cluster_bytes)
        if needed > self.root_clusters:
            raise BlockAllocationError(
                f"root directory needs {needed} clusters but only "
                f"{self.root_clusters} are reserved; increase root_clusters"
            )
        root_first = 2
        if needed > 1:
            self._chain(root_first, needed)
        else:
            self.set_fat_entry(root_first, FAT_END_OF_CHAIN)
        # Zero the whole reservation so a reader sees end-of-directory rather
        # than stale bytes past the last entry.
        for offset in range(needed):
            self.write_cluster_data(root_first + offset, b"\x00" * self.geometry.cluster_bytes)
        self.write_bytes(self._cluster_sector(root_first) * BYTES_PER_SECTOR, payload)

    # -- reading back --------------------------------------------------------

    def list_root(self) -> List[DirectoryEntry]:
        """Read the root directory, reassembling long names.

        Returns:
            Entries in on-disk order, excluding the volume label

        Raises:
            ImageCorruptionError: if the root cluster is not marked as a chain
                start or LFN records are malformed
        """
        size = self._root_size_bytes()
        payload = self.read_cluster_chain(2, size)
        return _decode_directory(payload)

    def list_directory(self, first_cluster: int) -> List[DirectoryEntry]:
        """Read a subdirectory's entries."""
        size = self._directory_size_bytes(first_cluster)
        payload = self.read_cluster_chain(first_cluster, size)
        return _decode_directory(payload)

    def read_file(self, path: str) -> bytes:
        """Read a file from the root directory by name.

        Args:
            path: file name

        Returns:
            File contents

        Raises:
            ImageCorruptionError: if the name is absent
        """
        for entry in self.list_root():
            if entry.name == path or entry.short_name.replace(" ", "") == path:
                return self.read_cluster_chain(entry.first_cluster, entry.size)
        raise ImageCorruptionError(f"file {path!r} not present in root directory")

    def _root_size_bytes(self) -> int:
        """Bytes occupied by the root cluster chain."""
        return self._chain_bytes(2)

    def _directory_size_bytes(self, first: int) -> int:
        """Bytes occupied by a directory's cluster chain."""
        return self._chain_bytes(first)

    def _chain_bytes(self, first: int) -> int:
        """Total bytes spanned by a cluster chain."""
        count = 0
        cluster = first
        seen = set()
        while cluster not in (FAT_END_OF_CHAIN, 0):
            if cluster in seen:
                raise ImageCorruptionError(f"cycle from {first}")
            seen.add(cluster)
            count += 1
            cluster = self.get_fat_entry(cluster)
        return count * self.geometry.cluster_bytes

    def to_bytes(self) -> bytes:
        """Materialise the whole volume.

        Returns:
            Every sector concatenated, zeros for unwritten ones

        Raises:
            BlockAllocationError: if materialising would exceed a sane bound. A
                32 GiB volume is legitimate but must not be materialised inside
                a test; write to a file with write_to() instead.
        """
        limit = 512 * 1024 * 1024
        if self.geometry.volume_bytes > limit:
            raise BlockAllocationError(
                f"volume is {self.geometry.volume_bytes} bytes; use write_to() "
                f"for volumes above {limit} bytes rather than materialising in RAM"
            )
        out = bytearray(self.geometry.volume_bytes)
        for sector, data in self._sectors.items():
            start = sector * BYTES_PER_SECTOR
            out[start:start + BYTES_PER_SECTOR] = data
        return bytes(out)

    def write_to(self, path) -> int:
        """Write the volume to a file sparsely, without materialising it.

        Args:
            path: destination path

        Returns:
            Bytes written (the full volume size; on-disk usage is smaller)
        """
        from pathlib import Path as _Path

        target = _Path(path)
        with open(target, "wb") as handle:
            handle.truncate(self.geometry.volume_bytes)
            for sector in sorted(self._sectors):
                handle.seek(sector * BYTES_PER_SECTOR)
                handle.write(self._sectors[sector])
        return self.geometry.volume_bytes

    def sha256(self) -> str:
        """Hash the volume's written content, ignoring unwritten sectors.

        Hashing a sparse volume sector-by-sector keeps the digest stable across
        runs regardless of which sectors happened to be touched, so two builds
        with identical logical content compare equal.

        Returns:
            Hex SHA-256 of the materialised volume
        """
        digest = hashlib.sha256()
        for sector in range(self.geometry.total_sectors):
            digest.update(self.read_sector(sector))
        return digest.hexdigest()


def _decode_directory(payload: bytes) -> List[DirectoryEntry]:
    """Decode a directory cluster's records, reassembling LFN sequences.

    Args:
        payload: concatenated 32-byte records

    Returns:
        Decoded entries, long names restored, volume label excluded

    Raises:
        ImageCorruptionError: on malformed LFN sequences or record sizes
    """
    if len(payload) % 32:
        raise ImageCorruptionError(
            f"directory payload must be a multiple of 32 bytes, got {len(payload)}"
        )
    entries: List[DirectoryEntry] = []
    pending: List[bytes] = []
    for offset in range(0, len(payload), 32):
        raw = payload[offset:offset + 32]
        if raw[0] == 0x00:
            break                      # end of directory
        if raw[0] == 0xE5:
            pending.clear()            # deleted entry
            continue
        if raw[11] == ATTR_LFN:
            pending.append(raw)
            continue
        entry = unpack_short_entry(raw)
        if entry.is_volume_label:
            pending.clear()
            continue
        if pending:
            entry = DirectoryEntry(
                name=decode_lfn_entries(pending),
                short_name=entry.short_name,
                is_directory=entry.is_directory,
                size=entry.size,
                first_cluster=entry.first_cluster,
                attributes=entry.attributes,
                mtime=entry.mtime,
            )
            pending.clear()
        entries.append(entry)
    return entries
