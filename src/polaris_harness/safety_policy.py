"""Fail-closed policy checks for running scenarios against physical targets."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable

from .protocol_contract import default_protocol_contract_path, load_protocol_contract

SAFETY_CLASSES = frozenset(
    {
        "read_only",
        "state_write",
        "motion",
        "capture",
        "media_destructive",
        "firmware_admin",
        "unknown",
    }
)
MUTATING_CLASSES = frozenset(SAFETY_CLASSES - {"read_only"})
REVERSIBLE_CLASSES = frozenset({"state_write", "motion"})


@dataclass(frozen=True)
class SafetyIssue:
    """One fail-closed safety finding."""

    code: str
    message: str
    rule_id: str | None = None


@dataclass
class SafetyReport:
    """Machine-readable result of a scenario safety assessment."""

    target: str
    allowed: bool
    operations: list[dict[str, Any]] = field(default_factory=list)
    errors: list[SafetyIssue] = field(default_factory=list)
    warnings: list[SafetyIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "allowed": self.allowed,
            "operations": self.operations,
            "errors": [issue.__dict__ for issue in self.errors],
            "warnings": [issue.__dict__ for issue in self.warnings],
        }


def _request_rule_ids(scenario: dict[str, Any]) -> tuple[list[str], list[SafetyIssue]]:
    rule_ids: list[str] = []
    errors: list[SafetyIssue] = []
    for state_name, state in scenario.get("states", {}).items():
        for index, rule in enumerate(state.get("on_request", [])):
            rule_id = rule.get("id")
            if not rule_id:
                errors.append(
                    SafetyIssue(
                        "RULE_ID_REQUIRED",
                        f"state {state_name!r} request rule {index} has no stable id",
                    )
                )
                continue
            if rule_id in rule_ids:
                errors.append(
                    SafetyIssue(
                        "DUPLICATE_RULE_ID",
                        f"request rule id is duplicated: {rule_id}",
                        rule_id,
                    )
                )
            rule_ids.append(rule_id)
    return rule_ids, errors


def _request_identity(rule: dict[str, Any]) -> tuple[int, int | None] | None:
    """Parse only an exact textual Polaris request for contract binding."""
    match = rule.get("match", {})
    if match.get("type") != "exact_text":
        return None
    value = match.get("value", "")
    parsed = re.match(r"^\s*[^&]+&(?P<code>\d+)&(?P<subtype>[^&]*)", value)
    if parsed is None:
        return None
    subtype_text = parsed.group("subtype")
    return int(parsed.group("code")), int(subtype_text) if subtype_text else None


def assess_scenario_safety(
    scenario: dict[str, Any],
    target: str,
    unlocked_categories: Iterable[str] = (),
    contract: dict[str, Any] | None = None,
) -> SafetyReport:
    """Assess whether a scenario may run against the selected target.

    ``harness`` is a synthetic target and does not need physical unlocks.
    ``physical`` is fail-closed: a scenario must carry an explicit safety
    manifest, exact contract IDs for every request rule, an operator plan, and
    restoration/audit metadata for reversible mutations.
    """

    report = SafetyReport(target=target, allowed=True)
    if target not in {"harness", "physical"}:
        report.allowed = False
        report.errors.append(
            SafetyIssue("TARGET_INVALID", f"unsupported execution target: {target}")
        )
        return report
    if target == "harness":
        report.warnings.append(
            SafetyIssue(
                "SYNTHETIC_TARGET",
                "physical safety unlocks are not required for the harness target",
            )
        )
        return report

    if scenario.get("source") == "synthetic" or scenario.get("status") == "synthetic":
        report.errors.append(
            SafetyIssue(
                "SYNTHETIC_SCENARIO_PHYSICAL_TARGET",
                "synthetic scenarios cannot be approved for a physical target",
            )
        )
    if not scenario.get("source_issue") or not scenario.get("evidence_id"):
        report.errors.append(
            SafetyIssue(
                "EVIDENCE_PROVENANCE_REQUIRED",
                "physical execution requires source_issue and evidence_id",
            )
        )

    safety = scenario.get("safety")
    if not isinstance(safety, dict):
        report.errors.append(
            SafetyIssue(
                "SAFETY_CONTRACT_REQUIRED",
                "physical execution requires an explicit safety contract",
            )
        )
        report.allowed = False
        return report

    if safety.get("dry_run_plan") is not True:
        report.errors.append(
            SafetyIssue(
                "DRY_RUN_PLAN_REQUIRED",
                "physical execution requires a reviewed dry-run plan",
            )
        )
    if safety.get("ledger") != "required":
        report.errors.append(
            SafetyIssue(
                "LEDGER_REQUIRED",
                "physical execution requires an execution ledger",
            )
        )
    if safety.get("operator_abort") is not True:
        report.errors.append(
            SafetyIssue(
                "OPERATOR_ABORT_REQUIRED",
                "physical execution requires an operator stop/abort path",
            )
        )

    try:
        protocol = contract or load_protocol_contract(default_protocol_contract_path())
    except (OSError, ValueError) as exc:
        report.errors.append(
            SafetyIssue("PROTOCOL_CONTRACT_UNAVAILABLE", str(exc))
        )
        report.allowed = False
        return report
    contract_by_id = {entry["id"]: entry for entry in protocol["entries"]}
    unlocked = set(unlocked_categories)
    invalid_unlocks = unlocked - (SAFETY_CLASSES - {"read_only"})
    for category in sorted(invalid_unlocks):
        report.errors.append(
            SafetyIssue("UNLOCK_CATEGORY_INVALID", f"invalid safety unlock: {category}")
        )

    rule_ids, rule_errors = _request_rule_ids(scenario)
    report.errors.extend(rule_errors)
    rules_by_id = {
        rule["id"]: rule
        for state in scenario.get("states", {}).values()
        for rule in state.get("on_request", [])
        if rule.get("id")
    }
    operations = safety.get("operations", [])
    seen_operation_rules: set[str | None] = set()
    for operation in operations:
        operation_rule_id = operation.get("rule_id")
        if operation_rule_id in seen_operation_rules:
            report.errors.append(
                SafetyIssue(
                    "DUPLICATE_OPERATION_MAPPING",
                    f"safety operation is duplicated: {operation_rule_id}",
                    operation_rule_id,
                )
            )
        seen_operation_rules.add(operation_rule_id)
    by_rule = {operation.get("rule_id"): operation for operation in operations}
    for rule_id in rule_ids:
        if rule_id not in by_rule:
            report.errors.append(
                SafetyIssue(
                    "OPERATION_MAPPING_REQUIRED",
                    f"request rule has no safety operation mapping: {rule_id}",
                    rule_id,
                )
            )
    for rule_id in by_rule:
        if rule_id not in rule_ids:
            report.errors.append(
                SafetyIssue(
                    "OPERATION_MAPPING_STALE",
                    f"safety operation does not name a request rule: {rule_id}",
                    rule_id,
                )
            )

    for operation in operations:
        rule_id = operation.get("rule_id")
        contract_id = operation.get("contract_id")
        declared_class = operation.get("safety_class")
        entry = contract_by_id.get(contract_id)
        if entry is None:
            report.errors.append(
                SafetyIssue(
                    "CONTRACT_ID_UNKNOWN",
                    f"protocol contract entry not found: {contract_id}",
                    rule_id,
                )
            )
            continue
        actual_class = entry["safety_class"]
        rule = rules_by_id.get(rule_id)
        identity = _request_identity(rule) if rule is not None else None
        if identity is None:
            report.errors.append(
                SafetyIssue(
                    "REQUEST_BINDING_UNPROVABLE",
                    f"physical operation requires an exact textual request: {rule_id}",
                    rule_id,
                )
            )
        elif identity[0] != entry["code"] or (
            entry["subtype"] is not None and identity[1] != entry["subtype"]
        ) or (
            entry["subtype"] is None and identity[1] is not None
        ):
            report.errors.append(
                SafetyIssue(
                    "REQUEST_CONTRACT_MISMATCH",
                    f"request {identity} does not match contract {contract_id}",
                    rule_id,
                )
            )
        if declared_class != actual_class:
            report.errors.append(
                SafetyIssue(
                    "SAFETY_CLASS_MISMATCH",
                    f"{contract_id} declares {declared_class}, contract says {actual_class}",
                    rule_id,
                )
            )
        if actual_class == "unknown" or entry["status"] in {"unverified", "superseded"}:
            report.errors.append(
                SafetyIssue(
                    "UNKNOWN_OPERATION_FORBIDDEN",
                    f"unverified/unknown operation cannot run physically: {contract_id}",
                    rule_id,
                )
            )
        elif actual_class == "read_only" and not entry["default_safe"]:
            report.errors.append(
                SafetyIssue(
                    "READ_ONLY_NOT_ALLOWLISTED",
                    f"read-only command is not in the default physical allowlist: {contract_id}",
                    rule_id,
                )
            )
        elif actual_class != "read_only" and actual_class not in unlocked:
            report.errors.append(
                SafetyIssue(
                    "CATEGORY_UNLOCK_REQUIRED",
                    f"explicit unlock required for {actual_class}: {contract_id}",
                    rule_id,
                )
            )
        if actual_class in MUTATING_CLASSES and operation.get("retry") is not False:
            report.errors.append(
                SafetyIssue(
                    "MUTATION_RETRY_FORBIDDEN",
                    f"automatic retry is forbidden for {actual_class}: {contract_id}",
                    rule_id,
                )
            )
        if actual_class in REVERSIBLE_CLASSES:
            for field_name in ("state_snapshot", "restore", "readback"):
                if operation.get(field_name) is not True:
                    report.errors.append(
                        SafetyIssue(
                            "RESTORATION_METADATA_REQUIRED",
                            f"{field_name}=true is required for reversible {actual_class}: {contract_id}",
                            rule_id,
                        )
                    )
        if actual_class == "media_destructive":
            for field_name in ("disposable_fixture", "exact_scope"):
                if operation.get(field_name) is not True:
                    report.errors.append(
                        SafetyIssue(
                            "DISPOSABLE_SCOPE_REQUIRED",
                            f"{field_name}=true is required for media deletion: {contract_id}",
                            rule_id,
                        )
                    )
        report.operations.append(
            {
                "rule_id": rule_id,
                "contract_id": contract_id,
                "safety_class": actual_class,
                "unlocked": actual_class == "read_only" or actual_class in unlocked,
                "retry": operation.get("retry"),
            }
        )

    report.allowed = not report.errors
    return report
