"""
Machine-readable invocation/result contract for ARM emulation.
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

Defines the JSON schema and serialization for ARM emulation run results,
binding every run to input binary SHA-256, patcher commit, harness revision,
emulator version, fixture/stub profile, command, and full machine-readable result.
"""

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from .emulator import EmulationResult, EmulationResultRecord, StubCallRecord


class ContractVersion(Enum):
    """Contract schema version."""
    V1 = "1"


@dataclass
class ArmEmulationContract:
    """
    Machine-readable contract for an ARM emulation run.

    Every field is required to ensure full provenance and reproducibility.
    This is the stable interface that consumers (e.g. the firmware patcher)
    depend on for release gating.
    """

    # Provenance bindings (mandatory per issue #15)
    binary_sha256: str
    patcher_commit: str
    harness_revision: str
    emulator_version: str
    fixture_stub_profile: str
    command: str

    # Emulation result
    result: str  # EmulationResult value as string
    exit_code: int
    duration_ms: int
    instructions_executed: int

    # Observed side effects
    stub_calls: List[Dict[str, Any]] = field(default_factory=list)
    final_registers: Dict[str, int] = field(default_factory=dict)

    # Error details (populated on failure)
    error_message: Optional[str] = None
    unsupported_instruction_address: Optional[int] = None
    unexpected_syscall_number: Optional[int] = None

    # Metadata
    contract_version: str = field(default=ContractVersion.V1.value)
    timestamp_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a dictionary suitable for JSON output."""
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    def write_to_file(self, path: Path, indent: int = 2) -> None:
        """Write the contract as JSON to a file.

        Args:
            path: Output file path
            indent: JSON indentation
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            f.write(self.to_json(indent=indent))
            f.write("\n")

    @classmethod
    def from_emulation_result(
        cls,
        result: EmulationResultRecord,
        contract_version: str = ContractVersion.V1.value,
    ) -> "ArmEmulationContract":
        """Create a contract from an emulation result record.

        Args:
            result: Emulation result record from ARMEmulator.run()
            contract_version: Contract schema version

        Returns:
            ArmEmulationContract instance
        """
        return cls(
            binary_sha256=result.binary_sha256,
            patcher_commit=result.patcher_commit,
            harness_revision=result.harness_revision,
            emulator_version=result.emulator_version,
            fixture_stub_profile=result.fixture_stub_profile,
            command=result.command,
            result=result.result.value,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            instructions_executed=result.instructions_executed,
            stub_calls=[
                {
                    "stub_name": call.stub_name,
                    "function_name": call.function_name,
                    "arguments": call.arguments,
                    "return_value": call.return_value,
                    "timestamp_ms": call.timestamp_ms,
                    "register_snapshot": call.register_snapshot,
                }
                for call in result.stub_calls
            ],
            final_registers=result.final_registers,
            error_message=result.error_message,
            unsupported_instruction_address=result.unsupported_instruction_address,
            unexpected_syscall_number=result.unexpected_syscall_number,
            contract_version=contract_version,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )

    def is_pass(self) -> bool:
        """Check if the emulation result is a pass.

        Returns:
            True if result is PASS
        """
        return self.result == EmulationResult.PASS.value

    def is_fail_closed(self) -> bool:
        """Check if the result represents a fail-closed condition.

        Returns:
            True if the result is any non-PASS outcome
        """
        return self.result != EmulationResult.PASS.value


def load_contract(path: Path) -> ArmEmulationContract:
    """Load a contract from a JSON file.

    Args:
        path: Path to the JSON contract file

    Returns:
        ArmEmulationContract instance

    Raises:
        FileNotFoundError: If the file does not exist
        json.JSONDecodeError: If the file is not valid JSON
        ValueError: If required fields are missing
    """
    with open(path) as f:
        data = json.load(f)

    required_fields = [
        "binary_sha256", "patcher_commit", "harness_revision",
        "emulator_version", "fixture_stub_profile", "command",
        "result", "exit_code", "duration_ms", "instructions_executed",
    ]
    missing = [f for f in required_fields if f not in data]
    if missing:
        raise ValueError(f"Missing required contract fields: {missing}")

    return ArmEmulationContract(**data)
