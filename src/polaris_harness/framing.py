"""
Framing layer for Polaris Harness.

Converts byte streams to protocol frames and back.
"""

import re
from typing import List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum


class FramingType(Enum):
    """Framing type enumeration."""
    DELIMITER = "delimiter"
    FIXED_LENGTH = "fixed_length"
    RAW_CONNECTION = "raw_connection"


@dataclass
class Frame:
    """Protocol frame data structure."""
    raw_bytes: bytes
    text: Optional[str] = None
    framing_type: FramingType = FramingType.DELIMITER
    delimiter: Optional[str] = None
    frame_number: int = 0


class FramingError(Exception):
    """Raised when framing operations fail."""

    def __init__(self, message: str, frame_data: bytes = None):
        super().__init__(message)
        self.frame_data = frame_data


class DelimiterFraming:
    """Delimiter-based framing."""

    def __init__(self, delimiter: str = "#"):
        self.delimiter = delimiter
        self.delimiter_bytes = delimiter.encode('utf-8')
        self.buffer = bytearray()
        self.frame_count = 0

    def feed(self, data: bytes) -> List[Frame]:
        """Feed data to the framer and return completed frames.

        Args:
            data: Raw bytes to process

        Returns:
            List of completed frames
        """
        self.buffer.extend(data)
        frames = []

        while True:
            # Find delimiter in buffer
            delimiter_pos = self.buffer.find(self.delimiter_bytes)
            if delimiter_pos == -1:
                break

            # Extract frame up to and including delimiter
            frame_bytes = bytes(self.buffer[:delimiter_pos + len(self.delimiter_bytes)])
            self.buffer = self.buffer[delimiter_pos + len(self.delimiter_bytes):]

            # Create frame
            frame = Frame(
                raw_bytes=frame_bytes,
                text=frame_bytes.decode('utf-8', errors='replace'),
                framing_type=FramingType.DELIMITER,
                delimiter=self.delimiter,
                frame_number=self.frame_count
            )
            frames.append(frame)
            self.frame_count += 1

        return frames

    def get_partial_frame(self) -> Optional[Frame]:
        """Get partial frame if buffer contains incomplete data.

        Returns:
            Partial frame or None
        """
        if not self.buffer:
            return None

        return Frame(
            raw_bytes=bytes(self.buffer),
            text=self.buffer.decode('utf-8', errors='replace'),
            framing_type=FramingType.DELIMITER,
            delimiter=self.delimiter,
            frame_number=self.frame_count
        )

    def reset(self) -> None:
        """Reset framer state."""
        self.buffer.clear()
        self.frame_count = 0


class FixedLengthFraming:
    """Fixed-length frame framing."""

    def __init__(self, frame_length: int):
        self.frame_length = frame_length
        self.buffer = bytearray()
        self.frame_count = 0

    def feed(self, data: bytes) -> List[Frame]:
        """Feed data to the framer and return completed frames.

        Args:
            data: Raw bytes to process

        Returns:
            List of completed frames
        """
        self.buffer.extend(data)
        frames = []

        while len(self.buffer) >= self.frame_length:
            # Extract complete frame
            frame_bytes = bytes(self.buffer[:self.frame_length])
            self.buffer = self.buffer[self.frame_length:]

            # Create frame
            frame = Frame(
                raw_bytes=frame_bytes,
                text=frame_bytes.decode('utf-8', errors='replace'),
                framing_type=FramingType.FIXED_LENGTH,
                frame_number=self.frame_count
            )
            frames.append(frame)
            self.frame_count += 1

        return frames

    def get_partial_frame(self) -> Optional[Frame]:
        """Get partial frame if buffer contains incomplete data.

        Returns:
            Partial frame or None
        """
        if len(self.buffer) < self.frame_length:
            return Frame(
                raw_bytes=bytes(self.buffer),
                text=self.buffer.decode('utf-8', errors='replace'),
                framing_type=FramingType.FIXED_LENGTH,
                frame_number=self.frame_count
            )
        return None

    def reset(self) -> None:
        """Reset framer state."""
        self.buffer.clear()
        self.frame_count = 0


class RawConnectionFraming:
    """Raw connection framing (no framing)."""

    def __init__(self):
        self.buffer = bytearray()
        self.frame_count = 0

    def feed(self, data: bytes) -> List[Frame]:
        """Feed data to the framer and return completed frames.

        Args:
            data: Raw bytes to process

        Returns:
            List of completed frames (each frame is one byte)
        """
        frames = []

        for byte in data:
            frame = Frame(
                raw_bytes=bytes([byte]),
                text=None,
                framing_type=FramingType.RAW_CONNECTION,
                frame_number=self.frame_count
            )
            frames.append(frame)
            self.frame_count += 1

        return frames

    def get_partial_frame(self) -> Optional[Frame]:
        """Get partial frame (not supported for raw connection)."""
        return None

    def reset(self) -> None:
        """Reset framer state."""
        self.buffer.clear()
        self.frame_count = 0


class FramingEngine:
    """Main framing engine that manages different framing types."""

    def __init__(self, framing_type: FramingType = FramingType.DELIMITER, **kwargs):
        self.framing_type = framing_type
        self.framing_instance = self._create_framing_instance(framing_type, **kwargs)
        self.max_frame_bytes = kwargs.get('max_frame_bytes', 65536)

    def _create_framing_instance(self, framing_type: FramingType, **kwargs):
        """Create framing instance based on type."""
        if framing_type == FramingType.DELIMITER:
            delimiter = kwargs.get('delimiter', '#')
            return DelimiterFraming(delimiter)
        elif framing_type == FramingType.FIXED_LENGTH:
            frame_length = kwargs.get('frame_length', 1024)
            return FixedLengthFraming(frame_length)
        elif framing_type == FramingType.RAW_CONNECTION:
            return RawConnectionFraming()
        else:
            raise ValueError(f"Unknown framing type: {framing_type}")

    def feed(self, data: bytes) -> List[Frame]:
        """Feed data to the framing engine and return completed frames.

        Args:
            data: Raw bytes to process

        Returns:
            List of completed frames

        Raises:
            FramingError: If frame exceeds maximum size
        """
        # Check frame size limit
        if len(data) > self.max_frame_bytes:
            raise FramingError(f"Frame exceeds maximum size: {len(data)} > {self.max_frame_bytes}")

        return self.framing_instance.feed(data)

    def get_partial_frame(self) -> Optional[Frame]:
        """Get partial frame if buffer contains incomplete data.

        Returns:
            Partial frame or None
        """
        return self.framing_instance.get_partial_frame()

    def reset(self) -> None:
        """Reset framing engine state."""
        self.framing_instance.reset()

    def get_frame_count(self) -> int:
        """Get number of frames processed."""
        return self.framing_instance.frame_count

    def set_max_frame_bytes(self, max_bytes: int) -> None:
        """Set maximum frame size."""
        self.max_frame_bytes = max_bytes


class FramingErrorHandler:
    """Handles framing errors and provides error recovery."""

    def __init__(self):
        self.error_count = 0
        self.last_error = None

    def handle_error(self, error: FramingError) -> bool:
        """Handle a framing error.

        Args:
            error: Framing error to handle

        Returns:
            True if error was handled, False otherwise
        """
        self.error_count += 1
        self.last_error = error

        # For now, just log the error
        print(f"Framing error: {error}")
        return True

    def reset(self) -> None:
        """Reset error handler state."""
        self.error_count = 0
        self.last_error = None

    def get_error_count(self) -> int:
        """Get number of errors encountered."""
        return self.error_count

    def get_last_error(self) -> Optional[FramingError]:
        """Get last error encountered."""
        return self.last_error