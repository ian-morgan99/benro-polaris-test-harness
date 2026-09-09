"""
Basic functionality tests for Polaris Harness.
"""

import pytest
from pathlib import Path

from polaris_harness import (
    get_clock,
    set_clock_mode,
    ClockMode,
    validate_scenario,
    validate_evidence,
    validate_personality,
    validate_runtime_manifest,
)


def test_clock_functionality():
    """Test basic clock functionality."""
    clock = get_clock()
    set_clock_mode(ClockMode.VIRTUAL)
    
    # Test initial time
    assert clock.now_ms() == 0
    
    # Test time advancement
    clock.advance_time(1000)
    assert clock.now_ms() == 1000
    
    # Test sleep until
    clock.sleep_until(2000)
    assert clock.now_ms() == 2000
    
    # Test mode
    assert clock.is_virtual
    assert not clock.is_real


def test_schema_validation():
    """Test schema validation."""
    # Test scenario validation
    scenario = {
        "schema_version": "1",
        "id": "test-scenario",
        "version": 1,
        "title": "Test Scenario",
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


def test_evidence_validation():
    """Test evidence validation."""
    evidence = {
        "schema_version": "1",
        "evidence_id": "test-evidence",
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


def test_personality_validation():
    """Test personality validation."""
    personality = {
        "schema_version": "1",
        "id": "test-personality",
        "version": 1,
        "title": "Test Personality",
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


def test_runtime_manifest_validation():
    """Test runtime manifest validation."""
    manifest = {
        "schema_version": "1",
        "id": "test-manifest",
        "version": 1,
        "title": "Test Manifest",
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


def test_scenario_file_validation():
    """Test scenario file validation."""
    scenario_file = Path("tests/test_scenario.yaml")
    
    # Create a simple test scenario file
    scenario_content = """
schema_version: 1
id: test-scenario-file
version: 1
title: Test Scenario File
source: synthetic
status: synthetic

endpoints:
  command:
    transport: tcp
    framing:
      type: delimiter
      delimiter_text: "#"
    bind:
      host: 127.0.0.1
      port: 0

initial_state: idle

limits:
  max_frame_bytes: 65536
  max_connections: 1
  scenario_timeout_ms: 10000
"""
    
    scenario_file.write_text(scenario_content)
    
    # Validate the file
    from polaris_harness.validation import validate_scenario_file
    errors = validate_scenario_file(scenario_file)
    
    # Clean up
    scenario_file.unlink()
    
    assert len(errors) == 0, f"Scenario file validation errors: {errors}"


def test_clock_mode_switching():
    """Test clock mode switching."""
    clock = get_clock()
    
    # Set to virtual mode
    set_clock_mode(ClockMode.VIRTUAL)
    assert clock.is_virtual
    assert not clock.is_real
    
    # Set to real mode
    set_clock_mode(ClockMode.REAL)
    assert not clock.is_virtual
    assert clock.is_real


def test_scenario_structure():
    """Test scenario structure validation."""
    # Test with missing required fields
    incomplete_scenario = {
        "schema_version": "1",
        "id": "test"
        # Missing other required fields
    }
    
    errors = validate_scenario(incomplete_scenario)
    assert len(errors) > 0, "Should have validation errors for incomplete scenario"


def test_evidence_structure():
    """Test evidence structure validation."""
    # Test with missing required fields
    incomplete_evidence = {
        "schema_version": "1",
        "evidence_id": "test"
        # Missing other required fields
    }
    
    errors = validate_evidence(incomplete_evidence)
    assert len(errors) > 0, "Should have validation errors for incomplete evidence"


def test_personality_structure():
    """Test personality structure validation."""
    # Test with missing required fields
    incomplete_personality = {
        "schema_version": "1",
        "id": "test"
        # Missing other required fields
    }
    
    errors = validate_personality(incomplete_personality)
    assert len(errors) > 0, "Should have validation errors for incomplete personality"


def test_runtime_manifest_structure():
    """Test runtime manifest structure validation."""
    # Test with missing required fields
    incomplete_manifest = {
        "schema_version": "1",
        "id": "test"
        # Missing other required fields
    }
    
    errors = validate_runtime_manifest(incomplete_manifest)
    assert len(errors) > 0, "Should have validation errors for incomplete manifest"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])