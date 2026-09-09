# Polaris Harness Setup Guide

## Overview

This guide provides comprehensive instructions for setting up and running the Polaris Harness test environment.

## Prerequisites

### System Requirements
- Linux operating system
- Python 3.12 or higher
- Git (for version control)
- QEMU (for hardware emulation)
- USB device drivers (for Pentax camera testing)

### Software Dependencies
- Python 3.12+
- pip (Python package installer)
- virtualenv or venv
- QEMU (for hardware emulation)
- USB device drivers

## Quick Start

### 1. Clone the Repository

```bash
# Clone the Polaris Harness repository
git clone https://github.com/ian-morgan99/PrivateResearch/polaris-harness.git
cd polaris-harness
```

### 2. Create Virtual Environment

```bash
# Create a virtual environment
python3 -m venv .venv

# Activate the virtual environment
# On Linux/macOS
source .venv/bin/activate
# On Windows
.venv\Scripts\activate
```

### 3. Install Dependencies

```bash
# Install the harness package in development mode
pip install -e .

# Install additional dependencies if needed
pip install pytest pytest-asyncio jsonschema PyYAML
```

### 4. Run Tests

```bash
# Run smoke tests to verify basic functionality
pytest tests/test_smoke.py -v --tb=short

# Run all tests
pytest tests/ -v --tb=short
```

### 5. Setup QEMU Environment (Optional)

```bash
# Setup QEMU harness environment
source scripts/setup_qemu_harness.sh setup

# Start QEMU harness
source scripts/setup_qemu_harness.sh start

# Check QEMU harness status
source scripts/setup_qemu_harness.sh status

# Stop QEMU harness
source scripts/setup_qemu_harness.sh stop
```

## Detailed Setup Instructions

### 1. Repository Structure

The Polaris Harness repository contains the following key directories:

- `src/polaris_harness/` - Core harness source code
- `tests/` - Test suite
- `docs/` - Documentation
- `scenarios/` - Test scenarios
- `schemas/` - JSON schemas
- `scripts/` - Setup and utility scripts

### 2. Core Components

#### CLI Interface
- `polaris-harness` - Main command-line interface
- Supports commands: `validate`, `serve`, `replay`, `runtime`, `trace`

#### Core Modules
- `clock.py` - Virtual and real clock management
- `engine.py` - Scenario execution engine
- `validation.py` - Schema validation
- `ledger.py` - Execution logging
- `transport.py` - Network transport
- `framing.py` - Protocol framing
- `matcher.py` - Pattern matching

#### Integration Modules
- `qemu_integration.py` - QEMU hardware emulation integration
- `runtime.py` - Runtime verification
- `evidence.py` - Evidence ingestion

### 3. Test Suite

#### Smoke Tests
- `test_smoke.py` - Basic functionality tests
- Tests clock functionality, validation, CLI interface, etc.

#### Evidence Validation Tests
- `test_evidence_validation.py` - Evidence schema validation tests
- Tests evidence ingestion and validation

#### Basic Functionality Tests
- `test_basic_functionality.py` - Core harness functionality tests
- Tests basic harness operations

#### Integration Tests
- `test_integration.py` - Integration tests with OpenPolaris
- Tests integration with external systems

### 4. Configuration

#### Configuration Files
- `pyproject.toml` - Python project configuration
- `config.yaml` - Harness configuration (if exists)
- `setup_qemu_harness.sh` - QEMU setup script

#### Environment Variables
- `POLARIS_HARNESS_LOG_LEVEL` - Log level (DEBUG, INFO, WARNING, ERROR)
- `POLARIS_HARNESS_HOST` - Host to bind to
- `POLARIS_HARNESS_PORT` - Port to bind to
- `POLARIS_HARNESS_CLOCK_MODE` - Clock mode (virtual, real)

### 5. Usage Examples

#### Basic Usage

```bash
# Validate a scenario
polaris-harness validate scenarios/usb-uart-gate-failure/

# Serve a scenario
polaris-harness serve --scenario scenarios/usb-uart-gate-failure/ --host 127.0.0.1 --port 9090

# Replay a scenario
polaris-harness replay --scenario scenarios/usb-uart-gate-failure/ --against localhost:9090

# Verify runtime
polaris-harness runtime verify --root /path/to/firmware --manifest manifest.yaml

# Generate runtime report
polaris-harness runtime report --root /path/to/firmware --manifest manifest.yaml --json report.json

# Ingest traces
polaris-harness trace ingest --input trace.pcap --output scenarios/
```

#### Scenario Structure

Scenarios are defined in YAML format and contain:

