"""
Clock and timing utilities for Polaris Harness.

Provides virtual and real clock implementations for deterministic testing.
"""

import time
from abc import ABC, abstractmethod
from typing import Protocol, List, Optional, Dict
from dataclasses import dataclass
from enum import Enum


class ClockMode(Enum):
    """Clock mode enumeration."""
    VIRTUAL = "virtual"
    REAL = "real"


@dataclass
class ClockEvent:
    """Clock event data structure."""
    timestamp_ms: int
    event_type: str
    data: dict = None


class ClockInterface(Protocol):
    """Clock interface protocol."""

    def now_ms(self) -> int:
        """Get current time in milliseconds."""
        ...

    def sleep_until(self, target_ms: int) -> None:
        """Sleep until target time."""
        ...

    def schedule_event(self, delay_ms: int, event_type: str, data: dict = None) -> None:
        """Schedule an event for future execution."""
        ...

    def advance_time(self, delta_ms: int) -> None:
        """Advance virtual time by delta milliseconds."""
        ...


class VirtualClock(ClockInterface):
    """Virtual clock for deterministic testing."""

    def __init__(self):
        self._current_time_ms = 0
        self._event_queue = []
        self._scheduled_events = []

    def now_ms(self) -> int:
        """Get current virtual time in milliseconds."""
        return self._current_time_ms

    def sleep_until(self, target_ms: int) -> None:
        """Advance time to target."""
        if target_ms > self._current_time_ms:
            self._current_time_ms = target_ms

    def schedule_event(self, delay_ms: int, event_type: str, data: dict = None) -> None:
        """Schedule an event for future execution."""
        target_time = self._current_time_ms + delay_ms
        self._scheduled_events.append({
            "time": target_time,
            "type": event_type,
            "data": data or {}
        })
        self._scheduled_events.sort(key=lambda x: x["time"])

    def advance_time(self, delta_ms: int) -> None:
        """Advance virtual time by delta milliseconds."""
        self._current_time_ms += delta_ms

    def process_events(self) -> List[ClockEvent]:
        """Process all events scheduled for current time."""
        events = []
        current_time = self._current_time_ms

        # Process events scheduled for current time
        while self._scheduled_events and self._scheduled_events[0]["time"] <= current_time:
            event = self._scheduled_events.pop(0)
            events.append(ClockEvent(
                timestamp_ms=event["time"],
                event_type=event["type"],
                data=event["data"]
            ))

        return events

    def get_next_event_time(self) -> Optional[int]:
        """Get time of next scheduled event."""
        if self._scheduled_events:
            return self._scheduled_events[0]["time"]
        return None

    def reset(self, initial_time_ms: int = 0) -> None:
        """Reset clock to initial time."""
        self._current_time_ms = initial_time_ms
        self._scheduled_events.clear()


class RealClock(ClockInterface):
    """Real clock using system time."""

    def __init__(self):
        self._start_time_ms = self._get_system_time_ms()

    def _get_system_time_ms(self) -> int:
        """Get current system time in milliseconds."""
        return int(time.time() * 1000)

    def now_ms(self) -> int:
        """Get current real time in milliseconds."""
        return self._get_system_time_ms() - self._start_time_ms

    def sleep_until(self, target_ms: int) -> None:
        """Sleep until target time."""
        current = self.now_ms()
        if target_ms > current:
            time.sleep((target_ms - current) / 1000.0)

    def schedule_event(self, delay_ms: int, event_type: str, data: dict = None) -> None:
        """Schedule an event for future execution (not supported for real clock)."""
        raise NotImplementedError("RealClock does not support event scheduling")

    def advance_time(self, delta_ms: int) -> None:
        """Advance real time (not supported)."""
        raise NotImplementedError("RealClock does not support time advancement")


class ClockManager:
    """Manages clock instances and switching between virtual/real modes."""

    def __init__(self, mode: ClockMode = ClockMode.VIRTUAL):
        self._mode = mode
        self._virtual_clock = VirtualClock()
        self._real_clock = RealClock()
        self._current_clock = self._virtual_clock

    def set_mode(self, mode: ClockMode) -> None:
        """Set clock mode."""
        self._mode = mode
        self._current_clock = self._virtual_clock if mode == ClockMode.VIRTUAL else self._real_clock

    def now_ms(self) -> int:
        """Get current time in milliseconds."""
        return self._current_clock.now_ms()

    def sleep_until(self, target_ms: int) -> None:
        """Sleep until target time."""
        self._current_clock.sleep_until(target_ms)

    def schedule_event(self, delay_ms: int, event_type: str, data: dict = None) -> None:
        """Schedule an event for future execution."""
        self._current_clock.schedule_event(delay_ms, event_type, data)

    def advance_time(self, delta_ms: int) -> None:
        """Advance virtual time by delta milliseconds."""
        self._current_clock.advance_time(delta_ms)

    def process_events(self) -> List[ClockEvent]:
        """Process events (virtual clock only)."""
        if isinstance(self._current_clock, VirtualClock):
            return self._current_clock.process_events()
        return []

    def get_next_event_time(self) -> Optional[int]:
        """Get time of next scheduled event (virtual clock only)."""
        if isinstance(self._current_clock, VirtualClock):
            return self._current_clock.get_next_event_time()
        return None

    def reset(self, initial_time_ms: int = 0) -> None:
        """Reset virtual clock to initial time."""
        if isinstance(self._current_clock, VirtualClock):
            self._current_clock.reset(initial_time_ms)

    @property
    def mode(self) -> ClockMode:
        """Get current clock mode."""
        return self._mode

    @property
    def is_virtual(self) -> bool:
        """Check if using virtual clock."""
        return self._mode == ClockMode.VIRTUAL

    @property
    def is_real(self) -> bool:
        """Check if using real clock."""
        return self._mode == ClockMode.REAL


# Global clock instance
_clock_manager = ClockManager()


def get_clock() -> ClockManager:
    """Get the global clock manager."""
    return _clock_manager


def set_clock_mode(mode: ClockMode) -> None:
    """Set the global clock mode."""
    _clock_manager.set_mode(mode)


def now_ms() -> int:
    """Get current time in milliseconds."""
    return _clock_manager.now_ms()


def sleep_until(target_ms: int) -> None:
    """Sleep until target time."""
    _clock_manager.sleep_until(target_ms)


def schedule_event(delay_ms: int, event_type: str, data: dict = None) -> None:
    """Schedule an event for future execution."""
    _clock_manager.schedule_event(delay_ms, event_type, data)


def advance_time(delta_ms: int) -> None:
    """Advance virtual time by delta milliseconds."""
    _clock_manager.advance_time(delta_ms)


def process_events() -> List[ClockEvent]:
    """Process events."""
    return _clock_manager.process_events()


def get_next_event_time() -> Optional[int]:
    """Get time of next scheduled event."""
    return _clock_manager.get_next_event_time()


def reset_clock(initial_time_ms: int = 0) -> None:
    """Reset virtual clock to initial time."""
    _clock_manager.reset(initial_time_ms)


def is_virtual_mode() -> bool:
    """Check if using virtual clock."""
    return _clock_manager.is_virtual


def is_real_mode() -> bool:
    """Check if using real clock."""
    return _clock_manager.is_real