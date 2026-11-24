"""Exercise a hash-pinned guest ELF closure in an ephemeral offline container."""

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path, PurePosixPath

from .arm_abi import inside


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def execute(args):
    report = {
        "level": "qemu-user",
        "status": "NOT TESTED",
        "mode": args.mode,
        "image": args.image,
        "qemu_sha256": args.qemu_sha256,
        "abi_sha256": args.abi_sha256,
    }
    if (
        not all(p.is_file() for p in (args.abi, args.qemu))
        or not args.root.is_dir()
        or not shutil.which("docker")
    ):
        report["diagnostic"] = (
            "requires ABI report, pinned qemu-arm-static, extracted root and Docker"
        )
        return report
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.image):
        raise ValueError("container image must be pinned by full local image ID")
    if digest(args.abi) != args.abi_sha256 or digest(args.qemu) != args.qemu_sha256:
        raise ValueError("input digest mismatch")
    abi = json.loads(args.abi.read_text())
    if abi["level"] != "static-abi" or abi["status"] != "PASS":
        raise ValueError("requires a passing static ABI report")
    main = abi["entries"][0]
    if args.mode == "busybox-true" and main != "/bin/busybox":
        raise ValueError("positive control requires /bin/busybox")
    if args.preload and args.preload not in abi["entries"][1:]:
        raise ValueError("preload must be an explicitly audited module")
    if args.omit and args.omit not in abi["objects"]:
        raise ValueError("negative control must identify a resolved object")
    report.update(
        candidate_sha256=abi["objects"][main]["sha256"],
        omitted=args.omit,
        preload=args.preload,
    )
    image = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", args.image],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if image.stdout.strip() != args.image or image.returncode:
        report["diagnostic"] = "pinned image unavailable locally"
        return report
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "root"
        stage.mkdir()
        # Only the verified ELF closure enters the container, never the whole firmware.
        stage.chmod(0o755)
        for guest, obj in abi["objects"].items():
            parts = PurePosixPath(guest)
            if parts.anchor != "/" or ".." in parts.parts:
                raise ValueError(f"invalid guest path: {guest}")
            source = inside(args.root.resolve(), guest)
            if not source.is_file():
                report["diagnostic"] = f"missing private input {guest}"
                return report
            if guest == args.omit:
                continue
            dest = stage / guest.lstrip("/")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
            if digest(dest) != obj["sha256"]:
                raise ValueError(f"ABI receipt hash mismatch: {guest}")
            dest.chmod(0o555)
        name = "polaris-abi-" + uuid.uuid4().hex
        cmd = [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--name",
            name,
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user=65534:65534",
            "--pids-limit=16",
            "--memory=128m",
            "--cpus=0.5",
            "--mount",
            f"type=bind,src={stage},dst=/target,readonly",
            "--mount",
            f"type=bind,src={args.qemu.resolve()},dst=/qemu,readonly",
            "--entrypoint",
            "/usr/bin/env",
            args.image,
            "-i",
            "/qemu",
            "-cpu",
            "cortex-a7",
            "-L",
            "/target",
            "-E",
            "LD_DEBUG=libs:versions",
        ]
        if abi["library_path"]:
            cmd += ["-E", "LD_LIBRARY_PATH=" + ":".join(abi["library_path"])]
        if args.preload:
            cmd += ["-E", "LD_PRELOAD=" + args.preload]
        if args.mode == "loader-list":
            cmd += [
                "/target" + abi["objects"][main]["interpreter"],
                "--list",
                "/target" + main,
            ]
        else:
            cmd += ["/target" + main, "true"]
        try:
            run = subprocess.run(
                cmd, capture_output=True, text=True, errors="replace", timeout=20
            )
        except subprocess.TimeoutExpired:
            subprocess.run(
                ["docker", "rm", "-f", name], capture_output=True, timeout=10
            )
            report.update(status="FAIL", diagnostic="20-second deadline exceeded")
            return report
        report.update(returncode=run.returncode, stdout=run.stdout, stderr=run.stderr)
        if run.returncode == 125:
            report["diagnostic"] = "container could not start"
        elif args.omit:
            # Missing inputs are deliberate only for this negative control.
            soname = PurePosixPath(args.omit).name
            detected = run.returncode != 0 and re.search(
                re.escape(soname) + r": cannot open shared object file", run.stderr
            )
            report["status"] = "PASS" if detected else "FAIL"
        else:
            evidence = "checking for version" in run.stderr
            if args.mode == "loader-list":
                # Check the real main/preload closure. Camlibs and iolibs require
                # a later dlopen; static coverage alone cannot mark them loaded.
                loaded = set(re.findall(r"=> ([^ ]+) \(0x", run.stdout))
                loaded.update(re.findall(r"^\s*(/[^ ]+) \(0x", run.stdout, re.M))
                loaded = {p.removeprefix("/target") for p in loaded}
                required = {abi["objects"][main]["interpreter"]}
                owners = [main]
                if args.preload:
                    owners.append(args.preload)
                    required.add(args.preload)
                while owners:
                    owner = owners.pop()
                    for edge in abi["edges"]:
                        if edge["owner"] == owner and edge["path"] not in required:
                            required.add(edge["path"])
                            owners.append(edge["path"])
                # glibc reports its interpreter once even if DT_NEEDED uses
                # the system-directory symlink. Compare provider content.
                loaded_hashes = {
                    abi["objects"][p]["sha256"] for p in loaded if p in abi["objects"]
                }
                missing = sorted(
                    p
                    for p in required
                    if abi["objects"][p]["sha256"] not in loaded_hashes
                )
                report.update(loaded_paths=sorted(loaded), missing_providers=missing)
                evidence = evidence and not missing and loaded <= set(abi["objects"])
            report["status"] = "PASS" if run.returncode == 0 and evidence else "FAIL"
    return report


def register(subparsers):
    parser = subparsers.add_parser(
        "arm-user", help="Run offline QEMU loader or positive control"
    )
    for key in ("root", "abi", "qemu"):
        parser.add_argument("--" + key, type=Path, required=True)
    for key in ("abi-sha256", "image", "qemu-sha256"):
        parser.add_argument("--" + key, required=True)
    parser.add_argument(
        "--mode", choices=["loader-list", "busybox-true"], required=True
    )
    parser.add_argument("--preload", help="explicit wrapper LD_PRELOAD guest path")
    parser.add_argument(
        "--omit", help="deliberately exclude this guest library for a negative control"
    )
    parser.set_defaults(func=command)


def command(args):
    try:
        report = execute(args)
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        report = {"level": "qemu-user", "status": "FAIL", "diagnostic": str(exc)}
    print(json.dumps(report, indent=2, sort_keys=True))
    return {"PASS": 0, "FAIL": 1, "NOT TESTED": 2}[report["status"]]
