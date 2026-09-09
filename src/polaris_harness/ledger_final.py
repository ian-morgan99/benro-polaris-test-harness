"""
Execution ledger for Polaris Harness.

Logs all scenario execution events in NDJSON format.
"""

import json
import uuid
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path


@dataclass
class LedgerEntry:
    """Ledger entry data structure."""
    run_id: str
    scenario_id: str
    scenario_version: int
    t_ms: int
    endpoint: str
    connection_id: str
    direction: str
    event: str
    evidence_id: Optional[str] = None
    clock: str = "virtual"
    raw_hex: Optional[str] = None
    text: Optional[str] = None
    state_before: Optional[str] = None
    state_after: Optional[str] = None
    metadata: Dict[str, Any] = None
    ledger_schema_version: str = "1"
    seq: int = 0
    
    def __post_init__(self):
        """Set default values after initialization."""
        if self.seq == 0:
            self.seq = 1


class LedgerError(Exception):
    """Raised when ledger operations fail."""

    def __init__(self, message: str):
        super().__init__(message)


class ExecutionLedger:
    """Execution ledger for logging scenario execution."""

    def __init__(self, run_id: str = None):
        self.run_id = run_id or str(uuid.uuid4())
        self.entries: List[LedgerEntry] = []
        self.next_seq = 1

    def add_entry(
        self,
        scenario_id: str,
        scenario_version: int,
        evidence_id: Optional[str],
        clock: str,
        t_ms: int,
        endpoint: str,
        connection_id: str,
        direction: str,
        event: str,
        raw_hex: Optional[str] = None,
        text: Optional[str] = None,
        state_before: Optional[str] = None,
        state_after: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """Add an entry to the ledger.

        Args:
            scenario_id: Scenario identifier
            scenario_version: Scenario version
            evidence_id: Evidence identifier
            clock: Clock type
            t_ms: Time in milliseconds
            endpoint: Endpoint name
            connection_id: Connection identifier
            direction: Direction (client_to_device or device_to_client)
            event: Event type
            raw_hex: Raw hex data
            text: Text data
            state_before: State before event
            state_after: State after event
            metadata: Additional metadata

        Returns:
            Sequence number of the entry
        """
        entry = LedgerEntry(
            run_id=self.run_id,
            scenario_id=scenario_id,
            scenario_version=scenario_version,
            evidence_id=evidence_id,
            clock=clock,
            t_ms=t_ms,
            endpoint=endpoint,
            connection_id=connection_id,
            direction=direction,
            event=event,
            raw_hex=raw_hex,
            text=text,
            state_before=state_before,
            state_after=state_after,
            metadata=metadata or {}
        )

        self.entries.append(entry)
        self.next_seq += 1
        return entry.seq

    def get_entries(self) -> List[LedgerEntry]:
        """Get all ledger entries.

        Returns:
            List of ledger entries
        """
        return self.entries.copy()

    def get_entries_by_scenario(self, scenario_id: str) -> List[LedgerEntry]:
        """Get entries for a specific scenario.

        Args:
            scenario_id: Scenario identifier

        Returns:
            List of ledger entries
        """
        return [entry for entry in self.entries if entry.scenario_id == scenario_id]

    def get_entries_by_time_range(
        self, start_ms: int, end_ms: int
    ) -> List[LedgerEntry]:
        """Get entries within a time range.

        Args:
            start_ms: Start time in milliseconds
            end_ms: End time in milliseconds

        Returns:
            List of ledger entries
        """
        return [
            entry for entry in self.entries
            if start_ms <= entry.t_ms <= end_ms
        ]

    def get_last_entry(self) -> Optional[LedgerEntry]:
        """Get the last ledger entry.

        Returns:
            Last ledger entry or None
        """
        if self.entries:
            return self.entries[-1]
        return None

    def get_entry_count(self) -> int:
        """Get number of ledger entries.

        Returns:
            Number of entries
        """
        return len(self.entries)

    def clear(self) -> None:
        """Clear all ledger entries."""
        self.entries.clear()
        self.next_seq = 1

    def save_to_file(self, file_path: str) -> None:
        """Save ledger to file.

        Args:
            file_path: Path to file
        """
        with open(file_path, "w") as f:
            for entry in self.entries:
                f.write(json.dumps(asdict(entry)) + "\n")

    def load_from_file(self, file_path: str) -> None:
        """Load ledger from file.

        Args:
            file_path: Path to file
        """
        with open(file_path, "r") as f:
            for line in f:
                data = json.loads(line.strip())
                entry = LedgerEntry(**data)
                self.entries.append(entry)

        if self.entries:
            self.next_seq = max(entry.seq for entry in self.entries) + 1

    def export_to_json(self) -> Dict[str, Any]:
        """Export ledger to JSON.

        Returns:
            JSON representation of ledger
        """
        return {
            "run_id": self.run_id,
            "entries": [asdict(entry) for entry in self.entries]
        }

    def import_from_json(self, data: Dict[str, Any]) -> None:
        """Import ledger from JSON.

        Args:
            data: JSON data
        """
        self.run_id = data.get("run_id", str(uuid.uuid4()))
        self.entries = [LedgerEntry(**entry) for entry in data.get("entries", [])]

        if self.entries:
            self.next_seq = max(entry.seq for entry in self.entries) + 1


class LedgerManager:
    """Manages ledger instances."""

    def __init__(self):
        self.ledgers: Dict[str, ExecutionLedger] = {}

    def create_ledger(self, run_id: str = None) -> ExecutionLedger:
        """Create a ledger.

        Args:
            run_id: Run identifier

        Returns:
            Execution ledger
        """
        ledger = ExecutionLedger(run_id)
        self.ledgers[ledger.run_id] = ledger
        return ledger

    def get_ledger(self, run_id: str) -> Optional[ExecutionLedger]:
        """Get a ledger.

        Args:
            run_id: Run identifier

        Returns:
            Execution ledger or None
        """
        return self.ledgers.get(run_id)

    def remove_ledger(self, run_id: str) -> None:
        """Remove a ledger.

        Args:
            run_id: Run identifier
        """
        if run_id in self.ledgers:
            del self.ledgers[run_id]

    def get_all_ledgers(self) -> Dict[str, ExecutionLedger]:
        """Get all ledgers.

        Returns:
            Dictionary of ledgers
        """
        return self.ledgers.copy()

    def stop_all(self) -> None:
        """Stop all ledgers."""
        self.ledgers.clear()


# Global ledger manager
_ledger_manager = LedgerManager()


def get_ledger_manager() -> LedgerManager:
    """Get the global ledger manager."""
    return _ledger_manager


def create_ledger(run_id: str = None) -> ExecutionLedger:
    """Create a ledger."""
    return _ledger_manager.create_ledger(run_id)


def get_ledger(run_id: str) -> Optional[ExecutionLedger]:
    """Get a ledger."""
    return _ledger_manager.get_ledger(run_id)


def remove_ledger(run_id: str) -> None:
    """Remove a ledger."""
    _ledger_manager.remove_ledger(run_id)


def stop_all() -> None:
    """Stop all ledgers."""
    _ledger_manager.stop_all()