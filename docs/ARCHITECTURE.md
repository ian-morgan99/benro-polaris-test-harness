# Architecture

## Purpose

This document turns the repository-level intent into an implementation architecture that multiple independent agents can follow without redefining the design.

The harness has two deliberately separate products:

1. **Protocol harness** — emulates the observable Polaris-facing network contract so clients such as OpenPolaris can be regression-tested deterministically.
2. **Runtime verifier** — validates firmware-package/runtime assumptions such as `/app` layout, loader resolution, ELF/ABI compatibility, provenance and executable launch paths.

They share evidence, schemas, logging and CI conventions, but they must not be coupled so tightly that one cannot be used independently.

## Non-goals

The project is not a camera emulator, libgphoto2 replacement, firmware simulator or proof of physical compatibility.

It must not:

- implement Pentax PTP behaviour merely because it is convenient;
- infer missing Polaris responses from OpenPolaris expectations;
- require a physical camera or Polaris for normal CI;
- silently rewrite captured traces into a cleaner protocol than hardware actually emitted;
- allow scenario fixtures to become an alternative source of truth over physical evidence.

## Recommended implementation language

Use **Python 3.12+** for the first implementation.

Reasons:

- deterministic async TCP/HTTP servers are straightforward with the standard library/`asyncio`;
- YAML/JSON schema tooling is mature;
- ELF/runtime verification can invoke `readelf`, `objdump`, `file` and `ldd`-style tooling where appropriate without embedding a linker;
- pytest is well suited to deterministic scenario tests;
- OpenPolaris and patcher CI can invoke the harness as a process/CLI without language coupling;
- rapid implementation matters more than maximum throughput;
- no part of the expected load requires a high-performance server.

Avoid a framework-heavy design. The initial dependency budget should be small and explicit:

- `PyYAML` or `ruamel.yaml` for manifests;
- `jsonschema` for schema validation;
- `pytest` for tests;
- standard-library `asyncio`, `socket`, `http.server`/minimal HTTP implementation where practical.

Add dependencies only when there is a concrete requirement.

## Top-level component model

```text
                         +---------------------+
                         | Evidence / traces   |
                         | manifests + hashes  |
                         +----------+----------+
                                    |
                                    v
+----------------+        +---------+----------+        +------------------+
| OpenPolaris or | <----> | Protocol Harness  | <----> | Execution ledger |
| other consumer |        | runner + endpoints|        | structured logs  |
+----------------+        +---------+----------+        +------------------+
                                    |
                                    v
                         +----------+-----------+
                         | Scenario engine      |
                         | match/state/schedule |
                         +----------------------+

+----------------------+       +-----------------------+
| Firmware-patcher CI  | ----> | Runtime Verifier      |
+----------------------+       | fs/ELF/loader/prov.   |
                               +-----------+-----------+
                                           |
                                           v
                               +-----------+-----------+
                               | Runtime manifest/report|
                               +-----------------------+
```

## Repository layout

Target layout:

```text
benro-polaris-test-harness/
├── pyproject.toml
├── README.md
├── AGENTS.md
├── src/polaris_harness/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── logging.py
│   ├── protocol/
│   │   ├── framing.py
│   │   ├── matcher.py
│   │   ├── server.py
│   │   ├── preview.py
│   │   └── transport.py
│   ├── scenario/
│   │   ├── loader.py
│   │   ├── model.py
│   │   ├── engine.py
│   │   ├── clock.py
│   │   └── validation.py
│   ├── trace/
│   │   ├── ingest.py
│   │   ├── normalize.py
│   │   └── redact.py
│   └── runtime/
│       ├── manifest.py
│       ├── filesystem.py
│       ├── elf.py
│       ├── loader.py
│       ├── provenance.py
│       └── report.py
├── schemas/
│   ├── scenario.schema.json
│   ├── personality.schema.json
│   ├── evidence.schema.json
│   └── runtime-manifest.schema.json
├── scenarios/
├── personalities/
├── traces/
├── runtime-fixtures/
├── tests/
│   ├── unit/
│   ├── protocol/
│   ├── scenarios/
│   ├── runtime/
│   └── contract/
└── docs/
```

Do not create directories with no immediate consumer merely to match this tree; use it as the ownership map.

## Command-line contract

The project should expose one stable executable:

```text
polaris-harness
```

Initial subcommands:

```text
polaris-harness validate <scenario-or-directory>
polaris-harness serve --scenario <id-or-path> [--host HOST] [--port PORT]
polaris-harness replay --scenario <id-or-path> --against HOST:PORT
polaris-harness trace ingest <input> --output <dir>
polaris-harness runtime verify --root <path> --manifest <file>
polaris-harness runtime report --root <path> --manifest <file> --json <out>
```

