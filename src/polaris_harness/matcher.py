"""
Matcher layer for Polaris Harness.

Matches protocol requests against scenario patterns.
"""

import re
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum

from .framing import Frame


class MatchType(Enum):
    """Matcher type enumeration."""
    EXACT_BYTES = "exact_bytes"
    EXACT_TEXT = "exact_text"
    REGEX_TEXT = "regex_text"
    FIELDS = "fields"
    ANY = "any"


@dataclass
class MatchResult:
    """Match result data structure."""
    matched: bool
    matched_text: Optional[str] = None
    matched_bytes: Optional[bytes] = None
    match_type: Optional[MatchType] = None
    mismatch_reason: Optional[str] = None
    confidence: float = 1.0


@dataclass
class MatchRule:
    """Match rule data structure."""
    id: str
    match_type: MatchType
    value: str
    encoding: str = "utf-8"
    hex: Optional[str] = None
    pattern: Optional[str] = None
    rationale: Optional[str] = None
    required: bool = True


class ExactBytesMatcher:
    """Exact bytes matcher."""

    def __init__(self, expected_bytes: bytes):
        self.expected_bytes = expected_bytes

    def match(self, actual_bytes: bytes) -> MatchResult:
        """Match exact bytes.

        Args:
            actual_bytes: Bytes to match

        Returns:
            Match result
        """
        if actual_bytes == self.expected_bytes:
            return MatchResult(
                matched=True,
                matched_bytes=actual_bytes,
                match_type=MatchType.EXACT_BYTES,
                confidence=1.0
            )
        else:
            return MatchResult(
                matched=False,
                matched_bytes=actual_bytes,
                match_type=MatchType.EXACT_BYTES,
                mismatch_reason=f"Expected {self.expected_bytes.hex()}, got {actual_bytes.hex()}",
                confidence=0.0
            )


class ExactTextMatcher:
    """Exact text matcher."""

    def __init__(self, expected_text: str, encoding: str = "utf-8"):
        self.expected_text = expected_text
        self.encoding = encoding

    def match(self, actual_text: str) -> MatchResult:
        """Match exact text.

        Args:
            actual_text: Text to match

        Returns:
            Match result
        """
        if actual_text == self.expected_text:
            return MatchResult(
                matched=True,
                matched_text=actual_text,
                match_type=MatchType.EXACT_TEXT,
                confidence=1.0
            )
        else:
            return MatchResult(
                matched=False,
                matched_text=actual_text,
                match_type=MatchType.EXACT_TEXT,
                mismatch_reason=f"Expected '{self.expected_text}', got '{actual_text}'",
                confidence=0.0
            )


class RegexTextMatcher:
    """Regex text matcher."""

    def __init__(self, pattern: str, encoding: str = "utf-8", rationale: str = ""):
        self.pattern = re.compile(pattern)
        self.encoding = encoding
        self.rationale = rationale

    def match(self, actual_text: str) -> MatchResult:
        """Match text against regex pattern.

        Args:
            actual_text: Text to match

        Returns:
            Match result
        """
        if self.pattern.match(actual_text):
            return MatchResult(
                matched=True,
                matched_text=actual_text,
                match_type=MatchType.REGEX_TEXT,
                confidence=1.0
            )
        else:
            return MatchResult(
                matched=False,
                matched_text=actual_text,
                match_type=MatchType.REGEX_TEXT,
                mismatch_reason=f"Text does not match pattern: {self.pattern.pattern}",
                confidence=0.0
            )


class AnyMatcher:
    """Any matcher (matches everything)."""

    def match(self, actual_text: str) -> MatchResult:
        """Match any text.

        Args:
            actual_text: Text to match

        Returns:
            Match result
        """
        return MatchResult(
            matched=True,
            matched_text=actual_text,
            match_type=MatchType.ANY,
            confidence=1.0
        )


