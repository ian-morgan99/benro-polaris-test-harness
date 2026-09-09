"""
Smoke tests for Polaris Harness.

Basic functionality tests to ensure the harness is working correctly.
"""

import pytest
import subprocess
import time
import socket
from pathlib import Path
from typing import Dict, Any

from polaris_harness import (
    get_clock,
    set_clock_mode,
    ClockMode,
    validate_scenario,
    validate_evidence,
    validate_personality,
    validate_runtime_manifest,
)


class TestSmokeFunctionality:
    """Smoke tests for basic harness functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.clock = get_clock()
        set_clock_mode(ClockMode.VIRTUAL)
        self.clock.reset()

    def test_basic_clock_functionality(self):
        """Test basic clock functionality."""
        # Test initial time
        assert self.clock.now_ms() == 0
        
        # Test time advancement
        self.clock.advance_time(1000)
        assert self.clock.now_ms() == 1000
        
        # Test sleep until
        self.clock.sleep_until(2000)
        assert self.clock.now_ms() == 2000

    def test_basic_schema_validation(self):
        """Test basic schema validation."""
        # Test scenario validation
        scenario = {
            "schema_version": "1",
            "id": "smoke-test-scenario",
            "version": 1,
            "title": "Smoke Test Scenario",
            "source": "synthetic",
            "status": "synthetic",
            "endpoints": {
                "command": {
                    "transport": "tcp",
                    "framing": {"type": "delimiter"},
                    "bind": {"host": "127.0.0.1", "port": 0}
                }
            },
            "initial_state": "idle",
            "limits": {
                "max_frame_bytes": 65536,
                "max_connections": 1,
                "scenario_timeout_ms": 10000
            }
        }
        
        errors = validate_scenario(scenario)
        assert len(errors) == 0, f"Scenario validation errors: {errors}"

    def test_basic_evidence_validation(self):
        """Test basic evidence validation."""
        evidence = {
            "schema_version": "1",
            "evidence_id": "smoke-test-evidence",
            "source": "synthetic",
            "status": "synthetic",
            "observed_date": "2026-09-09",
            "observed_layer": "polaris-runtime",
            "source_issue": "test",
            "camera": {
                "manufacturer": "Pentax",
                "model": "K-3 III",
                "firmware": "4.0.0.32",
                "usb_mode": "MTP",
                "usb_id": "25fb:0189"
            },
            "polaris": {
                "firmware": "4.0.0.32",
                "patcher_sha": "unknown",
                "fwpkt_sha256": "unknown"
            },
            "libgphoto2": {
                "sha": "unknown"
            }
        }
        
        errors = validate_evidence(evidence)
        assert len(errors) == 0, f"Evidence validation errors: {errors}"

    def test_basic_personality_validation(self):
        """Test basic personality validation."""
        personality = {
            "schema_version": "1",
            "id": "smoke-test-personality",
            "version": 1,
            "title": "Smoke Test Personality",
            "source": "synthetic",
            "status": "synthetic",
            "camera": {
                "manufacturer": "Pentax",
                "model": "K-3 III",
                "firmware": "4.0.0.32",
                "usb_mode": "MTP",
                "usb_id": "25fb:0189"
            },
            "polaris": {
                "firmware": "4.0.0.32",
                "patcher_sha": "unknown",
                "fwpkt_sha256": "unknown"
            },
            "libgphoto2": {
                "sha": "unknown"
            }
        }
        
        errors = validate_personality(personality)
        assert len(errors) == 0, f"Personality validation errors: {errors}"

    def test_basic_runtime_manifest_validation(self):
        """Test basic runtime manifest validation."""
        manifest = {
            "schema_version": "1",
            "id": "smoke-test-manifest",
            "version": 1,
            "title": "Smoke Test Manifest",
            "source": "synthetic",
            "status": "synthetic",
            "artifacts": [
                {
                    "path": "/app/lib/stage2/libgphoto2_port.so.12",
                    "sha256": "da0370339953d28b6011a30de71189ef1b59c5f6a3b9c31d0320575fc8646ab6",
                    "source_provenance": "build",
                    "target_abi": "arm64",
                    "expected_path": "/app/lib/stage2/libgphoto2_port.so.12"
                }
            ],
            "resolution_rules": [
                {
                    "artifact_path": "/app/lib/stage2/libgphoto2_port.so.12",
                    "resolution_method": "explicit_path"
                }
            ]
        }
        
        errors = validate_runtime_manifest(manifest)
        assert len(errors) == 0, f"Runtime manifest validation errors: {errors}"

    def test_cli_help(self):
        """Test CLI help functionality."""
        # Test CLI help
        result = subprocess.run(
            ["polaris-harness", "--help"],
            capture_output=True,
            text=True
        )
        
        assert result.returncode == 0
        assert "usage:" in result.stdout
        assert "polaris-harness" in result.stdout

    def test_scenario_file_exists(self):
        """Test that scenario files exist."""
        scenario_file = Path("scenarios/usb-uart-gate-failure/scenario.yaml")
        assert scenario_file.exists(), f"Scenario file not found: {scenario_file}"

    def test_scenario_file_valid(self):
        """Test that scenario file is valid."""
        from polaris_harness.validation import validate_scenario_file
        
        scenario_file = Path("scenarios/usb-uart-gate-failure/scenario.yaml")
        errors = validate_scenario_file(scenario_file)
        
        assert len(errors) == 0, f"Scenario file validation errors: {errors}"

    def test_basic_ledger_functionality(self):
        """Test basic ledger functionality."""
        from polaris_harness.ledger import create_ledger
        
        # Create a ledger
        ledger = create_ledger("smoke-test")
        
        # Add entries
        seq1 = ledger.add_entry(
            scenario_id="smoke-test-scenario",
            scenario_version=1,
            evidence_id="smoke-test-evidence",
            clock="virtual",
            t_ms=0,
            endpoint="command",
            connection_id="conn1",
            direction="client_to_device",
            event="connection_opened"
        )
        
        seq2 = ledger.add_entry(
            scenario_id="smoke-test-scenario",
            scenario_version=1,
            evidence_id="smoke-test-evidence",
            clock="virtual",
            t_ms=100,
            endpoint="command",
            connection_id="conn1",
            direction="device_to_client",
            event="request_received",
            text="SP_TtyUsbUartInit"
        )
        
        # Verify entries
        assert seq1 == 1
        assert seq2 == 2
        
        entries = ledger.get_entries()
        assert len(entries) == 2

    def test_basic_clock_mode_switching(self):
        """Test basic clock mode switching."""
        # Test virtual mode
        set_clock_mode(ClockMode.VIRTUAL)
        assert self.clock.is_virtual
        assert not self.clock.is_real
        
        # Test real mode
        set_clock_mode(ClockMode.REAL)
        assert not self.clock.is_virtual
        assert self.clock.is_real

    def test_basic_scenario_structure(self):
        """Test basic scenario structure."""
        # Test with valid scenario
        valid_scenario = {
            "schema_version": "1",
            "id": "valid-smoke-test",
            "version": 1,
            "title": "Valid Smoke Test",
            "source": "synthetic",
            "status": "synthetic",
            "endpoints": {
                "command": {
                    "transport": "tcp",
                    "framing": {"type": "delimiter"},
                    "bind": {"host": "127.0.0.1", "port": 0}
                }
            },
            "initial_state": "idle",
            "limits": {
                "max_frame_bytes": 65536,
                "max_connections": 1,
                "scenario_timeout_ms": 10000
            }
        }
        
        errors = validate_scenario(valid_scenario)
        assert len(errors) == 0, f"Scenario validation errors: {errors}"

    def test_basic_evidence_structure(self):
        """Test basic evidence structure."""
        # Test with valid evidence
        valid_evidence = {
            "schema_version": "1",
            "evidence_id": "valid-smoke-test-evidence",
            "source": "synthetic",
            "status": "synthetic",
            "observed_date": "2026-09-09",
            "observed_layer": "polaris-runtime",
            "source_issue": "test",
            "camera": {
                "manufacturer": "Pentax",
                "model": "K-3 III",
                "firmware": "4.0.0.32",
                "usb_mode": "MTP",
                "usb_id": "25fb:0189"
            },
            "polaris": {
                "firmware": "4.0.0.32",
                "patcher_sha": "unknown",
                "fwpkt_sha256": "unknown"
            },
            "libgphoto2": {
                "sha": "unknown"
            }
        }
        
        errors = validate_evidence(valid_evidence)
        assert len(errors) == 0, f"Evidence validation errors: {errors}"

    def test_basic_personality_structure(self):
        """Test basic personality structure."""
        # Test with valid personality
        valid_personality = {
            "schema_version": "1",
            "id": "valid-smoke-test-personality",
            "version": 1,
            "title": "Valid Smoke Test Personality",
            "source": "synthetic",
            "status": "synthetic",
            "camera": {
                "manufacturer": "Pentax",
                "model": "K-3 III",
                "firmware": "4.0.0.32",
                "usb_mode": "MTP",
                "usb_id": "25fb:0189"
            },
            "polaris": {
                "firmware": "4.0.0.32",
                "patcher_sha": "unknown",
                "fwpkt_sha256": "unknown"
            },
            "libgphoto2": {
                "sha": "unknown"
            }
        }
        
        errors = validate_personality(valid_personality)
        assert len(errors) == 0, f"Personality validation errors: {errors}"

    def test_basic_runtime_manifest_structure(self):
        """Test basic runtime manifest structure."""
        # Test with valid manifest
        valid_manifest = {
            "schema_version": "1",
            "id": "valid-smoke-test-manifest",
            "version": 1,
            "title": "Valid Smoke Test Manifest",
            "source": "synthetic",
            "status": "synthetic",
            "artifacts": [
                {
                    "path": "/app/lib/stage2/libgphoto2_port.so.12",
                    "sha256": "da0370339953d28b6011a30de71189ef1b59c5f6a3b9c31d0320575fc8646ab6",
                    "source_provenance": "build",
                    "target_abi": "arm64",
                    "expected_path": "/app/lib/stage2/libgphoto2_port.so.12"
                }
            ],
            "resolution_rules": [
                {
                    "artifact_path": "/app/lib/stage2/libgphoto2_port.so.12",
                    "resolution_method": "explicit_path"
                }
            ]
        }
        
        errors = validate_runtime_manifest(valid_manifest)
        assert len(errors) == 0, f"Runtime manifest validation errors: {errors}"

    def test_harness_initialization(self):
        """Test harness initialization."""
        # Test that all core components can be imported
        from polaris_harness import (
            get_clock,
            set_clock_mode,
            ClockMode,
            validate_scenario,
            validate_evidence,
            validate_personality,
            validate_runtime_manifest,
            create_ledger,
            create_server,
            get_transport_manager,
            get_ledger_manager,
        )
        
        # Test basic functionality
        clock = get_clock()
        set_clock_mode(ClockMode.VIRTUAL)
        
        assert clock.is_virtual
        assert not clock.is_real
        
        # Test ledger creation
        ledger = create_ledger("smoke-test")
        assert ledger is not None
        
        # Test server creation
        server = create_server("smoke-test-server", "127.0.0.1", 0)
        assert server is not None

    def test_scenario_execution_smoke(self):
        """Test basic scenario execution."""
        from polaris_harness.engine import ScenarioEngine
        
        # Create a simple scenario
        scenario_data = {
            "schema_version": "1",
            "id": "smoke-execution-test",
            "version": 1,
            "title": "Smoke Execution Test",
            "source": "synthetic",
            "status": "synthetic",
            "endpoints": {
                "command": {
                    "transport": "tcp",
                    "framing": {"type": "delimiter"},
                    "bind": {"host": "127.0.0.1", "port": 0}
                }
            },
            "initial_state": "idle",
            "states": {
                "idle": {
                    "on_request": [
                        {
                            "id": "smoke_request",
                            "match": {
                                "type": "exact_text",
                                "value": "SMOKE#"
                            },
                            "actions": [
                                {
                                    "type": "send",
                                    "endpoint": "command",
                                    "after_ms": 0,
                                    "text": "SMOKE_RESPONSE#"
                                },
                                {
                                    "type": "transition",
                                    "endpoint": "command",
                                    "after_ms": 100,
                                    "next_state": "completed"
                                }
                            ]
                        }
                    ]
                },
                "completed": {}
            },
            "limits": {
                "max_frame_bytes": 65536,
                "max_connections": 1,
                "scenario_timeout_ms": 10000
            }
        }
        
        # Create scenario engine
        engine = ScenarioEngine("smoke-execution-test", scenario_data)
        
        # Verify initial state
        assert engine.state_machine.get_current_state() == "idle"
        
        # Create a test frame
        from polaris_harness.framing import Frame, FramingType
        frame = Frame(
            raw_bytes=b"SMOKE#",
            text="SMOKE#",
            framing_type=FramingType.DELIMITER,
            delimiter="#"
        )
        
        # Execute scenario
        actions = engine.execute_scenario(frame, "smoke-test-connection")
        
        # Verify actions
        assert len(actions) == 2
        assert actions[0].action_type == "send"
        assert actions[1].action_type == "transition"
        
        # Execute actions
        events = engine.execute_actions(actions)
        
        # Verify events
        assert len(events) == 2
        assert events[0].event_type.name == "ACTION_EXECUTED"
        assert events[1].event_type.name == "ACTION_EXECUTED"

    def test_error_handling_smoke(self):
        """Test basic error handling."""
        from polaris_harness.validation import validate_scenario
        
        # Test with invalid scenario (missing required fields)
        invalid_scenario = {
            "schema_version": "1",
            "id": "invalid-smoke-test"
            # Missing other required fields
        }
        
        errors = validate_scenario(invalid_scenario)
        assert len(errors) > 0, "Should have validation errors for invalid scenario"

    def test_harness_lifecycle_smoke(self):
        """Test harness lifecycle."""
        from polaris_harness import (
            get_clock,
            set_clock_mode,
            ClockMode,
            get_transport_manager,
            create_server,
            remove_server,
            get_ledger_manager,
            create_ledger,
            stop_all as stop_all_ledgers,
            stop_all as stop_all_transport,
        )
        
        # Initialize components
        clock = get_clock()
        set_clock_mode(ClockMode.VIRTUAL)
        
        transport_manager = get_transport_manager()
        ledger_manager = get_ledger_manager()
        
        # Create test components
        server = create_server("lifecycle-smoke-test", "127.0.0.1", 0)
        ledger = create_ledger("lifecycle-smoke-test")
        
        # Verify components are created
        assert server is not None
        assert ledger is not None
        
        # Clean up
        remove_server("lifecycle-smoke-test")
        stop_all_ledgers()
        stop_all_transport()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])