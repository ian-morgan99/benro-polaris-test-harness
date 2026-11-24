"""Offline ELF dependency inspection. Never invokes the candidate or host ldd."""

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections import deque
from pathlib import Path, PurePosixPath


def inspect(path):
    data = subprocess.run(
        ["readelf", "-W", "-h", "-l", "-d", "-A", "-V", "--", str(path)],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=20,
        env={**os.environ, "LC_ALL": "C"},
    )
    if data.returncode or data.stderr.strip():
        raise ValueError(f"readelf {path.name}: {data.stderr.strip()}")
    text = data.stdout
    fields = dict(
        re.findall(r"^\s*(Class|Data|Machine|Flags|Tag_\w+):\s*(.+)$", text, re.M)
    )
    needed = re.findall(r"\(NEEDED\).*?\[([^\]]+)\]", text)
    interp = re.search(r"Requesting program interpreter: ([^\]]+)\]", text)
    paths = {}
    for kind, value in re.findall(r"\((RPATH|RUNPATH)\).*?\[([^\]]*)\]", text):
        paths[kind] = value.split(":")
    versions = {}
    definitions = []
    section = provider = None
    for line in text.splitlines():
        if line.startswith("Version "):
            section = line.split()[1]
            provider = None
        if section == "needs":
            if match := re.search(r"\bFile: (\S+)", line):
                provider = match[1]
            if provider and (match := re.search(r"\bName: (\S+)", line)):
                versions.setdefault(provider, []).append(match[1])
        if section == "definition" and (match := re.search(r"\bName: (\S+)", line)):
            definitions.append(match[1])
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "sha256": digest,
        "fields": fields,
        "interpreter": interp[1] if interp else None,
        "needed": needed,
        "paths": paths,
        "versions": versions,
        "definitions": definitions,
    }


def inside(root, guest):
    """Interpret absolute symlinks in the guest namespace. Never follow host paths."""
    name = PurePosixPath(guest)
    if not name.is_absolute() or name.anchor != "/":
        raise ValueError(f"expected absolute guest path: {guest!r}")
    pending = deque(name.parts[1:])
    current = root
    links = 0
    while pending:
        part = pending.popleft()
        if part == "..":
            current = current.parent if current != root else root
            continue
        current = current / part
        if current.is_symlink():
            links += 1
            if links > 40:
                raise ValueError(f"symlink loop: {guest}")
            target = PurePosixPath(os.readlink(current))
            current = root if target.is_absolute() else current.parent
            pending.extendleft(
                reversed(target.parts[1:] if target.is_absolute() else target.parts)
            )
    return current


def directories(values, guest):
    result = []
    for value in values:
        value = value.replace("${ORIGIN}", str(PurePosixPath(guest).parent))
        value = value.replace("$ORIGIN", str(PurePosixPath(guest).parent))
        if not value.startswith("/") or "$" in value:
            raise ValueError(f"{guest}: unsupported loader directory {value!r}")
        result.append(value)
    return result