class Matcher:
    """Main matcher that handles different match types."""

    def __init__(self):
        self.matchers = {}

    def add_match_rule(self, rule: MatchRule) -> None:
        """Add a match rule.

        Args:
            rule: Match rule to add
        """
        self.matchers[rule.id] = rule

    def match_request(self, frame: Frame, rule_id: str) -> MatchResult:
        """Match a request frame against a rule.

        Args:
            frame: Frame to match
            rule_id: ID of rule to match against

        Returns:
            Match result

        Raises:
            ValueError: If rule not found
        """
        if rule_id not in self.matchers:
            raise ValueError(f"Match rule not found: {rule_id}")

        rule = self.matchers[rule_id]

        # Get text from frame
        text = frame.text
        if text is None and frame.raw_bytes:
            try:
                text = frame.raw_bytes.decode('utf-8', errors='replace')
            except UnicodeDecodeError:
                text = ""

        # Create matcher based on rule type
        if rule.match_type == MatchType.EXACT_BYTES:
            if rule.hex:
                expected_bytes = bytes.fromhex(rule.hex)
                matcher = ExactBytesMatcher(expected_bytes)
                return matcher.match(frame.raw_bytes)

        elif rule.match_type == MatchType.EXACT_TEXT:
            matcher = ExactTextMatcher(rule.value, rule.encoding)
            return matcher.match(text)

        elif rule.match_type == MatchType.REGEX_TEXT:
            if rule.pattern:
                matcher = RegexTextMatcher(rule.pattern, rule.encoding, rule.rationale)
                return matcher.match(text)

        elif rule.match_type == MatchType.ANY:
            matcher = AnyMatcher()
            return matcher.match(text)

        # Default to exact text match
        matcher = ExactTextMatcher(rule.value, rule.encoding)
        return matcher.match(text)

    def match_frame(self, frame: Frame, rule: MatchRule) -> MatchResult:
        """Match a frame against a rule.

        Args:
            frame: Frame to match
            rule: Rule to match against

        Returns:
            Match result
        """
        # Get text from frame
        text = frame.text
        if text is None and frame.raw_bytes:
            try:
                text = frame.raw_bytes.decode('utf-8', errors='replace')
            except UnicodeDecodeError:
                text = ""

        # Create matcher based on rule type
        if rule.match_type == MatchType.EXACT_BYTES:
            if rule.hex:
                expected_bytes = bytes.fromhex(rule.hex)
                matcher = ExactBytesMatcher(expected_bytes)
                return matcher.match(frame.raw_bytes)

        elif rule.match_type == MatchType.EXACT_TEXT:
            matcher = ExactTextMatcher(rule.value, rule.encoding)
            return matcher.match(text)

        elif rule.match_type == MatchType.REGEX_TEXT:
            if rule.pattern:
                matcher = RegexTextMatcher(rule.pattern, rule.encoding, rule.rationale)
                return matcher.match(text)

        elif rule.match_type == MatchType.ANY:
            matcher = AnyMatcher()
            return matcher.match(text)

        # Default to exact text match
        matcher = ExactTextMatcher(rule.value, rule.encoding)
        return matcher.match(text)

    def get_match_rules(self) -> Dict[str, MatchRule]:
        """Get all match rules.

        Returns:
            Dictionary of match rules
        """
        return self.matchers.copy()

    def clear(self) -> None:
        """Clear all match rules."""
        self.matchers.clear()


class MatchErrorHandler:
    """Handles match errors and provides error recovery."""

    def __init__(self):
        self.error_count = 0
        self.last_error = None

    def handle_error(self, error: Exception) -> bool:
        """Handle a match error.

        Args:
            error: Error to handle

        Returns:
            True if error was handled, False otherwise
        """
        self.error_count += 1
        self.last_error = error

        # For now, just log the error
        print(f"Match error: {error}")
        return True

    def reset(self) -> None:
        """Reset error handler state."""
        self.error_count = 0
        self.last_error = None

    def get_error_count(self) -> int:
        """Get number of errors encountered."""
        return self.error_count

    def get_last_error(self) -> Optional[Exception]:
        """Get last error encountered."""
        return self.last_error