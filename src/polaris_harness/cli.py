#!/usr/bin/env python3
"""
Command-line interface for Polaris Harness.

Provides deterministic protocol/runtime testing for Benro Polaris ecosystem.
"""

import argparse
from collections.abc import Iterable
from datetime import UTC, datetime
import hashlib
import json
import os
import socket
import sys
import time
from pathlib import Path

import yaml

from .arm_emulator import (
    ARMEmulator,
    FixtureManager,
    run_all_lifecycle_cases,
)
from .arm_emulator.contract import ArmEmulationContract
from .astro_webui import create_app, run_server
from .runtime import RuntimeVerifier
from .scenario_service import ScenarioService, load_scenario
from .transport import TransportError
from .validation import SchemaValidator
from .version import __version__


_SCHEMA_TYPES = ("scenario", "evidence", "personality", "runtime_manifest")


def _schema_type_for_path(path: Path) -> str | None:
    """Infer a schema type from a conventional manifest filename."""
    name = path.name.lower()
    if name in {"scenario.yaml", "scenario.yml", "scenario.json"}:
        return "scenario"
    if name in {"evidence.yaml", "evidence.yml", "evidence.json"}:
        return "evidence"
    if name in {
        "personality.yaml",
        "personality.yml",
        "personality.json",
        "manifest.yaml",
        "manifest.yml",
        "manifest.json",
    }:
        parent_names = {part.lower() for part in path.parts}
        if "personalities" in parent_names:
            return "personality"
        return "runtime_manifest"
    if name in {
        "runtime-manifest.yaml",
        "runtime-manifest.yml",
        "runtime-manifest.json",
    }:
        return "runtime_manifest"
    return None


def _manifest_files(path: Path) -> Iterable[Path]:
    """Yield manifest files in a file or directory deterministically."""
    if path.is_file():
        yield path
        return
    if path.is_dir():
        for candidate in sorted(path.rglob("*")):
            if candidate.is_file() and candidate.suffix.lower() in {".yaml", ".yml", ".json"}:
                yield candidate


def _validate_path(path: Path, schema_type: str | None) -> tuple[int, int]:
    """Validate one manifest or directory and return (files, failures)."""
    if not path.exists():
        print(f"ERROR: path does not exist: {path}", file=sys.stderr)
        return 0, 1

    validator = SchemaValidator()
    files = 0
    failures = 0
    for manifest in _manifest_files(path):
        files += 1
        selected_type = schema_type or _schema_type_for_path(manifest)
        if selected_type is None:
            print(
                f"ERROR: cannot infer schema type for {manifest}; "
                "use --type",
                file=sys.stderr,
            )
            failures += 1
            continue
        errors = validator.validate_file(manifest, selected_type)
        if errors:
            failures += 1
            print(f"FAIL {selected_type}: {manifest}")
            for error in errors:
                print(f"  - {error}")
        else:
            print(f"PASS {selected_type}: {manifest}")

    if files == 0:
        print(f"ERROR: no YAML/JSON manifests found under {path}", file=sys.stderr)
        failures += 1
    return files, failures


def validate_command(args: argparse.Namespace) -> int:
    """Validate scenario or directory against schema."""
    files, failures = _validate_path(Path(args.path), args.schema_type)
    print(f"Validated {files} manifest(s); failures={failures}")
    return 0 if failures == 0 else 2


def serve_command(args: argparse.Namespace) -> int:
    """Serve a scenario as a deterministic protocol endpoint."""
    try:
        scenario = load_scenario(args.scenario)
        service = ScenarioService(scenario, args.host, args.port)
        port = service.start()
    except (OSError, ValueError, TransportError) as exc:
        print(f"ERROR: cannot start scenario service: {exc}", file=sys.stderr)
        return 2
    print(f"Serving {scenario['id']} on {args.host}:{port}")
    try:
        if args.duration > 0:
            time.sleep(args.duration)
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
    return 0


def replay_command(args: argparse.Namespace) -> int:
    """Replay a scenario against a running endpoint."""
    try:
        scenario = load_scenario(args.scenario)
        directory = Path(args.scenario)
        if directory.is_file():
            directory = directory.parent
        request_file = directory / "requests.ndjson"
        requests = []
        for line in request_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if isinstance(record, str):
                requests.append((record, None))
            else:
                requests.append((record["request"], record.get("expect")))
        if not requests:
            raise ValueError(f"no replay requests in {request_file}")
        framing = scenario["endpoints"]["command"].get("framing", {})
        delimiter = framing.get("delimiter_text", "#").encode("utf-8")
        with socket.create_connection((args.host, args.port), timeout=args.timeout) as client:
            client.settimeout(args.timeout)
            for request, expected in requests:
                client.sendall(request.encode("utf-8"))
                response = bytearray()
                while delimiter not in response:
                    response.extend(client.recv(4096))
                received = bytes(response).split(delimiter, 1)[0] + delimiter
                text = received.decode("utf-8", errors="replace")
                print(f"{request.rstrip()} -> {text.rstrip()}")
                if expected is not None and text != expected:
                    print(
                        f"ERROR: expected {expected!r}, received {text!r}",
                        file=sys.stderr,
                    )
                    return 3
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: replay failed: {exc}", file=sys.stderr)
        return 3
    return 0


