"""
Integration tests for Polaris Harness with OpenPolaris.

Tests that validate harness scenarios against OpenPolaris consumer behavior.
"""

import pytest
import subprocess
import time
import socket
from pathlib import Path
from typing import Dict, Any

from polaris_harness import (
    create_server,
    get_server,
    remove_server,
    get_clock,
    set_clock_mode,
    ClockMode,
    validate_scenario,
    validate_evidence,
)


class TestOpenPolarisIntegration:
    """Integration tests with OpenPolaris."""

    def setup_method(self):
        """Set up test fixtures."""
        self.clock = get_clock()
        set_clock_mode(ClockMode.VIRTUAL)
        self.clock.reset()

    def test_scenario_validation_integration(self):
        """Test scenario validation integration."""
        # Load the USB-UART gate scenario
        scenario_path = Path("scenarios/usb-uart-gate-failure/scenario.yaml")
        
        # Validate scenario file
        from polaris_harness.validation import validate_scenario_file
        errors = validate_scenario_file(scenario_path)
        
        assert len(errors) == 0, f"Scenario validation errors: {errors}"

    def test_evidence_validation_integration(self):
        """Test evidence validation integration."""
        # Load evidence from SSH evidence
        evidence_path = Path("scenarios/usb-uart-gate-failure/scenario.yaml")
        
        # Parse scenario to extract evidence
        import yaml
        with open(evidence_path) as f:
            scenario = yaml.safe_load(f)
        
        # Validate scenario contains evidence
        assert "camera" in scenario
        assert "polaris" in scenario
        assert "libgphoto2" in scenario
        assert scenario["source"] == "physical"

    def test_harness_server_integration(self):
        """Test harness server integration."""
        # Create a test scenario
        test_scenario = {
            "schema_version": "1",
            "id": "test-integration-scenario",
            "version": 1,
            "title": "Test Integration Scenario",
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
        
        # Validate scenario
        errors = validate_scenario(test_scenario)
        assert len(errors) == 0, f"Scenario validation errors: {errors}"

    def test_transport_integration(self):
        """Test transport layer integration."""
        # Create a TCP server
        server = create_server("test-server", "127.0.0.1", 0)
        
        try:
            # Start server
            server.start()
            
            # Get server port
            port = server.get_port()
            assert port > 0
            
            # Test server is running
            connections = server.get_connections()
            assert len(connections) == 0
            
        finally:
            # Clean up
            remove_server("test-server")

    def test_clock_integration(self):
        """Test clock integration."""
        clock = get_clock()
        
        # Test virtual clock functionality
        assert clock.is_virtual
        assert not clock.is_real
        
        # Test time advancement
        clock.advance_time(1000)
        assert clock.now_ms() == 1000
        
        # Test sleep until
        clock.sleep_until(2000)
        assert clock.now_ms() == 2000

    def test_ledger_integration(self):
        """Test ledger integration."""
        from polaris_harness.ledger import create_ledger
        
        # Create a ledger
        ledger = create_ledger("test-run")
        
        # Add entries
        seq1 = ledger.add_entry(
            scenario_id="test-scenario",
            scenario_version=1,
            evidence_id="test-evidence",
            clock="virtual",
            t_ms=0,
            endpoint="command",
            connection_id="conn1",
            direction="client_to_device",
            event="connection_opened"
        )
        
        seq2 = ledger.add_entry(
            scenario_id="test-scenario",
            scenario_version=1,
            evidence_id="test-evidence",
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
        
        # Verify entry content
        entry1 = entries[0]
        assert entry1.event == "connection_opened"
        assert entry1.t_ms == 0
        
        entry2 = entries[1]
        assert entry2.event == "request_received"
        assert entry2.text == "SP_TtyUsbUartInit"

    def test_validation_integration(self):
        """Test validation integration."""
        # Test scenario validation
        valid_scenario = {
            "schema_version": "1",
            "id": "valid-test",
            "version": 1,
            "title": "Valid Test",
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

    def test_scenario_execution_integration(self):
        """Test scenario execution integration."""
        from polaris_harness.engine import ScenarioEngine
        
        # Create a simple scenario
        scenario_data = {
            "schema_version": "1",
            "id": "test-execution",
            "version": 1,
            "title": "Test Execution",
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
                            "id": "test_request",
                            "match": {
                                "type": "exact_text",
                                "value": "TEST#"
                            },
                            "actions": [
                                {
                                    "type": "send",
                                    "endpoint": "command",
                                    "after_ms": 0,
                                    "text": "RESPONSE#"
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
        engine = ScenarioEngine("test-execution", scenario_data)
        
        # Verify initial state
        assert engine.state_machine.get_current_state() == "idle"
        
        # Create a test frame
        from polaris_harness.framing import Frame, FramingType
        frame = Frame(
            raw_bytes=b"TEST#",
            text="TEST#",
            framing_type=FramingType.DELIMITER,
            delimiter="#"
        )
        
        # Execute scenario
        actions = engine.execute_scenario(frame, "test-connection")
        
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

    def test_cross_repo_evidence_integration(self):
        """Test cross-repository evidence integration."""
        # This test validates that evidence from PrivateResearch
        # can be properly integrated into the harness
        
        # Simulate evidence from SSH evidence
        evidence_from_ssh = {
            "schema_version": "1",
            "evidence_id": "ssh-evidence-probes-20260904-122940",
            "source": "physical",
            "status": "confirmed",
            "observed_date": "2026-09-04",
            "observed_layer": "polaris-runtime",
            "source_issue": "unknown",
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
                "fwpkt_sha256": "92da888387b14dc02976b5fa22b94067"
            },
            "libgphoto2": {
                "sha": "unknown"
            }
        }
        
        # Validate evidence
        errors = validate_evidence(evidence_from_ssh)
        assert len(errors) == 0, f"Evidence validation errors: {errors}"

    def test_harness_cli_integration(self):
        """Test harness CLI integration."""
        # This test validates that the CLI can be invoked
        # and provides proper help output
        
        # Test CLI help
        import subprocess
        result = subprocess.run(
            ["polaris-harness", "--help"],
            capture_output=True,
            text=True
        )
        
        assert result.returncode == 0
        assert "usage:" in result.stdout
        assert "polaris-harness" in result.stdout

    def test_scenario_file_validation_integration(self):
        """Test scenario file validation integration."""
        # Test that scenario files can be validated
        scenario_file = Path("scenarios/usb-uart-gate-failure/scenario.yaml")
        
        # Validate scenario file
        from polaris_harness.validation import validate_scenario_file
        errors = validate_scenario_file(scenario_file)
        
        assert len(errors) == 0, f"Scenario file validation errors: {errors}"

    def test_evidence_file_validation_integration(self):
        """Test evidence file validation integration."""
        # Test that evidence files can be validated
        # This would require actual evidence files from PrivateResearch
        
        # For now, test that validation function exists
        from polaris_harness.validation import validate_evidence_file
        
        # Test with non-existent file (should handle gracefully)
        errors = validate_evidence_file("non-existent-file.yaml")
        assert len(errors) > 0

    def test_harness_configuration_integration(self):
        """Test harness configuration integration."""
        # Test that harness can be configured properly
        from polaris_harness import get_clock, set_clock_mode, ClockMode
        
        clock = get_clock()
        set_clock_mode(ClockMode.VIRTUAL)
        
        assert clock.is_virtual
        assert not clock.is_real

    def test_scenario_persistence_integration(self):
        """Test scenario persistence integration."""
        # Test that scenarios can be saved and loaded
        from polaris_harness.ledger import create_ledger
        
        # Create a ledger
        ledger = create_ledger("persistence-test")
        
        # Add scenario execution entries
        ledger.add_entry(
            scenario_id="test-scenario",
            scenario_version=1,
            evidence_id="test-evidence",
            clock="virtual",
            t_ms=0,
            endpoint="command",
            connection_id="conn1",
            direction="client_to_device",
            event="scenario_start"
        )
        
        ledger.add_entry(
            scenario_id="test-scenario",
            scenario_version=1,
            evidence_id="test-evidence",
            clock="virtual",
            t_ms=100,
            endpoint="command",
            connection_id="conn1",
            direction="device_to_client",
            event="scenario_complete"
        )
        
        # Verify entries
        entries = ledger.get_entries()
        assert len(entries) == 2
        
        # Test scenario-specific queries
        scenario_entries = ledger.get_entries_by_scenario("test-scenario")
        assert len(scenario_entries) == 2

    def test_error_handling_integration(self):
        """Test error handling integration."""
        # Test that errors are properly handled and logged
        from polaris_harness.validation import validate_scenario
        
        # Test with invalid scenario (missing required fields)
        invalid_scenario = {
            "schema_version": "1",
            "id": "invalid-test"
            # Missing other required fields
        }
        
        errors = validate_scenario(invalid_scenario)
        assert len(errors) > 0, "Should have validation errors for invalid scenario"

    def test_harness_lifecycle_integration(self):
        """Test harness lifecycle integration."""
        # Test that harness can be properly initialized and cleaned up
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
        server = create_server("lifecycle-test", "127.0.0.1", 0)
        ledger = create_ledger("lifecycle-test")
        
        # Verify components are created
        assert server is not None
        assert ledger is not None
        
        # Clean up
        remove_server("lifecycle-test")
        stop_all_ledgers()
        stop_all_transport()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])