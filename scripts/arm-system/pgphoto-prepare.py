"""Build a private v115 guest initramfs from the two qualified issue #28 receipts."""

import argparse
import gzip
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from polaris_harness.arm_abi import inside
from polaris_harness.arm_user import digest

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE.parents[1] / "docs/evidence"


def prepare(root, abi, bundle, output, legacy_ports):
    loader = json.loads((EVIDENCE / "issue-28-v115-loader.json").read_text())
    system = json.loads((EVIDENCE / "issue-28-arm-system.json").read_text())
    system_lock = system["independent_bundle_runs"][0]["inputs"]["sha256"]
    if digest(abi) != loader["abi_receipt_sha256"]:
        raise ValueError("v115 ABI receipt mismatch")
    bootstrap = bundle / "initramfs.gz"
    if digest(bootstrap) != system_lock["initramfs.gz"]:
        raise ValueError("generic guest bootstrap mismatch")
    output = output.resolve()
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        with gzip.open(bootstrap) as stream:
            subprocess.run(
                ["cpio", "-idm", "--quiet"], input=stream.read(), cwd=stage, check=True
            )
        expected = {
            g: o["sha256"] for g, o in json.loads(abi.read_text())["objects"].items()
        }
        if legacy_ports:
            expected.update(json.loads((HERE / "v115-legacy-ports.json").read_text()))
        for guest, sha in expected.items():
            source = inside(root.resolve(), guest)
            dest = stage / guest.lstrip("/")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
            if digest(dest) != sha:
                raise ValueError(f"v115 ELF mismatch: {guest}")
            dest.chmod(0o755)
        shutil.copyfile(HERE / "pgphoto-init", stage / "init")
        for path in [*stage.rglob("*"), stage]:
            os.utime(path, (0, 0))
        names = sorted(str(p.relative_to(stage)) for p in stage.rglob("*"))
        archive = subprocess.run(
            ["cpio", "--null", "-o", "-H", "newc", "--reproducible", "--owner=0:0"],
            input=("\0".join(names) + "\0").encode(),
            cwd=stage,
            capture_output=True,
            check=True,
        )
        with output.open("xb") as stream:
            stream.write(gzip.compress(archive.stdout, mtime=0))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("root", "abi", "bundle", "output"):
        parser.add_argument("--" + key, required=True, type=Path)
    parser.add_argument("--legacy-ports", action="store_true")
    args = parser.parse_args()
    prepare(args.root, args.abi, args.bundle, args.output, args.legacy_ports)
