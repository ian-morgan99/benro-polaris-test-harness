"""Tests for lifecycle assertions that cannot be evaluated against an artifact.

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

Covers the distinction between an assertion that was evaluated and failed, and
one the executed artifact cannot answer. The two must not be reported the same
way: reporting "expected exactly 1 close call, got 0" for a function that
contains no close edge at all invites deleting the assertion to get green, which
silently discards part of issue #251's spec. Reporting it as PASS instead would
claim coverage that does not exist.
"""

import json
import shutil
import sys
from pathlib import Path

import pytest

from polaris_harness.arm_emulator.fixtures import FixtureManager
from polaris_harness.arm_emulator.lifecycle import (
    Case1NormalBulbCompletion,
    Case3CancelResultSurvivesStop,
    Case5DeferredTimedCaptureCancelled,
    LifecycleCase,
    run_all_lifecycle_cases,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "arm"


def _probe_dir(tmp_path: Path, mutate=None) -> Path:
    """Build a fixtures dir holding the synthetic probe as stage2_region.

    The suite defaults to stage2_region, so a case run against a temp dir needs
    the probe artifact under that name. ``mutate`` can edit the sidecar before it
    is written, which is how a fixture with fewer declared boundaries is made.

    Args:
        tmp_path: pytest temporary directory
        mutate: optional callable receiving the parsed sidecar dict

    Returns:
        Path to the fixtures directory
    """
    d = tmp_path / "arm"
    d.mkdir()
    sidecar = json.loads((FIXTURES_DIR / "lifecycle_probe_fixture.json").read_text())
    if mutate is not None:
        mutate(sidecar)
    shutil.copy(FIXTURES_DIR / "lifecycle_probe_fixture.bin", d / "stage2_region.bin")
    (d / "stage2_region.json").write_text(json.dumps(sidecar))
    return d


class TestCase5TimerOracle:
    """Timer queries must not be mistaken for an actual cancellation edge."""

    def test_timer_queries_do_not_qualify_cancellation(self, tmp_path):
        d = tmp_path / "arm"
        d.mkdir()
        sidecar = json.loads(
            (FIXTURES_DIR / "lifecycle_probe_fixture.json").read_text()
        )
        sidecar["provenance"]["stub_entries"] = [
            {"address": 4128, "stub_name": "timer",
             "function_name": "delayTime", "model": "target"},
            {"address": 4132, "stub_name": "timer",
             "function_name": "timediff_now", "model": "target"},
        ]
        shutil.copy(FIXTURES_DIR / "lifecycle_probe_fixture.bin",
                    d / "timer_only_fixture.bin")
        (d / "timer_only_fixture.json").write_text(json.dumps(sidecar))

        result = Case5DeferredTimedCaptureCancelled(
            LifecycleCase.CASE_5_DEFERRED_TIMED_CAPTURE_CANCELLED,
            FixtureManager(d), fixture_name="timer_only_fixture",
        ).run()

        assert result.contract is not None
        assert [c["function_name"] for c in result.contract.stub_calls] == [
            "delayTime", "timediff_now"
        ]
        assert not any(
            "Timer was not cancelled before expiry" in error
            for error in result.assertion_errors
        )
        assert not result.passed
        assert any("cancel_timer" in note for note in result.not_evaluated)


class TestNotEvaluatedReporting:
    """Absent boundaries must be reported as not evaluated, never as a failure."""

    def test_missing_boundary_is_not_an_assertion_error(self, tmp_path):
        """A case asserting on an undeclared edge reports it as unevaluated.

        The probe fixture declares stop and close but no cancel. Case 1 asserts
        normal completion avoids the cancel path; against this artifact that
        cannot be answered, so it belongs in not_evaluated rather than
        assertion_errors.
        """
        d = _probe_dir(tmp_path)
        case = Case1NormalBulbCompletion(
            LifecycleCase.CASE_1_NORMAL_BULB_COMPLETION,
            FixtureManager(d),
            fixture_name="stage2_region",
        )
        result = case.run()

        assert not result.skipped
        assert not any("cancel" in e.lower() for e in result.assertion_errors), (
            f"cancel was reported as a behavioural failure: {result.assertion_errors}"
        )
        assert any("cancel" in n.lower() for n in result.not_evaluated), (
            f"cancel gap not recorded: {result.not_evaluated}"
        )

    def test_partial_is_not_reported_as_passed(self, tmp_path):
        """is_partial must be true and passed false when something was skipped."""
        d = _probe_dir(tmp_path)
        case = Case1NormalBulbCompletion(
            LifecycleCase.CASE_1_NORMAL_BULB_COMPLETION,
            FixtureManager(d),
            fixture_name="stage2_region",
        )
        result = case.run()

        assert result.not_evaluated, "expected at least one unevaluated assertion"
        assert not result.passed, "a partial case must never report passed"
        assert result.is_partial

    def test_evaluated_assertions_still_run(self, tmp_path):
        """Guarding an absent edge must not disable the assertions that do apply.

        If _evaluable were used to skip the whole assertion block, a genuine
        regression such as two stop calls would go unnoticed.
        """
        d = _probe_dir(tmp_path)
        case = Case1NormalBulbCompletion(
            LifecycleCase.CASE_1_NORMAL_BULB_COMPLETION,
            FixtureManager(d),
            fixture_name="stage2_region",
        )
        result = case.run()

        # stop is declared and observed once, so its assertion is evaluated and
        # holds. close is declared too, so it is evaluated as well.
        assert not any("stop" in e for e in result.assertion_errors), (
            f"stop assertion failed unexpectedly: {result.assertion_errors}"
        )
        assert result.contract is not None and result.contract.is_pass()

    def test_case_3_reports_missing_cancel(self, tmp_path):
        """Case 3's cancel assertion must be unevaluated, not a false masking bug.

        "Cancel was not invoked - result may have been lost" reads as a firmware
        masking defect when the function never calls cancel at all.
        """
        d = _probe_dir(tmp_path)
        case = Case3CancelResultSurvivesStop(
            LifecycleCase.CASE_3_CANCEL_RESULT_SURVIVES_STOP,
            FixtureManager(d),
            fixture_name="stage2_region",
        )
        result = case.run()

        assert not any("may have been lost" in e for e in result.assertion_errors), (
            f"missing cancel reported as a masking bug: {result.assertion_errors}"
        )
        assert any("cancel" in n.lower() for n in result.not_evaluated)

    def test_notes_are_not_duplicated(self, tmp_path):
        """A gap named by the case must not also appear as a generic base note."""
        d = _probe_dir(tmp_path)
        case = Case3CancelResultSurvivesStop(
            LifecycleCase.CASE_3_CANCEL_RESULT_SURVIVES_STOP,
            FixtureManager(d),
            fixture_name="stage2_region",
        )
        result = case.run()

        cancel_notes = [n for n in result.not_evaluated if "cancel" in n.lower()]
        assert len(cancel_notes) == 1, f"cancel gap reported {len(cancel_notes)}x: {cancel_notes}"

    def test_accumulator_resets_between_runs(self, tmp_path):
        """A reused scenario instance must not carry notes from a previous run."""
        d = _probe_dir(tmp_path)
        case = Case3CancelResultSurvivesStop(
            LifecycleCase.CASE_3_CANCEL_RESULT_SURVIVES_STOP,
            FixtureManager(d),
            fixture_name="stage2_region",
        )
        first = case.run()
        second = case.run()

        assert len(first.not_evaluated) == len(second.not_evaluated), (
            "not_evaluated grew across runs: "
            f"{len(first.not_evaluated)} then {len(second.not_evaluated)}"
        )


class TestFailureStillBeatsPartial:
    """A real behavioural failure must not be softened into a partial."""

    def test_faulting_run_is_a_failure_not_partial(self, tmp_path):
        """An entry address that faults is a real failure, exit 3 not 4."""
        def break_entry(sidecar):
            sidecar["provenance"]["entry_address"] = 0x9000

        d = _probe_dir(tmp_path, mutate=break_entry)
        results = run_all_lifecycle_cases(FixtureManager(d), fixture_name="stage2_region")

        ran = [r for r in results.values() if not r.skipped]
        assert ran, "expected at least one case to execute"
        assert any(not r.passed and not r.is_partial for r in ran), (
            "a faulting run must be a real failure, not partial"
        )


class TestCliPartialExitCode:
    """Exit 4 must distinguish incomplete coverage from verified success."""

    def _argv(self, monkeypatch, *extra):
        monkeypatch.setattr(sys, "argv", ["polaris-harness", "arm-emulate", *extra])

    def _stub_results(self, monkeypatch, make_result):
        """Force the suite to report exactly one synthesised result.

        Running the real suite against the probe cannot produce a pure-partial
        outcome: cases 5-9 assert on timer/callback/output edges the probe does
        not have, so they fail for real and exit 3 is correct. The aggregation
        decision is what needs testing, so the runner is stubbed.

        Args:
            monkeypatch: pytest monkeypatch
            make_result: callable building the LifecycleTestResult to return
        """
        import polaris_harness.cli as cli

        def fake_runner(fixture_manager, **kwargs):
            case = LifecycleCase.CASE_1_NORMAL_BULB_COMPLETION
            return {case: make_result(case)}

        monkeypatch.setattr(cli, "run_all_lifecycle_cases", fake_runner)

    def test_partial_only_exits_four_not_zero(self, tmp_path, monkeypatch, capsys):
        """Nothing contradicted but spec unverified must not look like success."""
        from polaris_harness.arm_emulator.contract import ArmEmulationContract
        from polaris_harness.arm_emulator.lifecycle import LifecycleTestResult

        def make(case):
            contract = ArmEmulationContract(
                binary_sha256="a" * 64, patcher_commit="x",
                harness_revision="0.1.0", emulator_version="unicorn-2.1.4",
                fixture_stub_profile="lifecycle", command="captureBulbImage",
                result="pass", exit_code=0, duration_ms=1,
                instructions_executed=154, mode="arm",
            )
            return LifecycleTestResult(
                case=case, passed=True, contract=contract,
                not_evaluated=["close: no close edge in the executed region"],
                fixture_name="capture_bulb_image_region",
            )

        self._stub_results(monkeypatch, make)
        d = _probe_dir(tmp_path)
        self._argv(monkeypatch, "--suite", "--fixtures-dir", str(d))
        code = main_exit_code()

        out = capsys.readouterr().out
        assert code == 4, f"expected exit 4 for partial coverage, got {code}: {out}"
        assert "PARTIAL" in out, f"partial cases not labelled: {out}"
        assert "not evaluated" in out

    def test_real_failure_still_exits_three(self, tmp_path, monkeypatch):
        """Exit 3 must win over 4 when a case genuinely fails."""
        def break_entry(sidecar):
            sidecar["provenance"]["entry_address"] = 0x9000

        d = _probe_dir(tmp_path, mutate=break_entry)
        self._argv(monkeypatch, "--suite", "--fixtures-dir", str(d))
        assert main_exit_code() == 3


def main_exit_code() -> int:
    """Run the CLI entry point and return its exit code.

    Returns:
        Exit code from polaris_harness.cli.main
    """
    from polaris_harness.cli import main
    return main()