def runtime_verify_command(args: argparse.Namespace) -> int:
    """Verify runtime package against manifest."""
    try:
        manifest = yaml.safe_load(Path(args.manifest).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        print(f"ERROR: cannot read manifest: {exc}", file=sys.stderr)
        return 2
    verifier = RuntimeVerifier(args.root)
    valid, issues = verifier.verify_manifest(manifest)
    for issue in issues:
        print(
            f"{issue.severity.value.upper()}: {issue.path}: {issue.message}",
            file=sys.stderr if issue.severity.value == "error" else sys.stdout,
        )
    print(
        json.dumps(
            {
                "valid": valid,
                "root": str(Path(args.root).resolve()),
                "resolution_results": verifier.resolution_results,
                "issue_count": len(issues),
            },
            sort_keys=True,
        )
    )
    return 0 if valid else 5


def runtime_report_command(args: argparse.Namespace) -> int:
    """Generate runtime verification report."""
    try:
        manifest = yaml.safe_load(Path(args.manifest).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        print(f"ERROR: cannot read manifest: {exc}", file=sys.stderr)
        return 2
    verifier = RuntimeVerifier(args.root)
    valid, issues = verifier.verify_manifest(manifest)
    report = {
        "valid": valid,
        "root": str(Path(args.root).resolve()),
        "manifest": manifest,
        "resolution_results": verifier.resolution_results,
        "issues": [
            {
                "severity": issue.severity.value,
                "message": issue.message,
                "path": issue.path,
                "details": issue.details,
            }
            for issue in issues
        ],
    }
    try:
        output = Path(args.json)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"ERROR: cannot write report: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote runtime report: {args.json}")
    return 0 if valid else 5


def trace_ingest_command(args: argparse.Namespace) -> int:
    """Ingest physical trace into harness format."""
    source = Path(args.input)
    output = Path(args.output)
    if not source.is_file():
        print(f"ERROR: trace input is not a file: {source}", file=sys.stderr)
        return 2
    if not args.sanitised:
        print(
            "ERROR: refuse trace ingestion without --sanitised; "
            "remove credentials and private media metadata first",
            file=sys.stderr,
        )
        return 2
    try:
        output.mkdir(parents=True, exist_ok=True)
        raw_dir = output / "raw"
        raw_dir.mkdir(exist_ok=True)
        destination = raw_dir / source.name
        if destination.exists():
            raise FileExistsError(f"destination already exists: {destination}")
        temporary = destination.with_name(f".{destination.name}.part")
        digest = hashlib.sha256()
        with source.open("rb") as source_file, temporary.open("wb") as target:
            while True:
                chunk = source_file.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        temporary.replace(destination)
        evidence_id = args.evidence_id or f"trace-{source.stem}"
        evidence = {
            "schema_version": "1",
            "evidence_id": evidence_id,
            "source": args.source,
            "status": "synthetic" if args.source == "synthetic" else "provisional",
            "observed_date": datetime.now(UTC).date().isoformat(),
            "observed_layer": args.observed_layer,
            "source_issue": args.source_issue,
            "camera": {
                "manufacturer": "unknown",
                "model": "unknown",
                "firmware": "unknown",
                "usb_mode": "unknown",
                "usb_id": "unknown",
            },
            "polaris": {
                "firmware": "unknown",
                "patcher_sha": "unknown",
                "fwpkt_sha256": "unknown",
            },
            "libgphoto2": {"sha": "unknown"},
            "artifacts": [
                {
                    "path": f"raw/{destination.name}",
                    "sha256": digest.hexdigest(),
                    "transformation": "copied byte-for-byte after caller sanitisation",
                }
            ],
            "transformations": [
                "Copied raw input without changing bytes",
                "Caller asserted that credentials and private metadata were removed",
            ],
            "limitations": [
                "Ingestion does not prove physical behavior",
                "Timing and protocol semantics require explicit scenario assertions",
            ],
        }
        evidence_errors = SchemaValidator().validate_evidence(evidence)
        if evidence_errors:
            raise ValueError("; ".join(evidence_errors))
        (output / "evidence.yaml").write_text(
            yaml.safe_dump(evidence, sort_keys=False),
            encoding="utf-8",
        )
    except (OSError, ValueError) as exc:
        print(f"ERROR: trace ingestion failed: {exc}", file=sys.stderr)
        return 2
    print(f"Imported sanitised trace: {destination}")
    print(f"Evidence manifest: {output / 'evidence.yaml'}")
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
        all_skipped = True
        any_real_failure = False
        for case, result in results.items():
            if result.skipped:
                print(f"  {case.value}: SKIP - {result.skip_reason}")
                continue
            all_skipped = False
            status = "PASS" if result.passed else "FAIL"
            print(f"  {case.value}: {status}")
            if not result.passed:
                all_passed = False
                any_real_failure = True
                for err in result.assertion_errors:
                    print(f"    - {err}")
                if result.contract and not result.contract.is_pass():
                    print(f"    result: {result.contract.result}")
                    print(f"    exit_code: {result.contract.exit_code}")
                    print(f"    error: {result.contract.error_message}")

        if all_skipped:
            print("⚠ All ARM lifecycle cases skipped (no real Stage-2 fixture available)")
            return 0
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


def astro_webui_command(args: argparse.Namespace) -> int:
    """Run the Astro WebUI harness server (issue #20).

    This is an independent component that serves synthetic Astro static assets
    and implements a documented synthetic Astro API surface. All actions are
    dry-run only — the harness never contacts or mutates a physical Polaris.
    """
    if args.profile != "synthetic":
        print(f"✗ Unknown profile: {args.profile}. Only 'synthetic' is supported.")
        return 2

    print("Starting Astro WebUI harness server (issue #20)")
    print(f"  Profile: {args.profile}")
    print(f"  Host: {args.host}")
    print(f"  Port: {args.port} (0 = ephemeral)")
    print("  Mode: dry-run only — no physical Polaris or camera contact")

    return run_server(host=args.host, port=args.port, profile=args.profile)


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
  polaris-harness replay --scenario scenarios/my-scenario/ --host 127.0.0.1 --port 9090
  polaris-harness runtime verify --root /path/to/firmware --manifest manifest.yaml
  polaris-harness runtime report --root /path/to/firmware --manifest manifest.yaml --json report.json
  polaris-harness trace ingest --input trace.pcap --output scenarios/
  polaris-harness arm-emulate --suite
  polaris-harness arm-emulate --fixture tiny_fixture --output result.json
  polaris-harness astro-webui --profile synthetic --host 127.0.0.1 --port 8080

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
    validate_parser.add_argument(
        "--type",
        dest="schema_type",
        choices=_SCHEMA_TYPES,
        help="Schema type when it cannot be inferred from the filename",
    )
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
    serve_parser.add_argument(
        "--duration",
        type=float,
        default=0,
        help="Stop after this many seconds; zero means until interrupted",
    )
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
    replay_parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Socket timeout in seconds",
    )
    replay_parser.set_defaults(func=replay_command)

    # Runtime verify command
    runtime_parser = subparsers.add_parser("runtime", help="Runtime verification commands")
    runtime_subparsers = runtime_parser.add_subparsers(
        dest="runtime_command", required=True
    )
    runtime_verify_parser = runtime_subparsers.add_parser(
        "verify", help="Verify runtime package against manifest"
    )
    runtime_verify_parser.add_argument("--root", required=True, help="Root path of runtime")
    runtime_verify_parser.add_argument("--manifest", required=True, help="Path to manifest file")
    runtime_verify_parser.set_defaults(func=runtime_verify_command)

    # Runtime report command
    runtime_report_parser = runtime_subparsers.add_parser(
        "report", help="Generate runtime verification report"
    )
    runtime_report_parser.add_argument("--root", required=True, help="Root path of runtime")
    runtime_report_parser.add_argument("--manifest", required=True, help="Path to manifest file")
    runtime_report_parser.add_argument("--json", required=True, help="Output JSON file path")
    runtime_report_parser.set_defaults(func=runtime_report_command)

    # Trace ingest command
    trace_parser = subparsers.add_parser("trace", help="Trace processing commands")
    trace_subparsers = trace_parser.add_subparsers(
        dest="trace_command", required=True
    )
    trace_ingest_parser = trace_subparsers.add_parser(
        "ingest", help="Ingest physical trace into harness format"
    )
    trace_ingest_parser.add_argument("--input", required=True, help="Input trace file")
    trace_ingest_parser.add_argument("--output", required=True, help="Output directory")
    trace_ingest_parser.add_argument(
        "--sanitised",
        action="store_true",
        help="Assert that credentials and private metadata were removed",
    )
    trace_ingest_parser.add_argument(
        "--source",
        choices=("physical", "synthetic", "derived"),
        default="derived",
    )
    trace_ingest_parser.add_argument(
        "--observed-layer",
        choices=("polaris-protocol", "polaris-runtime", "libgphoto2", "physical-camera"),
        default="polaris-runtime",
    )
    trace_ingest_parser.add_argument("--source-issue", default="unknown")
    trace_ingest_parser.add_argument("--evidence-id")
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

    # Astro webui command (issue #20 - independent component)
    astro_webui_parser = subparsers.add_parser(
        "astro-webui", help="Run Astro WebUI harness server (synthetic, dry-run only)"
    )
    astro_webui_parser.add_argument(
        "--profile", default="synthetic",
        help="Profile name (default: synthetic)"
    )
    astro_webui_parser.add_argument(
        "--host", default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)"
    )
    astro_webui_parser.add_argument(
        "--port", type=int, default=0,
        help="Port to bind to (default: 0 = ephemeral)"
    )
    astro_webui_parser.set_defaults(func=astro_webui_command)

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
