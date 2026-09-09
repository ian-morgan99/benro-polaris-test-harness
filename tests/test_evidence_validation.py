"""
Evidence validation tests for Polaris Harness.

Tests that validate evidence against the evidence schema.
"""

import pytest
import json
from pathlib import Path
from typing import Dict, Any

from polaris_harness import (
    validate_evidence,
    validate_evidence_file,
    validate_scenario,
    validate_scenario_file,
)


class TestEvidenceValidation:
    """Evidence validation tests."""

    def setup_method(self):
        """Set up test fixtures."""
        pass

    def test_valid_evidence_validation(self):
        """Test validation of valid evidence."""
        evidence = {
            "schema_version": "1",
            "evidence_id": "test-evidence",
            "source": "physical",
            "status": "confirmed",
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

    def test_invalid_evidence_missing_required_fields(self):
        """Test validation of evidence with missing required fields."""
        evidence = {
            "schema_version": "1",
            "evidence_id": "test-evidence"
            # Missing other required fields
        }
        
        errors = validate_evidence(evidence)
        assert len(errors) > 0, "Should have validation errors for incomplete evidence"

    def test_invalid_evidence_wrong_schema_version(self):
        """Test validation of evidence with wrong schema version."""
        evidence = {
            "schema_version": "2",  # Wrong schema version
            "evidence_id": "test-evidence",
            "source": "physical",
            "status": "confirmed",
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
        assert len(errors) > 0, "Should have validation errors for wrong schema version"

    def test_invalid_evidence_wrong_source(self):
        """Test validation of evidence with wrong source."""
        evidence = {
            "schema_version": "1",
            "evidence_id": "test-evidence",
            "source": "invalid_source",  # Wrong source
            "status": "confirmed",
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
        assert len(errors) > 0, "Should have validation errors for wrong source"

    def test_invalid_evidence_wrong_status(self):
        """Test validation of evidence with wrong status."""
        evidence = {
            "schema_version": "1",
            "evidence_id": "test-evidence",
            "source": "physical",
            "status": "invalid_status",  # Wrong status
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
        assert len(errors) > 0, "Should have validation errors for wrong status"

    def test_invalid_evidence_wrong_observed_layer(self):
        """Test validation of evidence with wrong observed layer."""
        evidence = {
            "schema_version": "1",
            "evidence_id": "test-evidence",
            "source": "physical",
            "status": "confirmed",
            "observed_date": "2026-09-09",
            "observed_layer": "invalid_layer",  # Wrong observed layer
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
        assert len(errors) > 0, "Should have validation errors for wrong observed layer"

    def test_evidence_file_validation(self):
        """Test validation of evidence file."""
        # Create a temporary evidence file
        evidence_file = Path("test_evidence.json")
        
        evidence = {
            "schema_version": "1",
            "evidence_id": "test-evidence",
            "source": "physical",
            "status": "confirmed",
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
        
        with open(evidence_file, "w") as f:
            json.dump(evidence, f, indent=2)
        
        # Validate the file
        errors = validate_evidence_file(evidence_file)
        
        # Clean up
        evidence_file.unlink()
        
        assert len(errors) == 0, f"Evidence file validation errors: {errors}"

    def test_evidence_file_not_found(self):
        """Test validation of non-existent evidence file."""
        errors = validate_evidence_file("non-existent-file.json")
        assert len(errors) > 0, "Should have validation errors for non-existent file"

    def test_evidence_from_ssh_source(self):
        """Test validation of evidence from SSH source."""
        # Simulate evidence from SSH evidence
        evidence = {
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
        
        errors = validate_evidence(evidence)
        assert len(errors) == 0, f"SSH evidence validation errors: {errors}"

    def test_evidence_with_artifacts(self):
        """Test validation of evidence with artifacts."""
        evidence = {
            "schema_version": "1",
            "evidence_id": "test-evidence",
            "source": "physical",
            "status": "confirmed",
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
            },
            "artifacts": [
                {
                    "path": "/app/sd/FwPkt.zip",
                    "sha256": "92da888387b14dc02976b5fa22b94067",
                    "transformation": "raw extraction"
                }
            ],
            "transformations": [
                "Converted from SSH evidence format",
                "Preserved raw evidence content"
            ],
            "limitations": [
                "Evidence may not capture all runtime details",
                "USB-UART gate failure not explicitly documented"
            ]
        }
        
        errors = validate_evidence(evidence)
        assert len(errors) == 0, f"Evidence with artifacts validation errors: {errors}"

    def test_evidence_with_transformations(self):
        """Test validation of evidence with transformations."""
        evidence = {
            "schema_version": "1",
            "evidence_id": "test-evidence",
            "source": "physical",
            "status": "confirmed",
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
            },
            "transformations": [
                "Converted from SSH evidence format",
                "Preserved raw evidence content",
                "Standardized field names"
            ]
        }
        
        errors = validate_evidence(evidence)
        assert len(errors) == 0, f"Evidence with transformations validation errors: {errors}"

    def test_evidence_with_limitations(self):
        """Test validation of evidence with limitations."""
        evidence = {
            "schema_version": "1",
            "evidence_id": "test-evidence",
            "source": "physical",
            "status": "confirmed",
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
            },
            "limitations": [
                "Evidence may not capture all runtime details",
                "USB-UART gate failure not explicitly documented",
                "Camera-specific behavior not observed"
            ]
        }
        
        errors = validate_evidence(evidence)
        assert len(errors) == 0, f"Evidence with limitations validation errors: {errors}"

    def test_evidence_validation_integration(self):
        """Test evidence validation integration."""
        # Test that evidence validation works with scenario validation
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
        
        # Validate scenario
        scenario_errors = validate_scenario(scenario)
        assert len(scenario_errors) == 0, f"Scenario validation errors: {scenario_errors}"
        
        # Validate evidence
        evidence = {
            "schema_version": "1",
            "evidence_id": "test-evidence",
            "source": "physical",
            "status": "confirmed",
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
        
        evidence_errors = validate_evidence(evidence)
        assert len(evidence_errors) == 0, f"Evidence validation errors: {evidence_errors}"

    def test_evidence_validation_with_scenario(self):
        """Test evidence validation with scenario."""
        # Create a scenario that references evidence
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
        
        # Validate scenario
        scenario_errors = validate_scenario(scenario)
        assert len(scenario_errors) == 0, f"Scenario validation errors: {scenario_errors}"

    def test_evidence_validation_with_personality(self):
        """Test evidence validation with personality."""
        # Create a personality that references evidence
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
            },
            "evidence_id": "test-evidence"
        }
        
        # Validate personality
        from polaris_harness.validation import validate_personality
        personality_errors = validate_personality(personality)
        assert len(personality_errors) == 0, f"Personality validation errors: {personality_errors}"

    def test_evidence_validation_with_runtime_manifest(self):
        """Test evidence validation with runtime manifest."""
        # Create a runtime manifest that references evidence
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
                    "resolution_method": "explicit_path",
                    "evidence_id": "test-evidence"
                }
            ]
        }
        
        # Validate manifest
        from polaris_harness.validation import validate_runtime_manifest
        manifest_errors = validate_runtime_manifest(manifest)
        assert len(manifest_errors) == 0, f"Runtime manifest validation errors: {manifest_errors}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])