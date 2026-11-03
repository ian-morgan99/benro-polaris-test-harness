"""Deterministic preview-stream failure harness.

The preview endpoint is deliberately independent from the command endpoint.
It models transport and producer outcomes that a consumer must distinguish:
an absent listener, an accepted connection that closes, an accepted connection
that produces no bytes, an explicit empty media frame, invalid media, delayed
valid media, and repeated valid media.

This is a synthetic harness protocol.  It does not claim to reproduce the
undocumented wire format used by a particular Polaris firmware.  The preview
payload is carried in a four-byte big-endian length-prefixed envelope so that
"no bytes" and "zero-length frame" remain observable as different outcomes.
An adapter for a real preview endpoint must translate its observed framing into
these outcome classes rather than treating this envelope as camera evidence.
"""

from __future__ import annotations

import base64
import socket
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class PreviewMode(StrEnum):
    """Synthetic preview producer/listener behavior."""

    LISTENER_ABSENT = "listener_absent"
    ACCEPTED_CLOSED = "accepted_closed"
    ACCEPTED_NO_DATA = "accepted_no_data"
    EMPTY_FRAME = "empty_frame"
    INVALID_FRAME = "invalid_frame"
    DELAYED_JPEG = "delayed_jpeg"
    REPEATED_JPEG = "repeated_jpeg"


class PreviewOutcome(StrEnum):
    """Classification returned by :func:`read_preview_frame`."""

    LISTENER_UNAVAILABLE = "listener_unavailable"
    CONNECTION_CLOSED = "connection_closed"
    NO_DATA = "no_data"
    EMPTY_FRAME = "empty_frame"
    INVALID_FRAME = "invalid_frame"
    VALID_JPEG = "valid_jpeg"


@dataclass(frozen=True)
class PreviewRead:
    """One decoded synthetic preview response."""

    outcome: PreviewOutcome
    payload: bytes = b""


def synthetic_jpeg() -> bytes:
    """Return a tiny generated/fixture JPEG used by deterministic tests.

    The bytes are embedded as a base64 fixture so preview tests have no image
    library dependency and never need to copy a user's photograph.
    """

    encoded = (
        "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////"
        "////////////////////////2wBDAf//////////////////////////////"
        "////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAA"
        "AAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAx"
        "AAAAH/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAA"
        "AAAAAAAAAAAAAAAA/9oACAEDAQE/AYf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9o"
        "ACAECAQE/AYf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwD/xA"
        "AUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/IV//2gAMAwEAAgADAAAAEP/E"
        "ABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQMBAT8QH//EABQRAQAAAAAAAAAAAA"
        "AAAAAAABD/2gAIAQIBAT8QH//EABQQAQAAAAAAAAAAAAAAAAAAABD/2gAIAQEA"
        "AT8QH//Z"
    )
    return base64.b64decode(encoded)


def is_jpeg(payload: bytes) -> bool:
    """Perform the minimal envelope-level JPEG integrity check."""

    return (
        len(payload) >= 4 and payload[:2] == b"\xff\xd8" and payload[-2:] == b"\xff\xd9"
    )


def _read_exact(sock: socket.socket, length: int) -> bytes | None:
    """Read exactly ``length`` bytes, returning None on clean EOF."""

    data = bytearray()
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            return None
        data.extend(chunk)
    return bytes(data)


def read_preview_frame(sock: socket.socket) -> PreviewRead:
    """Read one synthetic length-prefixed preview frame from ``sock``.

    A socket timeout is classified as ``no_data``.  An EOF before the four-byte
    envelope is ``connection_closed``.  A zero length envelope is an explicit
    ``empty_frame`` and is therefore distinct from both cases.
    """

    try:
        header = _read_exact(sock, 4)
    except TimeoutError:
        return PreviewRead(PreviewOutcome.NO_DATA)
    if header is None:
        return PreviewRead(PreviewOutcome.CONNECTION_CLOSED)

    length = int.from_bytes(header, "big")
    if length == 0:
        return PreviewRead(PreviewOutcome.EMPTY_FRAME)

    try:
        payload = _read_exact(sock, length)
    except TimeoutError:
        return PreviewRead(PreviewOutcome.NO_DATA)
    if payload is None:
        return PreviewRead(PreviewOutcome.INVALID_FRAME)
    if is_jpeg(payload):
        return PreviewRead(PreviewOutcome.VALID_JPEG, payload)
    return PreviewRead(PreviewOutcome.INVALID_FRAME, payload)


