# ARM User-Space Emulation for Stage-2 Lifecycle Tests

This document describes the ARM user-space emulation component added to address
[issue #15](https://github.com/ian-morgan99/benro-polaris-test-harness/issues/15).

## Purpose

Provide a real, repeatable ARM user-space emulator for relevant Stage-2/`pgphoto`
code paths, starting with the v90-era `capture_image_with_Bulb` capture lifecycle
needed by patcher issue #251.

The harness owns the emulator and test fixtures; callers such as the firmware
patcher consume its qualification result.

This replaces the current architectural assumption that the harness must never
emulate executable ARM code.

## Scope and Boundary

### In scope

- CPU/ABI emulation for exact, provenance-identified ARM binaries or extracted
  code regions, starting with the v90-era `capture_image_with_Bulb` / capture
  lifecycle needed by patcher issue #251.
- Stub external camera, libgphoto, OS, timer, and callback boundaries explicitly
  and record every call. This is **not** a Pentax/PTP or physical camera emulator.
- Independent harness component/CLI, not coupled to the Polaris protocol scenario
  engine or runtime verifier.
- Every run bound to input binary SHA-256, patcher commit, harness revision,
  emulator version, fixture/stub profile, command, and full machine-readable
  result.
- Fail closed on unknown binary hashes/layouts, unexpected syscalls/callbacks,
  unsupported instructions, missing stubs, timeouts, and unobserved side effects.
- Never patch or mutate the input artifact.

### Out of scope

- No privileged QEMU setup, host networking/bridge/iptables changes, or implicit
  system package installation.
- No Pentax/PTP protocol emulation.
- No physical camera emulation.
- No coupling to the Polaris protocol scenario engine or runtime verifier.

## Design

### Emulator

The emulator is built on [Unicorn](https://www.unicorn-engine.org/), a lightweight
multi-architecture CPU emulator framework. It runs in user space, requires no
elevated privileges, and no host networking changes.

Key properties:

- **ARM and Thumb mode** execution — `mode="arm"` or `mode="thumb"`. An unknown
  value is rejected at construction. The mode a run actually used is recorded in
  the contract on every path, including failures.
- **Instruction tracing** — every executed instruction is counted
- **Fail-closed hooks** — unsupported instructions, unexpected syscalls, missing
  stubs, and instruction limits all cause emulation to stop with an error result
- **Read-only input** — the binary is never patched or mutated
- **SHA-256 binding** — the input binary hash is computed before emulation and
  recorded in the result
- **Truthful failure records** — `mode`, `instructions_executed`, `stub_calls`
  and best-effort `final_registers` are populated on failure paths too, so a
  faulting run cannot report the defaults (`mode="thumb"`,
  `instructions_executed=0`) when it actually ran ARM and executed instructions.
  A failing record is the one a gate has to trust.

### Stub entry interception

`register_stub_entry(stub_name, address, function_name=None, abi_shape=None,
model="call-site")` intercepts execution at `address`, marshals AAPCS arguments,
dispatches the stub, writes the return value to `R0`, and resumes according to
`model`:

| `model` | `address` is | Resume point |
|---|---|---|
| `"call-site"` (default) | the `BL`/`BLX` instruction itself | the following instruction (`address + size`) |
| `"target"` | the callee — a PLT-style stub body the branch jumps to | the caller, via `LR` (honours ARM/Thumb interworking) |

Real position-independent binaries resolve external calls through PLT-style
targets, so `model="target"` is what real code needs. Registering a target under
call-site behaviour resumes inside the stub body instead of returning to the
caller. An unknown `model` raises `ValueError`.

`function_name` gives each entry a semantic identity (`"stop"`, `"close"`,
`"cancel"`, …) recorded in `stub_calls`. It defaults to `"aapcs_call"` for
compatibility, but lifecycle assertions filter on the real name, so entries used
by lifecycle cases must declare one. One stub may expose several identities at
different addresses.

### ABI shape

`StubProfile.abi_shape` maps stub name to a per-boundary signature and is
consumed by marshaling:

```python
StubProfile(
    name="lifecycle",
    camera_stub=camera_handler,
    abi_shape={
        "camera": {
            "arg_count": 6,          # words past R3 are read from the stack
            "pointer_args": [0],     # argument indices to dereference
            "deref_bytes": {0: 4},   # bytes to read at each pointer
        },
    },
)
```

Precedence is per-entry `abi_shape`, then the profile-level shape for that stub,
then the four-register default. A declared stack word or pointer that points at
unmapped memory raises rather than recording a partial argument list.

### Stubs

External boundaries are explicitly stubbed:

| Stub | Boundary |
|------|----------|
| `camera` | Camera device calls (stop, close, capture, cancel, etc.) |
| `libgphoto` | libgphoto2 library calls |
| `os` | OS syscalls |
| `timer` | Timer creation, expiry, cancellation |
| `callback` | Callback invocations (ordering, arguments, register state) |

Every stub invocation is recorded with arguments, return value, timestamp, and
CPU register snapshot. Stubs are registered via `StubRegistry` and fail closed
on unregistered stubs.

### Contract

Every emulation run produces a machine-readable JSON contract (`ArmEmulationContract`)
binding:

- `binary_sha256` — SHA-256 of the input binary
- `patcher_commit` — patcher commit SHA
- `harness_revision` — harness revision
- `emulator_version` — Unicorn version
- `fixture_stub_profile` — stub profile name
- `command` — command being emulated
- `result` — pass/fail/error outcome
- `exit_code` — process exit code
- `duration_ms` — emulation duration
- `instructions_executed` — number of instructions executed
- `mode` — the instruction mode the run actually used (`arm` or `thumb`)
- `stub_calls` — all recorded stub invocations
- `final_registers` — final CPU register state
- `error_message` — error details on failure

`mode`, `instructions_executed`, `stub_calls` and `final_registers` are set in a
`finally` block, so they are correct for pass, timeout, unsupported-instruction,
missing-stub and unexpected-syscall outcomes alike. Consumers must not treat a
zero `instructions_executed` as proof nothing ran without also checking `result`.

## CLI Usage

### Run all 9 lifecycle cases

```bash
polaris-harness arm-emulate --suite
```

### Emulate a single fixture

```bash
polaris-harness arm-emulate \
  --fixture tiny_fixture \
  --command capture_image_with_Bulb \
  --output result.json
```

### Options

| Option | Description | Default |
|--------|-------------|---------|
| `--suite` | Run all 9 #251 lifecycle test cases | — |
| `--fixture` | Fixture name to emulate | — |
| `--fixtures-dir` | Directory containing fixture binaries | `tests/fixtures/arm` |
| `--command` | Command being emulated | `capture_image_with_Bulb` |
| `--stub-profile` | Stub profile name | `default` |
| `--entry-address` | Entry point address | `0x1000` |
| `--patcher-commit` | Patcher commit SHA | `unknown` |
| `--harness-revision` | Harness revision | `0.1.0` |
| `--emulator-version` | Emulator version | `unknown` |
| `--output` | Output JSON contract file path | — |

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success / contract satisfied |
| 2 | Invalid CLI/config/schema |
| 3 | Scenario mismatch / assertion failure |
| 7 | Internal harness error |

## Lifecycle Test Cases

The 9 initial #251 lifecycle cases are implemented in
`src/polaris_harness/arm_emulator/lifecycle.py`:

| # | Case | What it proves |
|---|------|---------------|
| 1 | Normal held-Bulb completion + exactly one stop/close | Happy path, single stop |
| 2 | Explicit message-stop vs context-cancel as distinct routes | Two cancel paths distinguishable |
| 3 | Cancellation result surviving successful stop callback | Cancel result not lost |
| 4 | Stop failure preserving primary error without masking | Stop error not swallowed |
| 5 | Deferred timed capture cancelled before timer | Cancel cannot issue new `bulb=0` |
| 6 | Normal timer expiry still issuing intended capture | Timer expiry works |
| 7 | Callback ordering, register preservation, widget pointer lifetime | ABI correctness |
| 8 | Output publication, completion signaling, cleanup | Completion and cleanup |
| 9 | Ordinary Manual/non-B dispatch + non-Pentax control | Non-Bulb path works |

Tests must fail if a cancel edge becomes unconditional or if normal completion
takes the cancel path.

## Fixtures

### Tiny fixture (`tests/fixtures/arm/tiny_fixture.bin`)

A minimal ARM Thumb binary (`MOV R0, #1; BX LR`) that proves the emulator
executes instructions end-to-end. This is a synthetic fixture, not physical
evidence.

### Stage-2 region (`tests/fixtures/arm/stage2_region.bin`)

**NOT CHECKED IN** — The provenance-pinned #251 binary region must NOT be
published on GitHub. It is extracted read-only from `pgphoto.stage2ondisk`
in the private research repository. See
`tests/fixtures/arm/stage2_region.bin.placeholder` for the retrieval
procedure and provenance fields.

When available locally, it must be accompanied by full provenance:
SHA-256, patcher commit, harness revision, emulator version, fixture/stub
profile, and command.

## Evidence Hierarchy

ARM emulator evidence is distinct from other evidence types:

```text
unit / harness PASS (ARM emulator)
        -> software handles a known recorded ARM contract

direct libgphoto2 + physical camera PASS
        -> camera/library behaviour proven

Polaris-local + physical camera PASS
        -> firmware/runtime integration proven

OpenPolaris + Polaris + physical camera PASS
        -> end-to-end behaviour proven
```

An ARM emulator PASS proves only that the consumer correctly handles a known
ARM code contract. It does **not** prove physical camera compatibility.

## CI Integration

CI installs only pinned project dependencies (including `unicorn`) and runs
the same suite. Missing emulator support is a **hard failure** for this job,
not a silent skip.

```bash
polaris-harness arm-emulate --suite
```

## Deprecation

The legacy `scripts/setup_qemu_harness.sh` is deprecated and must not be run
as part of this implementation. It used privileged QEMU setup with host
networking/bridge/iptables changes, which violates the issue #15 constraints.

## Relationship to Patcher Issue #251

Patcher issue #251 is the first consumer of this ARM emulation platform. Its
known outstanding defects include:

- Cancellation-result loss in the shared stop callback
- Deferred `bulb=0` cancel path initiating a new capture

The harness issue #15 tracks the reusable emulator platform; #251 remains the
owner of the camera-specific fix and acceptance criteria.

No firmware candidate should be treated as qualified merely because emulator
tests pass; supervised physical K-3 III/K-1 II testing remains required where
available.
