"""Helpers for assembling a Benro-shaped SD card volume.

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

Separates "what a Benro card looks like" from "what one test needs", so tests
and the CLI build the same shape rather than each inventing their own. The mode
directory names and system/log layout come from a retained SD-card snapshot;
log file contents are synthetic because real logs were not retained with
content worth reproducing here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .device import SimulatedDevice, UpdateReport
from .image import SDCardImage

# Mode directories seen on a real card. Names are mixed case exactly as they
# appeared, because that is what forces LFN entries to be exercised.
MODE_DIRECTORIES: Tuple[str, ...] = (
    "normal",
    "focusStack",
    "panorama",
    "Lapse",
    "sun",
    "HDR",
    "starskyStack",
)

# Log file name pattern seen under system/log in the retained snapshot.
LOG_FILE_TEMPLATE = "{kind}_{index:06d}.log"
LOG_KINDS = ("Mlog", "Clog", "access", "error")


def build_benro_card(total_bytes: int = 32 * 1024 * 1024 * 1024,
                     volume_label: str = "BENRO",
                     log_count: int = 1,
                     packets: Optional[Iterable[Tuple[str, bytes]]] = None,
                     mode_directories: Iterable[str] = MODE_DIRECTORIES,
                     ) -> SDCardImage:
    """Build a FAT32 volume shaped like a Benro SD card.

    Args:
        total_bytes: volume size
        volume_label: label written to the boot sector and root
        log_count: number of each log kind to create under system/log
        packets: (name, bytes) pairs to place at the card root, e.g.
            ("FwPkt.zip", payload)
        mode_directories: top-level mode directory names

    Returns:
        A flushed, readable volume
    """
    image = SDCardImage(total_bytes=total_bytes, volume_label=volume_label)
    image.write_boot_sector()

    for name in mode_directories:
        image.add_directory(name)

    system = image.add_directory("system")
    log_dir = image.add_directory("log", parent_cluster=system.first_cluster)
    for kind in LOG_KINDS:
        for index in range(log_count):
            name = LOG_FILE_TEMPLATE.format(kind=kind, index=index)
            # Synthetic content: the retained snapshot's logs are not reproduced
            # here, but their names and location are what the device looks for.
            image.add_file(name, f"{kind} {index}\n".encode("utf-8"),
                           parent_cluster=log_dir.first_cluster)

    for name, payload in (packets or []):
        image.add_file(name, payload)

    image.flush_directories()
    return image


def run_update(image: SDCardImage, current_version: str = "4.0.0.32",
               allow_downgrade: bool = False,
               mock_packet_bytes: Optional[dict] = None) -> UpdateReport:
    """Run one simulated update against a volume.

    Args:
        image: the card to read
        current_version: version the device reports beforehand
        allow_downgrade: accept an older packet
        mock_packet_bytes: explicit test-only payload injection keyed by card
            name; omit this for the qualified FAT cluster-chain path

    Returns:
        The update report
    """
    device = SimulatedDevice(current_version=current_version,
                             allow_downgrade=allow_downgrade)
    return device.attempt_update(image, mock_packet_bytes=mock_packet_bytes)


def packet_from_local_file(path: Path) -> bytes:
    """Read a packet archive from disk.

    Args:
        path: path to FwPkt.zip or OmsPkt.zip

    Returns:
        Archive bytes

    Raises:
        FileNotFoundError: if the path does not exist
    """
    resolved = Path(path)
    if not resolved.exists():
        raise FileNotFoundError(f"packet file not found: {resolved}")
    return resolved.read_bytes()
