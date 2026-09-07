# V1 Contracts

## Purpose

This document fixes the first implementation contracts tightly enough that independent agents can build compatible components. It is intentionally narrower than the full roadmap.

The v1 target is:

- one deterministic command TCP endpoint;
- optional independent preview endpoint;
- data-driven state machine;
- exact/pattern request matching;
- real or virtual timing;
- NDJSON execution ledger;
- schema-validated scenario/evidence files;
- read-only runtime/package verifier.

Anything outside that list needs a separate issue/design decision.

---

# 1. Scenario package contract

Each scenario is a directory:

```text
scenarios/<scenario-id>/
  scenario.yaml
  README.md
  evidence.yaml            # required for physical/derived scenarios
  fixtures/                # optional binary/text payloads
```

A scenario must be self-contained except for reusable personality references explicitly declared by ID/version.

## 1.1 Scenario ID

Format:

```text
[a-z0-9][a-z0-9-]{2,79}
```

IDs are stable. Do not rename an established physical scenario merely for aesthetics.

## 1.2 Scenario YAML conceptual shape

```yaml
schema_version: 1
id: synthetic-smoke-echo
version: 1
title: Synthetic exact-match protocol smoke test
source: synthetic
status: synthetic

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
      - id: ping
        match:
          type: exact_text
          value: "PING#"
          encoding: utf-8
        actions:
          - type: send
            endpoint: command
            after_ms: 0
            text: "PONG#"
        next_state: idle

limits:
  max_frame_bytes: 65536
  max_connections: 1
  scenario_timeout_ms: 10000
```

## 1.3 State/event model

V1 input event types:

```text
connection_opened
connection_closed
request
scheduled_timer
```

V1 action types:

```text
send
close_connection
set_endpoint_available
emit_marker
transition
```

`transition` may be represented as `next_state` on a rule; choose one canonical persisted representation in the schema.

Do not add arbitrary embedded Python callbacks to scenario data.

## 1.4 Request matching

V1 matcher types:

### exact_bytes

```yaml
match:
  type: exact_bytes
  hex: "50494e4723"
```

### exact_text

```yaml
match:
  type: exact_text
  value: "PING#"
  encoding: utf-8
```

### regex_text

```yaml
match:
  type: regex_text
  pattern: '^1&264&4&state:1;bulb:0;c:-1;#$'
  encoding: utf-8
  rationale: "Example only; physical regex requires evidenced variable fields"
```

For physical scenarios, `regex_text` requires non-empty `rationale`.

No structured Polaris field parser is required for v1. Add it later only when evidenced protocol work shows a stable grammar worth encoding.

## 1.5 Framing

V1 framing types:

```text
delimiter
fixed_length
raw_connection
```

Start with `delimiter`; implement others only if tests/scenarios require them.

Delimiter framing rules:

- delimiter bytes belong to the emitted frame;
- multiple frames in one TCP read are split deterministically;
- partial trailing bytes remain buffered;
- exceeding `max_frame_bytes` before delimiter is a scenario/transport failure;
- raw received chunks are logged separately from framed messages if debug logging is enabled.

## 1.6 Timing

`after_ms` is relative to the triggering event unless a future schema explicitly introduces absolute logical timestamps.

Actions with the same deadline execute in YAML declaration order.

Virtual clock advances directly to the next scheduled event.

Real clock uses monotonic time only.

## 1.7 Endpoint availability

For preview/listener lifecycle tests, the scenario engine needs to represent endpoint availability independently of logical server state.

Conceptual action:

```yaml
- type: set_endpoint_available
  endpoint: preview
  available: false
```

When false, new connections must fail as closely as practical to a genuinely unbound listener. Implementation should prefer actual socket unbind/rebind for external integration tests rather than accepting and returning a synthetic application error.

---

# 2. Evidence contract

Physical or derived scenarios require `evidence.yaml`.

Conceptual v1 shape:

```yaml
schema_version: 1
evidence_id: ev-20260907-k3iii-capture-001
source: physical
status: confirmed
observed_date: 2026-09-07
observed_layer: polaris-protocol
source_issue: ian-morgan99/benro-polaris-firmware-patcher#37

camera:
  manufacturer: Pentax
  model: K-3 III
  firmware: unknown
  usb_mode: MTP
  usb_id: "25fb:0189"

polaris:
  firmware: "6.0.0.54"
  patcher_sha: af5b0d3
  fwpkt_sha256: 61a000cf62d911b8494c9f70e7a3d82775223acc6ca98b407601651cd6a4f02a

libgphoto2:
  sha: 6aa3e4e66240d4b4d68a65b75631e0f6aadf308a

artifacts:
  - path: fixtures/request-response.ndjson
    sha256: <exact hash>
    transformation: normalized-from-source-log

transformations:
  - "Removed unrelated shell prompt text"
  - "Preserved request/response bytes and ordering"

limitations:
  - "Recorded timing is representative, not a universal timing guarantee"
```

Rules:

- SHA fields are full hashes where available;
- `unknown` is valid and preferable to inference;
- physical evidence cannot omit `observed_layer` and `source_issue`;
- if an artifact was transformed, transformation must be stated;
- sanitization must not hide protocol-significant bytes without a documented placeholder strategy.

---

# 3. Execution ledger contract

Every run produces NDJSON.

Each line has:

```json
{
  "ledger_schema_version": 1,
  "seq": 1,
  "run_id": "uuid-or-stable-generated-id",
  "scenario_id": "synthetic-smoke-echo",
  "scenario_version": 1,
  "evidence_id": null,
  "clock": "virtual",
  "t_ms": 0,
  "endpoint": "command",
  "connection_id": "c1",
  "direction": "client_to_device",
  "event": "frame_received",
  "raw_hex": "50494e4723",
  "text": "PING#",
  "state_before": "idle",
  "state_after": "idle"
}
```

