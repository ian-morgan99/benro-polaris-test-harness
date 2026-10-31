"""Release-gated tests for the read-only runtime verifier."""

import hashlib
import struct

from polaris_harness.runtime import RuntimeVerifier, VerificationResult


def manifest_for(path: str, digest: str, target_abi: str = "data") -> dict:
    return {
        "schema_version": "1",
        "id": "synthetic-runtime",
        "version": 1,
        "title": "Synthetic runtime fixture",
        "source": "synthetic",
        "status": "synthetic",
        "artifacts": [
            {
                "path": path,
                "sha256": digest,
                "source_provenance": "synthetic-test-fixture",
                "target_abi": target_abi,
                "expected_path": path,
            }
        ],
        "resolution_rules": [
            {
                "artifact_path": path,
                "resolution_method": "explicit_path",
            }
        ],
    }


def test_verifier_maps_device_absolute_paths_inside_fixture_root(tmp_path):
    data = b"runtime fixture\n"
    path = tmp_path / "app" / "data.txt"
    path.parent.mkdir()
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    manifest = manifest_for("/app/data.txt", digest)
    manifest["runtime_assertions"] = [
        {"assertion": "file_exists:/app/data.txt", "severity": "error"},
        {"assertion": f"hash:/app/data.txt={digest}", "severity": "error"},
        {
            "assertion": "resolution_selected:/app/data.txt",
            "severity": "error",
        },
    ]

    valid, issues = RuntimeVerifier(str(tmp_path)).verify_manifest(manifest)

    assert valid
    assert not [issue for issue in issues if issue.severity == VerificationResult.ERROR]


def test_verifier_checks_elf_machine_without_executing_it(tmp_path):
    path = tmp_path / "app" / "lib.so"
    path.parent.mkdir()
    header = bytearray(20)
    header[:4] = b"\x7fELF"
    header[4] = 2
    header[5] = 1
    struct.pack_into("<H", header, 18, 183)
    path.write_bytes(header)
    digest = hashlib.sha256(header).hexdigest()

    valid, issues = RuntimeVerifier(str(tmp_path)).verify_manifest(
        manifest_for("/app/lib.so", digest, "arm64")
    )

    assert valid
    assert issues == []


def test_verifier_rejects_path_escape_and_unknown_assertion(tmp_path):
    data = b"runtime fixture\n"
    path = tmp_path / "app" / "data.txt"
    path.parent.mkdir()
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    manifest = manifest_for("../outside.txt", digest)
    manifest["runtime_assertions"] = [
        {"assertion": "execute:shell-command", "severity": "error"}
    ]

    valid, issues = RuntimeVerifier(str(tmp_path)).verify_manifest(manifest)

    assert not valid
    assert any("escapes verification root" in issue.message for issue in issues)
    assert any("Unsupported runtime assertion" in issue.message for issue in issues)


def test_verifier_rejects_abi_mismatch(tmp_path):
    path = tmp_path / "app" / "lib.so"
    path.parent.mkdir()
    header = bytearray(20)
    header[:4] = b"\x7fELF"
    header[4] = 2
    header[5] = 1
    struct.pack_into("<H", header, 18, 62)
    path.write_bytes(header)
    digest = hashlib.sha256(header).hexdigest()

    valid, issues = RuntimeVerifier(str(tmp_path)).verify_manifest(
        manifest_for("/app/lib.so", digest, "arm64")
    )

    assert not valid
    assert any("ABI mismatch" in issue.message for issue in issues)
