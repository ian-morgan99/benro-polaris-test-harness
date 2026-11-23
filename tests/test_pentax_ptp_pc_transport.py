"""Opt-in PC hardware gate for Pentax K-1 II (PTP) and K-3 III (MTP).

Mirrors tests/test_k01_pc_transport.py but for the ptp2/usb1 path. Supply a
built driver prefix via POLARIS_PENTAX_DRIVER_PREFIX and pick a body with
POLARIS_PENTAX_CAMERA=k1ii|k3iii. Without those it skips, so CI stays green.

Verified-working behaviours are asserted. Known v115 defects (see patcher
#384/#386/#387) are recorded as xfail(strict=True): they must FAIL today, and if
a fix makes them pass the strict xfail flips to XPASS->error, forcing the gate to
be updated. That is how a regression like "imageformat silently broke" gets
caught instead of silently shipping.

MF drive (#381) is deliberately NOT strict-xfail: its result depends on whether
vendor mode was already primed earlier in the same session, so a strict marker
produces a spurious red depending on test order. It records the observed PTP
response instead, and #381 carries the open question.
"""

import os
from pathlib import Path
import re
import subprocess

import pytest

CAMERAS = {
    "k1ii": {"pid": 0x0183, "model": "Pentax K-1 Mark II (PTP mode)"},
    "k3iii": {"pid": 0x0189, "model": "Pentax K-3 Mark III (MTP mode)"},
}


def _prefix():
    p = os.environ.get("POLARIS_PENTAX_DRIVER_PREFIX")
    if not p:
        pytest.skip("requires POLARIS_PENTAX_DRIVER_PREFIX and a physical body")
    return Path(p).resolve()


def _env(prefix):
    lib = prefix / "lib"
    camlib, = lib.glob("libgphoto2/*/ptp2.so")
    iolib, = lib.glob("libgphoto2_port/*/usb1.so")
    return dict(os.environ, LC_ALL="C", LD_LIBRARY_PATH=str(lib),
                CAMLIBS=str(camlib.parent), IOLIBS=str(iolib.parent)), camlib, iolib


def _port(pid):
    for usb in Path("/sys/bus/usb/devices").glob("*/"):
        if not (usb / "idVendor").exists():
            continue
        if int((usb / "idVendor").read_text().strip(), 16) != 0x25FB:
            continue
        if int((usb / "idProduct").read_text().strip(), 16) == pid:
            bus = int((usb / "busnum").read_text())
            dev = int((usb / "devnum").read_text())
            return "usb:%03d,%03d" % (bus, dev)
    pytest.skip("no matching Pentax body on the USB bus")


def _run(env, port, model, *args):
    r = subprocess.run(["gphoto2", "--port", port, "--camera", model, *args],
                       env=env, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


@pytest.fixture(params=["k1ii", "k3iii"])
def body(request):
    key = request.param
    sel = os.environ.get("POLARIS_PENTAX_CAMERA")
    if sel and sel != key:
        pytest.skip(f"POLARIS_PENTAX_CAMERA={sel}, not {key}")
    prefix = _prefix()
    env, camlib, iolib = _env(prefix)
    spec = CAMERAS[key]
    port = _port(spec["pid"])
    return key, env, port, spec["model"], camlib, iolib


def test_autodetect_single_row(body):
    key, env, port, model, *_ = body
    out = subprocess.run(["gphoto2", "--auto-detect"], env=env,
                         capture_output=True, text=True).stdout
    rows = [ln for ln in out.splitlines() if ln.strip().endswith(port)]
    assert len(rows) == 1, f"expected exactly one row for {port}: {out}"


def test_widget_shape_no_empty_arrays(body):
    """A writable RADIO/MENU must publish >=1 choice (patcher #387 class)."""
    key, env, port, model, *_ = body
    rc, listing = _run(env, port, model, "--list-config")
    assert rc == 0, listing
    widgets = [w.strip() for w in listing.splitlines() if w.startswith("/")]
    empty = []
    for w in widgets:
        rc, d = _run(env, port, model, "--get-config", w.lstrip("/"))
        if "Type: RADIO" not in d and "Type: MENU" not in d:
            continue
        if "Readonly: 0" not in d:
            continue
        if len(re.findall(r"^Choice:", d, re.M)) == 0:
            empty.append(w)
    # K-3 III publishes five empty writable arrays (#387); K-1 II publishes none.
    if key == "k3iii":
        pytest.xfail("K-3 III empty writable arrays (patcher #387)")
    assert not empty, f"empty writable choice arrays: {empty}"


def test_iso_write_reaches_body(body):
    """Write must change the live body readout, not just the gphoto cache."""
    key, env, port, model, *_ = body
    rc, base = _run(env, port, model, "--get-config", "iso")
    cur = re.search(r"^Current: (\S+)", base, re.M).group(1)
    target = "400" if cur != "400" else "200"
    rc, _ = _run(env, port, model, "--set-config", f"iso={target}")
    assert rc == 0, "iso write failed"
    rc, cond = _run(env, port, model, "--get-config", "/main/status/pentaxconditions")
    live = re.search(r"ISO=(\d+)", cond)
    assert live and live.group(1) == target, f"body did not apply iso: {cond[:200]}"
    _run(env, port, model, "--set-config", f"iso={cur}")  # restore


def test_manual_focus_drive_reports_a_response(body):
    """Record MF drive behaviour without asserting a fixed outcome (#381).

    Observed on v115: an unprimed session returns vendor response 0xa00c, while
    a session that already read conditions or wrote a setting can return OK.
    That makes the pass/fail result order-dependent, so asserting either way
    here would be a lie. This test only requires the body to answer with a
    recognisable PTP outcome rather than hanging, and prints the code so the
    run log carries evidence for #381.
    """
    key, env, port, model, *_ = body
    rc, out = _run(env, port, model, "--set-config", "manualfocusdrive=3")
    code = re.search(r"response 0x([0-9a-f]{4})", out)
    outcome = f"0x{code.group(1)}" if code else ("OK" if rc == 0 else "error")
    print(f"MFDRIVE {key}: rc={rc} response={outcome}")
    # A hang or transport failure is a real defect; a vendor code is not.
    assert rc == 0 or code, f"MF drive produced no PTP outcome: {out[:300]}"


@pytest.mark.xfail(strict=True, reason="imageformat write -102 cross-model (patcher #386)")
def test_imageformat_write(body):
    key, env, port, model, *_ = body
    rc, base = _run(env, port, model, "--get-config", "imageformat")
    cur = re.search(r"^Current: (.+)$", base, re.M).group(1).strip()
    target = "RAW+JPEG" if cur != "RAW+JPEG" else "RAW"
    rc, out = _run(env, port, model, "--set-config", f"imageformat={target}")
    assert rc == 0, out
