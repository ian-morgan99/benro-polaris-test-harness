"""
Astro WebUI harness server for issue #20.

Provides a Flask-based server that serves synthetic Astro static assets
and implements a documented synthetic Astro API surface. All actions are
dry-run only — the harness never contacts or mutates a physical Polaris.

This is an independent harness component and does not depend on the Polaris
protocol scenario engine or runtime verifier.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory

from .compatibility import generate_compatibility_report
from .scenarios import ASTRO_SCENARIOS, get_scenario

# Asset directory. Ships inside the wheel at polaris_harness/_data/astro_assets
# (see pyproject force-include); the repository layout is only a development
# fallback, because a site-packages install has no tests/ directory.
_PACKAGED_ASSETS = Path(__file__).parent.parent / "_data" / "astro_assets"
_REPO_ASSETS = (
    Path(__file__).parent.parent.parent.parent / "tests" / "fixtures" / "astro"
)


def assets_dir() -> Path:
    """Locate the Astro asset directory for this installation.

    Returns:
        Packaged asset directory, or the repository one in a source checkout

    Raises:
        FileNotFoundError: If neither exists. Previously this module opened the
            manifest at import time, so `import polaris_harness` - and therefore
            `polaris-harness --help` - died on an installed wheel even when the
            caller wanted nothing to do with the Astro server.
    """
    if _PACKAGED_ASSETS.is_dir():
        return _PACKAGED_ASSETS
    if _REPO_ASSETS.is_dir():
        return _REPO_ASSETS
    raise FileNotFoundError(
        "No Astro asset directory found. Expected packaged data at "
        f"{_PACKAGED_ASSETS} or a source checkout at {_REPO_ASSETS}."
    )


def load_asset_manifest() -> dict[str, Any]:
    """Read the asset provenance manifest.

    Returns:
        Parsed manifest

    Raises:
        FileNotFoundError: If the manifest is absent, rather than serving an
            empty asset set that would silently pass hash verification.
    """
    path = assets_dir() / "astro_assets.json"
    with open(path) as f:
        return json.load(f)


def compute_asset_hashes(manifest: dict[str, Any]) -> dict[str, str | None]:
    """Hash every asset named in the manifest.

    Args:
        manifest: Parsed asset provenance manifest

    Returns:
        Asset name to SHA-256, or None where the file is missing
    """
    base = assets_dir()
    hashes: dict[str, str | None] = {}
    for name in manifest.get("assets", {}):
        asset_path = base / name
        if asset_path.exists():
            hashes[name] = hashlib.sha256(asset_path.read_bytes()).hexdigest()
        else:
            hashes[name] = None
    return hashes


def create_app() -> Flask:
    """Create the Astro WebUI Flask application.

    Returns:
        Configured Flask app instance
    """
    asset_dir = assets_dir()
    manifest = load_asset_manifest()
    asset_hashes = compute_asset_hashes(manifest)

    app = Flask(__name__, static_folder=str(asset_dir))

    # In-memory state for synthetic sessions (stored on app.config to avoid
    # Flask attribute restrictions)
    app.config["ASTRO_STATE"] = {
        "mount_status": "idle",
        "camera_ready": False,
        "tracking": False,
        "rate_control": "unavailable",
        "session_id": str(uuid.uuid4()),
        "capture_count": 0,
        "solve_count": 0,
        "goto_count": 0,
        "last_error": None,
        "exif_enabled": False,
        "location": {
            "latitude": 0.0,
            "longitude": 0.0,
            "altitude_m": 0.0,
            "source": "synthetic",
        },
    }

    # ------------------------------------------------------------------
    # Static asset routes
    # ------------------------------------------------------------------

    @app.route("/")
    def index():
        """Serve the Astro WebUI SPA."""
        return send_from_directory(asset_dir, "index.html")

    @app.route("/<path:filename>")
    def static_assets(filename):
        """Serve static assets (JS, CSS, etc.) with hash verification."""
        if filename not in manifest.get("assets", {}):
            return jsonify({"error": f"Unknown asset: {filename}"}), 404

        expected_hash = manifest["assets"][filename]["sha256"]
        actual_hash = asset_hashes.get(filename)

        if actual_hash != expected_hash:
            return jsonify(
                {
                    "error": f"Asset hash mismatch for {filename}",
                    "expected": expected_hash,
                    "actual": actual_hash,
                }
            ), 500

        return send_from_directory(asset_dir, filename)

    @app.route("/api/astro/manifest")
    def asset_manifest():
        """Return the asset provenance manifest."""
        return jsonify(manifest)

    # ------------------------------------------------------------------
    # Synthetic Astro API routes
    # ------------------------------------------------------------------

    @app.route("/api/astro/location", methods=["GET"])
    def astro_location():
        """Return the bounded synthetic location used by the harness."""
        return jsonify(
            {
                "ok": True,
                "location": dict(app.config["ASTRO_STATE"]["location"]),
                "source": "synthetic",
                "dry_run": True,
            }
        )

    @app.route("/api/astro/state", methods=["GET"])
    def astro_state():
        """Return current synthetic camera/mount state."""
        return jsonify(
            {
                "session_id": app.config["ASTRO_STATE"]["session_id"],
                "mount_status": app.config["ASTRO_STATE"]["mount_status"],
                "camera_ready": app.config["ASTRO_STATE"]["camera_ready"],
                "tracking": app.config["ASTRO_STATE"]["tracking"],
                "rate_control": app.config["ASTRO_STATE"]["rate_control"],
                "capture_count": app.config["ASTRO_STATE"]["capture_count"],
                "solve_count": app.config["ASTRO_STATE"]["solve_count"],
                "goto_count": app.config["ASTRO_STATE"]["goto_count"],
                "last_error": app.config["ASTRO_STATE"]["last_error"],
                "exif_enabled": app.config["ASTRO_STATE"]["exif_enabled"],
                "location": dict(app.config["ASTRO_STATE"]["location"]),
                "source": "synthetic",
                "dry_run": True,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

    @app.route("/api/astro/camera-ready", methods=["POST"])
    def astro_camera_ready():
        """Set synthetic camera readiness for a disposable UI session."""
        data = request.get_json(silent=True) or {}
        ready = data.get("ready", True)
        if not isinstance(ready, bool):
            return jsonify(
                {
                    "ok": False,
                    "error": "ready must be boolean",
                    "action": "camera-ready",
                    "dry_run": True,
                }
            ), 400
        app.config["ASTRO_STATE"]["camera_ready"] = ready
        return jsonify(
            {
                "ok": True,
                "action": "camera-ready",
                "camera_ready": ready,
                "dry_run": True,
            }
        )

    @app.route("/api/astro/capture", methods=["POST"])
    def astro_capture():
        """Synthetic capture action (dry-run only)."""
        data = request.get_json(silent=True) or {}
        exposure_ms = data.get("exposure_ms", 1000)
        iso = data.get("iso", 800)

        # Validate inputs
        if exposure_ms <= 0:
            return jsonify(
                {
                    "ok": False,
                    "error": "exposure_ms must be positive",
                    "action": "capture",
                    "dry_run": True,
                }
            ), 400

        if not app.config["ASTRO_STATE"]["camera_ready"]:
            return jsonify(
                {
                    "ok": False,
                    "error": "camera not ready",
                    "action": "capture",
                    "mount_status": app.config["ASTRO_STATE"]["mount_status"],
                    "dry_run": True,
                }
            ), 409

        # Dry-run capture
        app.config["ASTRO_STATE"]["capture_count"] += 1
        app.config["ASTRO_STATE"]["mount_status"] = "capturing"
        app.config["ASTRO_STATE"]["last_error"] = None

        return jsonify(
            {
                "ok": True,
                "action": "capture",
                "exposure_ms": exposure_ms,
                "iso": iso,
                "capture_id": f"cap_{app.config['ASTRO_STATE']['capture_count']:04d}",
                "mount_status": app.config["ASTRO_STATE"]["mount_status"],
                "dry_run": True,
            }
        )

    @app.route("/api/astro/solve", methods=["POST"])
    def astro_solve():
        """Synthetic platesolve action (dry-run only)."""
        data = request.get_json(silent=True) or {}
        image_id = data.get("image_id", "unknown")

        # Simulate solve result based on image_id
        if image_id == "reject" or image_id == "ambiguous":
            app.config["ASTRO_STATE"]["last_error"] = (
                f"solve rejected for image: {image_id}"
            )
            return jsonify(
                {
                    "ok": False,
                    "action": "solve",
                    "image_id": image_id,
                    "error": f"solve rejected: insufficient stars in {image_id}",
                    "mount_actions": 0,
                    "exif_actions": 0,
                    "dry_run": True,
                }
            ), 200

        app.config["ASTRO_STATE"]["solve_count"] += 1
        app.config["ASTRO_STATE"]["mount_status"] = "solved"

        return jsonify(
            {
                "ok": True,
                "action": "solve",
                "image_id": image_id,
                "ra": 0.0,
                "dec": 0.0,
                "confidence": 0.95,
                "mount_actions": 0,
                "exif_actions": 0,
                "dry_run": True,
            }
        )

    @app.route("/api/astro/goto", methods=["POST"])
    def astro_goto():
        """Synthetic GOTO action (dry-run only)."""
        data = request.get_json(silent=True) or {}
        ra = data.get("ra", 0.0)
        dec = data.get("dec", 0.0)

        if not app.config["ASTRO_STATE"]["camera_ready"]:
            return jsonify(
                {
                    "ok": False,
                    "error": "camera not ready",
                    "action": "goto",
                    "dry_run": True,
                }
            ), 409

        app.config["ASTRO_STATE"]["goto_count"] += 1
        app.config["ASTRO_STATE"]["mount_status"] = "slewing"

        return jsonify(
            {
                "ok": True,
                "action": "goto",
                "ra": ra,
                "dec": dec,
                "mount_status": app.config["ASTRO_STATE"]["mount_status"],
                "dry_run": True,
            }
        )

    @app.route("/api/astro/tracking", methods=["POST"])
    def astro_tracking():
        """Synthetic tracking toggle (dry-run only)."""
        data = request.get_json(silent=True) or {}
        enable = data.get("enable", True)

        app.config["ASTRO_STATE"]["tracking"] = enable
        app.config["ASTRO_STATE"]["mount_status"] = "tracking" if enable else "idle"

        return jsonify(
            {
                "ok": True,
                "action": "tracking",
                "enable": enable,
                "mount_status": app.config["ASTRO_STATE"]["mount_status"],
                "dry_run": True,
            }
        )

    @app.route("/api/astro/exif", methods=["POST"])
    def astro_exif():
        """Synthetic EXIF opt-in (dry-run only)."""
        data = request.get_json(silent=True) or {}
        enable = data.get("enable", True)
        if not isinstance(enable, bool):
            return jsonify(
                {
                    "ok": False,
                    "error": "enable must be boolean",
                    "action": "exif",
                    "dry_run": True,
                }
            ), 400

        app.config["ASTRO_STATE"]["exif_enabled"] = enable
        return jsonify(
            {
                "ok": True,
                "action": "exif",
                "enable": enable,
                "dry_run": True,
            }
        )

    @app.route("/api/astro/rate-control", methods=["POST"])
    def astro_rate_control():
        """Rate control — explicitly unavailable unless separately qualified."""
        return jsonify(
            {
                "ok": False,
                "action": "rate_control",
                "error": "rate control unavailable",
                "reason": "no separately qualified contract supplied",
                "dry_run": True,
            }
        ), 403

    @app.route("/api/astro/reset", methods=["POST"])
    def astro_reset():
        """Reset synthetic state (test helper)."""
        app.config["ASTRO_STATE"]["mount_status"] = "idle"
        app.config["ASTRO_STATE"]["camera_ready"] = False
        app.config["ASTRO_STATE"]["tracking"] = False
        app.config["ASTRO_STATE"]["rate_control"] = "unavailable"
        app.config["ASTRO_STATE"]["session_id"] = str(uuid.uuid4())
        app.config["ASTRO_STATE"]["capture_count"] = 0
        app.config["ASTRO_STATE"]["solve_count"] = 0
        app.config["ASTRO_STATE"]["goto_count"] = 0
        app.config["ASTRO_STATE"]["last_error"] = None
        app.config["ASTRO_STATE"]["exif_enabled"] = False

        return jsonify(
            {
                "ok": True,
                "action": "reset",
                "session_id": app.config["ASTRO_STATE"]["session_id"],
                "dry_run": True,
            }
        )

    @app.route("/api/astro/scenarios", methods=["GET"])
    def astro_scenarios():
        """Return the schema-backed synthetic scenario catalogue."""
        return jsonify(
            {
                "source": "synthetic",
                "dry_run": True,
                "scenarios": list(ASTRO_SCENARIOS.values()),
            }
        )

    @app.route("/api/astro/scenarios/<scenario_id>", methods=["GET"])
    def astro_scenario(scenario_id: str):
        """Return one schema-backed synthetic scenario definition."""
        scenario = get_scenario(scenario_id)
        if scenario is None:
            return jsonify(
                {
                    "ok": False,
                    "error": f"unknown synthetic scenario: {scenario_id}",
                    "source": "synthetic",
                    "dry_run": True,
                }
            ), 404
        return jsonify(scenario)

    @app.route("/api/astro/diagnostics", methods=["GET"])
    def astro_diagnostics():
        """Return bounded, read-only synthetic diagnostics for the SPA."""
        compatibility = generate_compatibility_report(app)
        asset_hashes = compute_asset_hashes(load_asset_manifest())
        return jsonify(
            {
                "ok": True,
                "source": "synthetic",
                "dry_run": True,
                "session_id": app.config["ASTRO_STATE"]["session_id"],
                "state": {
                    "capture_count": app.config["ASTRO_STATE"]["capture_count"],
                    "solve_count": app.config["ASTRO_STATE"]["solve_count"],
                    "goto_count": app.config["ASTRO_STATE"]["goto_count"],
                    "exif_enabled": app.config["ASTRO_STATE"]["exif_enabled"],
                    "rate_control": app.config["ASTRO_STATE"]["rate_control"],
                },
                "assets": {
                    "manifest_present": all(
                        digest is not None for digest in asset_hashes.values()
                    ),
                    "hashes": asset_hashes,
                },
                "compatibility": {
                    "all_documented_served": compatibility["all_documented_served"],
                    "missing": compatibility["missing"],
                    "extra": compatibility["extra"],
                },
                "limitations": [
                    "synthetic only; no physical Polaris or camera contact",
                    "rate control unavailable until separately qualified",
                ],
            }
        )

    # ------------------------------------------------------------------
    # Compatibility report
    # ------------------------------------------------------------------

    @app.route("/api/astro/compatibility")
    def astro_compatibility():
        """Return route/API compatibility report."""
        return jsonify(generate_compatibility_report(app))

    return app


def run_server(
    host: str = "127.0.0.1", port: int = 0, profile: str = "synthetic"
) -> int:
    """Run the Astro WebUI server.

    Args:
        host: Host to bind to (default: 127.0.0.1)
        port: Port to bind to (default: 0 = ephemeral)
        profile: Profile name (default: synthetic)

    Returns:
        Exit code (0 = success)
    """
    if profile != "synthetic":
        print(f"✗ Unknown profile: {profile}. Only 'synthetic' is supported.")
        return 2

    app = create_app()
    print(f"Starting Astro WebUI server on {host}:{port}")
    print(f"Profile: {profile}")
    print(f"Assets: {assets_dir()}")
    print(
        f"Asset hashes verified: "
        f"{all(h is not None for h in compute_asset_hashes(load_asset_manifest()).values())}"
    )

    app.run(host=host, port=port, debug=False)
    return 0
