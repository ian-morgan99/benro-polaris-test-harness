"""
Astro WebUI harness package for issue #20.

Provides a Flask-based server that serves synthetic Astro static assets
and implements a documented synthetic Astro API surface. All actions are
dry-run only — the harness never contacts or mutates a physical Polaris.
"""

from .server import create_app, run_server
from .scenarios import ASTRO_SCENARIOS, get_scenario, list_scenarios, validate_scenario_result
from .compatibility import generate_compatibility_report

__all__ = [
    "create_app",
    "run_server",
    "ASTRO_SCENARIOS",
    "get_scenario",
    "list_scenarios",
    "validate_scenario_result",
    "generate_compatibility_report",
]
