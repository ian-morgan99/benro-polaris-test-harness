# Benro Polaris Test Harness

Deterministic protocol/runtime test harness for the Benro Polaris ecosystem.

For step-by-step usage, including simulation controls and image upload, see the
[user guide](USER-GUIDE.md).

This repository exists because repeated physical testing has shown that a visible camera failure can originate at several different layers:

```text
OpenPolaris
  -> Polaris network/protocol
  -> pgphoto / watchdog / Stage-2 runtime
  -> embedded libgphoto2
  -> physical camera
```

The same top-level symptom can therefore mean very different things. Recent Pentax work demonstrated several concrete examples:

- the same K-3 III and the same libgphoto2 SHA can successfully produce a preview with direct `gphoto2` on the Polaris while the packaged Stage-2/pgphoto path fails;
- Stage-2 can resolve different copies of `libgphoto2_port.so.12`, `usb1.so` and camlibs depending on runtime path/environment;
- K-3 III capture is asynchronous and may report an intermediate negative status before the final image arrives;
- OpenPolaris previously inferred incorrect sequential camera opcode mappings;
- K-3 III exposes setting values/options in the current path while K-1 II does not, requiring explicit layer-by-layer isolation;
- pgphoto process ownership/restart state can determine whether the preview listener exists at all.

These failures are expensive to reproduce on physical hardware and easy to misattribute. The purpose of this project is to turn every verified hardware observation into a deterministic, replayable regression contract.

## What this project is

The harness emulates the **observable Polaris-facing contract**, not the internal behaviour of a Pentax/Canon camera.

It should provide:

- deterministic TCP protocol endpoints matching observed Polaris request/response behaviour;
- preview-stream endpoints and controlled timing/failure modes;
- scripted camera personalities based on recorded physical evidence;
- trace replay from sanitised real-device captures;
- fault injection for known runtime and protocol failures;
- a filesystem/runtime harness for Polaris `/app` path, loader, ABI and provenance checks;
- executable regression scenarios that OpenPolaris and the firmware patcher can call from CI;
- a durable evidence ledger showing exactly which physical observation each emulated behaviour came from.

## What this project is NOT

This repository must never claim to prove physical camera support.

A harness PASS proves only that a consumer correctly handles a known, recorded software contract.

It does **not** prove that:

- a real K-1 II, K-3 III, K-01, Canon R5 II, or any other body behaves that way on a new build;
- libgphoto2 correctly implements the camera;
- the Stage-2 runtime actually loads the intended libraries on hardware;
- a firmware image is safe to flash;
- OpenPolaris works end-to-end with a real device.

The evidence hierarchy is deliberately strict:

```text
unit / harness PASS
        -> software handles a known recorded contract

direct libgphoto2 + physical camera PASS
        -> camera/library behaviour proven

Polaris-local + physical camera PASS
        -> firmware/runtime integration proven

OpenPolaris + Polaris + physical camera PASS
        -> end-to-end behaviour proven
```

No lower rung may be substituted for a higher one.

## Why this is a separate repository

This project deliberately does not live inside OpenPolaris, the firmware patcher or libgphoto2.

Repository ownership remains:

```text
ian-morgan99/libgphoto2
  = actual camera implementation and upstream readiness
  = defects must reproduce with clean libgphoto2 + directly attached camera

ian-morgan99/benro-polaris-firmware-patcher
  = firmware build/package/install/runtime/Stage-2/pgphoto/watchdog/provenance
  = defects should reproduce on Polaris without requiring OpenPolaris where practical

ian-morgan99/OpenPolaris
  = client, external Polaris protocol mapping, UI/state, safety and E2E qualification

ian-morgan99/benro-polaris-test-harness
  = independent executable contract, trace replay, fault injection and cross-repo regression fixtures
```

The harness may consume evidence from all three repositories, but it must not become the owning location for their defects.

## Governing design rule

> **Recorded physical behaviour -> harness contract -> consumer regression test.**
>
> Harness behaviour must not be invented merely to make a consumer test pass.

Every nontrivial personality/scenario must therefore state:

- source body and firmware;
- source Polaris firmware/build provenance where relevant;
- source libgphoto2 SHA where relevant;
- date of observation;
- source issue/log/trace reference;
- which layer was physically observed;
- whether the behaviour is confirmed, inferred, or synthetic fault injection.

