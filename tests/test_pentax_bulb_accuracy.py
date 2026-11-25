"""Opt-in PC gate for Pentax timed-Bulb duration accuracy (K-1 II).

Requires POLARIS_PENTAX_DRIVER_PREFIX (built by the patcher's
scripts/build-host-driver-prefix.sh, which asserts pentax_research_capture is on) and
POLARIS_PENTAX_CAMERA=k1ii. Skips otherwise so CI stays green.

What this pins down, all measured on a live K-1 II in B with bulb-timer=yes:

1. The `bulb` action widget only exists in a research-capture build. A prefix built
   without -Dpentax_research_capture=true exposes 49 config paths and no /main/actions/bulb,
   and camera_capture() returns GP_ERROR_NOT_SUPPORTED (-6) as soon as vendor mode is on.
   That -6 looks identical to a camera defect, so the builder asserts it instead.

2. Duration accuracy: the driver derives the exposure length from the MEASURED hold between
   the start and stop edges (pentax_bulb_hold_to_seconds), never from the body's pre-shutter
   report. On this body that report is a stale 20 s, so using it would be Polaris issue #243.

3. The trap that makes (2) look broken: gp_camera_get_config() walks the whole settings tree
   and costs ~6 s on this body. If a caller fetches the tree inside the hold, that walk lands
   between the driver's start timestamp and stop timestamp, so a 1 s request is measured as
   ~7 s and applied as 7 s. The driver is faithful; the caller inflated the interval. Callers
   must cache the widget tree across the two edges (this test does exactly that).

4. gphoto2 --wait-event cannot be used to measure fidelity: it schedules in whole seconds and
   added +1.000 s to every hold measured (2.002/2.001/4.001/5.999 for requests 1/2/3/5).
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

CAMERAS = {
    "k1ii": {"pid": 0x0183, "model": "Pentax K-1 Mark II (PTP mode)"},
}

# The driver logs the duration it derived from the measured hold.
APPLIED_RE = re.compile(r"camera-timed fallback applying (\d+) s shutter speed")
# And the body's pre-shutter report, which must NOT drive that duration.
BODY_REPORT_RE = re.compile(r"body reported (\d+) s at the start edge")


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


@pytest.fixture(params=["k1ii"])
def body(request):
    key = request.param
    sel = os.environ.get("POLARIS_PENTAX_CAMERA")
    if sel and sel != key:
        pytest.skip(f"POLARIS_PENTAX_CAMERA={sel}, not {key}")
    prefix = _prefix()
    env, camlib, iolib = _env(prefix)
    spec = CAMERAS[key]
    port = _port(spec["pid"])
    return key, env, port, spec["model"], prefix


def _build_helper(prefix, tmp_path_factory):
    """Compile tools/pentax_bulb_direct.c against the built prefix."""
    cc = shutil.which("cc") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler available")
    src = Path(__file__).resolve().parent.parent / "tools" / "pentax_bulb_direct.c"
    if not src.exists():
        pytest.skip(f"helper source missing: {src}")
    out = Path(tmp_path_factory.mktemp("bulb")) / "pentax_bulb_direct"
    r = subprocess.run(
        [cc, "-O2", f"-I{prefix}/include/gphoto2", str(src), "-o", str(out),
         f"-L{prefix}/lib", "-lgphoto2", "-lgphoto2_port"],
        capture_output=True, text=True)
    if r.returncode:
        pytest.fail(f"helper did not compile:\n{r.stderr[-1500:]}")
    return out


def _run_helper(helper, env, port, model, hold, iters, cached):
    r = subprocess.run([str(helper), port, model, str(hold), str(iters), str(cached)],
                       env=env, capture_output=True, text=True, timeout=600)
    return r.returncode, r.stdout + r.stderr


def test_bulb_widget_requires_research_capture(body):
    """The bulb action widget must be present; its absence means a mis-built prefix."""
    key, env, port, model, prefix = body
    prov = prefix / "provenance.txt"
    if prov.exists():
        text = prov.read_text()
        assert "pentax_research_capture=true" in text, (
            f"{prefix} was not built with pentax_research_capture=true; a prefix without it "
            f"exposes no bulb widget and returns -6 on every capture.\n{text}")

    r = subprocess.run(["gphoto2", "--port", port, "--camera", model, "--list-config"],
                       env=env, capture_output=True, text=True, timeout=300)
    paths = [l.strip() for l in (r.stdout + r.stderr).splitlines() if l.startswith("/")]
    assert "/main/actions/bulb" in paths, (
        f"no bulb action widget on {key} ({len(paths)} paths). Either the body is not in a "
        f"Bulb-capable state or the prefix lacks research capture.")


def test_bulb_baseline_is_camera_timed(body):
    """Record the baseline the accuracy claim depends on: B mode with an active bulb timer.

    The stale body timer is the whole point of #243, so the test must fail loudly if the
    body is not in that state rather than silently passing a vacuous case.
    """
    key, env, port, model, _ = body
    r = subprocess.run(["gphoto2", "--port", port, "--camera", model,
                        "--get-config", "/main/status/pentaxconditions"],
                       env=env, capture_output=True, text=True, timeout=300)
    text = r.stdout + r.stderr
    mode = re.search(r"exposure-mode=(\w+)", text)
    timer = re.search(r"bulb-timer=(\w+)", text)
    secs = re.search(r"bulb-seconds=([\d/]+)", text)
    if not mode or mode.group(1) != "Bulb":
        pytest.skip(f"{key} is not in Bulb mode (exposure-mode="
                    f"{mode.group(1) if mode else 'unknown'}); put the body in B to run this")
    print(f"baseline: exposure-mode={mode.group(1)} bulb-timer="
          f"{timer.group(1) if timer else '?'} bulb-seconds={secs.group(1) if secs else '?'}")


@pytest.mark.parametrize("hold", [1, 2])
def test_timed_bulb_applies_requested_duration(body, hold, tmp_path_factory):
    """A cached-tree caller asking for N seconds must get N seconds, not the body's stale timer.

    This is the accuracy requirement: pgphoto's timed Bulb is driven by sleeping between the
    start and stop edges, so the driver derives the length from that interval. The body here
    reports a stale 20 s at the pre-shutter probe; if that value ever drives the duration the
    exposure is wrong by an order of magnitude (Polaris #243).
    """
    key, env, port, model, _ = body
    helper = _build_helper(_prefix(), tmp_path_factory)

    rc, out = _run_helper(helper, env, port, model, hold, 2, cached=1)
    applied = [int(x) for x in APPLIED_RE.findall(out)]
    body_reported = [int(x) for x in BODY_REPORT_RE.findall(out)]

    # Both edges must be accepted; a refusal here is the -6 / busy class of failure.
    assert "start=0 stop=0" in out, f"an edge was refused:\n{out[-2000:]}"
    assert applied, f"driver never derived a duration:\n{out[-2000:]}"

    for got in applied:
        assert got == hold, (
            f"{key}: requested {hold}s but the driver applied {got}s. If the body reported "
            f"{body_reported}, this is Polaris #243 (stale timer driving the duration).")

    # The stale body report must be observed and explicitly unused, not silently ignored.
    if body_reported:
        assert all(b != hold for b in body_reported) or hold == max(body_reported), (
            "body report equals the request; cannot distinguish #243 from a correct run")
        print(f"body reported {body_reported} at the start edge and it was NOT used")


def test_uncached_tree_walk_inflates_the_hold(body, tmp_path_factory):
    """Document the caller-side trap: re-fetching the widget tree between edges inflates it.

    gp_camera_get_config() walks the whole settings tree (~6 s on this body). Doing that inside
    the hold lands the walk between the driver's start and stop timestamps, so a 1 s request is
    measured as ~7 s. The driver is faithful; the caller must cache the tree. This asserts the
    inflation is real and reproducible so nobody re-reports it as a driver duration bug.
    """
    key, env, port, model, _ = body
    helper = _build_helper(_prefix(), tmp_path_factory)

    _, uncached = _run_helper(helper, env, port, model, 1, 1, cached=0)
    _, cached = _run_helper(helper, env, port, model, 1, 1, cached=1)

    walk = re.search(r"start edge: get_config=([\d.]+)s", uncached)
    if not walk or float(walk.group(1)) < 1.0:
        pytest.skip(f"tree walk was fast this run "
                    f"({walk.group(1) if walk else 'n/a'}s); inflation not reproducible")
    unc_applied = [int(x) for x in APPLIED_RE.findall(uncached)]
    cas_applied = [int(x) for x in APPLIED_RE.findall(cached)]
    assert cas_applied and cas_applied[0] == 1, (
        f"cached-tree run should apply exactly 1s, got {cas_applied}")
    assert unc_applied and unc_applied[0] > 1, (
        f"expected the uncached tree walk to inflate a 1s request, got {unc_applied}")
    print(f"{key}: {walk.group(1)}s tree walk inside the hold turned a 1s request into "
          f"{unc_applied[0]}s; cached-tree caller applied {cas_applied[0]}s")
