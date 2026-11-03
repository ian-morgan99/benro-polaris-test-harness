"""Synthetic preview producer/listener contract tests."""

import socket

import pytest

from polaris_harness.preview import (
    PreviewMode,
    PreviewOutcome,
    PreviewServer,
    is_jpeg,
    read_preview_frame,
    synthetic_jpeg,
)


def _connect(server: PreviewServer) -> socket.socket:
    return socket.create_connection((server.host, server.port), timeout=0.5)


def test_synthetic_fixture_is_a_jpeg() -> None:
    payload = synthetic_jpeg()

    assert is_jpeg(payload)
    assert payload[:2] == b"\xff\xd8"
    assert payload[-2:] == b"\xff\xd9"


def test_listener_absence_is_connection_refused() -> None:
    server = PreviewServer(PreviewMode.LISTENER_ABSENT)
    server.start()
    try:
        with pytest.raises(OSError):
            _connect(server)
    finally:
        server.stop()


def test_accepted_close_is_distinct_from_listener_absence() -> None:
    server = PreviewServer(PreviewMode.ACCEPTED_CLOSED)
    server.start()
    try:
        with _connect(server) as client:
            assert (
                read_preview_frame(client).outcome == PreviewOutcome.CONNECTION_CLOSED
            )
    finally:
        server.stop()


def test_accepted_no_data_is_distinct_from_immediate_close() -> None:
    server = PreviewServer(PreviewMode.ACCEPTED_NO_DATA, idle_timeout_ms=150)
    server.start()
    try:
        with _connect(server) as client:
            client.settimeout(0.03)
            assert read_preview_frame(client).outcome == PreviewOutcome.NO_DATA
    finally:
        server.stop()


def test_zero_length_frame_is_not_no_data() -> None:
    server = PreviewServer(PreviewMode.EMPTY_FRAME)
    server.start()
    try:
        with _connect(server) as client:
            assert read_preview_frame(client).outcome == PreviewOutcome.EMPTY_FRAME
    finally:
        server.stop()


def test_invalid_media_is_not_admitted_as_jpeg() -> None:
    server = PreviewServer(PreviewMode.INVALID_FRAME)
    server.start()
    try:
        with _connect(server) as client:
            result = read_preview_frame(client)
            assert result.outcome == PreviewOutcome.INVALID_FRAME
            assert result.payload == b"not-a-jpeg"
    finally:
        server.stop()


def test_delayed_valid_jpeg_is_bounded_and_admitted() -> None:
    server = PreviewServer(PreviewMode.DELAYED_JPEG, delay_ms=25)
    server.start()
    try:
        with _connect(server) as client:
            client.settimeout(0.5)
            result = read_preview_frame(client)
            assert result.outcome == PreviewOutcome.VALID_JPEG
            assert result.payload == synthetic_jpeg()
    finally:
        server.stop()


def test_repeated_valid_jpegs_remain_separate_frames() -> None:
    server = PreviewServer(PreviewMode.REPEATED_JPEG, repeat_count=2)
    server.start()
    try:
        with _connect(server) as client:
            first = read_preview_frame(client)
            second = read_preview_frame(client)
            assert first.outcome == PreviewOutcome.VALID_JPEG
            assert second.outcome == PreviewOutcome.VALID_JPEG
            assert first.payload == second.payload == synthetic_jpeg()
    finally:
        server.stop()


def test_listener_can_restart_on_the_same_port() -> None:
    server = PreviewServer(PreviewMode.DELAYED_JPEG, delay_ms=0)
    server.start()
    port = server.port
    try:
        server.restart()
        assert server.port == port
        with _connect(server) as client:
            assert read_preview_frame(client).outcome == PreviewOutcome.VALID_JPEG
    finally:
        server.stop()


def test_server_can_be_built_from_preview_scenario() -> None:
    scenario = {
        "endpoints": {
            "preview": {
                "transport": "tcp",
                "framing": {"type": "length_prefixed"},
                "bind": {"host": "127.0.0.1", "port": 0},
                "failure_mode": "empty_frame",
            }
        }
    }

    server = PreviewServer.from_scenario(scenario)
    server.start()
    try:
        with _connect(server) as client:
            assert read_preview_frame(client).outcome == PreviewOutcome.EMPTY_FRAME
    finally:
        server.stop()