Required rules:

- `seq` starts at 1 and increments by one;
- `t_ms` is logical elapsed milliseconds from scenario start;
- raw bytes are canonical; `text` is optional derived representation;
- state before/after is present for engine-handled events;
- errors are ledger events before process exit where possible;
- external integration can specify an output path; default can be stderr/stdout plus temp file as CLI design decides.

Do not put secrets or full environment dumps into the ledger.

---

# 4. Runner control contract

## Serve

```bash
polaris-harness serve --scenario <path-or-id>
```

Default behavior:

- validate schema/evidence before binding;
- bind loopback only;
- port `0` means OS-assigned ephemeral port;
- print a single machine-readable readiness record containing actual endpoint addresses;
- then execute until scenario completes/fails or external termination.

Recommended readiness line on stdout:

```json
{"event":"ready","scenario_id":"synthetic-smoke-echo","endpoints":{"command":{"host":"127.0.0.1","port":43117}}}
```

All execution diagnostics should go to stderr or explicit ledger file so consumers can parse readiness safely.

## Validate

```bash
polaris-harness validate scenarios/
```

Validates all discovered scenario/evidence/personality documents and cross-references. It performs no network activity.

---

# 5. Runtime manifest v1

The firmware patcher should eventually generate a manifest similar to:

```yaml
schema_version: 1
target:
  os: linux
  machine: ARM
  elf_class: ELF32

root: /app

environment:
  CAMLIBS: /app/lib/stage2/libgphoto2/2.5.34
  IOLIBS: /app/lib/stage2/libgphoto2_port/0.12.2
  LD_LIBRARY_PATH: /app/lib/stage2:/app/lib
  LD_PRELOAD: /app/lib/stage2/libpolaris_stage2.so

artifacts:
  - id: libgphoto2-core
    path: /app/lib/stage2/libgphoto2.so.6
    type: elf_shared
    sha256: <hash>
    source:
      repository: ian-morgan99/libgphoto2
      commit: <full-sha>
    expected:
      soname: libgphoto2.so.6
      required_exports: []

  - id: libgphoto2-port
    path: /app/lib/stage2/libgphoto2_port.so.12
    type: elf_shared
    sha256: <hash>
    source:
      repository: ian-morgan99/libgphoto2
      commit: <full-sha>
    expected:
      soname: libgphoto2_port.so.12
      required_symbol_versions:
        - LIBGPHOTO2_5_0

  - id: ptp2
    path: /app/lib/stage2/libgphoto2/2.5.34/ptp2.so
    type: elf_shared
    sha256: <hash>
    source:
      repository: ian-morgan99/libgphoto2
      commit: <full-sha>

  - id: usb1
    path: /app/lib/stage2/libgphoto2_port/0.12.2/usb1.so
    type: elf_shared
    sha256: <hash>
    source:
      repository: ian-morgan99/libgphoto2
      commit: <full-sha>

launchers:
  - path: /app/bin/pgphoto
    expected_target: /app/lib/stage2/pgphoto.stage2ondisk

resolution_expectations:
  - component: libgphoto2_port.so.12
    expected_selected_path: /app/lib/stage2/libgphoto2_port.so.12
  - component: ptp2
    mechanism: CAMLIBS
    expected_selected_path: /app/lib/stage2/libgphoto2/2.5.34/ptp2.so
  - component: usb1
    mechanism: IOLIBS
    expected_selected_path: /app/lib/stage2/libgphoto2_port/0.12.2/usb1.so
```

The exact target architecture values must be established from known-good firmware evidence; do not copy illustrative values without verification.

---

# 6. Runtime verification result contract

`runtime verify --json result.json` produces:

```json
{
  "report_schema_version": 1,
  "result": "pass",
  "checks": [],
  "components": [],
  "warnings": []
}
```

Each failed check has stable machine-readable `code`, e.g.:

```text
FILE_MISSING
HASH_MISMATCH
ELF_CLASS_MISMATCH
ELF_MACHINE_MISMATCH
SONAME_MISMATCH
DT_NEEDED_UNRESOLVED
SYMBOL_MISSING
SYMBOL_VERSION_MISSING
LAUNCH_TARGET_MISSING
RESOLUTION_WINNER_MISMATCH
PROVENANCE_MISSING
SHADOWED_ARTIFACT
```

Do not make CI depend on prose parsing.

---

# 7. Configuration precedence

V1 configuration precedence should be:

```text
CLI explicit flags
  > scenario/manifest values
  > documented defaults
```

Environment variables may be added for CI ergonomics later, but they must not silently override scenario semantics.

---

# 8. Compatibility rules

- unknown schema version: fail closed;
- unknown action/matcher type: fail closed;
- extra schema fields: decide explicitly per object; default to rejecting unexpected persisted fields in v1 to catch typos;
- new optional fields may be added within schema v1 only if old readers can safely ignore them; otherwise increment schema version;
- physical scenario behavioral change increments scenario `version`.

---

# 9. Test contract

Before issue #1 can close, tests must prove:

1. schema validation before server start;
2. exact text frame split across two TCP sends still matches once;
3. two complete frames in one TCP send are processed in order;
4. unexpected request fails loudly;
5. virtual time preserves action order;
6. real-time serve prints readiness address;
7. NDJSON ledger is ordered and includes raw bytes;
8. server binds only loopback by default;
9. Ctrl-C/termination shuts sockets cleanly;
10. no Pentax/body-specific code exists in generic protocol engine.
