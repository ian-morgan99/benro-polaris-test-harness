"""Fail-closed physical-target safety policy tests."""

from polaris_harness.safety_policy import assess_scenario_safety


def scenario_with_operation(
    contract_id="cam-get-state-286",
    safety_class="read_only",
    **operation_fields,
):
    request_by_contract = {
        "cam-get-state-286": "1&286&&#",
        "wifi-band-set-803": "1&803&&band:1;#",
        "file-delete-all-789": "1&789&2&path:normal/#",
        "focus-set-262": "1&262&&#",
    }
    operation = {
        "rule_id": "read-state",
        "contract_id": contract_id,
        "safety_class": safety_class,
        "retry": False,
        **operation_fields,
    }
    return {
        "id": "physical-read-state-v1",
        "version": 1,
        "title": "Physical read-only safety fixture",
        "source": "physical",
        "status": "provisional",
        "source_issue": "owner/repo#1",
        "evidence_id": "evidence-1",
        "states": {
            "idle": {
                "on_request": [
                    {
                        "id": "read-state",
                        "match": {
                            "type": "exact_text",
                            "value": request_by_contract.get(
                                contract_id, "1&286&&#"
                            ),
                        },
                        "actions": [],
                    }
                ]
            }
        },
        "safety": {
            "dry_run_plan": True,
            "ledger": "required",
            "operator_abort": True,
            "operations": [operation],
        },
    }


def test_read_only_physical_scenario_is_allowed_by_default():
    report = assess_scenario_safety(scenario_with_operation(), "physical")

    assert report.allowed
    assert report.errors == []
    assert report.operations[0]["unlocked"]


def test_harness_target_does_not_require_physical_unlocks():
    scenario = scenario_with_operation()
    scenario["source"] = "synthetic"
    scenario["status"] = "synthetic"
    scenario.pop("safety")

    report = assess_scenario_safety(scenario, "harness")

    assert report.allowed
    assert report.warnings[0].code == "SYNTHETIC_TARGET"


def test_physical_target_requires_safety_metadata_and_provenance():
    scenario = scenario_with_operation()
    scenario.pop("safety")
    scenario.pop("evidence_id")

    report = assess_scenario_safety(scenario, "physical")

    codes = {issue.code for issue in report.errors}
    assert not report.allowed
    assert "SAFETY_CONTRACT_REQUIRED" in codes
    assert "EVIDENCE_PROVENANCE_REQUIRED" in codes


def test_state_write_requires_unlock_snapshot_restore_and_readback():
    scenario = scenario_with_operation(
        "wifi-band-set-803",
        "state_write",
        state_snapshot=True,
        restore=True,
        readback=True,
    )

    locked = assess_scenario_safety(scenario, "physical")
    assert not locked.allowed
    assert any(issue.code == "CATEGORY_UNLOCK_REQUIRED" for issue in locked.errors)

    unlocked = assess_scenario_safety(
        scenario, "physical", unlocked_categories=("state_write",)
    )
    assert unlocked.allowed
    assert unlocked.errors == []


def test_physical_operation_must_bind_to_exact_request_identity():
    scenario = scenario_with_operation()
    scenario["safety"]["operations"][0]["contract_id"] = "file-list-770"

    report = assess_scenario_safety(scenario, "physical")

    assert not report.allowed
    assert any(issue.code == "REQUEST_CONTRACT_MISMATCH" for issue in report.errors)


def test_non_exact_request_matching_is_not_physically_approvable():
    scenario = scenario_with_operation()
    scenario["states"]["idle"]["on_request"][0]["match"] = {
        "type": "regex_text",
        "pattern": "1&286",
        "rationale": "synthetic test",
    }

    report = assess_scenario_safety(scenario, "physical")

    assert not report.allowed
    assert any(
        issue.code == "REQUEST_BINDING_UNPROVABLE" for issue in report.errors
    )


def test_mutating_retry_and_missing_restoration_are_rejected():
    scenario = scenario_with_operation(
        "wifi-band-set-803",
        "state_write",
        retry=True,
    )

    report = assess_scenario_safety(
        scenario, "physical", unlocked_categories=("state_write",)
    )

    codes = {issue.code for issue in report.errors}
    assert "MUTATION_RETRY_FORBIDDEN" in codes
    assert "RESTORATION_METADATA_REQUIRED" in codes


def test_unknown_and_destructive_operations_are_not_default_safe():
    unknown = scenario_with_operation("focus-set-262", "unknown")
    unknown_report = assess_scenario_safety(unknown, "physical")
    assert not unknown_report.allowed
    assert any(
        issue.code == "UNKNOWN_OPERATION_FORBIDDEN"
        for issue in unknown_report.errors
    )

    destructive = scenario_with_operation(
        "file-delete-all-789",
        "media_destructive",
        disposable_fixture=True,
        exact_scope=True,
    )
    locked = assess_scenario_safety(destructive, "physical")
    assert not locked.allowed
    assert any(
        issue.code == "CATEGORY_UNLOCK_REQUIRED"
        for issue in locked.errors
    )

    unlocked = assess_scenario_safety(
        destructive, "physical", unlocked_categories=("media_destructive",)
    )
    assert unlocked.allowed
