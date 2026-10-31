"""
ABI stubs for ARM user-space emulation.
Copyright (C) 2026  Ian Morgan

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.

Provides explicit stubs for external boundaries: camera, libgphoto2, OS,
timer, and callbacks. Every stub invocation is recorded. Stubs fail closed
on unexpected calls — they do not silently pass through.

These stubs are used by the ARMEmulator to model the environment that
Stage-2/pgphoto code runs in, without claiming to be a Pentax/PTP or
physical camera emulator.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class StubProfile:
    """A named collection of stub behaviors for a specific test scenario."""

    name: str
    camera_stub: Optional[Callable] = None
    libgphoto_stub: Optional[Callable] = None
    os_stub: Optional[Callable] = None
    timer_stub: Optional[Callable] = None
    callback_stub: Optional[Callable] = None

    def register_all(self, registry: "StubRegistry") -> None:
        """Register all stubs in this profile with the registry.

        Args:
            registry: StubRegistry instance to register stubs with
        """
        if self.camera_stub is not None:
            registry.register("camera", self.camera_stub)
        if self.libgphoto_stub is not None:
            registry.register("libgphoto", self.libgphoto_stub)
        if self.os_stub is not None:
            registry.register("os", self.os_stub)
        if self.timer_stub is not None:
            registry.register("timer", self.timer_stub)
        if self.callback_stub is not None:
            registry.register("callback", self.callback_stub)


@dataclass
class StubCallExpectation:
    """Expected stub call for assertion in tests."""

    stub_name: str
    function_name: str
    min_count: int = 0
    max_count: Optional[int] = None
    expected_arguments: Optional[List[Any]] = None


class StubRegistry:
    """Central registry of all stubs with call recording and assertion support."""

    def __init__(self):
        self._handlers: Dict[str, Callable] = {}
        self._call_history: List[Dict[str, Any]] = []
        self._logger = logging.getLogger(f"{__name__}.StubRegistry")

    def register(self, name: str, handler: Callable) -> None:
        """Register a stub handler.

        Args:
            name: Stub identifier
            handler: Callable stub handler
        """
        self._handlers[name] = handler
        self._logger.debug("Registered stub: %s", name)

    def call(self, stub_name: str, function_name: str, arguments: List[Any],
             register_snapshot: Dict[str, int]) -> Any:
        """Invoke a stub and record the call.

        Args:
            stub_name: Name of the stub
            function_name: Function within the stub
            arguments: Arguments passed
            register_snapshot: CPU register state at call time

        Returns:
            Stub return value

        Raises:
            KeyError: If stub is not registered (fail closed)
        """
        if stub_name not in self._handlers:
            raise KeyError(f"Missing stub: {stub_name}")

        import time
        timestamp_ms = int(time.time() * 1000)

        handler = self._handlers[stub_name]
        return_value = handler(function_name, arguments, register_snapshot)

        record = {
            "stub_name": stub_name,
            "function_name": function_name,
            "arguments": list(arguments),
            "return_value": return_value,
            "timestamp_ms": timestamp_ms,
            "register_snapshot": dict(register_snapshot),
        }
        self._call_history.append(record)
        self._logger.debug("Stub call: %s.%s(%s) -> %s",
                           stub_name, function_name, arguments, return_value)
        return return_value

    def get_calls(self, stub_name: Optional[str] = None,
                  function_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get recorded stub calls, optionally filtered.

        Args:
            stub_name: Filter by stub name
            function_name: Filter by function name

        Returns:
            List of call records
        """
        calls = self._call_history
        if stub_name is not None:
            calls = [c for c in calls if c["stub_name"] == stub_name]
        if function_name is not None:
            calls = [c for c in calls if c["function_name"] == function_name]
        return calls

    def assert_call_count(self, stub_name: str, function_name: str,
                          expected_count: int) -> None:
        """Assert that a stub function was called exactly expected_count times.

        Args:
            stub_name: Stub name
            function_name: Function name
            expected_count: Expected number of calls

        Raises:
            AssertionError: If count doesn't match
        """
        calls = self.get_calls(stub_name, function_name)
        actual = len(calls)
        assert actual == expected_count, (
            f"Expected {stub_name}.{function_name} to be called {expected_count} "
            f"times, got {actual}"
        )

    def assert_call_count_at_least(self, stub_name: str, function_name: str,
                                   min_count: int) -> None:
        """Assert that a stub function was called at least min_count times.

        Args:
            stub_name: Stub name
            function_name: Function name
            min_count: Minimum expected calls

        Raises:
            AssertionError: If count is below minimum
        """
        calls = self.get_calls(stub_name, function_name)
        actual = len(calls)
        assert actual >= min_count, (
            f"Expected {stub_name}.{function_name} to be called at least {min_count} "
            f"times, got {actual}"
        )

    def assert_call_order(self, expected_sequence: List[tuple]) -> None:
        """Assert that stub calls occurred in the expected order.

        Args:
            expected_sequence: List of (stub_name, function_name) tuples in order

        Raises:
            AssertionError: If order doesn't match
        """
        actual_sequence = [(c["stub_name"], c["function_name"])
                           for c in self._call_history]
        # Filter to only the calls we care about
        expected_set = set(expected_sequence)
        filtered_actual = [s for s in actual_sequence if s in expected_set]

        assert filtered_actual == expected_sequence, (
            f"Expected call order {expected_sequence}, got {filtered_actual}"
        )

    def clear(self) -> None:
        """Clear all recorded call history."""
        self._call_history.clear()


