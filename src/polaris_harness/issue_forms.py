"""Validation for structured GitHub issue-form submissions."""

import re

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PROVENANCE_FIELDS = ("region_sha256", "source_binary_sha256")
_FIELD_RE = re.compile(
    r"^\s*(region_sha256|source_binary_sha256)\s*:\s*(.*?)\s*$"
)


def validate_pin_request_provenance(body: str) -> list[str]:
    """Validate provenance hashes in a rendered pin-request issue body.

    GitHub's required validation only checks that a textarea is nonempty.
    This gate is intended for downstream intake/triage, where the rendered issue
    body must contain one real SHA-256 value for both the extracted region and
    its source binary. Missing, duplicate, blank, placeholder, uppercase or
    incorrectly sized values are rejected.
    """
    values: dict[str, list[str]] = {field: [] for field in _PROVENANCE_FIELDS}
    for line in body.splitlines():
        match = _FIELD_RE.fullmatch(line)
        if match:
            values[match.group(1)].append(match.group(2))

    errors = []
    for field in _PROVENANCE_FIELDS:
        entries = values[field]
        if len(entries) != 1:
            errors.append(f"{field} must occur exactly once")
            continue
        if _SHA256_RE.fullmatch(entries[0]) is None:
            errors.append(
                f"{field} must be exactly 64 lowercase hexadecimal characters"
            )
    return errors
