"""
Runtime verifier for Polaris Harness.

Validates firmware packages and runtime configurations.
"""

import hashlib
import struct
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from enum import Enum

from .validation import validate_runtime_manifest


class VerificationResult(Enum):
    """Verification result enumeration."""
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class VerificationIssue:
    """Verification issue data structure."""
    severity: VerificationResult
    message: str
    path: str
    details: Dict[str, Any] = None
    code: str = "RUNTIME_VERIFICATION_ERROR"


@dataclass
class RuntimeManifest:
    """Runtime manifest data structure."""
    id: str
    version: int
    title: str
    source: str
    status: str
    artifacts: List[Dict[str, Any]]
    resolution_rules: List[Dict[str, Any]]
    runtime_assertions: List[Dict[str, Any]] = None


class RuntimeVerifier:
    """Verifies runtime packages and configurations."""

    def __init__(self, root_path: str):
        self.root_path = Path(root_path).resolve()
        self.issues: List[VerificationIssue] = []
        self.resolution_results: List[Dict[str, Any]] = []

    def verify_manifest(self, manifest: Dict[str, Any]) -> Tuple[bool, List[VerificationIssue]]:
        """Verify a runtime manifest.

        Args:
            manifest: Runtime manifest to verify

        Returns:
            Tuple of (is_valid, list_of_issues)
        """
        self.clear_issues()
        self.resolution_results.clear()

        # Validate manifest against schema
        errors = validate_runtime_manifest(manifest)
        if errors:
            self.issues.extend([
                VerificationIssue(
                    severity=VerificationResult.ERROR,
                    message=f"Schema validation error: {error}",
                    path="manifest"
                )
                for error in errors
            ])
            return False, self.issues

        # Convert only the fields represented by the verifier model. The
        # schema_version is contract metadata, not a RuntimeManifest field.
        runtime_manifest = RuntimeManifest(
            id=manifest["id"],
            version=manifest["version"],
            title=manifest["title"],
            source=manifest["source"],
            status=manifest["status"],
            artifacts=manifest["artifacts"],
            resolution_rules=manifest["resolution_rules"],
            runtime_assertions=manifest.get("runtime_assertions"),
        )

        # Verify artifacts
        artifact_issues = self._verify_artifacts(runtime_manifest.artifacts)
        self.issues.extend(artifact_issues)

        # Verify resolution rules
        resolution_issues = self._verify_resolution_rules(runtime_manifest.resolution_rules)
        self.issues.extend(resolution_issues)

        # Verify runtime assertions
        if runtime_manifest.runtime_assertions:
            assertion_issues = self._verify_runtime_assertions(runtime_manifest.runtime_assertions)
            self.issues.extend(assertion_issues)

        return len([i for i in self.issues if i.severity == VerificationResult.ERROR]) == 0, self.issues

    def _verify_artifacts(self, artifacts: List[Dict[str, Any]]) -> List[VerificationIssue]:
        """Verify artifacts.

        Args:
            artifacts: List of artifacts to verify

        Returns:
            List of verification issues
        """
        issues = []

        for artifact in artifacts:
            path = artifact.get("path", "")
            full_path = self._safe_path(path)
            if full_path is None:
                issues.append(VerificationIssue(
                    severity=VerificationResult.ERROR,
                    message=f"Artifact path escapes verification root: {path}",
                    path=path,
                ))
                continue

            # Check if artifact exists
            if not full_path.is_file():
                issues.append(VerificationIssue(
                    severity=VerificationResult.ERROR,
                    message=f"Artifact not found: {path}",
                    path=path
                ))
                continue

            # Verify hash if provided
            expected_hash = artifact.get("sha256")
            actual_hash = self._compute_file_hash(full_path)
            if actual_hash != expected_hash:
                issues.append(VerificationIssue(
                    severity=VerificationResult.ERROR,
                    message=f"Hash mismatch for {path}",
                    path=path,
                    details={
                        "expected": expected_hash,
                        "actual": actual_hash,
                    },
                ))

            # Verify target ABI
            target_abi = artifact.get("target_abi")
            if target_abi:
                abi_issues = self._verify_abi(full_path, target_abi)
                issues.extend(abi_issues)

            # Verify expected path
            expected_path = artifact.get("expected_path")
            if expected_path:
                expected_full_path = self._safe_path(expected_path)
                if expected_full_path is None or not expected_full_path.is_file():
                    issues.append(VerificationIssue(
                        severity=VerificationResult.ERROR,
                        message=f"Expected path does not exist: {expected_path}",
                        path=expected_path,
                    ))

        return issues

    def _verify_resolution_rules(self, resolution_rules: List[Dict[str, Any]]) -> List[VerificationIssue]:
        """Verify each declared loader/search mechanism independently.

        A rule is a declarative model of the search order supplied by the
        wrapper or runtime manifest. The verifier does not execute a loader.
        For directory-based mechanisms, ``search_paths`` plus ``filename``
        produce the candidates in declared order; the first existing candidate
        is selected and later candidates are reported as shadowed.
        """
        issues: List[VerificationIssue] = []
        expected_environment = {
            "ld_preload": "LD_PRELOAD",
            "ld_library_path": "LD_LIBRARY_PATH",
            "camlibs": "CAMLIBS",
            "iolibs": "IOLIBS",
        }

        for rule in resolution_rules:
            artifact_path = rule.get("artifact_path")
            resolution_method = rule.get("resolution_method")
            if not artifact_path or not resolution_method:
                issues.append(
                    VerificationIssue(
                        severity=VerificationResult.ERROR,
                        message="Resolution rule missing required fields",
                        path="resolution_rules",
                        code="RESOLUTION_RULE_INVALID",
                    )
                )
                continue

            required_environment = expected_environment.get(resolution_method)
            declared_environment = rule.get("environment_variable")
            if required_environment and declared_environment != required_environment:
                issues.append(
                    VerificationIssue(
                        severity=VerificationResult.ERROR,
                        message=(
                            f"{resolution_method} must declare "
                            f"environment_variable={required_environment}"
                        ),
                        path=artifact_path,
                        code="RESOLUTION_ENVIRONMENT_MISMATCH",
                    )
                )

            candidates = self._resolution_candidates(rule)
            existing: List[Tuple[str, Path]] = []
            for candidate in candidates:
                candidate_path = self._safe_path(candidate)
                if candidate_path is None:
                    issues.append(
                        VerificationIssue(
                            severity=VerificationResult.ERROR,
                            message=f"Resolution path escapes verification root: {candidate}",
                            path=artifact_path,
                            code="RESOLUTION_PATH_ESCAPE",
                        )
                    )
                elif candidate_path.is_file():
                    existing.append((candidate, candidate_path))

            expected_selected = rule.get("expected_selected")
            if expected_selected:
                expected_path = self._safe_path(expected_selected)
                if expected_path is None or not expected_path.is_file():
                    issues.append(
                        VerificationIssue(
                            severity=VerificationResult.ERROR,
                            message=(
                                "Expected resolution winner does not exist: "
                                f"{expected_selected}"
                            ),
                            path=artifact_path,
                            code="EXPECTED_RESOLUTION_MISSING",
                        )
                    )

            if not existing:
                issues.append(
                    VerificationIssue(
                        severity=VerificationResult.ERROR,
                        message=f"No candidate resolves for: {artifact_path}",
                        path=artifact_path,
                        code="RESOLUTION_UNRESOLVED",
                    )
                )
                continue

            selected, selected_path = existing[0]
            if expected_selected and selected != expected_selected:
                issues.append(
                    VerificationIssue(
                        severity=VerificationResult.ERROR,
                        message=(
                            f"Resolution winner mismatch for {artifact_path}: "
                            f"expected {expected_selected}, selected {selected}"
                        ),
                        path=artifact_path,
                        details={
                            "expected_selected": expected_selected,
                            "selected": selected,
                            "shadowed": [candidate for candidate, _ in existing[1:]],
                        },
                        code="RESOLUTION_WINNER_MISMATCH",
                    )
                )

            self.resolution_results.append(
                {
                    "artifact_path": artifact_path,
                    "resolution_method": resolution_method,
                    "environment_variable": declared_environment,
                    "search_order": candidates,
                    "selected": selected,
                    "selected_path": str(selected_path),
                    "expected_selected": expected_selected,
                    "shadowed": [candidate for candidate, _ in existing[1:]],
                    "selection_basis": "declared-search-order",
                }
            )

        return issues

    def _resolution_candidates(self, rule: Dict[str, Any]) -> List[str]:
        """Return candidate device paths in the manifest's declared order."""
        search_paths = rule.get("search_paths")
        if not search_paths:
            return [rule["artifact_path"], *rule.get("fallback_paths", [])]

        filename = rule.get("filename")
        candidates: List[str] = []
        for search_path in search_paths:
            if not isinstance(search_path, str) or not search_path:
                continue
            if filename:
                candidates.append(f"{search_path.rstrip('/')}/{filename}")
            else:
                candidates.append(search_path)
        return candidates

    def _verify_runtime_assertions(self, assertions: List[Dict[str, Any]]) -> List[VerificationIssue]:
        """Verify runtime assertions.

        Args:
            assertions: List of runtime assertions to verify

        Returns:
            List of verification issues
        """
        issues = []

        for assertion in assertions:
            assertion_text = assertion.get("assertion", "")
            severity = assertion.get("severity", "error")

            if not assertion_text:
                issues.append(VerificationIssue(
                    severity=VerificationResult.ERROR,
                    message="Runtime assertion missing text",
                    path="runtime_assertions"
                ))
                continue

            result = self._evaluate_runtime_assertion(assertion_text)
            if result is None:
                issue_severity = (
                    VerificationResult.ERROR
                    if severity == "error"
                    else VerificationResult.WARNING
                )
                issues.append(VerificationIssue(
                    severity=issue_severity,
                    message=f"Unsupported runtime assertion: {assertion_text}",
                    path="runtime_assertions",
                ))
            elif not result[0]:
                issue_severity = (
                    VerificationResult.ERROR
                    if severity == "error"
                    else VerificationResult.WARNING
                )
                issues.append(VerificationIssue(
                    severity=issue_severity,
                    message=result[1],
                    path="runtime_assertions",
                ))

        return issues

    def _safe_path(self, manifest_path: str) -> Optional[Path]:
        """Resolve a manifest path inside the read-only verification root.

        Manifest paths may use the device-style absolute form /app/...; inside
        a fixture this means root/app/..., never the host's /app directory.
        """
        if not isinstance(manifest_path, str) or not manifest_path:
            return None
        relative = Path(manifest_path.lstrip("/"))
        candidate = (self.root_path / relative).resolve()
        if candidate == self.root_path or self.root_path in candidate.parents:
            return candidate
        return None

    def _compute_file_hash(self, file_path: Path) -> str:
        """Compute SHA-256 hash of a file.

        Args:
            file_path: Path to file

        Returns:
            SHA-256 hash
        """
        sha256_hash = hashlib.sha256()
        with file_path.open("rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def _verify_abi(self, file_path: Path, target_abi: str) -> List[VerificationIssue]:
        """Verify ABI compatibility.

        Args:
            file_path: Path to file
            target_abi: Target ABI

        Returns:
            List of verification issues
        """
        target = target_abi.lower().replace("-", "").replace("_", "")
        if target in {"any", "data", "unknown"}:
            return []

        try:
            header = file_path.read_bytes()[:20]
        except OSError as exc:
            return [VerificationIssue(
                severity=VerificationResult.ERROR,
                message=f"Cannot read artifact for ABI verification: {exc}",
                path=str(file_path),
            )]

        if len(header) < 20 or header[:4] != b"\x7fELF":
            return [VerificationIssue(
                severity=VerificationResult.ERROR,
                message=f"Artifact is not an ELF file for target ABI {target_abi}",
                path=str(file_path),
            )]

        elf_class = {1: 32, 2: 64}.get(header[4])
        endian = "<" if header[5] == 1 else ">" if header[5] == 2 else None
        if elf_class is None or endian is None:
            return [VerificationIssue(
                severity=VerificationResult.ERROR,
                message="ELF header has unsupported class or byte order",
                path=str(file_path),
            )]
        machine = struct.unpack(f"{endian}H", header[18:20])[0]
        actual = {
            40: "arm",
            183: "aarch64",
            3: "x86",
            62: "x8664",
        }.get(machine)
        expected = {
            "arm": "arm",
            "armhf": "arm",
            "aarch64": "aarch64",
            "arm64": "aarch64",
            "x86": "x86",
            "i386": "x86",
            "amd64": "x8664",
        }.get(target)
        if actual is None or expected is None or actual != expected:
            return [VerificationIssue(
                severity=VerificationResult.ERROR,
                message=(
                    f"ELF ABI mismatch for {file_path.name}: "
                    f"expected {target_abi}, machine={actual or machine}, "
                    f"class={elf_class}"
                ),
                path=str(file_path),
            )]
        return []

    def _evaluate_runtime_assertion(self, assertion: str) -> Optional[Tuple[bool, str]]:
        """Evaluate the small, explicit assertion language.

        Unknown assertion text fails closed. Arbitrary code or shell commands
        are never executed by the verifier.
        """
        if assertion.startswith("file_exists:"):
            path = assertion.split(":", 1)[1]
            resolved = self._safe_path(path)
            return (
                resolved is not None and resolved.is_file(),
                f"Required file does not exist: {path}",
            )
        if assertion.startswith("hash:"):
            value = assertion.split(":", 1)[1]
            if "=" not in value:
                return False, f"Malformed hash assertion: {assertion}"
            path, expected = value.split("=", 1)
            resolved = self._safe_path(path)
            if resolved is None or not resolved.is_file():
                return False, f"Hash assertion file does not exist: {path}"
            actual = self._compute_file_hash(resolved)
            return (
                actual == expected,
                f"Hash assertion failed for {path}: expected {expected}, got {actual}",
            )
        if assertion.startswith("resolution_selected:"):
            expected = assertion.split(":", 1)[1]
            selected = {
                result["selected"]
                for result in self.resolution_results
            }
            return (
                expected in selected,
                f"Resolution did not select {expected}; selected={sorted(selected)}",
            )
        return None

    def get_issues(self) -> List[VerificationIssue]:
        """Get verification issues.

        Returns:
            List of verification issues
        """
        return self.issues.copy()

    def clear_issues(self) -> None:
        """Clear verification issues."""
        self.issues.clear()

    def has_errors(self) -> bool:
        """Check if there are any errors.

        Returns:
            True if there are errors, False otherwise
        """
        return any(issue.severity == VerificationResult.ERROR for issue in self.issues)

    def has_warnings(self) -> bool:
        """Check if there are any warnings.

        Returns:
            True if there are warnings, False otherwise
        """
        return any(issue.severity == VerificationResult.WARNING for issue in self.issues)


class RuntimeVerifierManager:
    """Manages runtime verifier instances."""

    def __init__(self):
        self.verifiers: Dict[str, RuntimeVerifier] = {}

    def create_verifier(self, root_path: str, verifier_id: str = None) -> RuntimeVerifier:
        """Create a runtime verifier.

        Args:
            root_path: Root path of runtime
            verifier_id: Verifier identifier

        Returns:
            Runtime verifier
        """
        verifier_id = verifier_id or str(hash(root_path))
        verifier = RuntimeVerifier(root_path)
        self.verifiers[verifier_id] = verifier
        return verifier

    def get_verifier(self, verifier_id: str) -> Optional[RuntimeVerifier]:
        """Get a runtime verifier.

        Args:
            verifier_id: Verifier identifier

        Returns:
            Runtime verifier or None
        """
        return self.verifiers.get(verifier_id)

    def remove_verifier(self, verifier_id: str) -> None:
        """Remove a runtime verifier.

        Args:
            verifier_id: Verifier identifier
        """
        if verifier_id in self.verifiers:
            del self.verifiers[verifier_id]

    def get_all_verifiers(self) -> Dict[str, RuntimeVerifier]:
        """Get all runtime verifiers.

        Returns:
            Dictionary of runtime verifiers
        """
        return self.verifiers.copy()

    def stop_all(self) -> None:
        """Stop all runtime verifiers."""
        self.verifiers.clear()


# Global runtime verifier manager
_runtime_verifier_manager = RuntimeVerifierManager()


def get_runtime_verifier_manager() -> RuntimeVerifierManager:
    """Get the global runtime verifier manager."""
    return _runtime_verifier_manager


def create_verifier(root_path: str, verifier_id: str = None) -> RuntimeVerifier:
    """Create a runtime verifier."""
    return _runtime_verifier_manager.create_verifier(root_path, verifier_id)


def get_verifier(verifier_id: str) -> Optional[RuntimeVerifier]:
    """Get a runtime verifier."""
    return _runtime_verifier_manager.get_verifier(verifier_id)


def remove_verifier(verifier_id: str) -> None:
    """Remove a runtime verifier."""
    _runtime_verifier_manager.remove_verifier(verifier_id)


def stop_all() -> None:
    """Stop all runtime verifiers."""
    _runtime_verifier_manager.stop_all()
