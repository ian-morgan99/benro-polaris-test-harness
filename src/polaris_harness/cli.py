#!/usr/bin/env python3
"""
Command-line interface for Polaris Harness.

Provides deterministic protocol/runtime testing for Benro Polaris ecosystem.
"""

import argparse
import json
import sys
from pathlib import Path

from .version import __version__
from .arm_emulator import (
    ARMEmulator,
    EmulationResult,
    FixtureManager,
    run_all_lifecycle_cases,
)
from .arm_emulator.contract import ArmEmulationContract


def validate_command(args: argparse.Namespace) -> int:
    """Validate scenario or directory against schema."""
    print(f"Validating: {args.path}")
    # TODO: Implement schema validation
    print("✓ Schema validation not yet implemented")
    return 0


def serve_command(args: argparse.Namespace) -> int:
    """Serve a scenario as a deterministic protocol endpoint."""
    print(f"Serving scenario: {args.scenario}")
    print(f"Host: {args.host}, Port: {args.port}")
    # TODO: Implement scenario serving
    print("✓ Scenario serving not yet implemented")
    return 0


def replay_command(args: argparse.Namespace) -> int:
    """Replay a scenario against a running endpoint."""
    print(f"Replaying scenario: {args.scenario}")
    print(f"Against: {args.host}:{args.port}")
    # TODO: Implement scenario replay
    print("✓ Scenario replay not yet implemented")
    return 0


def runtime_verify_command(args: argparse.Namespace) -> int:
    """Verify runtime package against manifest."""
    print(f"Verifying runtime: {args.root}")
    print(f"Manifest: {args.manifest}")
    # TODO: Implement runtime verification
    print("✓ Runtime verification not yet implemented")
    return 0


def runtime_report_command(args: argparse.Namespace) -> int:
    """Generate runtime verification report."""
    print(f"Generating runtime report for: {args.root}")
    print(f"Manifest: {args.manifest}")
    print(f"Output: {args.json}")
    # TODO: Implement runtime reporting
    print("✓ Runtime reporting not yet implemented")
    return 0


def trace_ingest_command(args: argparse.Namespace) -> int:
    """Ingest physical trace into harness format."""
    print(f"Ingesting trace: {args.input}")
    print(f"Output: {args.output}")
    # TODO: Implement trace ingestion
    print("✓ Trace ingestion not yet implemented")
    return 0


def arm_emulate_command(args: argparse.Namespace) -> int:
    """Run ARM emulation for Stage-2 lifecycle tests.

    This is an independent component (issue #15) that does not couple to the
    Polaris protocol scenario engine or runtime verifier.
    """
    fixtures_dir = Path(args.fixtures_dir) if args.fixtures_dir else None
    fixture_manager = FixtureManager(fixtures_dir) if fixtures_dir else FixtureManager(
        Path(__file__).parent.parent.parent / "tests" / "fixtures" / "arm"
    )

    if args.suite:
        # Run all 9 lifecycle cases
        print("Running ARM lifecycle suite (issue #15)...")
        results = run_all_lifecycle_cases(
            fixture_manager,
            patcher_commit=args.patcher_commit or "unknown",
            harness_revision=args.harness_revision or "0.1.0",
            emulator_version=args.emulator_version or "unknown",
        )

        all_passed = True
        for case, result in results.items():
            status = "PASS" if result.passed else "FAIL"
            print(f"  {case.value}: {status}")
            if not result.passed:
                all_passed = False
                for err in result.assertion_errors:
                    print(f"    - {err}")
                if result.contract and not result.contract.is_pass():
                    print(f"    result: {result.contract.result}")
                    print(f"    exit_code: {result.contract.exit_code}")
                    print(f"    error: {result.contract.error_message}")

        if all_passed:
            print("✓ All ARM lifecycle cases passed")
            return 0
        else:
            print("✗ Some ARM lifecycle cases failed")
            return 3

    elif args.fixture:
        # Run a single fixture emulation
        fixture = fixture_manager.get_fixture(args.fixture)
        if fixture is None:
            print(f"✗ Fixture not found: {args.fixture}")
            return 2

        from .arm_emulator import EmulationConfig

        config = EmulationConfig(
            binary_path=fixture.path,
            binary_sha256=fixture.sha256,
            patcher_commit=args.patcher_commit or "unknown",
            harness_revision=args.harness_revision or "0.1.0",
            emulator_version=args.emulator_version or "unknown",
            fixture_stub_profile=args.stub_profile or "default",
            command=args.command or "capture_image_with_Bulb",
            entry_address=args.entry_address or 0x1000,
        )

        emulator = ARMEmulator(config)
        result_record = emulator.run()
        contract = ArmEmulationContract.from_emulation_result(result_record)

        output_path = Path(args.output) if args.output else None
        if output_path:
            contract.write_to_file(output_path)
            print(f"✓ Contract written to {output_path}")
        else:
            print(contract.to_json())

        if contract.is_pass():
            print("✓ ARM emulation passed")
            return 0
        else:
            print(f"✗ ARM emulation failed: {contract.result}")
            if contract.error_message:
                print(f"  Error: {contract.error_message}")
            return 3

    else:
        print("✗ Must specify either --suite or --fixture")
        return 2


