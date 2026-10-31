"""
Fixture loading and management for ARM emulation.
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

Provides access to checked-in ARM fixture binaries with SHA-256 verification
and provenance tracking. Fixtures are never mutated.
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .emulator import EmulationConfig


@dataclass
class FixtureMetadata:
    """Metadata for an ARM fixture binary."""

    name: str
    path: Path
    sha256: str
    size_bytes: int
    description: str
    source_issue: Optional[str] = None
    provenance: Dict[str, Any] = field(default_factory=dict)

    # Execution parameters declared by the sidecar. These are required to run a
    # real extracted region correctly: an ARM region decoded as Thumb faults on
    # instruction one, and external calls only reach stubs when their entry
    # addresses are registered.
    mode: Optional[str] = None
    entry_address: Optional[int] = None
    code_address: Optional[int] = None
    load_address: Optional[int] = None
    code_size: Optional[int] = None
    max_instructions: Optional[int] = None
    # SHA-256 recorded in the sidecar. When present, discovery verifies the
    # bytes on disk against it and execution refuses to proceed on a mismatch
    # (issue #18: "refuses to run it against a mismatched recorded hash").
    expected_sha256: Optional[str] = None
    # [{"address": int, "stub_name": str, "function_name": str,
    #   "model": "call-site"|"target", "abi_shape": {...}}]
    stub_entries: List[Dict[str, Any]] = field(default_factory=list)


class FixtureManager:
    """Manages ARM fixture binaries with integrity verification."""

    def __init__(self, fixtures_dir: Path):
        self.fixtures_dir = Path(fixtures_dir)
        self._fixtures: Dict[str, FixtureMetadata] = {}
        self._discover_fixtures()

    def _discover_fixtures(self) -> None:
        """Discover all fixture binaries in the fixtures directory."""
        if not self.fixtures_dir.exists():
            return

        for bin_file in self.fixtures_dir.glob("*.bin"):
            name = bin_file.stem
            sha256 = self._compute_sha256(bin_file)
            size = bin_file.stat().st_size

            # Look for a sidecar metadata JSON file
            meta_file = bin_file.with_suffix(".json")
            description = ""
            source_issue = None
            provenance = {}
            mode = None
            entry_address = None
            code_address = None
            load_address = None
            code_size = None
            max_instructions = None
            expected_sha256 = None
            stub_entries: List[Dict[str, Any]] = []

            if meta_file.exists():
                try:
                    with open(meta_file) as f:
                        meta = json.load(f)
                    description = meta.get("description", "")
                    source_issue = meta.get("source_issue")
                    provenance = meta.get("provenance", {})

                    prov = provenance if isinstance(provenance, dict) else {}
                    mode = prov.get("mode")
                    entry_address = self._coerce_int(prov.get("entry_address"))
                    code_address = self._coerce_int(prov.get("code_address"))
                    load_address = self._coerce_int(prov.get("load_address"))
                    code_size = self._coerce_int(prov.get("code_size"))
                    max_instructions = self._coerce_int(
                        prov.get("max_instructions"))
                    entries = prov.get("stub_entries", [])
                    if isinstance(entries, list):
                        stub_entries = [e for e in entries if isinstance(e, dict)]
                    recorded = prov.get("region_sha256") or prov.get("sha256")
                    if isinstance(recorded, str) and recorded.strip():
                        expected_sha256 = recorded.strip().lower()
                except (json.JSONDecodeError, OSError):
                    pass

            self._fixtures[name] = FixtureMetadata(
                name=name,
                path=bin_file,
                sha256=sha256,
                size_bytes=size,
                description=description,
                source_issue=source_issue,
                provenance=provenance,
                mode=mode,
                entry_address=entry_address,
                code_address=code_address,
                load_address=load_address,
                code_size=code_size,
                max_instructions=max_instructions,
                expected_sha256=expected_sha256,
                stub_entries=stub_entries,
            )

    @staticmethod
    def _coerce_int(value: Any) -> Optional[int]:
        """Coerce a sidecar value to an int, accepting hex strings.

        Provenance is usually recorded in hex (``"0x101aac"``). Accepting both
        forms avoids a silent misread or crash when a real region's sidecar is
        written from those notes.

        Args:
            value: int, decimal/hex string, or None

        Returns:
            The integer value, or None if it cannot be interpreted
        """
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value, 0)
            except ValueError:
                return None
        return None

    @staticmethod
    def _compute_sha256(path: Path) -> str:
        """Compute SHA-256 of a file.

        Args:
            path: Path to the file

        Returns:
            Hex-encoded SHA-256 digest
        """
        sha256 = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def list_fixtures(self) -> Dict[str, FixtureMetadata]:
        """List all discovered fixtures.

        Returns:
            Dictionary mapping fixture name to metadata
        """
        return dict(self._fixtures)

    def get_fixture(self, name: str) -> Optional[FixtureMetadata]:
        """Get a fixture by name.

        Args:
            name: Fixture name (stem of the .bin file)

        Returns:
            FixtureMetadata or None if not found
        """
        return self._fixtures.get(name)

    def verify_fixture(self, name: str, expected_sha256: str) -> bool:
        """Verify a fixture's SHA-256 matches the expected value.

        Args:
            name: Fixture name
            expected_sha256: Expected SHA-256 digest

        Returns:
            True if the fixture matches, False otherwise
        """
        fixture = self.get_fixture(name)
        if fixture is None:
            return False
        return fixture.sha256 == expected_sha256

    def to_emulation_config(
        self,
        fixture_name: str,
        patcher_commit: str,
        harness_revision: str,
        emulator_version: str,
        stub_profile: str,
        command: str,
        entry_address: Optional[int] = None,
        mode: Optional[str] = None,
        **kwargs,
    ) -> EmulationConfig:
        """Create an EmulationConfig from a fixture.

        Execution parameters come from the fixture sidecar when present, so a
        real ARM region loads in ARM mode at its recorded entry address instead
        of silently defaulting to Thumb at 0x1000. Explicit arguments always
        win over sidecar values.

        Args:
            fixture_name: Name of the fixture to use
            patcher_commit: Patcher commit SHA
            harness_revision: Harness revision
            emulator_version: Emulator version
            stub_profile: Stub profile name
            command: Command being emulated
            entry_address: Override for the entry point address
            mode: Override for the instruction mode ("arm" or "thumb")
            **kwargs: Additional EmulationConfig overrides

        Returns:
            EmulationConfig instance

        Raises:
            ValueError: If the fixture is not found, or its sidecar declares an
                unknown instruction mode (fail closed)
        """
        fixture = self.get_fixture(fixture_name)
        if fixture is None:
            raise ValueError(f"Fixture not found: {fixture_name}")

        # Fail closed on a hash mismatch (issue #18). The digest recorded in the
        # sidecar is the contract; recomputing here rather than trusting the
        # value cached at discovery means a blob swapped or truncated after
        # discovery still cannot be executed. This is what makes "bound every
        # run to input binary SHA-256" true rather than cosmetic.
        if fixture.expected_sha256:
            actual_sha256 = self._compute_sha256(fixture.path)
            if actual_sha256 != fixture.expected_sha256:
                raise ValueError(
                    f"Fixture {fixture_name} SHA-256 mismatch: recorded "
                    f"{fixture.expected_sha256}, on-disk {actual_sha256}. "
                    f"Refusing to run a provenance-pinned artifact whose bytes "
                    f"do not match its recorded hash."
                )

        resolved_mode = mode if mode is not None else (fixture.mode or "thumb")
        if resolved_mode not in ("arm", "thumb"):
            raise ValueError(
                f"Fixture {fixture_name} declares invalid mode {resolved_mode!r}; "
                f"expected 'arm' or 'thumb'"
            )

        if entry_address is not None:
            resolved_entry = int(entry_address)
        elif fixture.entry_address is not None:
            resolved_entry = int(fixture.entry_address)
        else:
            resolved_entry = 0x1000

        config = EmulationConfig(
            binary_path=fixture.path,
            binary_sha256=fixture.sha256,
            patcher_commit=patcher_commit,
            harness_revision=harness_revision,
            emulator_version=emulator_version,
            fixture_stub_profile=stub_profile,
            command=command,
            entry_address=resolved_entry,
            mode=resolved_mode,
        )

        # Sidecar-level memory/limit overrides, still subordinate to kwargs.
        if fixture.code_address is not None:
            config.code_address = int(fixture.code_address)
        if fixture.load_address is not None:
            config.load_address = int(fixture.load_address)
        if fixture.code_size is not None:
            config.code_size = int(fixture.code_size)
        if fixture.max_instructions is not None:
            config.max_instructions = int(fixture.max_instructions)

        # Apply any overrides
        for key, value in kwargs.items():
            if hasattr(config, key):
                setattr(config, key, value)

        return config

    def read_binary(self, name: str) -> bytes:
        """Read a fixture binary's contents (read-only).

        Args:
            name: Fixture name

        Returns:
            Raw binary bytes

        Raises:
            ValueError: If the fixture is not found
        """
        fixture = self.get_fixture(name)
        if fixture is None:
            raise ValueError(f"Fixture not found: {name}")

        with open(fixture.path, "rb") as f:
            return f.read()
