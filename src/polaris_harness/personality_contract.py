"""Small fail-closed helper for camera personality consumers."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from .validation import validate_personality

SETTING_NAMES = ("iso", "shutter", "aperture", "white_balance")


def load_personality(path: str | Path) -> dict[str, Any]:
    """Load one schema-valid personality manifest."""

    manifest_path = Path(path)
    try:
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read personality {manifest_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("personality manifest must contain an object")
    errors = validate_personality(data)
    if errors:
        raise ValueError("invalid personality: " + "; ".join(errors))
    return data


class PersonalitySession:
    """Expose only the currently selected personality to a consumer.

    Selection replaces the previous personality completely. No sibling-camera
    values or options are carried across a session, and an unknown ``writable``
    value never authorises a write. This helper is a contract aid, not a camera
    implementation.
    """

    def __init__(self) -> None:
        self._personality: dict[str, Any] | None = None

    def select(self, personality: dict[str, Any]) -> None:
        """Select a validated personality and discard all prior state."""

        if not isinstance(personality, dict):
            raise TypeError("personality must be a mapping")
        self._personality = deepcopy(personality)

    def clear(self) -> None:
        """Remove the active personality and all setting state."""

        self._personality = None

    @property
    def personality_id(self) -> str | None:
        """Return the active personality ID, if any."""

        if self._personality is None:
            return None
        return self._personality.get("id")

    def setting(self, name: str) -> dict[str, Any] | None:
        """Return only the selected personality's setting observation."""

        if self._personality is None:
            return None
        settings = self._personality.get("settings", {})
        value = settings.get(name)
        return deepcopy(value) if isinstance(value, dict) else None

    def field_observed(self, name: str, field: str) -> bool:
        """Return true only for an explicitly observed setting field."""

        value = self.setting(name)
        field_value = value.get(field) if value else None
        return isinstance(field_value, dict) and field_value.get("observed") is True

    def can_write(self, name: str) -> bool:
        """Allow writes only when the manifest explicitly says writable=true."""

        value = self.setting(name)
        return bool(value and value.get("writable") is True)
