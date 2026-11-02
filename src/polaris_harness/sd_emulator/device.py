"""Simulated Benro Polaris device performing an SD-card firmware update.

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

The device never touches hardware: it reads the emulated SD volume, validates
what firmwareInfo declares, writes to simulated MTD partitions, and reports an
outcome. Nothing is mounted, flashed, or rebooted for real.

Two behaviours come straight from retained evidence rather than assumption:

- The camera mounts the card at /app/sd (seen in Mlog as "can't unmount
  /app/sd"), so the emulator treats that mount point as the volume root.
- A real snapshot contained both FwPkt.zip and OmsPkt.zip, and a later capture
  recorded OmsPkt.zip removed from the card. Which packet wins is NOT documented
  in the retained evidence, so this module refuses to guess: seeing both is
  reported as an explicit ambiguity instead of silently preferring one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from .image import ImageCorruptionError, SDCardImage
from .packet import (
    ComponentRecord,
    FirmwarePacket,
    PacketIntegrityError,
    PacketLayoutError,
    compare_versions,
)


class DeviceState(Enum):
    """Device lifecycle states relevant to an SD-card update."""

    IDLE = "idle"
    CARD_MOUNTED = "card-mounted"
    PACKET_FOUND = "packet-found"
    VALIDATING = "validating"
    WRITING = "writing"
    REBOOTING = "rebooting"
    VERIFYING = "verifying"
    UPDATED = "updated"
    FAILED = "failed"


class UpdateOutcome(Enum):
    """Terminal outcome of an update attempt."""

    UPDATED = "updated"
    NO_PACKET = "no-packet"
    AMBIGUOUS_PACKETS = "ambiguous-packets"
    INVALID_PACKET = "invalid-packet"
    VERSION_REJECTED = "version-rejected"
    WRITE_FAILED = "write-failed"
    VERIFY_FAILED = "verify-failed"


@dataclass(frozen=True)
class DeviceEvent:
    """One step in the update flow.

    Attributes:
        state: state entered by this step
        code: machine-readable event code
        message: human-readable detail
        details: structured facts for reporting
    """

    state: DeviceState
    code: str
    message: str
    details: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class UpdateReport:
    """Machine-readable result of one update attempt.

    Attributes:
        outcome: terminal outcome
        events: every step taken, in order
        installed_version: version now reported by the device, if updated
        previous_version: version before the attempt
        packet_source: ``fat-cluster-chain`` for the qualified card path, or
            ``mock-only`` when tests explicitly inject archive bytes
        partitions_written: simulated MTD partition names written
        bytes_written: total payload bytes across those partitions
        failures: reasons for failure, empty on success
    """

    outcome: UpdateOutcome
    events: List[DeviceEvent]
    installed_version: Optional[str]
    previous_version: str
    packet_source: str = "fat-cluster-chain"
    partitions_written: List[str] = field(default_factory=list)
    bytes_written: int = 0
    failures: List[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.outcome is UpdateOutcome.UPDATED

    def to_dict(self) -> Dict[str, object]:
        """Plain-dict form for JSON reporting."""
        return {
            "outcome": self.outcome.value,
            "succeeded": self.succeeded,
            "installed_version": self.installed_version,
            "previous_version": self.previous_version,
            "packet_source": self.packet_source,
            "partitions_written": list(self.partitions_written),
            "bytes_written": self.bytes_written,
            "failures": list(self.failures),
            "events": [
                {
                    "state": e.state.value,
                    "code": e.code,
                    "message": e.message,
                    "details": dict(e.details),
                }
                for e in self.events
            ],
        }


# Simulated NAND partition map, taken from FwPkt/camera/config mtdparts:
#   512K(u-boot.bin),512K(factoryParam),1M(userParam),5M(uImage),
#   40M(rootfs.ubifs),81M(appfs.ubifs)
MTD_PARTITIONS: Dict[str, int] = {
    "u-boot.bin": 512 * 1024,
    "factoryParam": 512 * 1024,
    "userParam": 1024 * 1024,
    "uImage": 5 * 1024 * 1024,
    "rootfs.ubifs": 40 * 1024 * 1024,
    "appfs.ubifs": 81 * 1024 * 1024,
}

# Which declared component lands in which partition. Gimbal firmware is written
# to the gimbal MCU over USB, not to NAND, so it has no MTD partition here.
COMPONENT_TO_PARTITION: Dict[str, str] = {
    "uImage": "uImage",
    "rootfs": "rootfs.ubifs",
    "appfs": "appfs.ubifs",
}

CARD_PACKET_NAMES = ("FwPkt.zip", "OmsPkt.zip")


class SimulatedDevice:
    """A camera that updates itself from an SD card volume.

    Args:
        current_version: firmware version the device reports before any update
        allow_downgrade: accept a packet older than the installed version. The
            real device's policy is not recorded in the retained evidence, so
            this defaults to rejecting and says so in the failure message.
    """

    def __init__(self, current_version: str = "4.0.0.32",
                 allow_downgrade: bool = False) -> None:
        self.state = DeviceState.IDLE
        self.current_version = current_version
        self.allow_downgrade = allow_downgrade
        self.events: List[DeviceEvent] = []
        # partition name -> sha256 of the payload last written there.
        self._partition_contents: Dict[str, str] = {}
        self._packet_source = "fat-cluster-chain"

    # -- event log -----------------------------------------------------------

    def _enter(self, state: DeviceState, code: str, message: str,
               **details: object) -> None:
        """Record entering a state."""
        self.state = state
        self.events.append(DeviceEvent(state=state, code=code, message=message,
                                       details=dict(details)))

    # -- card discovery ------------------------------------------------------

    def find_packets(self, image: SDCardImage) -> List[str]:
        """List firmware packet archives present on the card.

        Args:
            image: mounted SD volume

        Returns:
            Present packet names in CARD_PACKET_NAMES order
        """
        present = {entry.name for entry in image.list_root()}
        return [name for name in CARD_PACKET_NAMES if name in present]

    # -- update flow ---------------------------------------------------------

    def attempt_update(self, image: SDCardImage,
                       mock_packet_bytes: Optional[Dict[str, bytes]] = None) -> UpdateReport:
        """Run the whole SD-card update flow against a volume.

        Args:
            image: the SD volume to read
            mock_packet_bytes: test-only archive contents keyed by card name.
                This is an explicitly non-qualified fast path. When omitted,
                the packet is always read from the FAT cluster chain.

        Returns:
            UpdateReport describing every step and the terminal outcome
        """
        self.events = []
        self._packet_source = ("mock-only" if mock_packet_bytes is not None
                               else "fat-cluster-chain")
        previous = self.current_version
        partitions: List[str] = []
        total_bytes = 0

        self._enter(DeviceState.CARD_MOUNTED, "card_mounted",
                    "mounted SD card at /app/sd",
                    packet_source=self._packet_source)

        present = self.find_packets(image)
        if not present:
            self._enter(DeviceState.FAILED, "no_packet",
                        "no FwPkt.zip or OmsPkt.zip on the card")
            return self._report(UpdateOutcome.NO_PACKET, previous, partitions,
                                total_bytes,
                                ["card has no firmware packet"])

        if len(present) > 1:
            # The retained evidence shows both packets on one card and a later
            # capture with OmsPkt.zip removed, but never states which the device
            # picks. Choosing silently would make a simulated result look like a
            # verified device behaviour.
            self._enter(DeviceState.FAILED, "ambiguous_packets",
                        f"card holds {present}; retained evidence does not record "
                        f"which packet the device prefers",
                        packets=present)
            return self._report(UpdateOutcome.AMBIGUOUS_PACKETS, previous,
                                partitions, total_bytes,
                                [f"multiple packets present: {present}; refusing "
                                 f"to guess precedence"])

        name = present[0]
        self._enter(DeviceState.PACKET_FOUND, "packet_found",
                    f"found {name}", packet=name,
                    packet_source=self._packet_source)

        try:
            if mock_packet_bytes is not None:
                payload = mock_packet_bytes.get(name)
                if payload is None:
                    raise PacketLayoutError(
                        f"mock-only payload missing for discovered packet {name}"
                    )
            else:
                payload = image.read_file(name)
            packet = _packet_from_bytes(payload, name)
        except (ImageCorruptionError, PacketLayoutError,
                PacketIntegrityError) as exc:
            self._enter(DeviceState.FAILED, "invalid_packet", str(exc))
            return self._report(UpdateOutcome.INVALID_PACKET, previous, partitions,
                                total_bytes, [str(exc)])

        self._enter(DeviceState.VALIDATING, "packet_parsed",
                    f"{name} declares version {packet.version}",
                    version=packet.version,
                    components=[c.name for c in packet.components])

        missing = packet.missing_expected_components()
        if missing:
            self._enter(DeviceState.FAILED, "missing_components",
                        f"firmwareInfo omits {missing}", missing=missing)
            return self._report(UpdateOutcome.INVALID_PACKET, previous, partitions,
                                total_bytes,
                                [f"firmwareInfo omits {missing}"])

        comparison = compare_versions(packet.version, previous)
        if comparison < 0 and not self.allow_downgrade:
            self._enter(DeviceState.FAILED, "version_rejected",
                        f"packet {packet.version} is older than installed "
                        f"{previous}; downgrade policy is not recorded in the "
                        f"retained evidence so it is refused",
                        packet_version=packet.version, installed=previous)
            return self._report(UpdateOutcome.VERSION_REJECTED, previous, partitions,
                                total_bytes,
                                [f"refusing downgrade {packet.version} < {previous}"])

        # Write each NAND-bound component to its simulated partition.
        for record in packet.components:
            partition = COMPONENT_TO_PARTITION.get(record.name)
            if partition is None:
                self._enter(DeviceState.WRITING, "component_skipped",
                            f"{record.name} has no NAND partition (gimbal firmware "
                            f"is flashed over USB)", component=record.name)
                continue
            capacity = MTD_PARTITIONS[partition]
            if record.size > capacity:
                self._enter(DeviceState.FAILED, "partition_overflow",
                            f"{record.name} is {record.size} bytes but partition "
                            f"{partition} holds {capacity}",
                            partition=partition, size=record.size, capacity=capacity)
                return self._report(UpdateOutcome.WRITE_FAILED, previous, partitions,
                                    total_bytes,
                                    [f"{record.name} exceeds {partition} capacity"])

            try:
                member = packet.read_member(record.path)
            except PacketLayoutError as exc:
                self._enter(DeviceState.FAILED, "member_unreadable", str(exc))
                return self._report(UpdateOutcome.WRITE_FAILED, previous, partitions,
                                    total_bytes, [str(exc)])

            digest = hashlib.sha256(member).hexdigest()
            self._partition_contents[partition] = digest
            partitions.append(partition)
            total_bytes += len(member)
            self._enter(DeviceState.WRITING, "partition_written",
                        f"wrote {record.name} to {partition}",
                        partition=partition, size=len(member), sha256=digest)

        if not partitions:
            self._enter(DeviceState.FAILED, "nothing_written",
                        "packet declared no NAND-bound components")
            return self._report(UpdateOutcome.WRITE_FAILED, previous, partitions,
                                total_bytes, ["no NAND components written"])

        self._enter(DeviceState.REBOOTING, "reboot",
                    "device reboot simulated; uptime resets",
                    partitions=partitions)
        self._enter(DeviceState.VERIFYING, "verify",
                    "re-read partition digests after reboot")

        # Verification: the device compares its reported FwVer against the packet.
        expected_fwver = f"FwVer:{packet.version};date:{packet.date};"
        self.current_version = packet.version
        self._enter(DeviceState.UPDATED, "updated",
                    f"device reports {expected_fwver}",
                    fwver=expected_fwver)

        return self._report(UpdateOutcome.UPDATED, previous, partitions, total_bytes,
                            [], installed=packet.version)

    def _report(self, outcome: UpdateOutcome, previous: str,
                partitions: List[str], total_bytes: int, failures: List[str],
                installed: Optional[str] = None) -> UpdateReport:
        """Build the report for the current event log."""
        return UpdateReport(
            outcome=outcome,
            events=list(self.events),
            installed_version=installed if installed is not None else (
                self.current_version if outcome is UpdateOutcome.UPDATED else None),
            previous_version=previous,
            packet_source=self._packet_source,
            partitions_written=list(partitions),
            bytes_written=total_bytes,
            failures=list(failures),
        )


def _packet_from_bytes(payload: bytes, card_name: str) -> FirmwarePacket:
    """Parse a packet from raw archive bytes held on the card.

    Args:
        payload: zip bytes
        card_name: name on the card, used to classify FwPkt vs OmsPkt

    Returns:
        Parsed packet

    Raises:
        PacketLayoutError: if the bytes are not a valid packet
    """
    kind = "OmsPkt" if card_name.lower().startswith("omspkt") else "FwPkt"
    return FirmwarePacket.from_bytes(payload, kind=kind, label=card_name)
