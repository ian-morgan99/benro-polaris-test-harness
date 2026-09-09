# User Guide for Polaris Harness

## Overview

The Polaris Harness is a deterministic protocol/runtime test harness for the Benro Polaris ecosystem. It turns verified hardware observations into deterministic, replayable regression contracts that can be used to test consumer behavior before deployment to Polaris.

## Quick Start

### 1. Installation

The Polaris Harness is distributed as a Python package. You can install it using pip:

```bash
pip install polaris-harness
```

Or install in development mode:

```bash
pip install -e .
```

### 2. Basic Usage

#### Command Line Interface

The main entry point is the `polaris-harness` command. Use `--help` to see all available commands:

```bash
polaris-harness --help
```

#### Common Commands

1. **Validate a scenario**

```bash
polaris-harness validate scenarios/usb-uart-gate-failure/
```

2. **Serve a scenario**

```bash
polaris-harness serve --scenario scenarios/usb-uart-gate-failure/ --host 127.0.0.1 --port 9090
```

3. **Replay a scenario**

```bash
polaris-harness replay --scenario scenarios/usb-uart-gate-failure/ --against localhost:9090
```

4. **Verify runtime**

```bash
polaris-harness runtime verify --root /path/to/firmware --manifest manifest.yaml
```

5. **Generate runtime report**

```bash
polaris-harness runtime report --root /path/to/firmware --manifest manifest.yaml --json report.json
```

6. **Ingest traces**

```bash
polaris-harness trace ingest --input trace.pcap --output scenarios/
```

### 3. Scenario Structure

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

#### Example Scenario

```yaml
schema_version: 1
id: usb-uart-gate-failure
version: 1
title: USB-UART gate failure prevents firmware install
source: physical
status: confirmed
observed_layer: polaris-runtime
source_issue: ian-morgan99/PrivateResearch#evidence-20260904
camera:
  manufacturer: Pentax
  model: K-3 III
  firmware: unknown
  usb_mode: MTP
  usb_id: "25fb:0189"
polaris:
  firmware: "4.0.0.32"
  patcher_sha: "unknown"
  fwpkt_sha256: "92da888387b14dc02976b5fa22b94067"
libgphoto2:
  sha: "unknown"

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

states:
  idle:
    on_request:
      - id: check_usb_uart
        match:
          type: exact_text
          value: "SP_TtyUsbUartInit"
          encoding: utf-8
        actions:
          - type: send
            endpoint: command
            after_ms: 0
            text: "SP_TtyUsbUartInit failed: no USB-UART device"
          - type: transition
            next_state: usb_uart_failed

  usb_uart_failed:
    on_request:
      - id: upgrade_check
        match:
          type: exact_text
          value: "SP_UpgradeCheckFw"
          encoding: utf-8
        actions:
          - type: send
            endpoint: command
            after_ms: 0
            text: "SP_UpgradeCheckFw: USB-UART gate failed, aborting upgrade"
          - type: transition
            next_state: upgrade_aborted

  upgrade_aborted:
    emit_marker: upgrade_aborted

limits:
  max_frame_bytes: 65536
  max_connections: 1
  scenario_timeout_ms: 10000

assertions:
  - usb_uart_gate_failure_is_detected
  - upgrade_aborts_when_usb_uart_missing
  - error_message_contains_usb_uart_reference
```

### 4. Evidence Ingestion

The harness can ingest physical evidence from the PrivateResearch repository. Evidence files should be placed in the `scenarios/` directory and follow the evidence schema.

#### Evidence Schema

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

### 5. Runtime Verification

Runtime verification validates firmware packages and runtime configurations against manifests.

#### Runtime Manifest

A runtime manifest contains:

- **Schema version**: Always "1"
- **ID**: Manifest identifier
- **Version**: Manifest version
- **Title**: Human-readable title
- **Source**: "physical", "synthetic", or "derived"
- **Status**: "confirmed", "provisional", "synthetic", or "superseded"
- **Artifacts**: List of artifacts with metadata
- **Resolution rules**: Rules for artifact resolution
- **Runtime assertions**: Runtime assertions

#### Runtime Verification Commands

```bash
# Verify runtime
polaris-harness runtime verify --root /path/to/firmware --manifest manifest.yaml

# Generate runtime report
polaris-harness runtime report --root /path/to/firmware --manifest manifest.yaml --json report.json
```

### 6. Trace Ingestion

The harness can ingest physical traces (PCAP files, logs, etc.) and convert them to harness format.

```bash
# Ingest traces
polaris-harness trace ingest --input trace.pcap --output scenarios/
```

### 7. Configuration

The harness can be configured using environment variables or configuration files.

#### Environment Variables

- `POLARIS_HARNESS_LOG_LEVEL`: Log level (DEBUG, INFO, WARNING, ERROR)
- `POLARIS_HARNESS_HOST`: Host to bind to
- `POLARIS_HARNESS_PORT`: Port to bind to
- `POLARIS_HARNESS_CLOCK_MODE`: Clock mode (virtual, real)

#### Configuration File

Create a `config.yaml` file:

```yaml
log_level: INFO
host: 127.0.0.1
port: 0
clock_mode: virtual
```

### 8. Testing

The harness includes comprehensive tests:

#### Running Tests

```bash
# Run all tests
pytest tests/

# Run specific test categories
pytest tests/test_basic_functionality.py
pytest tests/test_integration.py
pytest tests/test_smoke.py
```

#### Test Coverage

The test suite includes:

- **Basic functionality tests**: Core harness functionality
- **Integration tests**: Integration with OpenPolaris
- **Smoke tests**: Basic functionality validation

### 9. Troubleshooting

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

- **Documentation**: See `docs/API-REFERENCE.md` for API documentation
- **Scenario authoring**: See `docs/SCENARIO-AUTHORING.md` for scenario authoring guidelines
- **Issues**: Report issues in the GitHub repository

### 10. Advanced Usage

#### Custom Scenarios

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

#### Custom Evidence

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

#### Custom Personalities

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

### 11. Migration Guide

#### From Previous Versions

If migrating from a previous version of the harness:

1. **Schema changes**: Ensure all scenarios are validated against the new schema
2. **API changes**: Update any code that uses the harness API
3. **Configuration changes**: Update configuration files to match new format

### 12. Support

#### Getting Help

- **Documentation**: See `docs/` for comprehensive documentation
- **Issues**: Report issues in the GitHub repository
- **Discussions**: Join the discussion forum
- **Slack**: Join the Slack channel

#### Contributing

- **Pull requests**: Submit pull requests to the main branch
- **Issues**: Report issues in the GitHub repository
- **Discussions**: Join the discussion forum

## Conclusion

The Polaris Harness provides a comprehensive solution for deterministic protocol/runtime testing of the Benro Polaris ecosystem. It turns verified hardware observations into deterministic, replayable regression contracts that can be used to test consumer behavior before deployment to Polaris.

For more information, see the other documentation files in the `docs/` directory.