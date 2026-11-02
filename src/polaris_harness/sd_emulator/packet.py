"""Firmware packet parsing and integrity checks.

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

The layout and metadata format come from a real FwPkt.zip retained in the
private research repository, not from guesswork:

    FwPkt/
      firmwareInfo          # "<name> size:<n>;<name> MD5:<hex>;" per component
      FwVer                 # "FwVer:6.0.0.54.90;date:2026.10.29;"
      camera/
        uImage              # 4,188,435 bytes
        rootfs.ubifs        # 21,102,592 bytes
        appfs.ubifs         # 64,356,352 bytes
        config              # 326 bytes, U-Boot env incl. mtdparts
      gimbal/
        polaris403_2.0.0.22.bin   # 84,328 bytes
        polaris413_2.0.0.22.bin   # 84,284 bytes

MD5 is used because that is what the device's own firmwareInfo declares. It is
not being recommended as a security primitive; it is reproduced so the emulator
checks exactly what the camera checks. SHA-256 is recorded separately for
harness-side provenance.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


class PacketLayoutError(Exception):
    """The packet's structure does not match what the device expects."""


class PacketIntegrityError(Exception):
    """A component's size or digest disagrees with firmwareInfo."""


# firmwareInfo lines look like: "appfs size:64356352;appfs MD5:d1e2...;"
_SIZE_RE = re.compile(r"^(?P<name>[A-Za-z0-9_]+)\s+size:(?P<size>\d+)$")
_MD5_RE = re.compile(r"^(?P<name>[A-Za-z0-9_]+)\s+MD5:(?P<md5>[0-9a-fA-F]{32})$")

# FwVer content: "FwVer:6.0.0.54.90;date:2026.10.29;"
_FWVER_RE = re.compile(
    r"^FwVer:(?P<version>[0-9][0-9A-Za-z.\-]*);date:(?P<date>[0-9.\-]+);?$"
)

# Components the device's firmwareInfo declares, in the order it declares them.
EXPECTED_COMPONENTS = (
    "config",
    "uImage",
    "rootfs",
    "appfs",
    "polaris403",
    "polaris413",
)

# Where each declared component lives inside the packet, relative to FwPkt/.
COMPONENT_PATHS: Dict[str, str] = {
    "config": "camera/config",
    "uImage": "camera/uImage",
    "rootfs": "camera/rootfs.ubifs",
    "appfs": "camera/appfs.ubifs",
    "polaris403": "gimbal/polaris403_2.0.0.22.bin",
    "polaris413": "gimbal/polaris413_2.0.0.22.bin",
}


@dataclass(frozen=True)
class ComponentRecord:
    """One component declared by firmwareInfo.

    Attributes:
        name: logical name as declared (e.g. "appfs")
        size: declared size in bytes
        md5: declared MD5 digest, lowercase hex
        path: location inside the packet, relative to FwPkt/
    """

    name: str
    size: int
    md5: str
    path: str = ""


def parse_firmware_info(text: str) -> List[ComponentRecord]:
    """Parse a firmwareInfo file into component records.

    The format is a flat sequence of semicolon-terminated tokens, where each
    component contributes a "<name> size:<n>" token followed by a
    "<name> MD5:<hex>" token. Tokens may be split across lines.

    Args:
        text: firmwareInfo contents

    Returns:
        Records in declaration order

    Raises:
        PacketLayoutError: on an unrecognised token, a size with no matching
            MD5, or an MD5 with no preceding size. Guessing here would let a
            truncated or hand-edited packet pass as complete.
    """
    tokens = [t.strip() for t in text.replace("\n", ";").split(";") if t.strip()]

    pending_size: Dict[str, int] = {}
    records: List[ComponentRecord] = []
    seen_order: List[str] = []

    for token in tokens:
        size_match = _SIZE_RE.match(token)
        if size_match:
            name = size_match.group("name")
            pending_size[name] = int(size_match.group("size"))
            continue

        md5_match = _MD5_RE.match(token)
        if md5_match:
            name = md5_match.group("name")
            if name not in pending_size:
                raise PacketLayoutError(
                    f"firmwareInfo declares MD5 for {name!r} with no preceding "
                    f"size token; refusing to infer a size"
                )
            records.append(ComponentRecord(
                name=name,
                size=pending_size.pop(name),
                md5=md5_match.group("md5").lower(),
                path=COMPONENT_PATHS.get(name, ""),
            ))
            seen_order.append(name)
            continue

        raise PacketLayoutError(
            f"unrecognised firmwareInfo token {token!r}; expected "
            f"'<name> size:<n>' or '<name> MD5:<hex>'"
        )

    if pending_size:
        raise PacketLayoutError(
            f"firmwareInfo declares sizes with no MD5 for "
            f"{sorted(pending_size)}; the packet metadata is incomplete"
        )

    return records


