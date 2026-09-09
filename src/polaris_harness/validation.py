"""
Schema validation for Polaris Harness.

Provides validation for scenarios, evidence, personalities, and runtime manifests.
"""

import json
import yaml
from pathlib import Path
from typing import Any, Dict, List, Union

from jsonschema import Draft7Validator, ValidationError

from .version import (
    EVIDENCE_SCHEMA_VERSION,
    PERSONALITY_SCHEMA_VERSION,
    PROTOCOL_HARNESS_VERSION,
    RUNTIME_MANIFEST_SCHEMA_VERSION,
    SCENARIO_SCHEMA_VERSION,
)


class ValidationError(Exception):
    """Raised when validation fails."""

    def __init__(self, message: str, path: List[str] = None):
        super().__init__(message)
        self.path = path or []


class SchemaValidator:
    """Validates schemas and data against them."""

    def __init__(self):
        self._validators = {}
        self._load_schemas()

    def _load_schemas(self):
        """Load all schemas from the schemas directory."""
        schemas_dir = Path(__file__).parent.parent.parent / "schemas"

        # Scenario schema
        scenario_schema_path = schemas_dir / "scenario.schema.json"
        with open(scenario_schema_path) as f:
            scenario_schema = json.load(f)
        self._validators["scenario"] = Draft7Validator(scenario_schema)

        # Evidence schema
        evidence_schema_path = schemas_dir / "evidence.schema.json"
        with open(evidence_schema_path) as f:
            evidence_schema = json.load(f)
        self._validators["evidence"] = Draft7Validator(evidence_schema)

        # Personality schema
        personality_schema_path = schemas_dir / "personality.schema.json"
        with open(personality_schema_path) as f:
            personality_schema = json.load(f)
        self._validators["personality"] = Draft7Validator(personality_schema)

        # Runtime manifest schema
        runtime_schema_path = schemas_dir / "runtime-manifest.schema.json"
        with open(runtime_schema_path) as f:
            runtime_schema = json.load(f)
        self._validators["runtime_manifest"] = Draft7Validator(runtime_schema)

    def validate_scenario(self, data: Dict[str, Any]) -> List[str]:
        """Validate scenario data against schema.

        Args:
            data: Scenario data to validate

        Returns:
            List of validation errors (empty if valid)
        """
        return self._validate("scenario", data)

    def validate_evidence(self, data: Dict[str, Any]) -> List[str]:
        """Validate evidence data against schema.

        Args:
            data: Evidence data to validate

        Returns:
            List of validation errors (empty if valid)
        """
        return self._validate("evidence", data)

    def validate_personality(self, data: Dict[str, Any]) -> List[str]:
        """Validate personality data against schema.

        Args:
            data: Personality data to validate

        Returns:
            List of validation errors (empty if valid)
        """
        return self._validate("personality", data)

    def validate_runtime_manifest(self, data: Dict[str, Any]) -> List[str]:
        """Validate runtime manifest data against schema.

        Args:
            data: Runtime manifest data to validate

        Returns:
            List of validation errors (empty if valid)
        """
        return self._validate("runtime_manifest", data)

    def _validate(self, schema_type: str, data: Dict[str, Any]) -> List[str]:
        """Internal validation method.

        Args:
            schema_type: Type of schema to validate against
            data: Data to validate

        Returns:
            List of validation errors
        """
        validator = self._validators.get(schema_type)
        if not validator:
            return [f"Unknown schema type: {schema_type}"]

        errors = []
        for error in validator.iter_errors(data):
            path = list(error.path) if error.path else []
            errors.append(f"{'.'.join(path)}: {error.message}")

        return errors

    def validate_file(self, file_path: Union[str, Path], schema_type: str) -> List[str]:
        """Validate a file against a schema.

        Args:
            file_path: Path to file to validate
            schema_type: Type of schema to validate against

        Returns:
            List of validation errors
        """
        try:
            file_path = Path(file_path)
            with open(file_path) as f:
                if file_path.suffix.lower() == '.yaml' or file_path.suffix.lower() == '.yml':
                    data = yaml.safe_load(f)
                else:
                    data = json.load(f)

            return self._validate(schema_type, data)
        except (json.JSONDecodeError, yaml.YAMLError) as e:
            return [f"Invalid YAML/JSON: {e}"]
        except Exception as e:
            return [f"Error reading file: {e}"]


# Global validator instance
_validator = SchemaValidator()


def validate_scenario(data: Dict[str, Any]) -> List[str]:
    """Validate scenario data against schema."""
    return _validator.validate_scenario(data)


def validate_evidence(data: Dict[str, Any]) -> List[str]:
    """Validate evidence data against schema."""
    return _validator.validate_evidence(data)


def validate_personality(data: Dict[str, Any]) -> List[str]:
    """Validate personality data against schema."""
    return _validator.validate_personality(data)


def validate_runtime_manifest(data: Dict[str, Any]) -> List[str]:
    """Validate runtime manifest data against schema."""
    return _validator.validate_runtime_manifest(data)


def validate_scenario_file(file_path: Union[str, Path]) -> List[str]:
    """Validate a scenario file against schema."""
    return _validator.validate_file(file_path, "scenario")


def validate_evidence_file(file_path: Union[str, Path]) -> List[str]:
    """Validate an evidence file against schema."""
    return _validator.validate_file(file_path, "evidence")


def validate_personality_file(file_path: Union[str, Path]) -> List[str]:
    """Validate a personality file against schema."""
    return _validator.validate_file(file_path, "personality")


def validate_runtime_manifest_file(file_path: Union[str, Path]) -> List[str]:
    """Validate a runtime manifest file against schema."""
    return _validator.validate_file(file_path, "runtime_manifest")