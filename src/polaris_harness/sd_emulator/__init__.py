"""SD card emulation for Benro Polaris firmware-update testing.

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

Provides a byte-backed SD card volume plus a simulated device that performs the
firmware-update flow against it. Nothing here touches a real device, mounts
anything, or executes firmware.

Grounded in retained evidence (all read-only):
- SD layout from sd-card-snapshot-2026-09-06.tar: top-level FwPkt.zip and
  OmsPkt.zip, mode directories (normal, focusStack, panorama, Lapse, sun, HDR,
  starskyStack), and system/log/{Mlog,Clog,access,error}_NNNNNN.log.
- Packet layout from a real FwPkt.zip: FwPkt/{FwVer, firmwareInfo, camera/,
  gimbal/} with camera/{uImage, rootfs.ubifs, appfs.ubifs, config} and
  gimbal/polaris{403,413}_*.bin.
- firmwareInfo format: "<name> size:<n>;<name> MD5:<hex>;" per component.
- Partition map from FwPkt/camera/config mtdparts.
"""

from .image import (
    BlockAllocationError,
    DirectoryEntry,
    ImageCorruptionError,
    SDCardImage,
)
from .packet import (
    ComponentRecord,
    FirmwarePacket,
    PacketIntegrityError,
    PacketLayoutError,
    compare_versions,
    parse_firmware_info,
    parse_fwver,
)
from .builder import (
    LOG_KINDS,
    MODE_DIRECTORIES,
    build_benro_card,
    packet_from_local_file,
    run_update,
)
from .device import (
    MTD_PARTITIONS,
    DeviceEvent,
    DeviceState,
    SimulatedDevice,
    UpdateOutcome,
    UpdateReport,
)

__all__ = [
    "BlockAllocationError",
    "ComponentRecord",
    "DeviceEvent",
    "DeviceState",
    "DirectoryEntry",
    "FirmwarePacket",
    "ImageCorruptionError",
    "LOG_KINDS",
    "MODE_DIRECTORIES",
    "MTD_PARTITIONS",
    "PacketIntegrityError",
    "PacketLayoutError",
    "SDCardImage",
    "SimulatedDevice",
    "UpdateOutcome",
    "UpdateReport",
    "build_benro_card",
    "compare_versions",
    "packet_from_local_file",
    "parse_firmware_info",
    "parse_fwver",
    "run_update",
]
