"""
Runtime verifier for Polaris Harness.

Validates firmware packages and runtime configurations.
"""

import os
import hashlib
import subprocess
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
        self.root_path = root_path
        self.issues: List[VerificationIssue] = []

    def verify_manifest(self, manifest: Dict[str, Any]) -> Tuple[bool, List[VerificationIssue]]:
        """Verify a runtime manifest.

        Args:
            manifest: Runtime manifest to verify

        Returns:
            Tuple of (is_valid, list_of_issues)
        """
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

        # Convert to RuntimeManifest object
        runtime_manifest = RuntimeManifest(**manifest)

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
            full_path = os.path.join(self.root_path, path)

            # Check if artifact exists
            if not os.path.exists(full_path):
                issues.append(VerificationIssue(
                    severity=VerificationResult.ERROR,
                    message=f"Artifact not found: {path}",
                    path=path
                ))
                continue

            # Verify hash if provided
            expected_hash = artifact.get("sha256")
            if expected_hash:
                actual_hash = self._compute_file_hash(full_path)
                if actual_hash != expected_hash:
                    issues.append(VerificationIssue(
                        severity=VerificationResult.ERROR,
                        message=f"Hash mismatch for {path}",
                        path=path,
                        details={
                            "expected": expected_hash,
                            "actual": actual_hash
                        }
                    ))

            # Verify target ABI
            target_abi = artifact.get("target_abi")
            if target_abi:
                abi_issues = self._verify_abi(full_path, target_abi)
                issues.extend(abi_issues)

            # Verify expected path
            expected_path = artifact.get("expected_path")
            if expected_path:
                expected_full_path = os.path.join(self.root_path, expected_path)
                if not os.path.exists(expected_full_path):
                    issues.append(VerificationIssue(
                        severity=VerificationResult.WARNING,
                        message=f"Expected path does not exist: {expected_path}",
                        path=expected_path
                    ))

        return issues

    def _verify_resolution_rules(self, resolution_rules: List[Dict[str, Any]]) -> List[VerificationIssue]:
        """Verify resolution rules.

        Args:
            resolution_rules: List of resolution rules to verify

        Returns:
            List of verification issues
        """
        issues = []

        for rule in resolution_rules:
            artifact_path = rule.get("artifact_path")
            resolution_method = rule.get("resolution_method")

            if not artifact_path or not resolution_method:
                issues.append(VerificationIssue(
                    severity=VerificationResult.ERROR,
                    message="Resolution rule missing required fields",
                    path="resolution_rules"
                ))
                continue

            full_path = os.path.join(self.root_path, artifact_path)
            if not os.path.exists(full_path):
                issues.append(VerificationIssue(
                    severity=VerificationResult.WARNING,
                    message=f"Artifact for resolution rule not found: {artifact_path}",
                    path=artifact_path
                ))

        return issues

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

            # TODO: Implement actual assertion checking
            # For now, just log the assertion
            print(f"Runtime assertion: {severity} - {assertion_text}")

        return issues

    def _compute_file_hash(self, file_path: str) -> str:
        """Compute SHA-256 hash of a file.

        Args:
            file_path: Path to file

        Returns:
            SHA-256 hash
        """
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def _verify_abi(self, file_path: str, target_abi: str) -> List[VerificationIssue]:
        """Verify ABI compatibility.

        Args:
            file_path: Path to file
            target_abi: Target ABI

        Returns:
            List of verification issues
        """
        issues = []

        # TODO: Implement ABI verification
        # For now, just check if file exists and is executable
        if not os.access(file_path, os.X_OK):
            issues.append(VerificationIssue(
                severity=VerificationResult.WARNING,
                message=f"File is not executable: {file_path}",
                path=file_path
            ))

        return issues

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