class PreviewServer:
    """Small loopback preview producer used by contract tests.

    The server is intentionally local-only by default.  ``LISTENER_ABSENT``
    reserves and releases a port, so a client receives the normal connection
    refusal produced by an unbound listener rather than an application error.
    """

    def __init__(
        self,
        mode: PreviewMode | str,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        delay_ms: int = 50,
        repeat_count: int = 2,
        idle_timeout_ms: int = 250,
        payload: bytes | None = None,
    ) -> None:
        self.mode = PreviewMode(mode)
        self.host = host
        self._configured_port = port
        self._port = port
        self.delay_ms = delay_ms
        self.repeat_count = repeat_count
        self.idle_timeout_ms = idle_timeout_ms
        self.payload = payload if payload is not None else synthetic_jpeg()
        self._server_socket: socket.socket | None = None
        self._connections: set[socket.socket] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False
        self._ready = threading.Event()

    @classmethod
    def from_scenario(cls, scenario: dict[str, Any]) -> PreviewServer:
        """Build a server from a schema-valid scenario's preview endpoint."""

        endpoint = scenario.get("endpoints", {}).get("preview")
        if not isinstance(endpoint, dict):
            raise ValueError("scenario does not declare a preview endpoint")
        framing = endpoint.get("framing", {})
        if framing.get("type", "length_prefixed") != "length_prefixed":
            raise ValueError("synthetic preview requires length_prefixed framing")
        bind = endpoint.get("bind", {})
        return cls(
            endpoint.get("failure_mode", PreviewMode.DELAYED_JPEG),
            host=bind.get("host", "127.0.0.1"),
            port=bind.get("port", 0),
            delay_ms=endpoint.get("delay_ms", 50),
            repeat_count=endpoint.get("repeat_count", 2),
            idle_timeout_ms=endpoint.get("idle_timeout_ms", 250),
        )

    def start(self) -> int:
        """Start the listener and return its port, or reserve a free port."""

        if self._running:
            return self.port
        self._ready.clear()
        if self.mode == PreviewMode.LISTENER_ABSENT:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind((self.host, self._configured_port))
                self._port = probe.getsockname()[1]
            self._running = True
            self._ready.set()
            return self.port

        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server_socket.bind((self.host, self._configured_port or self._port))
            server_socket.listen(8)
        except OSError:
            server_socket.close()
            raise
        self._server_socket = server_socket
        self._port = server_socket.getsockname()[1]
        self._running = True
        self._thread = threading.Thread(
            target=self._accept_loop,
            name="polaris-preview-server",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait(timeout=1.0)
        return self.port

    def stop(self) -> None:
        """Stop the listener and close active connections."""

        self._running = False
        server_socket = self._server_socket
        self._server_socket = None
        if server_socket is not None:
            try:
                server_socket.close()
            except OSError:
                pass
        with self._lock:
            connections = list(self._connections)
            self._connections.clear()
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def restart(self) -> int:
        """Make the listener disappear, then bind it again on the same port."""

        if self.mode == PreviewMode.LISTENER_ABSENT:
            raise ValueError("an absent listener cannot be restarted")
        if self.port == 0:
            self.start()
        port = self.port
        self.stop()
        self._configured_port = port
        self._port = port
        return self.start()

    @property
    def port(self) -> int:
        """The configured or allocated TCP port."""

        return self._port

    def _accept_loop(self) -> None:
        self._ready.set()
        while self._running:
            server_socket = self._server_socket
            if server_socket is None:
                return
            try:
                server_socket.settimeout(0.1)
                connection, _address = server_socket.accept()
            except TimeoutError:
                continue
            except OSError:
                if self._running:
                    continue
                return
            with self._lock:
                self._connections.add(connection)
            worker = threading.Thread(
                target=self._serve_connection,
                args=(connection,),
                name="polaris-preview-connection",
                daemon=True,
            )
            worker.start()

    def _serve_connection(self, connection: socket.socket) -> None:
        try:
            if self.mode == PreviewMode.ACCEPTED_CLOSED:
                return
            if self.mode == PreviewMode.ACCEPTED_NO_DATA:
                deadline = time.monotonic() + self.idle_timeout_ms / 1000.0
                while self._running and time.monotonic() < deadline:
                    time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
                return
            if self.mode == PreviewMode.EMPTY_FRAME:
                self._send_frame(connection, b"")
                return
            if self.mode == PreviewMode.INVALID_FRAME:
                self._send_frame(connection, b"not-a-jpeg")
                return
            if self.mode == PreviewMode.DELAYED_JPEG:
                self._sleep_delay()
                self._send_frame(connection, self.payload)
                return
            if self.mode == PreviewMode.REPEATED_JPEG:
                for index in range(max(1, self.repeat_count)):
                    if index:
                        self._sleep_delay()
                    self._send_frame(connection, self.payload)
                return
            raise RuntimeError(f"unsupported preview mode: {self.mode}")
        except (BrokenPipeError, ConnectionResetError, OSError):
            # The consumer may intentionally abandon a delayed/no-data read.
            return
        finally:
            with self._lock:
                self._connections.discard(connection)
            try:
                connection.close()
            except OSError:
                pass

    def _sleep_delay(self) -> None:
        deadline = time.monotonic() + self.delay_ms / 1000.0
        while self._running and time.monotonic() < deadline:
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))

    @staticmethod
    def _send_frame(connection: socket.socket, payload: bytes) -> None:
        connection.sendall(len(payload).to_bytes(4, "big") + payload)
