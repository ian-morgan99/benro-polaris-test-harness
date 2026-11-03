"""Packaging tests: the built wheel must be usable, not just buildable.

The first wheel this repo produced installed cleanly and then died on
`polaris-harness --help`, because three modules resolved runtime data against
the repository root - which does not exist in a site-packages install. These
tests pin that behaviour so a future refactor cannot reintroduce it silently.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestRuntimeDataResolvesInsidePackage:
    """Data must be reachable without a repository checkout."""

    def test_schemas_dir_is_packaged_or_repo(self):
        from polaris_harness.validation import _schemas_dir

        d = _schemas_dir()
        assert d.is_dir(), f"schema directory missing: {d}"
        # In an installed wheel this is site-packages/polaris_harness/_data.
        assert (d / "scenario.schema.json").exists()

    def test_schemas_dir_is_not_the_repo_root_in_a_wheel_install(self, tmp_path):
        """The packaged copy wins over the development fallback."""
        from polaris_harness import validation

        packaged = Path(validation.__file__).parent / "_data" / "schemas"
        if not packaged.is_dir():
            pytest.skip("running from a source checkout, not an installed wheel")
        assert validation._schemas_dir() == packaged

    def test_missing_schemas_raises_instead_of_validating_against_nothing(
        self, monkeypatch, tmp_path
    ):
        """Fail closed: no schemas must not mean 'everything is valid'."""
        from polaris_harness import validation

        monkeypatch.setattr(validation, "_PACKAGED_DATA", tmp_path / "absent")
        monkeypatch.setattr(
            validation.Path, "is_dir",
            lambda self: str(self).endswith("__never__"),
        )
        with pytest.raises(FileNotFoundError):
            validation._schemas_dir()

    def test_astro_assets_resolve_without_repo(self):
        from polaris_harness.astro_webui.server import assets_dir

        d = assets_dir()
        assert d.is_dir(), f"astro asset directory missing: {d}"
        assert (d / "astro_assets.json").exists()

    def test_astro_manifest_is_not_read_at_import_time(self):
        """Importing the server must not touch disk.

        `polaris-harness --help` imports cli -> astro_webui -> server. An
        import-time open() made every CLI invocation depend on Astro assets.
        """
        import ast

        source = (
            REPO_ROOT / "src" / "polaris_harness" / "astro_webui" / "server.py"
        ).read_text()
        tree = ast.parse(source)
        module_level_opens = [
            node for node in tree.body
            if isinstance(node, (ast.With, ast.Assign))
            and "open(" in ast.unparse(node)
        ]
        assert not module_level_opens, (
            "astro_webui/server.py opens files at module scope; this makes "
            "`polaris-harness --help` fail on an installed wheel"
        )


class TestWheelContents:
    """The built artifact must carry the data the code reads."""

    @pytest.fixture(scope="class")
    def wheel(self, tmp_path_factory):
        dist = REPO_ROOT / "dist"
        wheels = sorted(dist.glob("polaris_harness-*.whl"))
        if not wheels:
            pytest.skip("no wheel built; run `uv build --out-dir dist`")
        return wheels[-1]

    def test_wheel_ships_schemas(self, wheel):
        import zipfile

        with zipfile.ZipFile(wheel) as z:
            names = z.namelist()
        for schema in ("scenario", "evidence", "personality",
                       "runtime-manifest", "package-manifest"):
            entry = f"polaris_harness/_data/schemas/{schema}.schema.json"
            assert entry in names, f"{entry} missing from wheel"

    def test_wheel_ships_astro_assets(self, wheel):
        import zipfile

        with zipfile.ZipFile(wheel) as z:
            names = z.namelist()
        assert "polaris_harness/_data/astro_assets/astro_assets.json" in names

    def test_wheel_ships_protocol_contracts(self, wheel):
        import zipfile

        with zipfile.ZipFile(wheel) as z:
            names = z.namelist()
        assert (
            "polaris_harness/_data/contracts/protocol/benro-9090-v1.json"
            in names
        )

    def test_wheel_excludes_private_arm_fixtures(self, wheel):
        """Proprietary firmware regions must never be packaged.

        They are gitignored and reach the harness via --fixtures-dir or
        POLARIS_ARM_FIXTURES_DIR. Shipping them would publish Benro firmware.
        """
        import zipfile

        with zipfile.ZipFile(wheel) as z:
            names = z.namelist()
        offenders = [n for n in names if n.endswith(".bin")]
        assert not offenders, f"binary fixtures packaged: {offenders}"


class TestInstalledWheelRuns:
    """End-to-end proof, skipped unless a wheel and uv are available."""

    @pytest.fixture(scope="class")
    def installed(self, tmp_path_factory):
        import shutil

        dist = REPO_ROOT / "dist"
        wheels = sorted(dist.glob("polaris_harness-*.whl"))
        if not wheels:
            pytest.skip("no wheel built; run `uv build --out-dir dist`")
        uv = shutil.which("uv")
        if not uv:
            pytest.skip("uv not available")

        venv = tmp_path_factory.mktemp("wheel-venv") / ".venv"
        subprocess.run([uv, "venv", str(venv)], check=True, capture_output=True)
        python = venv / "bin" / "python"
        subprocess.run(
            [uv, "pip", "install", "-q", "--python", str(python), str(wheels[-1])],
            check=True, capture_output=True,
        )
        return python

    def test_help_runs_from_outside_the_repo(self, installed, tmp_path):
        """The original defect: import-time asset open crashed the CLI."""
        result = subprocess.run(
            [str(installed), "-m", "polaris_harness.cli", "--help"],
            cwd=tmp_path, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_schemas_load_from_site_packages(self, installed, tmp_path):
        code = (
            "import json;"
            "from polaris_harness.validation import SchemaValidator, _schemas_dir;"
            "print(_schemas_dir());"
            "print(json.dumps(sorted(SchemaValidator()._validators)))"
        )
        result = subprocess.run(
            [str(installed), "-c", code],
            cwd=tmp_path, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        resolved = Path(result.stdout.splitlines()[0])
        assert "site-packages" in str(resolved), (
            f"installed wheel resolved schemas to {resolved}, not packaged data"
        )
        assert json.loads(result.stdout.splitlines()[1]) == [
            "evidence", "personality", "runtime_manifest", "scenario"
        ]

    def test_astro_app_serves_from_site_packages(self, installed, tmp_path):
        code = (
            "from polaris_harness.astro_webui import create_app;"
            "a=create_app().test_client();"
            "print(a.get('/').status_code, a.get('/api/astro/manifest').status_code)"
        )
        result = subprocess.run(
            [str(installed), "-c", code],
            cwd=tmp_path, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "200 200"