def parse_fwver(text: str) -> Dict[str, str]:
    """Parse an FwVer file.

    Args:
        text: FwVer contents, e.g. "FwVer:6.0.0.54.90;date:2026.10.29;"

    Returns:
        {"version": ..., "date": ...}

    Raises:
        PacketLayoutError: if the content does not match the expected form
    """
    stripped = text.strip()
    match = _FWVER_RE.match(stripped)
    if not match:
        raise PacketLayoutError(
            f"FwVer content {stripped!r} does not match "
            f"'FwVer:<version>;date:<date>;' - the device compares this string "
            f"verbatim, so a near miss is a real mismatch, not a formatting "
            f"detail worth tolerating."
        )
    return {"version": match.group("version"), "date": match.group("date")}


def compare_versions(left: str, right: str) -> int:
    """Compare dotted firmware versions numerically where possible.

    Components are compared as integers when both sides are numeric, otherwise
    as strings, so "6.0.0.54.90" > "6.0.0.54.13" - which a plain string compare
    would get wrong because "9" sorts after "1".

    Args:
        left: first version
        right: second version

    Returns:
        -1, 0, or 1
    """
    def parts(value: str):
        out = []
        for piece in value.split("."):
            out.append(int(piece) if piece.isdigit() else piece)
        return out

    a, b = parts(left), parts(right)
    length = max(len(a), len(b))
    a += [""] * (length - len(a))
    b += [""] * (length - len(b))
    for x, y in zip(a, b):
        if isinstance(x, int) and isinstance(y, int):
            if x != y:
                return -1 if x < y else 1
        elif x != y:
            sx, sy = str(x), str(y)
            return -1 if sx < sy else 1
    return 0


