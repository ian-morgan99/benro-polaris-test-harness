"""Load and validate evidence-linked Polaris command contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator
import yaml

_PACKAGED_DATA = Path(__file__).parent / "_data"
_REPO_ROOT = Path(__file__).parent.parent.parent


def _schema_path() -> Path:
    packaged = _PACKAGED_DATA / "schemas" / "protocol-contract.schema.json"
    if packaged.is_file():
        return packaged
    candidate = _REPO_ROOT / "schemas" / "protocol-contract.schema.json"
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(f"protocol contract schema not found: {candidate}")


def default_protocol_contract_path() -> Path:
    """Return the repository or packaged initial 9090 contract pack path."""

    packaged = _PACKAGED_DATA / "contracts" / "protocol" / "benro-9090-v1.json"
    if packaged.is_file():
        return packaged
    return _REPO_ROOT / "contracts" / "protocol" / "benro-9090-v1.json"


def _schema_errors(contract: dict[str, Any]) -> list[str]:
    schema = json.loads(_schema_path().read_text(encoding="utf-8"))
    validator = Draft7Validator(schema)
    return [error.message for error in validator.iter_errors(contract)]


def validate_protocol_contract(contract: dict[str, Any]) -> list[str]:
    """Validate schema and fail-closed safety/provenance invariants."""

    if not isinstance(contract, dict):
        return ["protocol contract must contain an object"]
    errors = _schema_errors(contract)
    if errors:
        return errors

    entries = contract["entries"]
    by_id: dict[str, dict[str, Any]] = {}
    by_key: dict[tuple[int, int | None], str] = {}
    for entry in entries:
        entry_id = entry["id"]
        if entry_id in by_id:
            errors.append(f"duplicate active contract id: {entry_id}")
        by_id[entry_id] = entry
        key = (entry["code"], entry["subtype"])
        if key in by_key:
            errors.append(
                "incompatible active contract collision for "
                f"code={key[0]} subtype={key[1]}: "
                f"{by_key[key]} vs {entry_id}"
            )
        by_key[key] = entry_id

        if entry["safety_class"] == "media_destructive" and (
            entry["automatic_probe"] or entry["default_safe"]
        ):
            errors.append(f"destructive command cannot be safe/probed: {entry_id}")
        if entry["status"] == "unverified" and (
            entry["automatic_probe"] or entry["default_safe"]
        ):
            errors.append(f"unverified command cannot be safe/probed: {entry_id}")
        if entry["confidence"] == "unknown" and (
            entry["automatic_probe"] or entry["default_safe"]
        ):
            errors.append(f"unknown-confidence command cannot be safe/probed: {entry_id}")
        if entry["default_safe"] and (
            entry["safety_class"] != "read_only"
            or entry["status"] != "verified"
            or entry["confidence"] not in {"physical", "trace"}
            or not entry["automatic_probe"]
        ):
            errors.append(
                "default-safe entry must be verified read-only with explicit "
                f"automatic probing: {entry_id}"
            )

    safe_ids = set(contract["default_safe"])
    unknown_safe_ids = safe_ids - set(by_id)
    errors.extend(
        f"default_safe references unknown entry: {entry_id}"
        for entry_id in sorted(unknown_safe_ids)
    )
    for entry_id in sorted(set(by_id) - safe_ids):
        if by_id[entry_id]["default_safe"]:
            errors.append(
                f"entry marked default_safe but absent from pack list: {entry_id}"
            )
    return errors


def load_protocol_contract(path: str | Path) -> dict[str, Any]:
    """Load one JSON/YAML contract pack and reject invalid data."""

    contract_path = Path(path)
    try:
        if contract_path.suffix.lower() in {".yaml", ".yml"}:
            contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        else:
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read protocol contract {contract_path}: {exc}") from exc
    errors = validate_protocol_contract(contract)
    if errors:
        raise ValueError("invalid protocol contract: " + "; ".join(errors))
    return contract


def validate_protocol_contract_file(path: str | Path) -> list[str]:
    """Return schema/safety errors for a contract file."""

    contract_path = Path(path)
    try:
        if contract_path.suffix.lower() in {".yaml", ".yml"}:
            contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        else:
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        return [f"cannot read protocol contract: {exc}"]
    return validate_protocol_contract(contract)


def find_contract(
    contract: dict[str, Any], code: int, subtype: int | None = None
) -> dict[str, Any] | None:
    """Find an exact code/subtype contract without inferring neighboring codes."""

    return next(
        (
            entry
            for entry in contract["entries"]
            if entry["code"] == code and entry["subtype"] == subtype
        ),
        None,
    )


def default_safe_entries(contract: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only entries explicitly named by the pack's safe-probe list."""

    by_id = {entry["id"]: entry for entry in contract["entries"]}
    return [by_id[entry_id] for entry_id in contract["default_safe"]]
