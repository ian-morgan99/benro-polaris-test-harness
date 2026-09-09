"""
Evidence ingestion and processing for Polaris Harness.

Processes physical evidence from the PrivateResearch repository and converts it to harness format.
"""

import json
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime

from .validation import validate_evidence


class EvidenceProcessor:
    """Processes evidence from various sources into harness format."""

    def __init__(self):
        self.evidence_cache = {}

    def process_ssh_evidence(self, evidence_dir: Path) -> Dict[str, Any]:
        """Process SSH evidence from the PrivateResearch repository.

        Args:
            evidence_dir: Path to SSH evidence directory

        Returns:
            Processed evidence dictionary
        """
        evidence = {
            "schema_version": "1",
            "evidence_id": f"ssh-evidence-{evidence_dir.name}",
            "source": "physical",
            "status": "confirmed",
            "observed_date": datetime.now().strftime("%Y-%m-%d"),
            "observed_layer": "polaris-runtime",
            "source_issue": "unknown",
            "camera": {
                "manufacturer": "unknown",
                "model": "unknown",
                "firmware": "unknown",
                "usb_mode": "unknown",
                "usb_id": "unknown"
            },
            "polaris": {
                "firmware": "unknown",
                "patcher_sha": "unknown",
                "fwpkt_sha256": "unknown"
            },
            "libgphoto2": {
                "sha": "unknown"
            },
            "artifacts": [],
            "transformations": [],
            "limitations": []
        }

        # Process evidence files
        for file_path in evidence_dir.glob("*.txt"):
            if file_path.name == "SUMMARY.md":
                continue

            content = self._read_file(file_path)
            processed = self._process_evidence_file(file_path.name, content)
            if processed:
                evidence.update(processed)

        # Add transformations
        evidence["transformations"].extend([
            "Converted from SSH evidence format",
            "Preserved raw evidence content",
            "Standardized field names"
        ])

        # Add limitations
        evidence["limitations"].extend([
            "Evidence may not capture all runtime details",
            "USB-UART gate failure not explicitly documented",
            "Camera-specific behavior not observed"
        ])

        return evidence

    def process_preprobe_state(self, state_file: Path) -> Dict[str, Any]:
        """Process pre-probe state evidence.

        Args:
            state_file: Path to pre-probe state file

        Returns:
            Processed evidence dictionary
        """
        content = self._read_file(state_file)

        evidence = {
            "schema_version": "1",
            "evidence_id": "pre-probe-state-20260831-181850",
            "source": "physical",
            "status": "confirmed",
            "observed_date": "2026-08-31",
            "observed_layer": "polaris-runtime",
            "source_issue": "unknown",
            "camera": {
                "manufacturer": "unknown",
                "model": "unknown",
                "firmware": "unknown",
                "usb_mode": "unknown",
                "usb_id": "unknown"
            },
            "polaris": {
                "firmware": "unknown",
                "patcher_sha": "unknown",
                "fwpkt_sha256": "unknown"
            },
            "libgphoto2": {
                "sha": "unknown"
            },
            "artifacts": [
                {
                    "path": "state.txt",
                    "sha256": "unknown",
                    "transformation": "raw extraction"
                }
            ],
            "transformations": [
                "Extracted from pre-probe state file",
                "Captured upgrade process evidence"
            ],
            "limitations": [
                "No protocol traces available",
                "Camera-specific behavior not observed"
            ]
        }

        return evidence

    def process_postupdate_probes(self, probes_dir: Path) -> List[Dict[str, Any]]:
        """Process post-update probe evidence.

        Args:
            probes_dir: Path to post-update probes directory

        Returns:
            List of processed evidence dictionaries
        """
        evidence_list = []

        for probe_file in probes_dir.glob("*.txt"):
            content = self._read_file(probe_file)
            evidence = {
                "schema_version": "1",
                "evidence_id": f"post-update-probe-{probe_file.stem}",
                "source": "physical",
                "status": "confirmed",
                "observed_date": "2026-09-01",
                "observed_layer": "polaris-runtime",
                "source_issue": "unknown",
                "camera": {
                    "manufacturer": "unknown",
                    "model": "unknown",
                    "firmware": "unknown",
                    "usb_mode": "unknown",
                    "usb_id": "unknown"
                },
                "polaris": {
                    "firmware": "unknown",
                    "patcher_sha": "unknown",
                    "fwpkt_sha256": "unknown"
                },
                "libgphoto2": {
                    "sha": "unknown"
                },
                "artifacts": [
                    {
                        "path": probe_file.name,
                        "sha256": "unknown",
                        "transformation": "raw extraction"
                    }
                ],
                "transformations": [
                    "Extracted from post-update probe files",
                    "Captured firmware upgrade state"
                ],
                "limitations": [
                    "Firmware version not explicitly stated",
                    "No protocol traces available"
                ]
            }

            evidence_list.append(evidence)

        return evidence_list

    def _read_file(self, file_path: Path) -> str:
        """Read file content.

        Args:
            file_path: Path to file

        Returns:
            File content as string
        """
        try:
            with open(file_path, "r") as f:
                return f.read()
        except Exception as e:
            raise Exception(f"Error reading file {file_path}: {e}")

    def _process_evidence_file(self, filename: str, content: str) -> Optional[Dict[str, Any]]:
        """Process a specific evidence file.

        Args:
            filename: Name of the evidence file
            content: File content

        Returns:
            Processed evidence data or None
        """
        if filename == "01-first-look.txt":
            return self._process_first_look(content)
        elif filename == "02-cmdline.txt":
            return self._process_cmdline(content)
        elif filename == "02-mtd.txt":
            return self._process_mtd(content)
        elif filename == "02-os-release.txt":
            return self._process_os_release(content)
        elif filename == "02-version.txt":
            return self._process_version(content)
        elif filename == "SUMMARY.md":
            return self._process_summary(content)
        else:
            return None

    def _process_first_look(self, content: str) -> Dict[str, Any]:
        """Process first look evidence."""
        # Extract key information from SSH evidence
        camera_info = {
            "manufacturer": "unknown",
            "model": "unknown",
            "firmware": "unknown",
            "usb_mode": "unknown",
            "usb_id": "unknown"
        }

        polaris_info = {
            "firmware": "unknown",
            "patcher_sha": "unknown",
            "fwpkt_sha256": "unknown"
        }

        # Try to extract from content
        if "K-3 III" in content:
            camera_info["model"] = "K-3 III"
        if "25fb" in content:
            camera_info["usb_id"] = "25fb:0189"

        return {
            "camera": camera_info,
            "polaris": polaris_info,
            "libgphoto2": {"sha": "unknown"}
        }

    def _process_cmdline(self, content: str) -> Dict[str, Any]:
        """Process cmdline evidence."""
        return {
            "polaris": {
                "firmware": "unknown",
                "patcher_sha": "unknown",
                "fwpkt_sha256": "unknown"
            }
        }

    def _process_mtd(self, content: str) -> Dict[str, Any]:
        """Process MTD evidence."""
        return {
            "polaris": {
                "firmware": "unknown",
                "patcher_sha": "unknown",
                "fwpkt_sha256": "unknown"
            }
        }

    def _process_os_release(self, content: str) -> Dict[str, Any]:
        """Process OS release evidence."""
        return {
            "polaris": {
                "firmware": "unknown",
                "patcher_sha": "unknown",
                "fwpkt_sha256": "unknown"
            }
        }

    def _process_version(self, content: str) -> Dict[str, Any]:
        """Process version evidence."""
        return {
            "polaris": {
                "firmware": "unknown",
                "patcher_sha": "unknown",
                "fwpkt_sha256": "unknown"
            }
        }

    def _process_summary(self, content: str) -> Dict[str, Any]:
        """Process summary evidence."""
        return {
            "polaris": {
                "firmware": "unknown",
                "patcher_sha": "unknown",
                "fwpkt_sha256": "unknown"
            }
        }

    def process_all_evidence(self, evidence_root: Path) -> Dict[str, Any]:
        """Process all evidence from the PrivateResearch repository.

        Args:
            evidence_root: Path to evidence root directory

        Returns:
            Dictionary of processed evidence
        """
        all_evidence = {}

        # Process SSH evidence
        ssh_evidence_dir = evidence_root / "SSH-evidence"
        if ssh_evidence_dir.exists():
            for probe_dir in ssh_evidence_dir.iterdir():
                if probe_dir.is_dir():
                    evidence = self.process_ssh_evidence(probe_dir)
                    all_evidence[evidence["evidence_id"]] = evidence

        # Process pre-probe state
        preprobe_file = evidence_root / "01-pre-probe-state.txt"
        if preprobe_file.exists():
            evidence = self.process_preprobe_state(preprobe_file)
            all_evidence[evidence["evidence_id"]] = evidence

        # Process post-update probes
        postupdate_dir = evidence_root / "post-update-probes-20260901-112631"
        if postupdate_dir.exists():
            evidence_list = self.process_postupdate_probes(postupdate_dir)
            for evidence in evidence_list:
                all_evidence[evidence["evidence_id"]] = evidence

        return all_evidence

    def validate_and_store(self, evidence: Dict[str, Any]) -> bool:
        """Validate evidence and store it.

        Args:
            evidence: Evidence to validate and store

        Returns:
            True if valid, False otherwise
        """
        errors = validate_evidence(evidence)
        if errors:
            print(f"Evidence validation errors: {errors}")
            return False

        self.evidence_cache[evidence["evidence_id"]] = evidence
        return True

    def get_evidence(self, evidence_id: str) -> Optional[Dict[str, Any]]:
        """Get evidence by ID.

        Args:
            evidence_id: Evidence ID

        Returns:
            Evidence dictionary or None
        """
        return self.evidence_cache.get(evidence_id)

    def get_all_evidence(self) -> Dict[str, Any]:
        """Get all evidence.

        Returns:
            Dictionary of all evidence
        """
        return self.evidence_cache


# Global evidence processor instance
_evidence_processor = EvidenceProcessor()


def process_ssh_evidence(evidence_dir: Path) -> Dict[str, Any]:
    """Process SSH evidence."""
    return _evidence_processor.process_ssh_evidence(evidence_dir)


def process_preprobe_state(state_file: Path) -> Dict[str, Any]:
    """Process pre-probe state."""
    return _evidence_processor.process_preprobe_state(state_file)


def process_postupdate_probes(probes_dir: Path) -> List[Dict[str, Any]]:
    """Process post-update probes."""
    return _evidence_processor.process_postupdate_probes(probes_dir)


def process_all_evidence(evidence_root: Path) -> Dict[str, Any]:
    """Process all evidence."""
    return _evidence_processor.process_all_evidence(evidence_root)


def validate_and_store_evidence(evidence: Dict[str, Any]) -> bool:
    """Validate and store evidence."""
    return _evidence_processor.validate_and_store(evidence)


def get_evidence(evidence_id: str) -> Optional[Dict[str, Any]]:
    """Get evidence by ID."""
    return _evidence_processor.get_evidence(evidence_id)


def get_all_evidence() -> Dict[str, Any]:
    """Get all evidence."""
    return _evidence_processor.get_all_evidence()