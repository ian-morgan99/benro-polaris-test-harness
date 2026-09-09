# Benro Polaris Test Harness - User Guide

## Overview

The Benro Polaris Test Harness is a deterministic protocol/runtime test harness for the Benro Polaris ecosystem. It turns verified hardware observations into deterministic, replayable regression contracts to ensure software correctly handles known recorded behavior.

## What This Harness Does

### Core Functionality
- **Deterministic Protocol Endpoints**: Emulates TCP protocol endpoints matching observed Polaris request/response behavior
- **Controlled Timing**: Provides preview-stream endpoints with controlled timing and failure modes
- **Evidence-Based Personalities**: Creates scripted camera personalities based on recorded physical evidence
- **Trace Replay**: Replays sanitized real-device captures deterministically
- **Fault Injection**: Injects known runtime and protocol failures for testing
- **Filesystem Harness**: Provides app filesystem, loader resolution, ABI checking, and provenance validation
- **Regression Testing**: Executes regression scenarios that OpenPolaris and firmware patcher can call from CI
- **Evidence Ledger**: Maintains durable evidence showing which physical observation each emulated behavior came from

### Evidence Hierarchy
```
unit / harness PASS
        -> software handles a known recorded contract

direct libgphoto2 + physical camera PASS
        -> camera/library behaviour proven

Polaris-local + physical camera PASS
        -> firmware/runtime integration proven

OpenPolaris + Polaris + physical camera PASS
        -> end-to-end behaviour proven
```

## How to Use the Harness

### 1. Basic Setup

The harness is designed to be used as a testing framework for OpenPolaris and firmware patcher. It provides:

- **Scenario Files**: YAML/JSON files defining deterministic test scenarios
- **Personality Files**: Camera personality definitions based on physical evidence
- **Evidence Validation**: Comprehensive validation of all evidence data
- **Test Framework**: Pytest-based testing suite

### 2. Running Tests

```bash
# Run smoke tests (basic functionality)
python3 -m pytest tests/test_smoke.py -v

# Run evidence validation tests
python3 -m pytest tests/test_evidence_validation.py -v

# Run all tests
python3 -m pytest tests/ -v
```

### 3. Validating Scenarios

The harness provides validation functions for different data types:

```python
from polaris_harness import (
    validate_scenario,
    validate_evidence,
    validate_personality,
    validate_runtime_manifest,
    validate_scenario_file,
    validate_evidence_file,
    validate_personality_file,
    validate_runtime_manifest_file,
)

# Validate scenario data
scenario = {
    "schema_version": "1",
    "id": "test-scenario",
    # ... other scenario fields
}
errors = validate_scenario(scenario)
if errors:
    print(f"Validation errors: {errors}")
else:
    print("Scenario is valid!")

# Validate scenario file (supports both JSON and YAML)
from pathlib import Path
scenario_file = Path("scenarios/usb-uart-gate-failure/scenario.yaml")
errors = validate_scenario_file(scenario_file)
```

## Understanding Results

### Test Output

The harness provides detailed test output showing:

1. **Test Status**: PASS/FAIL for each test case
2. **Validation Errors**: Detailed error messages for validation failures
3. **Execution Details**: Information about scenario execution
4. **Evidence Validation**: Results of evidence validation against schemas

### Example Test Output

```
tests/test_smoke.py::TestSmokeFunctionality::test_basic_clock_functionality PASS
[  5%]
tests/test_smoke.py::TestSmokeFunctionality::test_basic_schema_validation PASSED
[ 11%]
tests/test_smoke.py::TestSmokeFunctionality::test_basic_evidence_validation PASS
[ 16%]
tests/test_smoke.py::TestSmokeFunctionality::test_basic_personality_validation PASSED
[ 22%]
tests/test_smoke.py::TestSmokeFunctionality::test_basic_runtime_manifest_validation PASSED
[ 27%]
tests/test_smoke.py::TestSmokeFunctionality::test_scenario_file_exists PASSED
[ 38%]
tests/test_smoke.py::TestSmokeFunctionality::test_scenario_file_valid PASSED
[ 44%]
```

### Validation Error Format

Validation errors are returned as lists of descriptive error messages:

```python
errors = validate_scenario(scenario)
# Example output:
# ["schema_version: 1 is not of type 'string'",
#  "schema_version: '1' was expected",
#  ": Additional properties are not allowed ('camera', 'libgphoto2', 'observed_layer', 'polaris', 'source_issue' were unexpected)"]
```

## Key Features and Capabilities

### 1. Evidence Cross-Referencing

The harness allows evidence to be referenced across different components:

- **Scenarios** can reference evidence via `evidence_id`
- **Personalities** can reference evidence via `evidence_id`
- **Runtime Manifests** can reference evidence via `evidence_id` in resolution_rules

### 2. Multi-Format Support

