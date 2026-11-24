"""Boot a generic guest twice against independent temporary QCOW2 overlays."""

import json
import re
import secrets
import shutil
import subprocess
import uuid
from pathlib import Path

from .arm_user import digest

FILES = ("kernel", "initramfs.gz", "base.qcow2")
BOOT = """
set -eu
qemu-img create -q -f qcow2 -F qcow2 -b /base.qcow2 /state/overlay
boot() {
    timeout -k 2 60 qemu-system-arm -accel tcg -machine virt,highmem=off -cpu cortex-a7 \
      -smp 1 -m 192 -nodefaults -display none -monitor none -serial stdio -nic none \
      -no-reboot -kernel /kernel -initrd /initramfs.gz \
      -append "console=ttyAMA0 rdinit=/init quiet panic=1 harness.token=$1" \
      -drive if=none,id=d,file=/state/overlay,format=qcow2 \
      -device virtio-blk-device,drive=d
}
boot "$1"
if [ -n "$2" ]; then boot "$2"; fi
"""


def verdict(log, token, replay_token):
    markers = [line.strip() for line in log.splitlines() if line.startswith("HARNESS ")]
    expected = ["HARNESS FRESH " + token]
    if replay_token:
        expected.append(f"HARNESS STALE {replay_token} {token}")
    return markers == expected


def run(bundle, lock_sha):
    result = {
        "level": "qemu-system",
        "scenario": "generic-arm-overlay-isolation-v1",
        "status": "NOT TESTED",
        "lock_sha256": lock_sha,
        "runs": [],
    }
    bundle = bundle.resolve()
    lock_path = bundle / "lock.json"
    if not lock_path.is_file() or not shutil.which("docker"):
        result["diagnostic"] = "requires locked guest bundle and local Docker"
        return result
    if digest(lock_path) != lock_sha:
        raise ValueError("lock SHA-256 mismatch")
    lock = json.loads(lock_path.read_text())
    if lock["schema"] != 1 or set(lock["sha256"]) != set(FILES):
        raise ValueError("unexpected system bundle schema or inputs")
    image = lock["image"]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("expected immutable tools image ID")
    result["inputs"] = lock
    for name in FILES:
        path = bundle / name
        if not path.is_file():
            result["diagnostic"] = f"missing {name}"
            return result
        if path.is_symlink() or digest(path) != lock["sha256"][name]:
            raise ValueError(f"unverified input {name}")
    inspect = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if inspect.returncode or inspect.stdout.strip() != image:
        result["diagnostic"] = "pinned tools image unavailable"
        return result
    for stale in (True, False):
        token = secrets.token_hex(16)
        replay_token = secrets.token_hex(16) if stale else ""
        name = "arm-system-" + uuid.uuid4().hex
        cmd = [
            "docker",
            "run",
            "--pull=never",
            "--rm",
            "--name",
            name,
            "--network=none",
            "--read-only",
            "--user=65534:65534",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=32",
            "--memory=384m",
            "--cpus=1",
            "--tmpfs=/state:rw,nosuid,nodev,noexec,size=16m,mode=1777",
        ]
        for file in FILES:
            cmd += ["--mount", f"type=bind,src={bundle / file},dst=/{file},readonly"]
        cmd += [
            "--entrypoint",
            "/usr/bin/env",
            image,
            "-i",
            "PATH=/usr/bin:/bin",
            "/bin/sh",
            "-c",
            BOOT,
            "boot",
            token,
            replay_token,
        ]
        try:
            boot = subprocess.run(
                cmd, capture_output=True, text=True, errors="replace", timeout=135
            )
        except subprocess.TimeoutExpired:
            subprocess.run(
                ["docker", "rm", "-f", name], capture_output=True, timeout=10
            )
            result.update(status="FAIL", diagnostic="outer boot deadline expired")
            return result
        result["runs"].append(
            {
                "token": token,
                "replay_token": replay_token,
                "returncode": boot.returncode,
                "serial": boot.stdout,
                "stderr": boot.stderr,
            }
        )
        if boot.returncode == 125:
            result["diagnostic"] = "container did not start"
            return result
        if boot.returncode or not verdict(boot.stdout, token, replay_token):
            result.update(
                status="FAIL", diagnostic="guest exit or serial evidence mismatch"
            )
            return result
    if digest(bundle / "base.qcow2") != lock["sha256"]["base.qcow2"]:
        raise ValueError("QCOW2 base changed during run")
    result["status"] = "PASS"
    return result


def register(subparsers):
    parser = subparsers.add_parser(
        "arm-system", help="Verify offline generic ARM overlay isolation"
    )
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--lock-sha256", required=True)
    parser.set_defaults(func=command)


def command(args):
    try:
        report = run(args.bundle, args.lock_sha256)
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        report = {"level": "qemu-system", "status": "FAIL", "diagnostic": str(exc)}
    print(json.dumps(report, indent=2, sort_keys=True))
    return {"PASS": 0, "FAIL": 1, "NOT TESTED": 2}[report["status"]]