def main() -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="polaris-harness",
        description="Deterministic protocol/runtime test harness for Benro Polaris ecosystem",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  polaris-harness --version
  polaris-harness validate scenarios/my-scenario/
  polaris-harness serve --scenario scenarios/my-scenario/ --host 127.0.0.1 --port 9090
  polaris-harness replay --scenario scenarios/my-scenario/ --against localhost:9090
  polaris-harness runtime verify --root /path/to/firmware --manifest manifest.yaml
  polaris-harness runtime report --root /path/to/firmware --manifest manifest.yaml --json report.json
  polaris-harness trace ingest --input trace.pcap --output scenarios/
  polaris-harness arm-emulate --suite
  polaris-harness arm-emulate --fixture tiny_fixture --output result.json

Exit codes:
  0  success / contract satisfied
  2  invalid CLI/config/schema
  3  scenario mismatch / unexpected request / assertion failure
  4  transport/startup failure
  5  runtime verification failure
  6  provenance/evidence policy failure
  7  internal harness error
        """,
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"polaris-harness {__version__}",
        help="Show version information and exit",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Validate command
    validate_parser = subparsers.add_parser(
        "validate", help="Validate scenario or directory against schema"
    )
    validate_parser.add_argument("path", help="Path to scenario or directory")
    validate_parser.set_defaults(func=validate_command)

    # Serve command
    serve_parser = subparsers.add_parser(
        "serve", help="Serve a scenario as a deterministic protocol endpoint"
    )
    serve_parser.add_argument(
        "--scenario", required=True, help="Scenario ID or path"
    )
    serve_parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    serve_parser.add_argument("--port", type=int, default=0, help="Port to bind to (0 = ephemeral)")
    serve_parser.set_defaults(func=serve_command)

    # Replay command
    replay_parser = subparsers.add_parser(
        "replay", help="Replay a scenario against a running endpoint"
    )
    replay_parser.add_argument(
        "--scenario", required=True, help="Scenario ID or path"
    )
    replay_parser.add_argument("--host", required=True, help="Host to connect to")
    replay_parser.add_argument("--port", type=int, required=True, help="Port to connect to")
    replay_parser.set_defaults(func=replay_command)

    # Runtime verify command
    runtime_verify_parser = subparsers.add_parser(
        "runtime verify", help="Verify runtime package against manifest"
    )
    runtime_verify_parser.add_argument("--root", required=True, help="Root path of runtime")
    runtime_verify_parser.add_argument("--manifest", required=True, help="Path to manifest file")
    runtime_verify_parser.set_defaults(func=runtime_verify_command)

    # Runtime report command
    runtime_report_parser = subparsers.add_parser(
        "runtime report", help="Generate runtime verification report"
    )
    runtime_report_parser.add_argument("--root", required=True, help="Root path of runtime")
    runtime_report_parser.add_argument("--manifest", required=True, help="Path to manifest file")
    runtime_report_parser.add_argument("--json", required=True, help="Output JSON file path")
    runtime_report_parser.set_defaults(func=runtime_report_command)

    # Trace ingest command
    trace_ingest_parser = subparsers.add_parser(
        "trace ingest", help="Ingest physical trace into harness format"
    )
    trace_ingest_parser.add_argument("--input", required=True, help="Input trace file")
    trace_ingest_parser.add_argument("--output", required=True, help="Output directory")
    trace_ingest_parser.set_defaults(func=trace_ingest_command)

    # ARM emulate command (issue #15 - independent component)
    arm_emulate_parser = subparsers.add_parser(
        "arm-emulate", help="Run ARM user-space emulation for Stage-2 lifecycle tests"
    )
    arm_emulate_parser.add_argument(
        "--suite", action="store_true", help="Run all 9 #251 lifecycle test cases"
    )
    arm_emulate_parser.add_argument(
        "--fixture", help="Fixture name to emulate (e.g. tiny_fixture)"
    )
    arm_emulate_parser.add_argument(
        "--fixtures-dir", help="Directory containing fixture binaries"
    )
    arm_emulate_parser.add_argument(
        "--command", default="capture_image_with_Bulb",
        help="Command being emulated (default: capture_image_with_Bulb)"
    )
    arm_emulate_parser.add_argument(
        "--stub-profile", default="default",
        help="Stub profile name (default: default)"
    )
    arm_emulate_parser.add_argument(
        "--entry-address", type=lambda x: int(x, 0), default=0x1000,
        help="Entry point address (default: 0x1000)"
    )
    arm_emulate_parser.add_argument(
        "--patcher-commit", default="unknown",
        help="Patcher commit SHA (default: unknown)"
    )
    arm_emulate_parser.add_argument(
        "--harness-revision", default="0.1.0",
        help="Harness revision (default: 0.1.0)"
    )
    arm_emulate_parser.add_argument(
        "--emulator-version", default="unknown",
        help="Emulator version (default: unknown)"
    )
    arm_emulate_parser.add_argument(
        "--output", help="Output JSON contract file path"
    )
    arm_emulate_parser.set_defaults(func=arm_emulate_command)

    # Parse arguments
    args = parser.parse_args()

    # Execute command
    if hasattr(args, "func"):
        return args.func(args)
    else:
        parser.print_help()
        return 2


if __name__ == "__main__":
    sys.exit(main())