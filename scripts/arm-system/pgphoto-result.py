"""Evaluate v115 service startup from guest observations on one serial console."""

import json
import re
import sys
from pathlib import Path


def evaluate(text, legacy):
    text = re.sub(r"\x1b\[[0-9;]*m", "", text).replace("\r", "")
    listeners = set()
    for line in text.splitlines():
        cols = line.split()
        if len(cols) >= 10 and cols[1].endswith(":1F90") and cols[3] == "0A":
            listeners.add(cols[9])
    descriptors = set(re.findall(r"^socket:\[(\d+)\]$", text, re.M))
    checks = {
        "packaged_process": "/app/lib/stage2/pgphoto.stage2ondisk\n" in text,
        "stage2_core_and_port": all(
            s in text
            for s in (
                "[stage2] dlopen core ok",
                "[stage2] dlopen port ok",
                "[stage2] slots filled 68/68",
            )
        ),
        "ipc_queues": all(
            f"create msg queue success, key[{k}]" in text for k in (1003, 1002)
        ),
        "owned_8080_listener": bool(listeners & descriptors),
        "stream_http_response": "HTTP/1.0 200 OK" in text
        and "Content-Type: multipart/x-mixed-replace" in text,
        "expected_camera_boundary": (
            "sp_Gphoto_Init ret -105" in text
            and "Could not detect any camera" in text
            and "state:-5;" in text
        )
        if legacy
        else (
            "No iolibs found in '../lib/libgphoto2_port/0.12.0'" in text
            and "sp_Gphoto_Init ret -2 " in text
            and "state:-2;" in text
        ),
        "sigterm_exit": "TERM_SENT\n" in text
        and "KILL_SENT\n" not in text
        and "SERVICE_EXIT=143\n" in text,
        "observer_completed": "GUEST_FINISHED\n" in text,
    }
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks}


if __name__ == "__main__":
    results = {
        arm: evaluate((Path(sys.argv[1]) / f"{arm}.log").read_text(), arm == "legacy")
        for arm in ("closure", "legacy")
    }
    print(json.dumps(results, indent=2, sort_keys=True))
    sys.exit(any(r["status"] != "PASS" for r in results.values()))
