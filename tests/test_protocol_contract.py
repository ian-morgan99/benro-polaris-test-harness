"""Tests for evidence-linked Polaris protocol command contracts."""

import copy
import json
from pathlib import Path

from polaris_harness.cli import main
from polaris_harness.protocol_contract import (
    default_safe_entries,
    find_contract,
    load_protocol_contract,
    validate_protocol_contract,
    validate_protocol_contract_file,
)

CONTRACT = Path("contracts/protocol/benro-9090-v1.json")


def test_protocol_pack_is_valid_and_safe_set_is_read_only():
    assert validate_protocol_contract_file(CONTRACT) == []
    contract = load_protocol_contract(CONTRACT)
    safe = default_safe_entries(contract)
    assert safe
    assert all(entry["safety_class"] == "read_only" for entry in safe)
    assert all(entry["status"] == "verified" for entry in safe)
    assert all(entry["automatic_probe"] for entry in safe)
    assert find_contract(contract, 789, 2)["default_safe"] is False


def test_lookup_requires_exact_code_and_subtype():
    contract = load_protocol_contract(CONTRACT)

    capture = find_contract(contract, 264, 4)
    assert capture is not None
    assert capture["completion"] == "asynchronous"
    assert find_contract(contract, 264) is None
    assert find_contract(contract, 277)["name"].startswith("SUN_SHOT")


def test_duplicate_code_and_subtype_is_rejected():
    contract = load_protocol_contract(CONTRACT)
    duplicate = copy.deepcopy(contract["entries"][0])
    duplicate["id"] = "duplicate-entry"
    contract["entries"].append(duplicate)

    errors = validate_protocol_contract(contract)

    assert any("incompatible active contract collision" in error for error in errors)


def test_unverified_and_destructive_entries_cannot_be_probed():
    contract = load_protocol_contract(CONTRACT)
    unverified = copy.deepcopy(contract["entries"][-2])
    unverified["id"] = "unsafe-unverified"
    unverified["automatic_probe"] = True
    contract["entries"].append(unverified)
    destructive = copy.deepcopy(
        next(entry for entry in contract["entries"] if entry["code"] == 789)
    )
    destructive["id"] = "unsafe-destructive"
    destructive["automatic_probe"] = True
    contract["entries"].append(destructive)

    errors = validate_protocol_contract(contract)

    assert any("unverified command cannot be safe/probed" in error for error in errors)
    assert any("destructive command cannot be safe/probed" in error for error in errors)


def test_protocol_cli_validates_pack_and_rejects_invalid_json(tmp_path):
    assert main(["protocol", "validate", "--contract", str(CONTRACT)]) == 0
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"schema_version": "1"}), encoding="utf-8")

    assert main(["protocol", "validate", "--contract", str(invalid)]) == 2