The CLI is the cross-repository integration boundary. Consumers must not import private Python internals as their primary integration method.

Exit-code contract:

```text
0  success / contract satisfied
2  invalid CLI/config/schema
3  scenario mismatch / unexpected request / assertion failure
4  transport/startup failure
5  runtime verification failure
6  provenance/evidence policy failure
7  internal harness error
```

Keep these codes stable once published.

## Protocol harness architecture

### 1. Transport

The transport layer owns sockets/connections only. It must not know camera semantics.

Responsibilities:

- bind configured TCP/HTTP endpoints;
- accept one or more connections as the scenario permits;
- preserve raw received bytes;
- deliver framed messages to the scenario engine;
- schedule outbound bytes/events requested by the engine;
- report connection lifecycle events.

### 2. Framing

Framing converts byte streams to protocol frames and back.

Requirements:

- preserve raw bytes for the execution ledger;
- distinguish incomplete frame vs malformed frame;
- support multiple frames in one TCP read;
- support one frame split over multiple TCP reads;
- do not assume one `recv()` equals one protocol message;
- framing rules are selectable/configurable by endpoint when evidence requires it.

For known textual Polaris command traffic, implement a framing strategy that recognizes the observed terminator while retaining exact raw payload bytes. Do not normalize whitespace, field order or encoding before raw evidence is logged.

### 3. Matcher

A scenario request matcher must support:

- `exact_bytes` — safest and default for physical traces;
- `exact_text` — explicit encoding then exact match;
- `regex_text` — only when an evidenced variable field requires it;
- `fields` — structured field matching only after a parser is evidence-backed;
- `any` — allowed only for explicitly synthetic scenarios or deliberately ignored noncontractual input.

Physical scenarios should prefer exact matching. Every wildcard/pattern must document why the ignored variability is non-semantic.

Unexpected client input fails the scenario by default.

### 4. Scenario engine

The engine is a deterministic state machine.

Inputs:

- connection events;
- framed client requests;
- elapsed virtual/real time;
- explicit test-control commands if enabled.

Outputs:

- outbound frames;
- preview/media events;
- connection close/rebind actions;
- state transitions;
- assertion/contract results.

State must be explicit in the scenario model rather than hidden in server implementation code.

Example conceptual model:

```yaml
initial_state: idle
states:
  idle:
    on:
      - request: capture
        transition: capture_pending
        emit:
          - after_ms: 12
            payload: "264@state:1#"
  capture_pending:
    emit:
      - after_ms: 2380
        payload: "264@state:-1005#"
      - after_ms: 3500
        event: image_ready
        transition: idle
```

The exact schema may differ, but state transitions must remain data-driven and testable.

### 5. Clock/timing model

Tests need two timing modes:

- **virtual time** for unit/scenario tests so multi-second hardware traces execute quickly and deterministically;
- **real time** for external consumer integration where OpenPolaris is a separate process expecting actual delays/timeouts.

The scenario must express timing in logical milliseconds/ranges. The runner chooses the clock implementation.

Never make correctness depend on CI scheduler precision at exact millisecond boundaries.

### 6. Preview/media endpoint

Preview must be independent from the command endpoint because the observed failure classes include listener absence, connection failure, empty producer output and delayed valid media.

The endpoint must be able to model independently:

- port not bound;
- connection accepted then closed;
- connection accepted but no bytes;
- zero-length logical frame;
- invalid/corrupt bytes;
- delayed valid JPEG;
- repeated valid JPEGs;
- listener termination/restart.

Fixtures should use small legally-owned/generated media, not copied proprietary/user images.

## Personality architecture

A personality is a reusable collection of evidenced capability fragments. It is not an executable camera simulator by itself.

Personality data should be composable by scenario:

```text
personality
  identity
  settings enumeration fragment
  capture fragment
  preview fragment
  known limitations
```

Rules:

- scenario evidence wins over generic personality defaults;
- unknown means unknown;
- body-specific differences are never filled from a sibling model;
- a personality can contain contradictory historical versions when firmware/path provenance differs; scenario selects the correct evidence version.

## Evidence ledger

Every physical scenario should resolve to one immutable evidence record containing:

- evidence ID;
- SHA-256 of committed normalized trace/fixture files;
- source issue/URL;
- observed date;
- camera identity;
- Polaris runtime provenance;
- libgphoto2 provenance where relevant;
- observed layer;
- transformation/sanitization notes;
- confidence/status.

The execution log should emit the evidence ID and scenario version at startup so CI results are auditable later.

## Runtime verifier architecture

