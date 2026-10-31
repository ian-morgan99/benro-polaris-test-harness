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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, request, send_from_directory

from .scenarios import ASTRO_SCENARIOS, get_scenario, validate_scenario_result
from .compatibility import generate_compatibility_report


# Asset directory
ASTRO_ASSETS_DIR = Path(__file__).parent.parent.parent.parent / "tests" / "fixtures" / "astro"

# Asset provenance manifest
MANIFEST_PATH = ASTRO_ASSETS_DIR / "astro_assets.json"

# Load asset manifest
with open(MANIFEST_PATH) as f:
    ASSET_MANIFEST = json.load(f)

# Compute asset hashes at load time
ASSET_HASHES: Dict[str, Optional[str]] = {}
for name, info in ASSET_MANIFEST.get("assets", {}).items():
    asset_path = ASTRO_ASSETS_DIR / name
    if asset_path.exists():
        sha256 = hashlib.sha256(asset_path.read_bytes())  # type: ignore[arg-type]
        ASSET_HASHES[name] = sha256.hexdigest()
    else:
        ASSET_HASHES[name] = None  # type: ignore[assignment]


def create_app() -> Flask:
    """Create the Astro WebUI Flask application.

    Returns:
        Configured Flask app instance
    """
    app = Flask(__name__, static_folder=str(ASTRO_ASSETS_DIR))

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
    }

    # ------------------------------------------------------------------
    # Static asset routes
    # ------------------------------------------------------------------

    @app.route("/")
    def index():
        """Serve the Astro WebUI SPA."""
        return send_from_directory(ASTRO_ASSETS_DIR, "index.html")

    @app.route("/<path:filename>")
    def static_assets(filename):
        """Serve static assets (JS, CSS, etc.) with hash verification."""
        if filename not in ASSET_MANIFEST.get("assets", {}):
            return jsonify({"error": f"Unknown asset: {filename}"}), 404

        expected_hash = ASSET_MANIFEST["assets"][filename]["sha256"]
        actual_hash = ASSET_HASHES.get(filename)

        if actual_hash != expected_hash:
            return jsonify({
                "error": f"Asset hash mismatch for {filename}",
                "expected": expected_hash,
                "actual": actual_hash,
            }), 500

        return send_from_directory(ASTRO_ASSETS_DIR, filename)

    @app.route("/api/astro/manifest")
    def asset_manifest():
        """Return the asset provenance manifest."""
        return jsonify(ASSET_MANIFEST)

    # ------------------------------------------------------------------
    # Synthetic Astro API routes
    # ------------------------------------------------------------------

    @app.route("/api/astro/state", methods=["GET"])
    def astro_state():
        """Return current synthetic camera/mount state."""
        return jsonify({
            "session_id": app.config["ASTRO_STATE"]["session_id"],
            "mount_status": app.config["ASTRO_STATE"]["mount_status"],
            "camera_ready": app.config["ASTRO_STATE"]["camera_ready"],
            "tracking": app.config["ASTRO_STATE"]["tracking"],
            "rate_control": app.config["ASTRO_STATE"]["rate_control"],
            "capture_count": app.config["ASTRO_STATE"]["capture_count"],
            "solve_count": app.config["ASTRO_STATE"]["solve_count"],
            "goto_count": app.config["ASTRO_STATE"]["goto_count"],
            "last_error": app.config["ASTRO_STATE"]["last_error"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    @app.route("/api/astro/capture", methods=["POST"])
    def astro_capture():
        """Synthetic capture action (dry-run only)."""
        data = request.get_json(silent=True) or {}
        exposure_ms = data.get("exposure_ms", 1000)
        iso = data.get("iso", 800)

        # Validate inputs
        if exposure_ms <= 0:
            return jsonify({
                "ok": False,
                "error": "exposure_ms must be positive",
                "action": "capture",
            }), 400

        if not app.config["ASTRO_STATE"]["camera_ready"]:
            return jsonify({
                "ok": False,
                "error": "camera not ready",
                "action": "capture",
                "mount_status": app.config["ASTRO_STATE"]["mount_status"],
            }), 409

        # Dry-run capture
        app.config["ASTRO_STATE"]["capture_count"] += 1
        app.config["ASTRO_STATE"]["mount_status"] = "capturing"
        app.config["ASTRO_STATE"]["last_error"] = None

        return jsonify({
            "ok": True,
            "action": "capture",
            "exposure_ms": exposure_ms,
            "iso": iso,
            "capture_id": f"cap_{app.config["ASTRO_STATE"]['capture_count']:04d}",
            "mount_status": app.config["ASTRO_STATE"]["mount_status"],
            "dry_run": True,
        })

    @app.route("/api/astro/solve", methods=["POST"])
    def astro_solve():
        """Synthetic platesolve action (dry-run only)."""
        data = request.get_json(silent=True) or {}
        image_id = data.get("image_id", "unknown")

        # Simulate solve result based on image_id
        if image_id == "reject" or image_id == "ambiguous":
            app.config["ASTRO_STATE"]["last_error"] = f"solve rejected for image: {image_id}"
            return jsonify({
                "ok": False,
                "action": "solve",
                "image_id": image_id,
                "error": f"solve rejected: insufficient stars in {image_id}",
                "mount_actions": 0,
                "exif_actions": 0,
                "dry_run": True,
            }), 200

        app.config["ASTRO_STATE"]["solve_count"] += 1
        app.config["ASTRO_STATE"]["mount_status"] = "solved"

        return jsonify({
            "ok": True,
            "action": "solve",
            "image_id": image_id,
            "ra": 0.0,
            "dec": 0.0,
            "confidence": 0.95,
            "mount_actions": 0,
            "exif_actions": 0,
            "dry_run": True,
        })

    @app.route("/api/astro/goto", methods=["POST"])
    def astro_goto():
        """Synthetic GOTO action (dry-run only)."""
        data = request.get_json(silent=True) or {}
        ra = data.get("ra", 0.0)
        dec = data.get("dec", 0.0)

        if not app.config["ASTRO_STATE"]["camera_ready"]:
            return jsonify({
                "ok": False,
                "error": "camera not ready",
                "action": "goto",
            }), 409

        app.config["ASTRO_STATE"]["goto_count"] += 1
        app.config["ASTRO_STATE"]["mount_status"] = "slewing"

        return jsonify({
            "ok": True,
            "action": "goto",
            "ra": ra,
            "dec": dec,
            "mount_status": app.config["ASTRO_STATE"]["mount_status"],
            "dry_run": True,
        })

    @app.route("/api/astro/tracking", methods=["POST"])
    def astro_tracking():
        """Synthetic tracking toggle (dry-run only)."""
        data = request.get_json(silent=True) or {}
        enable = data.get("enable", True)

        app.config["ASTRO_STATE"]["tracking"] = enable
        app.config["ASTRO_STATE"]["mount_status"] = "tracking" if enable else "idle"

        return jsonify({
            "ok": True,
            "action": "tracking",
            "enable": enable,
            "mount_status": app.config["ASTRO_STATE"]["mount_status"],
            "dry_run": True,
        })

    @app.route("/api/astro/exif", methods=["POST"])
    def astro_exif():
        """Synthetic EXIF opt-in (dry-run only)."""
        data = request.get_json(silent=True) or {}
        enable = data.get("enable", True)

        return jsonify({
            "ok": True,
            "action": "exif",
            "enable": enable,
            "dry_run": True,
        })

    @app.route("/api/astro/rate-control", methods=["POST"])
    def astro_rate_control():
        """Rate control — explicitly unavailable unless separately qualified."""
        return jsonify({
            "ok": False,
            "action": "rate_control",
            "error": "rate control unavailable",
            "reason": "no separately qualified contract supplied",
            "dry_run": True,
        }), 403

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

        return jsonify({
            "ok": True,
            "action": "reset",
            "session_id": app.config["ASTRO_STATE"]["session_id"],
            "dry_run": True,
        })

    # ------------------------------------------------------------------
    # Compatibility report
    # ------------------------------------------------------------------

    @app.route("/api/astro/compatibility")
    def astro_compatibility():
        """Return route/API compatibility report."""
        return jsonify(generate_compatibility_report(app))

    return app


def run_server(host: str = "127.0.0.1", port: int = 0, profile: str = "synthetic") -> int:
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
    print(f"Assets: {ASTRO_ASSETS_DIR}")
    print(f"Asset hashes verified: {all(h is not None for h in ASSET_HASHES.values())}")

    app.run(host=host, port=port, debug=False)
    return 0
