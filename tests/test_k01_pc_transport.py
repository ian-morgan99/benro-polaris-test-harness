"""Opt-in PC hardware gate for patcher #379. Supply a built driver prefix."""

import os
from pathlib import Path
import re
import subprocess

import pytest


def test_k01_msc_autodetect_and_read(tmp_path):
    prefix = os.environ.get("POLARIS_K01_DRIVER_PREFIX")
    if not prefix:
        pytest.skip("requires a physical K-01 in MSC mode and POLARIS_K01_DRIVER_PREFIX")
    lib = Path(prefix).resolve() / "lib"
    camlib, = lib.glob("libgphoto2/*/pentax.so")
    iolib, = lib.glob("libgphoto2_port/*/usbscsi.so")
    env = dict(os.environ, LC_ALL="C", LD_LIBRARY_PATH=str(lib),
               CAMLIBS=str(camlib.parent), IOLIBS=str(iolib.parent))
    sg, = [p for p in Path("/sys/class/scsi_generic").glob("sg*")
           if (p / "device/model").read_text().strip() == "DSC_K-01"
           and (p / "device/vendor").read_text().strip() == "PENTAX"]
    usb, = [p for p in (sg / "device").resolve().parents
            if (p / "idVendor").exists() and (p / "idProduct").exists()
            and (p / "idVendor").read_text().strip() == "25fb"
            and (p / "idProduct").read_text().strip() == "0130"]
    plain = "usb:%03d,%03d" % (int((usb / "busnum").read_text()),
                                int((usb / "devnum").read_text()))
    scsi = f"usbscsi:/dev/{sg.name}"

    def run(*args):
        result = subprocess.run(["gphoto2", *args], env=env, check=True,
                                capture_output=True, text=True, timeout=45)
        assert "*** Error ***" not in result.stderr
        return result.stdout

    found = run("--auto-detect").splitlines()
    assert [row.split(scsi)[0].strip() for row in found if row.endswith(scsi)] == ["Pentax K01"]
    assert not any(row.endswith(plain) for row in found), found
    debug = tmp_path / "read.log"
    settings = run("--debug", "--debug-logfile", str(debug), "--port", scsi,
                   "--camera", "Pentax K01", "--get-config", "shutterspeed",
                   "--get-config", "aperture", "--get-config", "iso")
    assert re.findall(r"^Label: (.+)$", settings, re.M) == ["Shutter Speed", "Aperture", "ISO"]
    assert len(re.findall(r"^Current: \S+.*$", settings, re.M)) == 3
    trace = debug.read_text()
    for module in (camlib, iolib):
        assert str(module.with_suffix("")) in trace
