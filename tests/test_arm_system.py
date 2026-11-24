import hashlib
import json

import pytest

from polaris_harness import arm_system


def test_stale_control_requires_exact_previous_disk_contents():
    first, replay = "a" * 32, "b" * 32
    log = f"HARNESS FRESH {first}\r\nHARNESS STALE {replay} {first}\r\n"
    assert arm_system.verdict(log, first, replay)
    assert not arm_system.verdict(
        log.replace(f"{replay} {first}", f"{replay} {replay}"), first, replay
    )
    assert not arm_system.verdict(log, first, "")
    assert not arm_system.verdict("", first, "")


def test_unprovisioned_bundle_is_not_tested(tmp_path):
    assert arm_system.run(tmp_path, "0" * 64)["status"] == "NOT TESTED"


def test_reject_tampered_base_before_container_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(arm_system.shutil, "which", lambda _: "/usr/bin/docker")
    hashes = {}
    for filename in arm_system.FILES:
        (tmp_path / filename).write_bytes(b"fixture")
        hashes[filename] = hashlib.sha256(b"fixture").hexdigest()
    lock = tmp_path / "lock.json"
    lock.write_text(
        json.dumps({"schema": 1, "image": "sha256:" + "a" * 64, "sha256": hashes})
    )
    (tmp_path / "base.qcow2").write_bytes(b"changed")
    with pytest.raises(ValueError, match="unverified input base.qcow2"):
        arm_system.run(tmp_path, arm_system.digest(lock))
