"""External TCP adapter for the pure deterministic scenario engine."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import yaml

from .engine import ScenarioEngine
from .framing import FramingEngine, FramingType
from .transport import TCPServer, TransportError
from .validation import validate_scenario


def scenario_manifest_path(path: str | Path) -> Path:
    """Resolve a scenario directory or manifest path."""
    candidate = Path(path)
    if candidate.is_dir():
        candidate = candidate / "scenario.yaml"
    if not candidate.is_file():
        raise FileNotFoundError(f"scenario manifest not found: {candidate}")
    return candidate


def load_scenario(path: str | Path) -> dict[str, Any]:
    """Load and schema-validate a scenario manifest."""
    manifest_path = scenario_manifest_path(path)
    try:
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid scenario YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("scenario manifest must contain an object")
    errors = validate_scenario(data)
    if errors:
        raise ValueError("; ".join(errors))
    return data


def _framing_factory(scenario: dict[str, Any]):
    endpoint = scenario.get("endpoints", {}).get("command", {})
    framing = endpoint.get("framing", {})
    framing_type = FramingType(framing.get("type", "delimiter"))
    limits = scenario.get("limits", {})
    max_frame_bytes = limits.get("max_frame_bytes", 65536)

    def factory() -> FramingEngine:
        kwargs = {"max_frame_bytes": max_frame_bytes}
        if framing_type == FramingType.DELIMITER:
            kwargs["delimiter"] = framing.get("delimiter_text", "#")
        elif framing_type == FramingType.FIXED_LENGTH:
            kwargs["frame_length"] = framing.get("frame_length", max_frame_bytes)
        return FramingEngine(framing_type, **kwargs)

    return factory


class ScenarioService:
    """Serve one schema-validated scenario over a loopback TCP endpoint."""

    def __init__(
        self,
        scenario: dict[str, Any],
        host: str | None = None,
        port: int | None = None,
    ):
        endpoint = scenario.get("endpoints", {}).get("command", {})
        bind = endpoint.get("bind", {})
        self.scenario = scenario
        self.engine = ScenarioEngine(scenario["id"], scenario)
        self.server = TCPServer(
            host or bind.get("host", "127.0.0.1"),
            port if port is not None else bind.get("port", 0),
        )
        self.server.set_framing_engine_factory(_framing_factory(scenario))
        self.server.add_event_handler("frame_received", self._on_frame)
        self._timers: list[threading.Timer] = []

    def start(self) -> int:
        """Start the service and return its bound port."""
        if not self.server.start():
            raise TransportError("scenario service failed to start")
        return self.server.get_port()

    def stop(self) -> None:
        """Stop the service and cancel pending response timers."""
        for timer in self._timers:
            timer.cancel()
        self._timers.clear()
        self.server.stop()

    def _on_frame(self, event: dict[str, Any]) -> None:
        connection_id = event["connection_id"]
        frame = event["frame"]
        actions = self.engine.execute_scenario(frame, connection_id)
        self.engine.execute_actions(actions)
        for action in actions:
            if action.action_type != "send":
                continue
            payload = action.parameters.get("text", "").encode("utf-8")
            if action.after_ms <= 0:
                self._send(connection_id, payload)
            else:
                timer = threading.Timer(
                    action.after_ms / 1000.0,
                    self._send,
                    args=(connection_id, payload),
                )
                timer.daemon = True
                timer.start()
                self._timers.append(timer)

    def _send(self, connection_id: str, payload: bytes) -> None:
        try:
            self.server.send_to_connection(connection_id, payload)
        except TransportError:
            # A disconnected consumer is a normal end-of-session condition;
            # the transport has already recorded the connection failure.
            return

    @property
    def port(self) -> int:
        return self.server.get_port()