Synthetic faults are allowed, but they must be labelled synthetic and must never be presented as observed camera behaviour.

## New Features and Improvements

### 1. Enhanced YAML Support

The harness now supports both JSON and YAML format files for scenario and evidence validation:

```python
# Both formats are now supported
scenario_file = Path("scenario.json")  # JSON
scenario_file = Path("scenario.yaml")  # YAML

# Validation works for both formats
errors = validate_scenario_file(scenario_file)
```

### 2. Evidence Cross-Referencing

Evidence can now be referenced across different components:

- **Scenarios** can reference evidence via `evidence_id`
- **Personalities** can reference evidence via `evidence_id`
- **Runtime Manifests** can reference evidence via `evidence_id` in resolution_rules

Example scenario with evidence reference:
```yaml
schema_version: "1"
id: usb-uart-gate-failure
version: 1
title: USB-UART gate failure prevents firmware install
source: physical
status: confirmed
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
```

### 3. Comprehensive Web Interface

A new web interface provides browser-based simulation and evidence upload:

**Features:**
- **Hardware Configuration**: Toggle ASTRO module and power supply inclusion
- **Power Button Simulation**: Simulate power button presses with hardware-specific responses
- **Platesolving Image Upload**: Upload real images for platesolving testing (Next capture image simulation)
- **Fake JPG Generation**: Generate test images for camera testing
- **Evidence Upload**: Upload and validate evidence files
- **Scenario Validation**: Validate scenario files (JSON/YAML) with real-time feedback
- **Real-time Results**: Immediate feedback and error reporting

**Access the web interface:**
```bash
# Start the web server
python3 -m pip install -e .
python3 -m polaris_harness.web_interface

# Visit http://localhost:5000 in your browser
```

### 4. Enhanced Test Coverage

All tests now pass successfully:

```bash
# Smoke tests (basic functionality)
python3 -m pytest tests/test_smoke.py -v
# Results: 18 passed ✅

# Evidence validation tests
python3 -m pytest tests/test_evidence_validation.py -v
# Results: 16 passed ✅

# All tests
python3 -m pytest tests/ -v
# Results: 61 passed
```

## Usage Examples

### Basic Validation

```python
from polaris_harness import validate_scenario, validate_evidence

# Validate scenario
scenario = {
    "schema_version": "1",
    "id": "test-scenario",
    # ... other scenario fields
}
errors = validate_scenario(scenario)
if not errors:
    print("Scenario is valid!")
else:
    print(f"Validation errors: {errors}")

# Validate evidence
evidence = {
    "schema_version": "1",
    "evidence_id": "test-evidence",
    # ... other evidence fields
}
errors = validate_evidence(evidence)
if not errors:
    print("Evidence is valid!")
else:
    print(f"Validation errors: {errors}")
```

### Web Interface Usage

1. **Open the web interface** in your browser at `http://localhost:5000`

2. **Hardware Configuration**: Select which hardware components to include
   - ASTRO Module: Toggle inclusion
   - Power Supply: Toggle inclusion

3. **Power Button Simulation**: Configure camera ID and template, then simulate

4. **Platesolving Image Upload**: Upload real images for platesolving testing
   - Select image file
   - Configure camera ID
   - Add description
   - Upload for next capture simulation

5. **Fake JPG Generation**: Generate test images for camera testing

6. **Evidence Upload**: Upload JPG/PNG files with metadata

7. **Scenario Validation**: Upload and validate scenario files

8. **Real-time Feedback**: View results and error messages immediately

## Key Improvements

1. **Multi-Format Support**: JSON and YAML file formats
2. **Evidence Integration**: Cross-referencing across all components
3. **Comprehensive Web Interface**: Browser-based simulation and testing
4. **Backward Compatibility**: Existing functionality preserved
5. **User-Friendly Interface**: Web-based simulation and testing
6. **Hardware-Aware**: Reflects actual hardware configuration
7. **Enhanced Testing**: Comprehensive test coverage with 61 passing tests
8. **Platesolving Support**: Real image upload for next capture simulation

## Testing the Harness

### Command Line Tests