The harness supports both JSON and YAML formats for scenario and evidence files:

```python
# Both formats are supported
scenario_file = Path("scenario.json")  # JSON
scenario_file = Path("scenario.yaml")  # YAML
```

### 3. Comprehensive Validation

All evidence data is validated against strict schemas:

- **Scenario Schema**: Validates scenario structure and content
- **Evidence Schema**: Validates evidence records
- **Personality Schema**: Validates camera personalities
- **Runtime Manifest Schema**: Validates runtime manifests

### 4. Deterministic Behavior

The harness provides deterministic behavior for testing:

- **Virtual Clock**: Controlled timing for reproducible tests
- **Controlled Failures**: Simulated failures for testing error handling
- **Trace Replay**: Replay of recorded interactions

## Example Usage

### Creating a Simple Scenario

```python
from polaris_harness import validate_scenario

# Create a simple scenario
scenario = {
    "schema_version": "1",
    "id": "simple-test",
    "version": 1,
    "title": "Simple Test Scenario",
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
    },
    "states": {
        "idle": {
            "on_request": [
                {
                    "id": "test_request",
                    "match": {
                        "type": "exact_text",
                        "value": "test",
                        "encoding": "utf-8"
                    },
                    "actions": [
                        {
                            "type": "send",
                            "endpoint": "command",
                            "after_ms": 0,
                            "text": "test response"
                        }
                    ]
                }
            ]
        }
    }
}

# Validate the scenario
errors = validate_scenario(scenario)
if not errors:
    print("Scenario is valid!")
else:
    print(f"Validation errors: {errors}")
```

### Validating Evidence

```python
from polaris_harness import validate_evidence

# Create evidence
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

# Validate evidence
errors = validate_evidence(evidence)
if not errors:
    print("Evidence is valid!")
else:
    print(f"Validation errors: {errors}")
```

## Testing the Harness

### Running the Test Suite

The harness includes comprehensive tests:

1. **Smoke Tests** (`tests/test_smoke.py`):
   - Basic functionality tests
   - Clock functionality
   - Schema validation
   - Evidence validation
   - Personality validation
   - Runtime manifest validation

2. **Evidence Validation Tests** (`tests/test_evidence_validation.py`):
   - Valid evidence validation
   - Invalid evidence validation (missing fields, wrong versions, etc.)
   - File validation tests
   - Integration tests

### Running Tests

```bash
# Install dependencies (if needed)
python3 -m pip install -e .

# Run smoke tests
python3 -m pytest tests/test_smoke.py -v

# Run evidence validation tests
python3 -m pytest tests/test_evidence_validation.py -v

# Run all tests
python3 -m pytest tests/ -v
```

## Advanced Features

### 1. Scenario Execution

The harness can execute scenarios:

```python
from polaris_harness import create_verifier, get_verifier

# Create a verifier for a scenario
verifier = create_verifier("scenario-id")

# Execute the scenario
result = verifier.execute()
print(f"Execution result: {result}")
```

### 2. Evidence Management

The harness provides evidence management capabilities:

```python
from polaris_harness import (
    validate_and_store_evidence,
    get_evidence,
    get_all_evidence,
)

# Validate and store evidence
validate_and_store_evidence(evidence)

# Retrieve specific evidence
evidence = get_evidence("test-evidence")

# Retrieve all evidence
all_evidence = get_all_evidence()
```

### 3. Transport Layer

The harness includes a transport layer for network communication:

```python
from polaris_harness import create_server, get_server

# Create a TCP server
server = create_server("127.0.0.1", 8080)
server.start()

# Get the server
retrieved_server = get_server("server-id")
```

## Troubleshooting

### Common Issues

1. **Validation Errors**
   - Check that all required fields are present
   - Ensure schema versions match
   - Verify data types are correct

2. **File Not Found**
   - Ensure file paths are correct
   - Check file permissions
   - Verify file format (JSON/YAML)

3. **Test Failures**
   - Check test setup
   - Verify dependencies are installed
   - Check for syntax errors in test files

### Getting Help

For more information:

1. **Documentation**: Check the `docs/` directory for detailed documentation
2. **Examples**: Look at the `examples/` directory for usage examples
3. **Issues**: Report issues on the GitHub repository
4. **Community**: Join the community for support

## Conclusion

The Benro Polaris Test Harness provides a comprehensive framework for testing OpenPolaris and firmware patcher with deterministic, evidence-based scenarios. It ensures that software correctly handles known recorded behavior while providing comprehensive validation and testing capabilities.

The harness is designed to be:

- **Deterministic**: Provides reproducible test results
- **Comprehensive**: Validates all aspects of harness data
- **Flexible**: Supports multiple formats and data types
- **Maintainable**: Well-structured with clear documentation
- **Testable**: Includes comprehensive test coverage

Use the harness to ensure your software correctly handles known recorded behavior and to catch regressions before they affect users.