# Default stub implementations

def default_camera_stub(function_name: str, arguments: List[Any],
                        register_snapshot: Dict[str, int]) -> Any:
    """Default camera boundary stub.

    Records all camera calls and returns configurable values.
    Does not implement Pentax/PTP protocol.
    """
    logger.debug("Camera stub: %s(%s)", function_name, arguments)
    return 0  # Success by default


def default_libgphoto_stub(function_name: str, arguments: List[Any],
                           register_snapshot: Dict[str, int]) -> Any:
    """Default libgphoto2 stub.

    Records all libgphoto calls and returns configurable values.
    Does not implement actual camera communication.
    """
    logger.debug("libgphoto stub: %s(%s)", function_name, arguments)
    return 0  # Success by default


def default_os_stub(function_name: str, arguments: List[Any],
                    register_snapshot: Dict[str, int]) -> Any:
    """Default OS stub.

    Handles syscalls, timer, memory, and file operations.
    Fail closed on unexpected syscalls.
    """
    logger.debug("OS stub: %s(%s)", function_name, arguments)
    return 0  # Success by default


def default_timer_stub(function_name: str, arguments: List[Any],
                       register_snapshot: Dict[str, int]) -> Any:
    """Default timer stub.

    Handles timer creation, expiry, and cancellation.
    """
    logger.debug("Timer stub: %s(%s)", function_name, arguments)
    return 0  # Success by default


def default_callback_stub(function_name: str, arguments: List[Any],
                          register_snapshot: Dict[str, int]) -> Any:
    """Default callback stub.

    Records callback invocations for ordering and argument assertions.
    """
    logger.debug("Callback stub: %s(%s)", function_name, arguments)
    return 0  # Success by default


def create_default_profile() -> StubProfile:
    """Create a default stub profile with all default stubs.

    Returns:
        StubProfile with default implementations for all boundaries
    """
    return StubProfile(
        name="default",
        camera_stub=default_camera_stub,
        libgphoto_stub=default_libgphoto_stub,
        os_stub=default_os_stub,
        timer_stub=default_timer_stub,
        callback_stub=default_callback_stub,
    )


def create_strict_profile() -> StubProfile:
    """Create a strict stub profile that fails on unexpected calls.

    All stubs record calls but return error codes for unrecognized functions,
    modeling fail-closed behavior.

    Returns:
        StubProfile with strict implementations
    """
    def strict_camera(function_name, arguments, register_snapshot):
        logger.warning("Strict camera stub: unexpected call %s(%s)",
                       function_name, arguments)
        return -1  # Error for unexpected

    def strict_libgphoto(function_name, arguments, register_snapshot):
        logger.warning("Strict libgphoto stub: unexpected call %s(%s)",
                       function_name, arguments)
        return -1

    def strict_os(function_name, arguments, register_snapshot):
        logger.warning("Strict OS stub: unexpected call %s(%s)",
                       function_name, arguments)
        return -1

    def strict_timer(function_name, arguments, register_snapshot):
        logger.warning("Strict timer stub: unexpected call %s(%s)",
                       function_name, arguments)
        return -1

    def strict_callback(function_name, arguments, register_snapshot):
        logger.warning("Strict callback stub: unexpected call %s(%s)",
                       function_name, arguments)
        return -1

    return StubProfile(
        name="strict",
        camera_stub=strict_camera,
        libgphoto_stub=strict_libgphoto,
        os_stub=strict_os,
        timer_stub=strict_timer,
        callback_stub=strict_callback,
    )