The runtime verifier is not a fake Linux system. It is a deterministic static/dynamic-resolution checker over an extracted candidate filesystem tree.

### Inputs

- candidate root directory, normally an extracted/staged `/app` tree;
- expected runtime manifest generated by the firmware patcher;
- optional known-good baseline manifest;
- explicit target architecture/ABI.

### Checks

#### Filesystem contract

- required paths exist;
- launch wrappers and targets exist and are executable where relevant;
- configured CAMLIBS/IOLIBS paths exist;
- required duplicated deployment paths are populated;
- forbidden/stale paths can be detected by policy.

#### Hash/provenance contract

- SHA-256 each managed artifact;
- compare manifest expected hash/source component;
- fail on missing provenance rather than inferring by filename/version string.

#### ELF contract

Use `readelf`/equivalent to check:

- ELF class;
- machine architecture;
- SONAME;
- `DT_NEEDED`;
- RPATH/RUNPATH where present;
- exported and required versioned symbols;
- obvious stripped/stub anomalies when a required symbol set is known.

#### Resolution contract

Given the wrapper environment and known runtime search rules, produce a deterministic report of which candidate file would be selected for:

```text
libgphoto2.so.6
libgphoto2_port.so.12
ptp2.so
usb1.so
libpolaris_stage2.so
pgphoto target
```

Where real `dlopen` path order differs from ELF loader order, encode that explicitly rather than approximating it as `LD_LIBRARY_PATH` only.

The verifier should report both:

- **selected path**;
- **shadowed competing paths**.

A shadowed stale library is operationally important even if the desired path currently wins.

### Runtime report

Machine-readable JSON plus concise human output, for example:

```json
{
  "result": "fail",
  "target": "armv7-or-exact-target",
  "checks": [
    {
      "component": "libgphoto2_port.so.12",
      "status": "fail",
      "selected_path": "/app/lib/stage2/libgphoto2_port.so.12",
      "reason": "required symbol version LIBGPHOTO2_5_0 absent"
    }
  ]
}
```

The patcher CI should consume the JSON report rather than scrape human text.

## Logging contract

Every protocol execution should produce NDJSON records with monotonic sequence numbers.

Minimum fields:

```json
{
  "seq": 12,
  "t_ms": 2380,
  "scenario": "k3iii-capture-delayed-success",
  "scenario_version": 1,
  "evidence_id": "...",
  "channel": "command",
  "direction": "device_to_client",
  "event": "frame",
  "raw_hex": "...",
  "text": "264@state:-1005#",
  "state_before": "capture_pending",
  "state_after": "capture_pending"
}
```

Raw payload retention is mandatory where safe. Derived parsed fields are additive, not replacements.

## Determinism rules

A deterministic scenario must have:

- no random behavior unless seed is explicit and recorded;
- no wall-clock-date dependence;
- bounded transport/startup timeouts;
- stable ordering for events sharing the same logical timestamp;
- explicit connection-count policy;
- fail-fast behavior on unexpected input;
- no dependency on external internet/services;
- no hidden state persisted between runs unless scenario explicitly models persistence.

## Security/safety

- bind localhost by default;
- require explicit flag to bind non-loopback;
- never execute payload content as shell/code;
- sanitize traces before commit;
- destructive media scenarios use synthetic state by default;
- runtime verifier must not mutate the candidate tree;
- trace ingestion writes a new normalized copy; never rewrites source evidence in place.

## Cross-repository integration

### OpenPolaris

Treat `polaris-harness serve ...` as an external device fixture. OpenPolaris CI should configure host/ports through normal dependency injection/configuration, not compile harness internals into the app.

### firmware patcher

Treat `polaris-harness runtime verify ...` as a pre-release validation command. The patcher owns creation of the runtime manifest and extracted candidate tree; this repo owns validating that contract.

### libgphoto2

No direct release dependency. libgphoto2 evidence can seed scenarios only after direct physical validation; harness outcomes cannot approve libgphoto2 behavior.

## Versioning

Version independently:

- harness software version;
- scenario version;
- schema version;
- evidence record version;
- runtime manifest schema version.

A scenario behavior change based on new physical evidence increments the scenario version even if the harness software version is unchanged.

## First implementation slice

The first mergeable vertical slice should contain only:

1. package/CLI skeleton;
2. schema validation;
3. one synthetic exact-match TCP scenario;
4. deterministic state engine;
5. virtual-time unit test;
6. real-time external-server integration test;
7. NDJSON execution log;
8. GitHub Actions test job.

Do **not** begin with K-3 III complexity. Prove the engine with a clearly synthetic smoke fixture, then add physical scenarios as data.