def qualify(root, stock, expected, entries, library_path, system_dirs):
    report = {
        "level": "static-abi",
        "status": "NOT TESTED",
        "entries": list(entries),
        "library_path": list(library_path),
        "system_dirs": list(system_dirs),
        "objects": {},
        "edges": [],
        "diagnostics": [],
    }
    if not root.is_dir() or not stock.is_file() or not shutil.which("readelf"):
        report["diagnostics"].append(
            "requires extracted root, stock reference and GNU readelf"
        )
        return report
    root = root.resolve()
    objects = report["objects"]
    try:
        reference = report["reference"] = inspect(stock)
        if reference["sha256"] != expected:
            raise ValueError("stock reference SHA-256 mismatch")
        ref = reference["fields"]
        if (
            ref["Machine"] != "ARM"
            or "Version5 EABI" not in ref["Flags"]
            or not reference["interpreter"]
        ):
            raise ValueError(
                "reference must be an ARM EABI5 dynamically linked executable"
            )
        if not entries:
            raise ValueError("at least one executable is required")
        if not inside(root, entries[0]).is_file():
            report["diagnostics"].append(f"missing private candidate {entries[0]}")
            return report
        if inside(root, "/etc/ld.so.cache").exists():
            raise ValueError("target ld.so.cache requires a cache-aware resolver")
        queue = deque((entry, []) for entry in entries)
        while queue:
            guest, inherited = queue.popleft()
            path = inside(root, guest)
            if not path.is_file():
                raise ValueError(f"missing guest file {guest}")
            if guest in objects:
                continue
            obj = objects[guest] = inspect(path)
            actual = obj["fields"]
            for key in ("Class", "Data", "Machine"):
                if actual[key] != ref[key]:
                    raise ValueError(f"{guest}: incompatible {key}: {actual[key]}")
            for flag in ("Version5 EABI", "soft-float ABI", "hard-float ABI"):
                if (flag in ref["Flags"]) != (flag in actual["Flags"]):
                    raise ValueError(
                        f"{guest}: incompatible ARM flags {actual['Flags']}"
                    )
            arch = actual.get("Tag_CPU_arch")
            if ref.get("Tag_CPU_arch") == "v7" and arch not in (
                "v4",
                "v4T",
                "v5T",
                "v5TE",
                "v5TEJ",
                "v6",
                "v6K",
                "v6KZ",
                "v6T2",
                "v7",
            ):
                raise ValueError(f"{guest}: unqualified CPU architecture {arch}")
            if actual.get("Tag_CPU_arch_profile") not in (
                None,
                ref.get("Tag_CPU_arch_profile"),
            ):
                raise ValueError(f"{guest}: incompatible CPU profile")
            if actual.get("Tag_ABI_VFP_args") != ref.get("Tag_ABI_VFP_args"):
                raise ValueError(
                    f"{guest}: incompatible floating-point argument convention"
                )
            # PT_INTERP applies when the kernel starts the main executable.
            # Some libc DSOs also contain a PT_INTERP for standalone invocation.
            if guest == entries[0]:
                if obj["interpreter"] != reference["interpreter"]:
                    raise ValueError(
                        f"{guest}: incompatible interpreter {obj['interpreter']}"
                    )
                queue.append((obj["interpreter"], []))
            paths = obj["paths"]
            runpath = directories(paths.get("RUNPATH", []), guest)
            rpath = (
                directories(paths.get("RPATH", []), guest)
                if "RUNPATH" not in paths
                else []
            )
            ancestors = rpath + inherited
            search = (
                ([] if "RUNPATH" in paths else ancestors)
                + library_path
                + runpath
                + system_dirs
            )
            for needed in obj["needed"]:
                if "/" in needed:
                    raise ValueError(
                        f"{guest}: unsupported path-qualified DT_NEEDED {needed}"
                    )
                for directory in search:
                    candidate = str(PurePosixPath(directory) / needed)
                    if inside(root, candidate).is_file():
                        report["edges"].append(
                            {"owner": guest, "needed": needed, "path": candidate}
                        )
                        queue.append((candidate, ancestors))
                        break
                else:
                    raise ValueError(f"{guest}: missing {needed}; searched {search}")
        for edge in report["edges"]:
            wanted = objects[edge["owner"]]["versions"].get(edge["needed"], [])
            missing = set(wanted) - set(objects[edge["path"]]["definitions"])
            if missing:
                raise ValueError(
                    f"{edge['path']}: lacks {sorted(missing)} needed by {edge['owner']}"
                )
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        report["status"] = "FAIL"
        report["diagnostics"].append(str(exc))
    else:
        report["status"] = "PASS"
    return report


def register(subparsers):
    parser = subparsers.add_parser(
        "arm-abi", help="Inspect target ELF files and their dependency closure"
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--stock", type=Path, required=True)
    parser.add_argument("--stock-sha256", required=True)
    parser.add_argument(
        "--system-dir",
        action="append",
        required=True,
        help="loader default directory, in target order",
    )
    parser.add_argument("--library-dir", action="append", default=[])
    parser.add_argument(
        "entry", nargs="+", help="absolute guest paths, including explicit plugin roots"
    )
    parser.set_defaults(func=command)


def command(args):
    result = qualify(
        args.root,
        args.stock,
        args.stock_sha256,
        args.entry,
        args.library_dir,
        args.system_dir,
    )
    print(json.dumps(result, sort_keys=True, indent=2))
    return {"PASS": 0, "FAIL": 1, "NOT TESTED": 2}[result["status"]]