- **Schema version**: Always "1"
- **ID**: Stable scenario identifier
- **Version**: Scenario version number
- **Title**: Human-readable title
- **Source**: "physical", "synthetic", or "derived"
- **Status**: "confirmed", "provisional", "synthetic", or "superseded"
- **Endpoints**: Protocol transport configuration
- **Initial state**: Starting state name
- **States**: State machine definition
- **Limits**: Resource limits
- **Assertions**: Verification assertions

#### Evidence Structure

Evidence records contain:

- **Schema version**: Always "1"
- **Evidence ID**: Unique identifier
- **Source**: "physical", "synthetic", or "derived"
- **Status**: "confirmed", "provisional", "synthetic", or "superseded"
- **Observed date**: Date of observation
- **Observed layer**: Layer where evidence was observed
- **Source issue**: Source issue reference
- **Camera**: Camera information
- **Polaris**: Polaris firmware information
- **Libgphoto2**: libgphoto2 information
- **Artifacts**: List of artifacts with hashes
- **Transformations**: List of transformations applied
- **Limitations**: Known limitations

### 6. Testing

#### Running Tests

```bash
# Run all tests
pytest tests/ -v --tb=short

# Run specific test categories
pytest tests/test_basic_functionality.py
pytest tests/test_integration.py
pytest tests/test_smoke.py
pytest tests/test_evidence_validation.py
```

#### Test Coverage

The test suite includes:

- **Basic functionality tests**: Core harness functionality
- **Integration tests**: Integration with OpenPolaris
- **Smoke tests**: Basic functionality validation
- **Evidence validation tests**: Evidence schema validation

### 7. Troubleshooting

#### Common Issues

1. **Schema validation errors**
   - Check that all required fields are present
   - Verify field types and formats
   - Ensure schema version is "1"

2. **Runtime verification failures**
   - Check that artifacts exist and have correct hashes
   - Verify resolution rules are correct
   - Check that runtime assertions pass

3. **Transport errors**
   - Check that ports are available
   - Verify firewall settings
   - Check that host is correct

#### Getting Help

- **Documentation**: See `docs/` for comprehensive documentation
- **Issues**: Report issues in the GitHub repository
- **Discussions**: Join the discussion forum
- **Slack**: Join the Slack channel

## Advanced Usage

### 1. Custom Scenarios

Create custom scenarios by extending the base scenario structure:

```yaml
schema_version: 1
id: custom-scenario
version: 1
title: Custom Scenario
source: synthetic
status: synthetic
# ... custom scenario definition
```

### 2. Custom Evidence

Create custom evidence by extending the base evidence structure:

```yaml
schema_version: 1
evidence_id: custom-evidence
source: synthetic
status: synthetic
observed_date: "2026-09-09"
observed_layer: polaris-runtime
source_issue: custom
# ... custom evidence definition
```

### 3. Custom Personalities

Create custom personalities by extending the base personality structure:

```yaml
schema_version: 1
id: custom-personality
version: 1
title: Custom Personality
source: synthetic
status: synthetic
# ... custom personality definition
```

### 4. Custom Runtime Manifests

Create custom runtime manifests by extending the base manifest structure:

```yaml
schema_version: 1
id: custom-manifest
version: 1
title: Custom Manifest
source: synthetic
status: synthetic
artifacts:
  - path: /app/lib/stage2/libgphoto2_port.so.12
    sha256: da0370339953d28b6011a30de71189ef1b59c5f6a3b9c31d0320575fc8646ab6
    source_provenance: build
    target_abi: arm64
    expected_path: /app/lib/stage2/libgphoto2_port.so.12
resolution_rules:
  - artifact_path: /app/lib/stage2/libgphoto2_port.so.12
    resolution_method: explicit_path
```

## Migration Guide

### From Previous Versions

If migrating from a previous version of the harness:

1. **Schema changes**: Ensure all scenarios are validated against the new schema
2. **API changes**: Update any code that uses the harness API
3. **Configuration changes**: Update configuration files to match new format

## Support

### Getting Help

- **Documentation**: See `docs/` for comprehensive documentation
- **Issues**: Report issues in the GitHub repository
- **Discussions**: Join the discussion forum
- **Slack**: Join the Slack channel

### Contributing

- **Pull requests**: Submit pull requests to the main branch
- **Issues**: Report issues in the GitHub repository
- **Discussions**: Join the discussion forum

## Conclusion

The Polaris Harness provides a comprehensive solution for deterministic protocol/runtime testing of the Benro Polaris ecosystem. It turns verified hardware observations into deterministic, replayable regression contracts that can be used to test consumer behavior before deployment to Polaris.

For more information, see the other documentation files in the `docs/` directory.