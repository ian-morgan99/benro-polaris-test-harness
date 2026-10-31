# Benro Polaris Test Harness

Deterministic protocol/runtime test harness for the Benro Polaris ecosystem.

For step-by-step usage, including simulation controls and image upload, see the
[user guide](USER-GUIDE.md).

## License

This project is licensed under the GNU General Public License v3.0 (GPL-3.0-only).
See the [LICENSE](LICENSE) file for details.

The `src/polaris_harness/arm_emulator/` component (ARM user-space emulation for
Stage-2 lifecycle tests, issue #15) is also GPL-3.0-only, as it depends on the
Unicorn CPU emulator framework which is GPL-licensed.

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
- a durable evidence ledger showing exactly which physical observation each emulated behaviour came from;
- ARM user-space emulation for Stage-2/`pgphoto` executable lifecycle tests (issue #15),
  using the Unicorn CPU emulator in user space without elevated privileges;
- Astro WebUI harness server with synthetic Astro API and browser smoke test (issue #20),
  serving synthetic static assets over loopback in dry-run mode only.

## ARM Emulation (Issue #15) — Agent Usage Guide

The ARM user-space emulation component lives in `src/polaris_harness/arm_emulator/` and is an **independent harness component** — it does not depend on the Polaris protocol scenario engine, runtime verifier, or transport layer.

### What it provides

- `ARMEmulator` — Unicorn-based ARM/Thumb CPU emulation with fail-closed hooks
  (instruction mode comes from the fixture sidecar, so a real ARM region is not
  silently decoded as Thumb)
- `StubRegistry` / `StubProfile` — ABI stubs for camera, libgphoto, OS, timer, callback boundaries
- `FixtureManager` — Checked-in ARM fixture binaries with SHA-256 integrity verification
- `ArmEmulationContract` — Machine-readable JSON invocation/result contract
- 9 lifecycle test scenarios (issue #15, cases 1–9) for Stage-2 `capture_image_with_Bulb`
- CLI: `polaris-harness arm-emulate --suite` or `--fixture <name>`

### How other agents should use it

**Do not** import from `polaris_harness.engine`, `polaris_harness.runtime`, or `polaris_harness.transport` inside `arm_emulator/`. Keep the boundary strict.

**Do** use the public API exported from `polaris_harness.arm_emulator`:

```python
from polaris_harness.arm_emulator import (
    ARMEmulator,
    EmulationConfig,
    FixtureManager,
    ArmEmulationContract,
    run_all_lifecycle_cases,
    LifecycleCase,
)

# Run all 9 lifecycle cases
fixture_manager = FixtureManager("tests/fixtures/arm")
results = run_all_lifecycle_cases(
    fixture_manager,
    patcher_commit="<sha>",
    harness_revision="0.1.0",
    emulator_version="unicorn-2.1.4",
)
for case, result in results.items():
    assert result.passed, f"{case} failed: {result.assertion_errors}"

# Emulate a single fixture
config = EmulationConfig(
    binary_path=Path("tests/fixtures/arm/tiny_fixture.bin"),
    binary_sha256="<sha256>",
    patcher_commit="<sha>",
    harness_revision="0.1.0",
    emulator_version="unicorn-2.1.4",
    fixture_stub_profile="default",
    command="capture_image_with_Bulb",
)
emulator = ARMEmulator(config)
result_record = emulator.run()
contract = ArmEmulationContract.from_emulation_result(result_record)
assert contract.is_pass()
```

### Sample prompt for testing new fixes

When asking another agent to test a fix to the ARM emulation component, provide this prompt:

> I've made a change to the ARM user-space emulation component in `src/polaris_harness/arm_emulator/`. Please:
>
> 1. Run the full ARM emulator test suite: `python -m pytest tests/test_arm_emulator.py -v`
> 2. Run the ARM lifecycle suite via CLI: `polaris-harness arm-emulate --suite`
>    (expects exit 3 today — see *Current status* above; case 4 PASS, cases 1–3
>    PARTIAL, cases 5–9 FAIL)
> 3. Verify the tiny fixture executes end-to-end: `polaris-harness arm-emulate --fixture tiny_fixture --output /tmp/result.json` and confirm the JSON contract shows `result: "pass"` and `instructions_executed > 0`
> 4. Run the complete test suite to check for regressions: `python -m pytest tests/ -v`
> 5. Confirm no imports from `polaris_harness.engine`, `polaris_harness.runtime`, or `polaris_harness.transport` exist inside `src/polaris_harness/arm_emulator/`
> 6. Report any failures with the full error output

### Key constraints (from issue #15)

- **Fail closed**: unknown binary hashes, unexpected syscalls, unsupported instructions, missing stubs, timeouts → error result
- **Never mutate** the input artifact
- **Bind every run** to binary SHA-256, patcher commit, harness revision, emulator version, stub profile, command
- **No privileged QEMU**, no host networking/bridge/iptables
- **Independent component** — no coupling to protocol engine or runtime verifier

### Current status (read before testing)

`arm-emulate --suite` currently exits **3**. That is expected, not a regression:

| case | status | why |
|---|---|---|
| 4 | **PASS** | runs against real `captureBulbImage` bytes with globals seeded; observes exactly one `stopCaptureImageForMessage` |
| 1–3 | **PARTIAL** | the stop edge is verified; assertions on `close` / `cancel` / `context_cancel` cannot be evaluated — see below |
| 5–9 | FAIL | run against `stage2_region`, which executes 71 instructions and reaches six real boundary calls before stopping at `0x101bd0` on `vmov.i32 q8,#0` — see below |

**Why cases 1–3 are PARTIAL, not PASS or FAIL.** Disassembling the 684-byte
`captureBulbImage` region resolves all 17 BL sites, and none of them is a close,
cancel, or context-cancel call. Cross-checking the whole executable segment (721,500
instructions) finds `stopCaptureImageForMessage` called from only two functions
(`captureBulbImage`, `sonyBurstEvent`) and `gp_context_cancel` from 26 — all of them
`gp_filesystem_*` or folder helpers. **No function in this binary contains both a
stop edge and a close or cancel edge.** The full inventory is recorded in the fixture
sidecar under `provenance.call_edge_inventory`.

Those assertions encode issue #251's spec, so they are kept and reported as *not
evaluated* rather than deleted to get green. PARTIAL means nothing was contradicted
and nothing was verified — it is deliberately not a pass, and `result.passed` is
False for it.

**Why cases 5–9 cannot pass with this engine.** With `.got`/`.data` seeded from the
retained binary, `capture_image_with_Bulb` executes 71 instructions and reaches six
real boundary calls (`waitCameraIdle`, `numOfCaptureImage`, `setCameraStatus`,
`gp_log`, `HI_LOG_Print`, `sqrt`) before stopping at `0x101bd0` on `vmov.i32 q8,#0`.
Unicorn 2.1.4's ARM backend raises `UC_ERR_INSN_INVALID` for every VFP/NEON encoding
regardless of FPU state — verified directly: both `vmov.i32` and `vstr` fault in a
bare `Uc(UC_ARCH_ARM, UC_MODE_ARM)`, and no CPACR/SCR register is exposed to enable
the coprocessor. No fixture configuration gets past this point, so the timer,
callback-ordering, output-publication and manual-dispatch edges those cases assert
are unreachable. Patching or skipping the NEON block would mutate the artifact under
test and invalidate the run, so it is recorded as a terminal engine limit in the
fixture's `observed_behaviour` instead.

**Prerequisite:** cases 1–4 need `POLARIS_FIRMWARE_ROOT` pointing at the directory
containing the retained `pgphoto.stage2ondisk`. Without it they SKIP — an unmet
environment prerequisite, deliberately not reported as a behavioural failure:

```bash
export POLARIS_FIRMWARE_ROOT=/path/to/extracted_appfs/stage2-ondisk/ondisk
```

`stage2_region` needs the same variable to apply its `.got`/`.data` memory patches.
Without it the globals stay zero-filled and the run faults at `0x101b38` instead of
`0x101bd0`, which is a measurement of a different environment, so the two
integrity tests that compare against recorded behaviour skip rather than compare
mismatched runs.

With it set, `python -m pytest tests/` reports 298 passed; without it, 294 passed
and 4 skipped. Both are green.

**Exit codes for `arm-emulate --suite`:**

| code | meaning |
|---|---|
| 0 | every case passed |
| 3 | at least one case failed on real behaviour — takes precedence over 4 |
| 4 | nothing contradicted, but some assertions could not be evaluated |

Exit 4 exists so unverified coverage cannot look like success in CI.

## Astro WebUI (Issue #20) — Agent Usage Guide

The Astro WebUI harness server lives in `src/polaris_harness/astro_webui/` and is an **independent harness component** — it does not depend on the Polaris protocol scenario engine, runtime verifier, or transport layer.

### What it provides

- `create_app()` — Flask-based server serving synthetic Astro static assets
- `run_server()` — CLI entry point (`polaris-harness astro-webui --profile synthetic`)
- Synthetic Astro API: state, capture, solve, goto, tracking, exif, rate-control, reset
- 9 schema-backed synthetic scenarios (all `source: synthetic`)
- Route/API compatibility report endpoint (`/api/astro/compatibility`)
- Asset provenance manifest with SHA-256 hashes

### How other agents should use it

**Do not** import from `polaris_harness.engine`, `polaris_harness.runtime`, or `polaris_harness.transport` inside `astro_webui/`. Keep the boundary strict.

**Do** use the public API exported from `polaris_harness.astro_webui`:

```python
from polaris_harness.astro_webui import create_app, run_server, list_scenarios

# Create the Flask app
app = create_app()

# List available synthetic scenarios
scenarios = list_scenarios()

# Run the server (dry-run only, loopback by default)
run_server(host="127.0.0.1", port=8080, profile="synthetic")
```

### Sample prompt for testing new fixes

When asking another agent to test a fix to the Astro WebUI component, provide this prompt:

> I've made a change to the Astro WebUI harness server in `src/polaris_harness/astro_webui/`. Please:
>
> 1. Run the full test suite: `python -m pytest tests/ -v`
> 2. Verify the Astro WebUI server starts: `polaris-harness astro-webui --profile synthetic --host 127.0.0.1 --port 0`
> 3. Verify static assets return HTTP 200 with recorded hashes
> 4. Verify the synthetic API responds correctly (state, capture, solve, goto, etc.)
> 5. Verify all actions are dry-run only (no physical Polaris contact)
> 6. Run mypy: `python -m mypy src/polaris_harness/astro_webui/ --ignore-missing-imports`
> 7. Confirm no imports from `polaris_harness.engine`, `polaris_harness.runtime`, or `polaris_harness.transport` exist inside `src/polaris_harness/astro_webui/`
> 8. Report any failures with the full error output

### Key constraints (from issue #20)

- **Dry-run only**: all motion and firmware actions are simulated; no physical Polaris or camera contact
- **No Benro Connect APK or decompiled assets** in the public harness
- **Rate control remains explicitly unavailable** unless a separately qualified contract is supplied
- **All scenarios labelled `source: synthetic`** — never presented as observed camera behaviour
- **Independent component** — no coupling to protocol engine or runtime verifier

## SD Card Emulation — Agent Usage Guide

Emulates a Benro-shaped FAT32 SD card and runs the firmware-update flow against
it. No device contact: nothing is mounted, flashed, or rebooted.

### What it provides

- **`sd_emulator.image.SDCardImage`** — sparse-backed FAT32 volume (boot sector,
  FSINFO, backup boot sector, LFN entries, cluster chains). Sectors live in a
  dict, so a 32 GiB card costs only the sectors actually written.
- **`sd_emulator.packet.FirmwarePacket`** — parses `FwPkt.zip` / `OmsPkt.zip`,
  verifies every component against `firmwareInfo`, and compares dotted versions
  numerically.
- **`sd_emulator.device.SimulatedDevice`** — the update flow with every failure
  path reported as a distinct outcome.
- **`sd_emulator.builder.build_benro_card`** — one place that knows what a Benro
  card looks like, shared by tests and the CLI.

### How other agents should use it

```bash
# Inspect the card shape
python -m polaris_harness.cli sd-emulate --list

# Run an update against a real packet (verified working: 89 MB in ~1 s)
python -m polaris_harness.cli sd-emulate \
    --packet /path/to/FwPkt.zip --current-version 4.0.0.32 --verbose

# Reproduce the both-packets-present condition seen on a real card
python -m polaris_harness.cli sd-emulate \
    --packet FwPkt.zip --oms-packet OmsPkt.zip

# Machine-readable report
python -m polaris_harness.cli sd-emulate --packet FwPkt.zip --output report.json
```

```python
from polaris_harness.sd_emulator import build_benro_card, run_update

card = build_benro_card(total_bytes=64 * 1024 * 1024,
                        packets=[("FwPkt.zip", payload)])
report = run_update(card, current_version="4.0.0.32")
assert report.succeeded and report.installed_version == "6.0.0.54.90"
```

### Exit codes

| code | meaning |
|---|---|
| 0 | update succeeded, or `--list` |
| 2 | setup problem: missing file, packet not named `FwPkt.zip`/`OmsPkt.zip`, no packet on card, both packets present |
| 3 | behavioural rejection: version rejected, write failure |

Code 2 and 3 are deliberately distinguishable: a setup problem is not evidence
about device behaviour.

### What is evidence-grounded vs synthetic

Grounded in retained artefacts:
- card layout (mode directories `normal`, `focusStack`, `panorama`, `Lapse`,
  `sun`, `HDR`, `starskyStack`; `system/log/{Mlog,Clog,access,error}_NNNNNN.log`)
- packet layout and the `firmwareInfo` `<name> size:<n>;<name> MD5:<hex>;` format
- NAND partition map from `FwPkt/camera/config` `mtdparts` (asserted against that
  file by a test)
- mount point `/app/sd`, seen in Mlog

Synthetic:
- packet *contents* in tests — real layout, real sizes, fabricated bytes. No
  proprietary firmware is committed.
- log file contents — names and location are real; bodies are placeholders.

### Two deliberate refusals (not bugs)

- **Both packets present → `ambiguous-packets`.** A retained snapshot held both
  `FwPkt.zip` and `OmsPkt.zip`, and a later capture had `OmsPkt.zip` removed.
  Nothing in the evidence records which the device prefers, so guessing would
  make a simulated result look like verified device behaviour.
- **Downgrades refused by default.** The device's downgrade policy is not
  recorded. Pass `--allow-downgrade` / `allow_downgrade=True` to override.

### Key constraints

- **Never claims device behaviour** for the two refusals above
- **MD5 is reproduced, not recommended** — it is what `firmwareInfo` declares, so
  the emulator checks exactly what the camera checks. SHA-256 is recorded
  separately for harness provenance.
- **No absolute host paths** in reports or committed sidecars
- **Independent component** — no coupling to protocol engine or runtime verifier

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
