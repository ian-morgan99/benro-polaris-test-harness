"""Read-only final FwPkt package integrity and provenance verification."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
import zipfile
from typing import Any

from jsonschema import Draft7Validator
import yaml

from .sd_emulator.packet import (
    COMPONENT_PATHS,
    FirmwarePacket,
    PacketIntegrityError,
    PacketLayoutError,
)

_PACKAGED_DATA = Path(__file__).parent / "_data"
_REPO_ROOT = Path(__file__).parent.parent.parent


@dataclass(frozen=True)
class PackageIssue:
    code: str
    message: str


@dataclass
class PackageVerificationResult:
    """Machine-readable package verification result."""

    valid: bool = False
    result: str = "FAIL"
    checks: dict[str, str] = field(default_factory=dict)
    errors: list[PackageIssue] = field(default_factory=list)
    warnings: list[PackageIssue] = field(default_factory=list)
    packet: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "result": self.result,
            "checks": self.checks,
            "errors": [issue.__dict__ for issue in self.errors],
            "warnings": [issue.__dict__ for issue in self.warnings],
            "packet": self.packet,
        }


def _schema_path() -> Path:
    packaged = _PACKAGED_DATA / "schemas" / "package-manifest.schema.json"
    if packaged.is_file():
        return packaged
    candidate = _REPO_ROOT / "schemas" / "package-manifest.schema.json"
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(f"package manifest schema not found: {candidate}")


def load_package_manifest(path: str | Path) -> dict[str, Any]:
    """Load and schema-validate one package manifest."""
    manifest_path = Path(path)
    try:
        if manifest_path.suffix.lower() in {".yaml", ".yml"}:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        else:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read package manifest {manifest_path}: {exc}") from exc
    errors = _manifest_errors(manifest)
    if errors:
        raise ValueError("invalid package manifest: " + "; ".join(errors))
    return manifest


def _manifest_errors(manifest: Any) -> list[str]:
    if not isinstance(manifest, dict):
        return ["package manifest must contain an object"]
    schema = json.loads(_schema_path().read_text(encoding="utf-8"))
    return [error.message for error in Draft7Validator(schema).iter_errors(manifest)]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_relative_member(relative_path: str) -> bool:
    path = Path(relative_path)
    return not path.is_absolute() and ".." not in path.parts


def _archive_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        return [name for name in archive.namelist() if not name.endswith("/")]


def _read_relative_member(path: Path, relative_path: str) -> bytes:
    packet = FirmwarePacket.from_zip(path)
    return packet.read_member(relative_path)


def verify_package(
    fwpkt: str | Path,
    manifest: dict[str, Any] | str | Path,
    stock: str | Path | None = None,
) -> PackageVerificationResult:
    """Verify an immutable FwPkt and its explicit provenance manifest.

    This function never extracts, mounts, installs, executes, or mutates the
    packet. A successful result is package integrity/provenance only.
    """
    report = PackageVerificationResult()
    packet_path = Path(fwpkt)
    try:
        package_manifest = (
            load_package_manifest(manifest)
            if isinstance(manifest, (str, Path))
            else manifest
        )
    except (OSError, ValueError) as exc:
        report.errors.append(PackageIssue("MANIFEST_INVALID", str(exc)))
        return report
    manifest_errors = _manifest_errors(package_manifest)
    if manifest_errors:
        report.errors.extend(
            PackageIssue("MANIFEST_INVALID", error) for error in manifest_errors
        )
        return report

    if not packet_path.is_file():
        report.errors.append(
            PackageIssue("PACKET_MISSING", f"packet does not exist: {packet_path}")
        )
        return report
    raw = packet_path.read_bytes()
    actual_sha = _sha256(raw)
    report.checks["packet_sha256"] = "PASS" if actual_sha == package_manifest["packet_sha256"] else "FAIL"
    if actual_sha != package_manifest["packet_sha256"]:
        report.errors.append(
            PackageIssue(
                "PACKET_SHA256_MISMATCH",
                f"manifest {package_manifest['packet_sha256']} != actual {actual_sha}",
            )
        )

    try:
        names = _archive_names(packet_path)
        if len(names) != len(set(names)):
            report.checks["zip_duplicates"] = "FAIL"
            report.errors.append(
                PackageIssue("DUPLICATE_ZIP_MEMBER", "packet contains duplicate ZIP members")
            )
        else:
            report.checks["zip_duplicates"] = "PASS"
        roots = {name.split("/", 1)[0] for name in names if "/" in name}
        if roots != {"FwPkt"}:
            report.checks["zip_layout"] = "FAIL"
            report.errors.append(
                PackageIssue(
                    "ZIP_ROOT_INVALID",
                    f"expected one FwPkt root, found {sorted(roots) or 'none'}",
                )
            )
        else:
            report.checks["zip_layout"] = "PASS"
        packet = FirmwarePacket.from_zip(packet_path, kind="FwPkt")
        report.packet = packet.to_dict()
        if packet.missing_expected_components():
            report.errors.append(
                PackageIssue(
                    "REQUIRED_COMPONENT_MISSING",
                    f"missing {packet.missing_expected_components()}",
                )
            )
            report.checks["firmwareInfo"] = "FAIL"
        else:
            report.checks["firmwareInfo"] = "PASS"
    except (OSError, zipfile.BadZipFile, PacketLayoutError, PacketIntegrityError) as exc:
        report.errors.append(PackageIssue("PACKET_INVALID", str(exc)))
        report.checks.setdefault("zip_layout", "FAIL")
        report.checks.setdefault("firmwareInfo", "FAIL")
        return report

    provenance = package_manifest["libgphoto2"]
    patcher_commit = package_manifest["patcher_commit"].lower()
    if patcher_commit in {"unknown", "unavailable", "none"}:
        report.errors.append(
            PackageIssue("PATCHER_PROVENANCE_MISSING", "patcher commit is not pinned")
        )
    if provenance["source_kind"] == "unknown":
        report.errors.append(
            PackageIssue("LIBGPHOTO_PROVENANCE_MISSING", "libgphoto2 source kind is unknown")
        )
    if "pentax" in package_manifest["target_profile"].lower() and provenance["source_kind"] != "patched":
        report.errors.append(
            PackageIssue(
                "PENTAX_SOURCE_POLICY_FAILED",
                "Pentax package must identify patched libgphoto2 provenance",
            )
        )
    report.checks["provenance"] = "FAIL" if any(
        issue.code.endswith("PROVENANCE_MISSING") or issue.code == "PENTAX_SOURCE_POLICY_FAILED"
        for issue in report.errors
    ) else "PASS"

    managed_seen: set[str] = set()
    managed_ok = True
    for managed in package_manifest["managed_members"]:
        relative = managed["path"]
        if relative in managed_seen:
            managed_ok = False
            report.errors.append(
                PackageIssue("MANAGED_MEMBER_DUPLICATE", f"duplicate managed member: {relative}")
            )
            continue
        managed_seen.add(relative)
        if not _safe_relative_member(relative):
            managed_ok = False
            report.errors.append(
                PackageIssue("MANAGED_MEMBER_PATH_INVALID", f"unsafe managed path: {relative}")
            )
            continue
        try:
            actual = _sha256(_read_relative_member(packet_path, relative))
        except (OSError, PacketLayoutError, zipfile.BadZipFile) as exc:
            managed_ok = False
            report.errors.append(
                PackageIssue("MANAGED_MEMBER_MISSING", f"{relative}: {exc}")
            )
            continue
        if actual != managed["sha256"].lower():
            managed_ok = False
            report.errors.append(
                PackageIssue(
                    "MANAGED_MEMBER_HASH_MISMATCH",
                    f"{relative}: manifest {managed['sha256']} != actual {actual}",
                )
            )
    report.checks["managed_member_hashes"] = "PASS" if managed_ok else "FAIL"

    appfs_sha = package_manifest.get("appfs_sha256")
    if appfs_sha:
        actual_appfs = _sha256(_read_relative_member(packet_path, COMPONENT_PATHS["appfs"]))
        report.checks["appfs_sha256"] = "PASS" if actual_appfs == appfs_sha.lower() else "FAIL"
        if actual_appfs != appfs_sha.lower():
            report.errors.append(
                PackageIssue("APPFS_SHA256_MISMATCH", "appfs hash differs from manifest")
            )

    stock_manifest = package_manifest.get("stock")
    if stock_manifest:
        if stock is None:
            report.errors.append(
                PackageIssue("STOCK_PACKET_REQUIRED", "manifest declares stock comparisons but no stock packet was supplied")
            )
            report.checks["stock_comparison"] = "FAIL"
        else:
            stock_path = Path(stock)
            stock_raw = stock_path.read_bytes()
            stock_sha = _sha256(stock_raw)
            if stock_sha != stock_manifest["packet_sha256"].lower():
                report.errors.append(
                    PackageIssue("STOCK_SHA256_MISMATCH", "stock packet hash differs from manifest")
                )
            stock_ok = stock_sha == stock_manifest["packet_sha256"].lower()
            for relative in stock_manifest["unchanged_members"]:
                try:
                    candidate_bytes = _read_relative_member(packet_path, relative)
                    stock_bytes = _read_relative_member(stock_path, relative)
                except (OSError, PacketLayoutError, zipfile.BadZipFile) as exc:
                    stock_ok = False
                    report.errors.append(
                        PackageIssue("STOCK_MEMBER_MISSING", f"{relative}: {exc}")
                    )
                    continue
                if candidate_bytes != stock_bytes:
                    stock_ok = False
                    report.errors.append(
                        PackageIssue("STOCK_COMPONENT_DRIFT", f"declared unchanged member differs: {relative}")
                    )
            report.checks["stock_comparison"] = "PASS" if stock_ok else "FAIL"
    report.checks["runtime_round_trip"] = "NOT_TESTED"
    report.warnings.append(
        PackageIssue(
            "RUNTIME_ROUND_TRIP_NOT_TESTED",
            "package verifier checks ZIP members; appfs extraction/loader qualification belongs to runtime verification",
        )
    )
    report.valid = not report.errors
    report.result = "PASS" if report.valid else "FAIL"
    return report
