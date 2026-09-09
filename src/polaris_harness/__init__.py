"""
Polaris Harness - Deterministic protocol/runtime test harness for Benro Polaris ecosystem.

This repository turns verified hardware observations into deterministic, replayable regression contracts.

Key principles:
- Recorded physical behaviour -> harness contract -> consumer regression test
- Repository boundary: owns harness implementation and fixtures, NOT underlying defects
- Evidence hierarchy: unit/harness PASS → software handles known recorded contract
"""

__version__ = "0.1.0"

from .cli import main
from .clock import (
    ClockManager,
    ClockMode,
    ClockEvent,
    get_clock,
    set_clock_mode,
    now_ms,
    sleep_until,
    schedule_event,
    advance_time,
    process_events,
    get_next_event_time,
    reset_clock,
    is_virtual_mode,
    is_real_mode,
)
from .evidence import (
    EvidenceProcessor,
    process_ssh_evidence,
    process_preprobe_state,
    process_postupdate_probes,
    process_all_evidence,
    validate_and_store_evidence,
    get_evidence,
    get_all_evidence,
)
from .framing import (
    FramingEngine,
    FramingType,
    Frame,
    DelimiterFraming,
    FixedLengthFraming,
    RawConnectionFraming,
    FramingError,
    FramingErrorHandler,
)
from .matcher import (
    Matcher,
    MatchRule,
    MatchResult,
    MatchType,
    ExactBytesMatcher,
    ExactTextMatcher,
    RegexTextMatcher,
    AnyMatcher,
    MatchErrorHandler,
)
from .transport import (
    TCPServer,
    TransportManager,
    Connection,
    ConnectionState,
    TransportError,
    get_transport_manager,
    create_server,
    get_server,
    remove_server,
    stop_all as stop_all_transport,
)
from .validation import (
    SchemaValidator,
    validate_scenario,
    validate_evidence,
    validate_personality,
    validate_runtime_manifest,
    validate_scenario_file,
    validate_evidence_file,
    validate_personality_file,
    validate_runtime_manifest_file,
)
from .runtime import (
    RuntimeVerifier,
    RuntimeVerifierManager,
    get_runtime_verifier_manager,
    create_verifier,
    get_verifier,
    remove_verifier,
    stop_all as stop_all_runtime_verifiers,
)
from .ledger import (
    ExecutionLedger,
    LedgerEntry,
    LedgerManager,
    get_ledger_manager,
    create_ledger,
    get_ledger,
    remove_ledger,
    stop_all as stop_all_ledgers,
)

# Global stop function for backward compatibility
def stop_all():
    """Stop all harness components."""
    stop_all_ledgers()
    stop_all_transport()
    stop_all_runtime_verifiers()
from .version import (
    __version__,
    SCENARIO_SCHEMA_VERSION,
    EVIDENCE_SCHEMA_VERSION,
    PERSONALITY_SCHEMA_VERSION,
    RUNTIME_MANIFEST_SCHEMA_VERSION,
    PROTOCOL_HARNESS_VERSION,
    RUNTIME_VERIFIER_VERSION,
)

__all__ = [
    "main",
    "ClockManager",
    "ClockMode",
    "ClockEvent",
    "get_clock",
    "set_clock_mode",
    "now_ms",
    "sleep_until",
    "schedule_event",
    "advance_time",
    "process_events",
    "get_next_event_time",
    "reset_clock",
    "is_virtual_mode",
    "is_real_mode",
    "EvidenceProcessor",
    "process_ssh_evidence",
    "process_preprobe_state",
    "process_postupdate_probes",
    "process_all_evidence",
    "validate_and_store_evidence",
    "get_evidence",
    "get_all_evidence",
    "FramingEngine",
    "FramingType",
    "Frame",
    "DelimiterFraming",
    "FixedLengthFraming",
    "RawConnectionFraming",
    "FramingError",
    "FramingErrorHandler",
    "ExecutionLedger",
    "LedgerEntry",
    "LedgerManager",
    "get_ledger_manager",
    "create_ledger",
    "get_ledger",
    "remove_ledger",
    "stop_all_ledgers",
    "Matcher",
    "MatchRule",
    "MatchResult",
    "MatchType",
    "ExactBytesMatcher",
    "ExactTextMatcher",
    "RegexTextMatcher",
    "AnyMatcher",
    "MatchErrorHandler",
    "TCPServer",
    "TransportManager",
    "Connection",
    "ConnectionState",
    "TransportError",
    "get_transport_manager",
    "create_server",
    "get_server",
    "remove_server",
    "stop_all_transport",
    "SchemaValidator",
    "validate_scenario",
    "validate_evidence",
    "validate_personality",
    "validate_runtime_manifest",
    "validate_scenario_file",
    "validate_evidence_file",
    "validate_personality_file",
    "validate_runtime_manifest_file",
    "RuntimeVerifier",
    "RuntimeVerifierManager",
    "get_runtime_verifier_manager",
    "create_verifier",
    "get_verifier",
    "remove_verifier",
    "stop_all_runtime_verifiers",
    "__version__",
    "SCENARIO_SCHEMA_VERSION",
    "EVIDENCE_SCHEMA_VERSION",
    "PERSONALITY_SCHEMA_VERSION",
    "RUNTIME_MANIFEST_SCHEMA_VERSION",
    "PROTOCOL_HARNESS_VERSION",
    "RUNTIME_VERIFIER_VERSION",
]