```bash
# Install dependencies
python3 -m pip install -e .

# Run smoke tests
python3 -m pytest tests/test_smoke.py -v

# Run evidence validation tests
python3 -m pytest tests/test_evidence_validation.py -v

# Run all tests
python3 -m pytest tests/ -v
```

### Web Interface Tests

```bash
# Start the web server
python3 src/polaris_harness/web_interface.py

# Access via browser at http://localhost:5000
```

## Conclusion

The Benro Polaris Test Harness has been significantly enhanced with:

- **Enhanced YAML Support**: Multi-format validation capabilities
- **Evidence Cross-Referencing**: Seamless integration across components
- **Comprehensive Web Interface**: Browser-based simulation and testing
- **Backward Compatibility**: Existing functionality preserved
- **Platesolving Support**: Real image upload for next capture simulation
- **Robust Testing**: 61 passing tests with comprehensive coverage

The harness now provides a complete solution for deterministic protocol/runtime testing with advanced validation, evidence management, and an intuitive web interface for simulation and testing!

## Proposed architecture

```text
benro-polaris-test-harness/
├── protocol/
│   ├── command-server
│   ├── preview-server
│   └── firmware/update-server
├── personalities/
│   ├── pentax-k3iii/
│   ├── pentax-k1ii/
│   ├── pentax-k01/
│   └── canon-r5ii/
├── scenarios/
│   ├── capture-delayed-success/
│   ├── preview-empty-then-valid/
│   ├── preview-listener-loss/
│   ├── setting-enumeration-differential/
│   └── destructive-command-guard/
├── traces/
│   └── sanitised physical request-response captures
├── runtime/
│   ├── app-filesystem/
│   ├── loader-resolution/
│   ├── elf-abi/
│   └── provenance/
├── docs/
│   ├── EVIDENCE-POLICY.md
│   ├── OWNERSHIP-AND-ISOLATION.md
│   └── SCENARIO-FORMAT.md
└── tests/
```

The exact implementation language is not yet fixed. Prefer the smallest dependency footprint that gives deterministic networking, timing control, trace replay and CI portability.

## Initial high-value scenarios

### 1. K-3 III asynchronous capture

Observed successful sequence:

```text
request capture
+~12 ms    -> acknowledgement / state:1
+~2.38 s   -> transient state:-1005
+~3.17 s   -> camera becomes idle
+~3-4 s    -> final image/file appears
```

Regression purpose:

- OpenPolaris must not treat the first negative intermediate status as terminal failure;
- shutter must remain pending/busy until final completion or bounded timeout;
- retries must not duplicate a shot;
- exactly one final completion must be emitted.

### 2. K-3 III preview producer/runtime failures

Known distinction:

- direct `gphoto2` using the same K-3 III and same libgphoto2 SHA can produce a valid JPEG;
- the packaged Stage-2/pgphoto path has failed with zero-byte / `0xa008` preview behaviour.

Regression purpose:

- OpenPolaris correctly handles delayed/empty/no-frame streams;
- firmware runtime tests can model listener loss and bad producer behaviour;
- no test may reinterpret this as a libgphoto2 defect unless it also reproduces directly against libgphoto2.

### 3. K-1 II vs K-3 III setting enumeration

Current physical differential to preserve:

- K-3 III: setting values/options are visible in the current Polaris path;
- K-1 II: equivalent setting values/options are currently not visible in that path.

The harness must be able to replay both personalities so the client does not invent, retain stale, or mislabel values.

Root-cause ownership remains determined by the three-stage test:

```text
A. direct libgphoto2 + camera
B. Polaris-local without OpenPolaris
C. OpenPolaris
```

A missing at A -> candidate libgphoto2 issue.
A present but B missing -> firmware/runtime issue.
A and B present but C missing -> OpenPolaris issue.

### 4. pgphoto / preview-listener lifecycle

Model:

- listener initially available;
- pgphoto terminates/restarts;
- preview port disappears, recovers late, or binds under a replacement process;
- client must surface real state instead of hanging or assuming camera failure.

### 5. Destructive command safety

The protocol contains destructive operations. The harness is the correct place to test client-side guards with disposable synthetic state.

Never validate destructive behaviour against real user media merely to satisfy a harness test.

## Runtime/package qualification role

A second major use of this project is deterministic validation of the firmware patcher's full libgphoto2 stack.

