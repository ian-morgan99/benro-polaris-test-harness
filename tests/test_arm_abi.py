import hashlib
import json
import shutil
import subprocess

import pytest

from polaris_harness import arm_abi


@pytest.fixture
def fixture(tmp_path):
    """Synthetic ARM EABI executable and versioned DSO, no private inputs."""
    cc = shutil.which("arm-linux-gnueabi-gcc")
    if not cc or not shutil.which("readelf"):
        pytest.skip("requires ARM cross compiler and GNU readelf")
    lib = tmp_path / "lib"
    lib.mkdir()
    (tmp_path / "lib.c").write_text("int answer(void) {return 42;}")
    (tmp_path / "main.c").write_text(
        "extern int answer(void); int _start(void){return answer();}"
    )
    (tmp_path / "version").write_text("TEST_1 { global: answer; };")
    common = [cc, "-march=armv7-a", "-mfloat-abi=soft", "-nostdlib"]
    subprocess.run(
        [
            *common,
            "-shared",
            "-fPIC",
            "lib.c",
            "-Wl,--version-script=version",
            "-Wl,-soname,libanswer.so",
            "-o",
            "lib/libanswer.so",
        ],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        [*common, "-no-pie", "main.c", "-Llib", "-lanswer", "-o", "main"],
        cwd=tmp_path,
        check=True,
    )
    # Parser-only loader stand-in. This fixture is never executed.
    shutil.copy(lib / "libanswer.so", lib / "ld-linux.so.3")
    stock = tmp_path / "stock"
    shutil.copy(tmp_path / "main", stock)

    def inspect():
        return arm_abi.qualify(
            tmp_path,
            stock,
            hashlib.sha256(stock.read_bytes()).hexdigest(),
            ["/main"],
            [],
            ["/lib"],
        )

    return tmp_path, inspect


def test_versioned_closure_and_repeat(fixture):
    _, inspect = fixture
    result = inspect()
    assert result["status"] == "PASS", result["diagnostics"]
    assert result["objects"]["/main"]["versions"]["libanswer.so"] == ["TEST_1"]
    assert json.dumps(result, sort_keys=True) == json.dumps(inspect(), sort_keys=True)


def test_missing_dependency(fixture):
    root, inspect = fixture
    (root / "lib/libanswer.so").unlink()
    assert "missing libanswer.so" in inspect()["diagnostics"][0]


def test_wrong_abi(fixture):
    root, inspect = fixture
    shutil.copy("/bin/true", root / "lib/libanswer.so")
    result = inspect()
    assert result["status"] == "FAIL"
    assert "incompatible" in result["diagnostics"][0]


def test_absent_private_inputs(tmp_path):
    result = arm_abi.qualify(
        tmp_path, tmp_path / "absent", "0" * 64, ["/main"], [], ["/lib"]
    )
    assert result["status"] == "NOT TESTED"


def test_guest_absolute_link_stays_inside(tmp_path):
    (tmp_path / "lib").symlink_to("/private/lib")
    assert (
        arm_abi.inside(tmp_path, "/lib/provider") == tmp_path / "private/lib/provider"
    )


def test_provider_version_mismatch(fixture):
    root, inspect = fixture
    original = root / "lib/libanswer.so"
    original.write_bytes(original.read_bytes().replace(b"TEST_1", b"TEST_2"))
    result = inspect()
    assert result["status"] == "FAIL"
    assert "lacks ['TEST_1']" in result["diagnostics"][0]


def test_missing_private_candidate(fixture):
    root, inspect = fixture
    (root / "main").unlink()
    assert inspect()["status"] == "NOT TESTED"
