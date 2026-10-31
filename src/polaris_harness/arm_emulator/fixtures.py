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
from typing import Any, Dict, Optional

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

            if meta_file.exists():
                try:
                    with open(meta_file) as f:
                        meta = json.load(f)
                    description = meta.get("description", "")
                    source_issue = meta.get("source_issue")
                    provenance = meta.get("provenance", {})
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
            )

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
        entry_address: int = 0x1000,
        **kwargs,
    ) -> EmulationConfig:
        """Create an EmulationConfig from a fixture.

        Args:
            fixture_name: Name of the fixture to use
            patcher_commit: Patcher commit SHA
            harness_revision: Harness revision
            emulator_version: Emulator version
            stub_profile: Stub profile name
            command: Command being emulated
            entry_address: Entry point address
            **kwargs: Additional EmulationConfig overrides

        Returns:
            EmulationConfig instance

        Raises:
            ValueError: If the fixture is not found
        """
        fixture = self.get_fixture(fixture_name)
        if fixture is None:
            raise ValueError(f"Fixture not found: {fixture_name}")

        config = EmulationConfig(
            binary_path=fixture.path,
            binary_sha256=fixture.sha256,
            patcher_commit=patcher_commit,
            harness_revision=harness_revision,
            emulator_version=emulator_version,
            fixture_stub_profile=stub_profile,
            command=command,
            entry_address=entry_address,
        )

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
