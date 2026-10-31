"""
Route/API compatibility report for Astro WebUI (issue #20).

Compares the served API routes against the documented synthetic Astro API
surface and reports missing or extra endpoints.
"""

from typing import Any, Dict, List

# Documented synthetic Astro API surface
DOCUMENTED_ROUTES: Dict[str, List[str]] = {
    "GET /api/astro/state": ["Read current camera/mount state"],
    "POST /api/astro/capture": ["Start a synthetic capture (dry-run)"],
    "POST /api/astro/solve": ["Run synthetic platesolve (dry-run)"],
    "POST /api/astro/goto": ["Slew to RA/Dec target (dry-run)"],
    "POST /api/astro/tracking": ["Toggle tracking (dry-run)"],
    "POST /api/astro/exif": ["Opt in/out of EXIF (dry-run)"],
    "POST /api/astro/rate-control": ["Rate control — always unavailable (dry-run)"],
    "POST /api/astro/reset": ["Reset synthetic state (test helper)"],
    "GET /api/astro/manifest": ["Return asset provenance manifest"],
    "GET /api/astro/compatibility": ["Return route compatibility report"],
}


def generate_compatibility_report(app) -> Dict[str, Any]:
    """Generate a compatibility report comparing served routes to documented API.

    Args:
        app: Flask application instance

    Returns:
        Compatibility report dict
    """
    served_routes: Dict[str, List[str]] = {}

    for rule in app.url_map.iter_rules():
        methods = sorted(rule.methods - {"HEAD", "OPTIONS"})
        for method in methods:
            key = f"{method} {rule.rule}"
            served_routes[key] = []

    documented_set = set(DOCUMENTED_ROUTES.keys())
    served_set = set(served_routes.keys())

    missing = documented_set - served_set
    extra = served_set - documented_set
    matching = documented_set & served_set

    return {
        "documented_endpoints": len(documented_set),
        "served_endpoints": len(served_set),
        "matching": len(matching),
        "missing": sorted(missing),
        "extra": sorted(extra),
        "all_documented_served": len(missing) == 0,
        "documented_routes": DOCUMENTED_ROUTES,
        "served_routes": served_routes,
    }