@dataclass
class FirmwarePacket:
    """A parsed FwPkt.zip / OmsPkt.zip.

    Attributes:
        kind: "FwPkt" or "OmsPkt"
        root: directory name inside the archive ("FwPkt")
        version: version from FwVer
        date: date string from FwVer
        components: records parsed from firmwareInfo
        sha256: digest of the archive bytes, for harness provenance
        size_bytes: archive size
    """

    kind: str
    root: str
    version: str
    date: str
    components: List[ComponentRecord] = field(default_factory=list)
    sha256: str = ""
    size_bytes: int = 0
    _archive: Optional[Path] = field(default=None, repr=False)
    # Member contents held in memory when the packet was parsed from bytes.
    # A card-backed packet has no file to re-open, so read_member falls back
    # to this cache.
    _members: Dict[str, bytes] = field(default_factory=dict, repr=False)

    @classmethod
    def from_bytes(cls, payload: bytes, kind: str = "FwPkt",
                   label: str = "<card packet>") -> "FirmwarePacket":
        """Parse a packet from archive bytes read off the card.

        Args:
            payload: zip bytes
            kind: "FwPkt" or "OmsPkt"
            label: name used in error messages

        Returns:
            Parsed packet

        Raises:
            PacketLayoutError: if the bytes are not a readable packet
        """
        import io

        try:
            archive = zipfile.ZipFile(io.BytesIO(payload))
        except zipfile.BadZipFile as exc:
            raise PacketLayoutError(f"{label} is not a readable zip: {exc}") from exc

        digest = hashlib.sha256(payload).hexdigest()
        with archive:
            try:
                return cls._parse(archive, kind=kind, label=label,
                                  sha256=digest, size_bytes=len(payload),
                                  archive_path=None)
            except zipfile.BadZipFile as exc:
                # A card can contain a truncated or partially overwritten ZIP
                # whose central directory opens but whose member read fails.
                # Keep that on the same fail-closed layout path as a bad header.
                raise PacketLayoutError(
                    f"{label} is not a readable zip member: {exc}"
                ) from exc

    @classmethod
    def _parse(cls, archive: zipfile.ZipFile, kind: str, label: str,
               sha256: str, size_bytes: int,
               archive_path: Optional[Path]) -> "FirmwarePacket":
        """Shared parsing for file- and bytes-backed packets.

        Args:
            archive: open zip
            kind: packet kind
            label: name used in error messages
            sha256: digest of the archive bytes
            size_bytes: archive size
            archive_path: backing file when one exists

        Returns:
            Parsed packet

        Raises:
            PacketLayoutError: on missing structure or metadata
        """
        names = [n for n in archive.namelist() if not n.endswith("/")]
        roots = {n.split("/", 1)[0] for n in names if "/" in n}
        if len(roots) != 1:
            raise PacketLayoutError(
                f"{label} must contain exactly one root directory, found "
                f"{sorted(roots) or 'none'}"
            )
        root = roots.pop()

        def read_member(suffix: str) -> Optional[bytes]:
            candidate = f"{root}/{suffix}"
            return archive.read(candidate) if candidate in names else None

        fwver_raw = read_member("FwVer")
        if fwver_raw is None:
            raise PacketLayoutError(
                f"{label} has no {root}/FwVer; the device reads this to decide "
                f"whether an update applies"
            )
        info_raw = read_member("firmwareInfo")
        if info_raw is None:
            raise PacketLayoutError(
                f"{label} has no {root}/firmwareInfo; component sizes and digests "
                f"cannot be verified without it"
            )

        fwver = parse_fwver(fwver_raw.decode("utf-8", "replace"))
        components = parse_firmware_info(info_raw.decode("utf-8", "replace"))

        packet = cls(
            kind=kind,
            root=root,
            version=fwver["version"],
            date=fwver["date"],
            components=components,
            sha256=sha256,
            size_bytes=size_bytes,
            _archive=archive_path,
        )
        packet._verify_members(archive, names)
        if archive_path is None:
            # Byte-backed: keep the components a device would read, so
            # read_member has something to return without a file.
            for record in packet.components:
                if record.path:
                    member = f"{root}/{record.path}"
                    if member in names:
                        packet._members[record.path] = archive.read(member)
        return packet

    @classmethod
    def from_zip(cls, path: Path, kind: Optional[str] = None) -> "FirmwarePacket":
        """Parse a firmware packet archive.

        Args:
            path: path to FwPkt.zip or OmsPkt.zip
            kind: override the detected kind

        Returns:
            Parsed packet

        Raises:
            PacketLayoutError: if the archive is unreadable, has no single root
                directory, or lacks FwVer / firmwareInfo
            PacketIntegrityError: if a component's bytes disagree with
                firmwareInfo
        """
        path = Path(path)
        if not path.exists():
            raise PacketLayoutError(f"packet {path} does not exist")

        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()

        try:
            archive = zipfile.ZipFile(path)
        except zipfile.BadZipFile as exc:
            raise PacketLayoutError(f"{path} is not a readable zip: {exc}") from exc

        detected = kind or (
            "OmsPkt" if path.name.lower().startswith("omspkt") else "FwPkt"
        )
        with archive:
            return cls._parse(archive, kind=detected, label=path.name,
                              sha256=digest, size_bytes=len(data),
                              archive_path=path)

    def _verify_members(self, archive: zipfile.ZipFile, names: List[str]) -> None:
        """Check every declared component against the archive's bytes.

        Raises:
            PacketIntegrityError: on a missing member, size mismatch, or MD5
                mismatch
        """
        for record in self.components:
            if not record.path:
                raise PacketIntegrityError(
                    f"component {record.name!r} is declared but this harness has "
                    f"no known path for it; add it to COMPONENT_PATHS rather than "
                    f"skipping the check"
                )
            member = f"{self.root}/{record.path}"
            if member not in names:
                raise PacketIntegrityError(
                    f"firmwareInfo declares {record.name} at {record.path} but the "
                    f"archive has no such member"
                )
            payload = archive.read(member)
            if len(payload) != record.size:
                raise PacketIntegrityError(
                    f"{record.name}: firmwareInfo declares {record.size} bytes, "
                    f"archive holds {len(payload)}"
                )
            actual_md5 = hashlib.md5(payload).hexdigest()
            if actual_md5 != record.md5:
                raise PacketIntegrityError(
                    f"{record.name}: firmwareInfo declares MD5 {record.md5}, "
                    f"bytes hash to {actual_md5}"
                )

    def component(self, name: str) -> ComponentRecord:
        """Look up one declared component.

        Args:
            name: logical component name

        Returns:
            The record

        Raises:
            PacketLayoutError: if firmwareInfo does not declare it
        """
        for record in self.components:
            if record.name == name:
                return record
        raise PacketLayoutError(
            f"firmwareInfo does not declare {name!r}; declares "
            f"{[c.name for c in self.components]}"
        )

    def missing_expected_components(self) -> List[str]:
        """Components the device expects but this packet does not declare.

        Returns:
            Names in EXPECTED_COMPONENTS order, empty when all are present
        """
        declared = {c.name for c in self.components}
        return [name for name in EXPECTED_COMPONENTS if name not in declared]

    def read_member(self, relative_path: str) -> bytes:
        """Read one member from the packet.

        Args:
            relative_path: path inside the packet root, e.g. "camera/config"

        Returns:
            Member bytes

        Raises:
            PacketLayoutError: if the member is absent from both the cache and
                the backing archive
        """
        cached = self._members.get(relative_path)
        if cached is not None:
            return cached
        if self._archive is None:
            raise PacketLayoutError(
                f"member {relative_path!r} is not available: this packet was "
                f"parsed from bytes and the member was not among the components "
                f"declared in firmwareInfo"
            )
        with zipfile.ZipFile(self._archive) as archive:
            member = f"{self.root}/{relative_path}"
            if member not in archive.namelist():
                raise PacketLayoutError(f"packet has no member {relative_path!r}")
            return archive.read(member)

    def to_dict(self) -> Dict[str, object]:
        """Machine-readable summary safe to include in a report.

        Returns:
            Plain dict with no absolute paths and no file contents
        """
        return {
            "kind": self.kind,
            "root": self.root,
            "version": self.version,
            "date": self.date,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "components": [
                {"name": c.name, "size": c.size, "md5": c.md5, "path": c.path}
                for c in self.components
            ],
            "missing_expected_components": self.missing_expected_components(),
        }
