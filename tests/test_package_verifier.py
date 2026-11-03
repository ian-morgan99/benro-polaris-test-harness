"""Final FwPkt package integrity and provenance verifier tests."""

import hashlib
import io
import json
from pathlib import Path
import zipfile

from polaris_harness.cli import main
from polaris_harness.package_verifier import verify_package


PATHS = {
    "config": "camera/config",
    "uImage": "camera/uImage",
    "rootfs": "camera/rootfs.ubifs",
    "appfs": "camera/appfs.ubifs",
    "polaris403": "gimbal/polaris403_2.0.0.22.bin",
    "polaris413": "gimbal/polaris413_2.0.0.22.bin",
}


def _payloads(marker=b"a"):
    return {name: marker + name.encode() for name in PATHS}


def _packet_bytes(payloads=None, duplicate=False, root="FwPkt"):
    payloads = payloads or _payloads()
    info = "".join(
        f"{name} size:{len(payloads[name])};{name} MD5:{hashlib.md5(payloads[name]).hexdigest()};\n"
        for name in PATHS
    )
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{root}/FwVer", "FwVer:6.0.0.54.99;date:2026.11.03;\n")
        archive.writestr(f"{root}/firmwareInfo", info)
        for name, relative in PATHS.items():
            archive.writestr(f"{root}/{relative}", payloads[name])
        if duplicate:
            archive.writestr(f"{root}/FwVer", "FwVer:6.0.0.54.99;date:2026.11.03;\n")
    return data.getvalue()


def _write(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)


def _manifest(payload: bytes, payloads=None, **extra):
    payloads = payloads or _payloads()
    manifest = {
        "schema_version": "1",
        "id": "synthetic-package-v1",
        "version": 1,
        "title": "Synthetic FwPkt package",
        "source": "synthetic",
        "status": "synthetic",
        "packet_kind": "FwPkt",
        "packet_sha256": hashlib.sha256(payload).hexdigest(),
        "patcher_commit": "abcdef1",
        "target_profile": "pentax-k3iii",
        "libgphoto2": {"source_sha": "1234567", "source_kind": "patched"},
        "managed_members": [
            {
                "path": relative,
                "sha256": hashlib.sha256(payloads[name]).hexdigest(),
            }
            for name, relative in PATHS.items()
        ],
        "appfs_sha256": hashlib.sha256(payloads["appfs"]).hexdigest(),
    }
    manifest.update(extra)
    return manifest


def test_valid_packet_passes_integrity_and_provenance(tmp_path):
    packet = _packet_bytes()
    packet_path = tmp_path / "FwPkt.zip"
    _write(packet_path, packet)

    report = verify_package(packet_path, _manifest(packet))

    assert report.valid
    assert report.result == "PASS"
    assert report.checks["firmwareInfo"] == "PASS"
    assert report.checks["managed_member_hashes"] == "PASS"
    assert report.checks["runtime_round_trip"] == "NOT_TESTED"


def test_one_byte_component_mutation_fails_packet_hash_and_firmware_info(tmp_path):
    original = _packet_bytes()
    mutated_buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(
        mutated_buffer, "w", zipfile.ZIP_DEFLATED
    ) as destination:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename.endswith("appfs.ubifs"):
                data = b"z" + data[1:]
            destination.writestr(item, data)
    packet_path = tmp_path / "FwPkt.zip"
    packet_path.write_bytes(mutated_buffer.getvalue())

    report = verify_package(packet_path, _manifest(original))

    codes = {issue.code for issue in report.errors}
    assert not report.valid
    assert "PACKET_SHA256_MISMATCH" in codes
    assert "PACKET_INVALID" in codes


def test_duplicate_member_is_rejected_even_when_zip_is_readable(tmp_path):
    packet = _packet_bytes(duplicate=True)
    path = tmp_path / "FwPkt.zip"
    _write(path, packet)

    report = verify_package(path, _manifest(packet))

    assert not report.valid
    assert any(issue.code == "DUPLICATE_ZIP_MEMBER" for issue in report.errors)


def test_wrong_root_and_untrusted_pentax_provenance_fail_closed(tmp_path):
    packet = _packet_bytes(root="Other")
    path = tmp_path / "FwPkt.zip"
    _write(path, packet)
    manifest = _manifest(packet)
    manifest["libgphoto2"]["source_kind"] = "upstream"

    report = verify_package(path, manifest)

    codes = {issue.code for issue in report.errors}
    assert not report.valid
    assert "ZIP_ROOT_INVALID" in codes


def test_pentax_upstream_source_is_rejected_after_valid_packet_parse(tmp_path):
    packet = _packet_bytes()
    path = tmp_path / "FwPkt.zip"
    _write(path, packet)
    manifest = _manifest(packet)
    manifest["libgphoto2"]["source_kind"] = "upstream"

    report = verify_package(path, manifest)

    assert any(issue.code == "PENTAX_SOURCE_POLICY_FAILED" for issue in report.errors)


def test_stock_unchanged_member_drift_is_detected(tmp_path):
    stock_payloads = _payloads(b"s")
    candidate_payloads = dict(stock_payloads)
    candidate_payloads["config"] = b"changed-config"
    stock_packet = _packet_bytes(stock_payloads)
    candidate_packet = _packet_bytes(candidate_payloads)
    stock_path = tmp_path / "stock.zip"
    candidate_path = tmp_path / "candidate.zip"
    _write(stock_path, stock_packet)
    _write(candidate_path, candidate_packet)
    manifest = _manifest(
        candidate_packet,
        candidate_payloads,
        stock={
            "packet_sha256": hashlib.sha256(stock_packet).hexdigest(),
            "unchanged_members": ["camera/config"],
        },
    )

    report = verify_package(candidate_path, manifest, stock_path)

    assert not report.valid
    assert any(issue.code == "STOCK_COMPONENT_DRIFT" for issue in report.errors)


def test_package_cli_returns_distinct_package_failure_code(tmp_path):
    packet = _packet_bytes()
    packet_path = tmp_path / "FwPkt.zip"
    manifest_path = tmp_path / "manifest.json"
    _write(packet_path, packet)
    manifest_path.write_text(json.dumps(_manifest(packet)), encoding="utf-8")

    assert main([
        "package", "verify", "--fwpkt", str(packet_path),
        "--manifest", str(manifest_path),
    ]) == 0
    manifest = _manifest(packet)
    manifest["packet_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert main([
        "package", "verify", "--fwpkt", str(packet_path),
        "--manifest", str(manifest_path),
    ]) == 5
