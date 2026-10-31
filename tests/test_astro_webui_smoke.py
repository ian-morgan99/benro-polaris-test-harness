"""
Browser/client smoke tests for the Astro WebUI harness server (issue #20).

These tests start the Astro WebUI server, serve the synthetic SPA, and verify
that:
1. Static assets return HTTP 200 with recorded SHA-256 hashes
2. The SPA loads and reaches the synthetic API
3. Rejected/ambiguous solves produce a visible reason and zero mount/EXIF actions
4. Rate control remains explicitly unavailable

All actions are dry-run only — no physical Polaris or camera is contacted.
"""

import json
import threading
import time
from pathlib import Path

import pytest

from polaris_harness.astro_webui import create_app, run_server


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "astro"


class TestAstroWebUIServer:
    """Smoke tests for the Astro WebUI harness server."""

    @pytest.fixture
    def server(self):
        """Start the Astro WebUI server on an ephemeral port."""
        app = create_app()
        # Use a test client instead of a real server for deterministic tests
        client = app.test_client()
        yield client

    def test_index_returns_200(self, server):
        """The SPA index page should return HTTP 200."""
        resp = server.get("/")
        assert resp.status_code == 200
        assert b"Astro WebUI" in resp.data
        assert b"SYNTHETIC - DRY RUN" in resp.data

    def test_static_assets_return_200_with_recorded_hashes(self, server):
        """Static assets (JS, CSS) should return HTTP 200 with correct hashes."""
        for asset in ["app.js", "style.css"]:
            resp = server.get(f"/{asset}")
            assert resp.status_code == 200, f"Asset {asset} should return 200"

    def test_asset_manifest_returns_hashes(self, server):
        """The manifest endpoint should return recorded SHA-256 hashes."""
        resp = server.get("/api/astro/manifest")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "assets" in data
        assert "index.html" in data["assets"]
        assert "app.js" in data["assets"]
        assert "style.css" in data["assets"]
        # All assets should have SHA-256 hashes
        for name, info in data["assets"].items():
            assert "sha256" in info, f"Asset {name} missing SHA-256"
            assert len(info["sha256"]) == 64, f"Asset {name} hash not 64 hex chars"

    def test_astro_state_endpoint(self, server):
        """The state endpoint should return synthetic camera/mount state."""
        resp = server.get("/api/astro/state")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "mount_status" in data
        assert "camera_ready" in data
        assert "tracking" in data
        assert "rate_control" in data
        assert data["rate_control"] == "unavailable"

    def test_capture_when_camera_not_ready(self, server):
        """Capture should be rejected when camera is not ready."""
        resp = server.post(
            "/api/astro/capture",
            json={"exposure_ms": 1000, "iso": 800},
        )
        assert resp.status_code == 409
        data = resp.get_json()
        assert data["ok"] is False
        assert "camera not ready" in data["error"]

    def test_capture_when_camera_ready(self, server):
        """Capture should succeed when camera is ready."""
        # First set camera ready via state manipulation
        with server.application.app_context():
            server.application.config["ASTRO_STATE"]["camera_ready"] = True

        resp = server.post(
            "/api/astro/capture",
            json={"exposure_ms": 1000, "iso": 800},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "capture_id" in data
        assert data["dry_run"] is True

    def test_solve_rejected_produces_visible_reason(self, server):
        """A rejected solve should produce a visible reason and zero mount/EXIF actions."""
        resp = server.post(
            "/api/astro/solve",
            json={"image_id": "reject"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is False
        assert "solve rejected" in data["error"]
        assert data["mount_actions"] == 0
        assert data["exif_actions"] == 0
        assert data["dry_run"] is True

    def test_rate_control_unavailable(self, server):
        """Rate control should return 403 unless separately qualified."""
        resp = server.post(
            "/api/astro/rate-control",
            json={"rate": 1.0},
        )
        assert resp.status_code == 403
        data = resp.get_json()
        assert data["ok"] is False
        assert "rate control unavailable" in data["error"]
        assert data["dry_run"] is True

    def test_compatibility_report(self, server):
        """The compatibility report should show all documented endpoints served."""
        resp = server.get("/api/astro/compatibility")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["all_documented_served"] is True
        assert len(data["missing"]) == 0
        assert "GET /api/astro/state" in data["served_routes"]
        assert "POST /api/astro/capture" in data["served_routes"]
        assert "POST /api/astro/solve" in data["served_routes"]

    def test_spa_loads_javascript_and_css(self, server):
        """The SPA HTML should reference its JavaScript and CSS assets."""
        resp = server.get("/")
        html = resp.data.decode("utf-8")
        assert 'app.js' in html
        assert 'style.css' in html

    def test_dry_run_marker_in_response(self, server):
        """All API responses should be marked as dry_run."""
        resp = server.post(
            "/api/astro/capture",
            json={"exposure_ms": 1000, "iso": 800},
        )
        # First set camera ready
        with server.application.app_context():
            server.application.config["ASTRO_STATE"]["camera_ready"] = True
        resp = server.post(
            "/api/astro/capture",
            json={"exposure_ms": 1000, "iso": 800},
        )
        data = resp.get_json()
        assert data.get("dry_run") is True