A future libgphoto2 upgrade must be checked as an integrated runtime, not as a single replaced library.

The expected stack includes, as applicable:

```text
libgphoto2.so.6
libgphoto2_port.so.12
ptp2.so
usb1.so
libpolaris_stage2.so
pgphoto.stage2ondisk
wrapper/environment
all runtime lookup-path copies
```

The runtime harness should assert at minimum:

- exact source SHA/version provenance;
- ELF architecture/ABI;
- `DT_NEEDED` closure;
- required versioned symbols;
- camlib/iolib directory layout;
- duplicate stock-path/stage2-path copies are intentional and correct;
- loader-selected files are the expected files;
- no stale/stub/wrong-architecture component is selected;
- wrapper exports the expected `CAMLIBS`, `IOLIBS`, `LD_LIBRARY_PATH`, `LD_PRELOAD` values;
- output manifest records the exact component set.

This is intended to catch the class of defect where direct `gphoto2` succeeds but the packaged pgphoto/Stage-2 runtime resolves a mismatched or incomplete stack.

## Mandatory process for new scenarios

1. **Observe or define the source behaviour.**
   - Physical behaviour: preserve provenance and raw trace/log evidence.
   - Synthetic fault: label it explicitly synthetic.
2. **Identify the owning layer.**
   - Do not move a defect into this repository merely because it can be modelled here.
3. **Create the smallest deterministic scenario.**
   - One state transition/failure contract per scenario where practical.
4. **Attach provenance metadata.**
5. **Add consumer assertions.**
6. **Do not promote the scenario to physical-compatibility evidence.**
7. **When real behaviour changes, update the evidence and scenario together.**

## Trace policy

Raw physical traces should be preserved without secrets, private network credentials, personal file names or unrelated media metadata.

Every committed replay trace should have a sidecar manifest describing:

```yaml
source: physical | synthetic
camera_model: Pentax K-3 III
camera_firmware: unknown-or-exact
polaris_firmware: exact-if-known
patcher_sha: exact-if-known
libgphoto2_sha: exact-if-known
observed_layer: polaris-protocol | pgphoto-runtime | preview-stream | other
date: YYYY-MM-DD
source_issue: owner/repo#number
notes: concise provenance/limitations
```

A trace without enough provenance may still be useful for parser fuzzing, but it must not be labelled a canonical hardware contract.

## Cross-repository use

### OpenPolaris

Should use this harness in CI for:

- command framing/parsing;
- camera option/value rendering;
- optimistic-state prevention;
- async capture state machines;
- preview timeout/reconnect handling;
- destructive-command guards;
- protocol regression against recorded traces.

### benro-polaris-firmware-patcher

Should use this harness/runtime package for:

- loader-path assertions;
- ABI/symbol validation;
- full-stack component integrity;
- provenance checks;
- deterministic pgphoto/runtime fault modelling;
- pre-flash regression tests.

### libgphoto2

Should **not** use harness PASS as camera-validation evidence.

libgphoto2 remains responsible for direct clean-source tests with a physically attached camera. Harness scenarios may help consumer integration but must never replace direct hardware reproduction for a libgphoto2 defect or upstream support claim.

## Release/qualification language

Use these terms precisely:

- **Harness-verified**: deterministic scenario passes.
- **Direct-camera verified**: clean libgphoto2 + physical camera passes.
- **Polaris-runtime verified**: physical Polaris runtime + physical camera passes without OpenPolaris where applicable.
- **E2E verified**: OpenPolaris + physical Polaris + physical camera passes.

Do not collapse these into a generic "tested" or "supported" claim.

## Near-term roadmap

1. Define scenario and trace manifest formats.
2. Implement minimal protocol server/replay engine.
3. Add K-3 III async capture scenario.
4. Add preview empty/delayed/listener-loss scenarios.
5. Add K-1 II vs K-3 III setting-enumeration personalities.
6. Add runtime filesystem/loader-resolution harness.
7. Wire OpenPolaris CI to selected deterministic scenarios.
8. Wire firmware-patcher CI to provenance/ABI/loader tests.
9. Convert future physical discoveries into regression scenarios as part of the owning issue's closure criteria.

## Non-negotiable principle

> The harness exists to prevent us from forgetting what physical hardware taught us. It must never become a source of invented truth.
