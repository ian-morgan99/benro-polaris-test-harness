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
| 4 | Every evaluated assertion held, but some could not be evaluated against the artifact |
| 7 | Internal harness error |

Exit 3 takes precedence over 4. Exit 4 exists so that unverified coverage cannot
look like success in CI: a PARTIAL case contradicted nothing and verified nothing.

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
profile, and command. It also needs a `stage2_region.json` sidecar declaring
`mode: "arm"`, the entry address and the `stub_entries` for every external
call - see [Fixture execution metadata](#fixture-execution-metadata). Without
those the region is decoded as Thumb and faults immediately.

### Two pinned regions, two build lines

Both candidates live in the PrivateResearch repo under `firmware-packets/` when
published, and both are pinned by a committed sidecar carrying coordinates and
hashes only - never the bytes.

| fixture | firmware | build line | pgphoto.stage2ondisk |
|---|---|---|---|
| `stage2_region` | 6.0.0.54.90 | **main** | `8973d948…c8d9eaac` |
| `stage2_region_v91` | 6.0.0.54.91 | **astro** | `b4a51cb7…384b28e4` |

They are **not interchangeable**. The two `pgphoto.stage2ondisk` files differ at
identical size (7,801,576 bytes), so a measurement taken on one does not transfer
to the other. `stage2_region` is retained as the main-build regression reference,
not as a stand-in for the Astro candidate.

The Astro candidate is **not accepted**. Its packet README requires that the
standard camera owner accept the shared baseline and that patcher issue #251 be
closed or explicitly waived before install; neither has happened. Nothing measured
against it qualifies it for deployment, and no emulator result qualifies any
candidate.

`stage2_region_v91` is currently pinned but its bytes are not obtainable here: the
candidate ships `pgphoto.stage2ondisk` inside `FwPkt/camera/appfs.ubifs`, a UBI
image with compressed payload, and no UBIFS reader is installed. The sidecar names
the exact missing path (`/app/lib/stage2/pgphoto.stage2ondisk`), its hash, and the
retrieval procedure. Discovery reports it as blocked rather than absent, so the
lifecycle suite skips it with that reason instead of silently testing nothing.

## Fixture execution metadata

A fixture's sidecar `*.json` may declare the parameters needed to execute it.
`FixtureManager.to_emulation_config()` reads them, so a real ARM region loads in
ARM mode at its recorded entry address instead of silently defaulting to Thumb
at `0x1000` (which faults on instruction one). Explicit arguments and CLI flags
always override sidecar values.

```json
{
  "provenance": {
    "mode": "arm",
    "entry_address": "0x101aac",
    "code_address": "0x101aac",
    "code_size": 65536,
    "max_instructions": 200000,
    "stub_entries": [
      {"address": "0x10f00", "stub_name": "camera", "function_name": "stop",
       "model": "target"},
      {"address": "0x10f04", "stub_name": "camera", "function_name": "close",
       "model": "target"}
    ]
  }
}
```

Addresses may be integers or hex strings, so they can be copied straight from
the provenance notes. `code_address` must contain both the entry address and
every stub entry address - the emulator maps exactly one code region.

- `mode` — `"arm"` or `"thumb"`; an unknown value raises rather than running.
- `entry_address` / `code_address` / `code_size` / `max_instructions` — execution
  parameters for the region.
- `stub_entries` — addresses to intercept, each with a semantic `function_name`
  and an interception `model`. Lifecycle assertions filter on those names, so a
  fixture used by lifecycle cases must declare them; without entries, external
  calls execute as real code instead of reaching stubs. An entry naming an
  unregistered stub raises (fail closed).

`tests/fixtures/arm/lifecycle_probe_fixture.{bin,json}` is a hand-assembled ARM
fixture that exercises this path and is committed - it contains no firmware.

The lifecycle suite targets `stage2_region` by default. When the binary is absent
locally every case reports **SKIP** and the CLI exits **0**; when it is present
the cases execute it, a behavioural failure exits **3**, and a case whose
assertions the artifact cannot answer reports **PARTIAL** (exit **4**) rather than
PASS.

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

**The harness cannot currently qualify #251, and the blocker is runtime state
the artifact needs rather than an engine limit.** An earlier revision of this
document recorded `UC_ERR_INSN_INVALID` at `0x101bd0` as a terminal VFP/NEON
engine limit. That was wrong: Unicorn exposes `UC_ARM_REG_FPEXC`, and setting bit
30 (`FPEXC.EN`) makes the same instruction execute with correct semantics —
`vmov.i32 q8,#0` zeroes Q8 instead of faulting. A real ARM Linux process has the
FPU enabled at entry because the kernel enables it, so this was uninitialised
harness CPU state. `EmulationConfig.enable_fpu` (default True) sets it while
establishing entry state.

With the FPU enabled and `.got`/`.data` seeded from the retained binary,
`capture_image_with_Bulb` executes **275 instructions** and reaches **21 real
boundary calls**, including `wait_and_handle_event`. It then faults at `0x102090`
on `ldr r3,[r3,#0xd0]`: the preceding load resolves `.got` slot `0x2f7564`, which
holds `0x3787dc`, the `sp_camera` symbol in `.bss`. `sp_camera` is populated by
runtime camera initialisation, so its file value is zero and the dereference yields
`0xd0`.

That fault is cleared. `sp_camera`'s pointee is a runtime allocation that exists in
no file, so it cannot be restored the way `.got`/`.data` can; it is supplied by an
`EnvironmentFixture`, which is deliberately a different type from `DataRegion` and
`MemoryPatch`. Seeding it takes execution past the gate at `0x102088` into the event
loop at `0x101f6c`, reaching 5001 instructions (the instruction limit) with 190
boundary calls instead of faulting at 275. Both measurements are retained in the
fixture sidecar, because the difference between them is the evidence that the
fixture is what clears the gate.

### What counts as legitimate emulator state

A source-derived, documented runtime fixture is legitimate emulator state. An
arbitrary value chosen to make an assertion pass is not. The distinction is not
"restored versus synthesised" — it is whether the value follows from evidence and
whether the run says so.

- `DataRegion` and `MemoryPatch` restore bytes that exist in the retained binary.
  They are restoration of artifact content.
- `EnvironmentFixture` supplies a runtime allocation. It is an assumption about
  process state, recorded separately so a reader of the run manifest can tell
  restored evidence from a seeded assumption.
- The size and shape come from the artifact itself: `sp_camera_creat` at `0x1817c`
  allocates via `movw r0,#0x1520; bl mallocz`, so the object is 5408 bytes. The
  address is written to the `.bss` global `0x3787dc`, **not** to the `.got` slot
  `0x2f7564`. Writing the GOT slot would collapse the artifact's two-step lookup
  (`GOT slot -> .bss global -> heap object`) and bypass the real global, producing
  path coverage of a process that never existed. Setup asserts the GOT slot is
  unchanged and resolves the full chain before the run starts.
- Field values are limited to what is evidenced. Only `+0xd0` is set, because that
  is the offset the gate reads. The rest of `_gp_camera`'s layout is not guessed.

What remains impermissible is picking a value because it makes a test green. When
the field or layout needed is unknown, name the exact missing field and leave the
case PARTIAL.

That gap is owned by the patcher repo per `OWNERSHIP-AND-ISOLATION.md` and is
filed as **patcher issue #258**. It has since been closed in the harness: the
pointee is supplied by an `EnvironmentFixture` derived from the artifact's own
allocation sequence, described above.

### Blockers to qualifying #251, with evidence

Three gaps remain. All three were verified directly rather than inferred, and all
three are questions for the patcher owner rather than harness work.

**1. `sp_camera` is zero at entry (cases 5-9).** `.got` slot `0x2f7564` holds
`0x3787dc`, the `sp_camera` symbol in `.bss`. It carries no dynamic relocation, so
the file value is final and Unicorn's zero-fill matches what the process has at
entry. The dereference yields `0xd0`. Populated by runtime camera initialisation.

**2. `gp_camera_exit` needs a valid context (cases 1-3, close edge).** The close
edge is real: disassembling the function resolves `gp_port_close` as a direct BL
target. Pinning must start at **`0x278e0`**, not the symbol address `0x278d4`, which
is an 8-byte hook trampoline:
```armasm
0x278d4: ldr ip, [pc]     ; ip = word at 0x278dc = 0x30000014
0x278d8: ldr pc, [ip]     ; jumps through an address in neither PT_LOAD
0x278e0: str r0, [fp,-0x10]  <- real prologue; pin from here
```

`0x30000014` lies outside both `PT_LOAD` segments (`0x10000..0x2df924`,
`0x2f0488..0x37cea4`) and has no relocation, so it is written by the patch loader at
runtime. The trampoline contributes no BL sites, which is why disassembling from
either address yields an identical edge list.

Pinning from `0x278e0` executes — `gp_log` is observed — then faults at `0x27968`:

```armasm
0x2795c: ldr r3, [fp, #-0x10]   ; r3 = camera context (caller argument)
0x27960: ldr r3, [r3, #0x10]    ; r3 = ctx->internal  == 0 in a synthetic buffer
0x27964: add r3, r3, #0x1000
0x27968: ldrb r3, [r3, #0x1d4]  ; FAULT at 0x11d4
```

Reaching `gp_port_close` therefore requires a `gp_camera` context with the correct
internal layout. That is libgphoto2's private `struct _gp_camera`, and its layout is
version-dependent. Fabricating it risks the worst outcome available here: a wrong
layout silently takes a different branch and produces a **green test asserting
something false**. PARTIAL reports "unverified", which is strictly better than a
fixture that reports "verified" wrongly.

**`gp_camera_exit`/`gp_port_close` is not the Bulb shutter-close operation.** It is
libgphoto2's camera teardown and port shutdown. Observing it says the session was
torn down; it says nothing about whether a physical shutter closed. A close edge in
an emulator trace must never be reported as shutter closure, and no emulator result
qualifies deployment. The Bulb stop path is `stopCaptureImageForMessage`, which is a
different call site with different semantics.

**3. The cancel mechanism is unidentified (cases 1-3, cancel edge).** All 26 callers
of `gp_context_cancel` inside `pgphoto.stage2ondisk` are `gp_filesystem_*` or folder
helpers — none sits on a capture lifecycle path.

That scan covers only the pinned binary and **cannot establish that the mechanism is
absent**. Camera drivers are loaded dynamically, so code outside this ELF can invoke
the same function. Verified directly: a `ptp2.so` from a firmware tree on this host
(`openpolaris-research/BenroPolarisPatcher/out/k1ii-k3iii-fixed/stage2-ondisk/`
`libgphoto2/2.5.34/ptp2.so`, 877,348 bytes, SHA-256 `bdc91554c38d0e59…`) lists
`gp_context_cancel` in its dynamic symbol table as an **undefined (imported)**
symbol, resolved at load time against the host binary. A camlib therefore does call
it, and a pgphoto-only caller scan is not sufficient evidence either way.

Provenance caveat: that copy is **not** the candidate's camlib. The candidate
manifest records `ptp2.so` as 1,198,944 bytes with SHA-256
`1c98e162504021b4…`, and no copy on this host matches it. So the finding establishes
that dynamically loaded drivers import and call `gp_context_cancel` — which is what
makes a pgphoto-only scan insufficient — but it says nothing specific about the
candidate's build. Confirming that requires extracting the candidate's own camlibs,
which is blocked by the same missing UBIFS reader.

The correct conclusion is narrower than the earlier one: within `pgphoto` no caller
sits on a capture lifecycle path, and the dynamically loaded paths have not been
analysed for this candidate. Pinning a filesystem-helper caller would still assert on
a mechanism unrelated to the defect under test, so the case stays PARTIAL.

The harness deliberately does not guess at any of these. Each is recorded as
not-evaluated so the gap stays visible.

Separately, disassembling `captureBulbImage` shows it contains a stop edge but no
close or cancel edge, so cases 1-3 report PARTIAL. No function in the binary has
both. Qualifying those edges means pinning the functions that own them (for
example `gp_context_cancel` at `0x358d0`) rather than expecting one region to
cover all nine cases.

The harness issue #15 tracks the reusable emulator platform; #251 remains the
owner of the camera-specific fix and acceptance criteria.

No firmware candidate should be treated as qualified merely because emulator
tests pass; supervised physical K-3 III/K-1 II testing remains required where
available.
