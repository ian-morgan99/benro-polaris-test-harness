# Implementation Plan

## Objective

Turn the architecture into a sequence of small, reviewable work packages that can be handed to independent coding agents when capacity/tokens are available.

The plan intentionally front-loads contracts and test infrastructure before physical scenarios. Agents should not skip ahead by hard-coding a K-3 III trace directly into a bespoke server.

## Delivery principles

1. **Vertical slices over scaffolding dumps.** Each phase must leave a runnable/testable increment.
2. **Schema first for shared data.** Multiple agents must not independently invent scenario formats.
3. **No physical claim without provenance.** Synthetic fixtures are used for engine development until evidence-backed scenarios are ready.
4. **CLI is the integration boundary.** OpenPolaris and firmware-patcher should not depend on Python internals.
5. **One issue/PR = one coherent architectural concern.** Avoid mega-PRs spanning protocol engine, runtime verifier and consumer integration.
6. **Tests accompany behavior.** No scenario engine feature is complete without unit/contract coverage.
7. **Do not alter source evidence to make tests pass.** Fix parser/engine or create a new evidence-backed version.

---

# Phase 0 — Repository engineering baseline

## Goal

Make the repo safe for parallel agent work.

## Deliverables

- `pyproject.toml` with Python >=3.12;
- src-layout package `polaris_harness`;
- `pytest` test discovery;
- formatting/linting/type-check decisions documented;
- GitHub Actions workflow for unit/schema tests;
- `AGENTS.md` with non-negotiable evidence and repo-boundary rules;
- `.gitignore` and development instructions;
- stable CLI placeholder with `--version` and `--help`.

## Recommended quality tools

Keep initial tools conventional:

- `ruff` for lint/format;
- `mypy` or `pyright` only if introduced with a narrow configuration that does not create large annotation-only work;
- `pytest`;
- `jsonschema`;
- YAML dependency.

## Acceptance

A clean checkout can run:

```bash
python -m pip install -e .[dev]
pytest
polaris-harness --help
```

in CI.

## Parallelism

One agent only. This establishes conventions everyone else consumes.

---

# Phase 1 — Schema and model contracts

## Goal

Freeze a v1 machine-readable contract before multiple agents write fixtures.

## Work packages

### 1A. Scenario schema

Define JSON Schema for:

- identity/version/title;
- source/status;
- evidence reference;
- endpoint configuration;
- initial state;
- states/transitions;
- request matcher;
- emitted events;
- timing;
- assertions metadata;
- limitations.

### 1B. Evidence schema

Define:

- evidence ID;
- source type;
- physical provenance fields;
- source issue/URL;
- hashes;
- transformations/redactions;
- observed layer;
- confidence/status.

### 1C. Personality schema

Keep optional/incomplete fields legal. Unknown data must not require invented placeholders beyond explicit `unknown` values where useful.

### 1D. Runtime manifest schema

Define exact artifacts, hashes, source provenance, target ABI, expected paths, environment and resolution rules.

## Acceptance

- valid synthetic fixture passes;
- invalid source/status combinations fail;
- physical scenario without evidence reference fails;
- synthetic scenario falsely claiming physical provenance is rejected or flagged;
- schema versions are explicit.

## Parallelism

1A/1B can be one agent; 1C/1D can be another after the common versioning conventions are established.

---

# Phase 2 — Core protocol engine

## Goal

Implement issue #1 as a generic deterministic engine.

## Work packages

### 2A. Framing

Tests must cover:

- complete single frame;
- frame split across reads;
- two frames in one read;
- trailing partial frame;
- malformed/oversized frame handling;
- raw byte preservation.

Set an explicit maximum frame size to avoid unbounded buffering.

### 2B. Matcher

Implement exact bytes/text first. Add regex/structured matching only with tests and a clear scenario need.

Matching result should explain mismatches:

```text
expected exact frame X
received Y
first differing byte/position
state S
```

### 2C. State engine

Pure engine, transport-independent.

Given `(state, input_event)`, return deterministic actions/state transition. This should be heavily unit-tested without sockets.

### 2D. Clock

Create clock interface:

```text
now_ms()
sleep_until(logical_ms)
```

Implement virtual and real clocks.

### 2E. TCP server

Thin adapter around engine/framing.

### 2F. Execution ledger

NDJSON logger with stable event schema and monotonic `seq`.

## Acceptance

Synthetic smoke scenario runs both:

- in-process virtual time;
- as an external real TCP process.

Unexpected request produces exit code 3 and useful ledger output.

## Parallelism

- Agent A: framing + matcher.
- Agent B: state model + clock.
- Agent C: server + logging after interfaces from A/B are merged.

Avoid parallel implementation of the same core interfaces before schema/model contracts land.

---

# Phase 3 — Preview/media harness

## Goal

Implement an independent endpoint capable of reproducing known producer/listener failure classes.

## Work packages

- endpoint lifecycle controller;
- bind/unbind/rebind actions;
- accepted connection/no data;
- delayed bytes;
- corrupt/empty payload;
- valid generated JPEG fixture;
- repeated frames;
- deterministic listener restart.

## Acceptance

Tests prove the distinction between:

```text
connection refused
connection accepted/no bytes
zero logical payload
invalid JPEG
valid delayed JPEG
```

These must remain different ledger events/error classes.

## Parallelism

Can proceed independently once the generic scenario engine action API is stable.

---

# Phase 4 — Trace ingestion and evidence pipeline

## Goal

Make physical evidence conversion auditable and reproducible.

## Work packages

### 4A. Raw ingest

Support initial text/log formats we actually possess. Do not build speculative pcap support until a real capture requires it.

### 4B. Sanitization

Rules for:

- IP/MAC/credentials where present;
- user paths/file names;
- unrelated media metadata;
- retaining protocol-significant values.

Redaction must be deterministic and recorded.

### 4C. Normalization

Convert source logs into ordered events while preserving:

- original raw line/byte reference;
- timestamps where available;
- direction/channel;
- transformation notes.

### 4D. Hash/evidence record generation

Produce sidecar evidence record with SHA-256 for source-derived committed artifacts.

## Acceptance

Ingesting the same input with same options produces byte-identical normalized output and hashes.

## Parallelism

Can proceed alongside Phase 3 after schemas are final.

---

# Phase 5 — First physical protocol scenarios

## Goal

Turn known discoveries into durable tests.

## Order

### 5A. K-3 III asynchronous capture — issue #2

Why first: simple command/event sequence and high regression value.

Required scenario variants:

- observed physical delayed success;
- synthetic bounded timeout;
- synthetic duplicate shutter attempt while pending.

Do not globally interpret `-1005`; semantics are scenario-local.

### 5B. Preview failures — issue #3

Separate scenario IDs for listener absent vs producer empty vs delayed valid frame.

### 5C. K-1 II/K-3 III settings differential — issue #4

Use separate personalities and scenario cases. Do not fill unobserved K-1 II values from direct-libgphoto2 knowledge unless the scenario is explicitly modelling that layer.

## Acceptance

Each physical scenario has:

- schema-valid manifest;
- evidence ID;
- source issue;
- limitations;
- replay test;
- consumer-facing contract test;
- no unsupported compatibility claim.

---

# Phase 6 — Runtime verifier foundation

## Goal

Implement issue #5 independently of protocol server work.

## Work packages

### 6A. Runtime manifest loader

Validate target, artifacts, hashes, expected paths and environment.

### 6B. Filesystem verifier

Check existence/type/permissions/symlink target as relevant.

Must be read-only.

### 6C. ELF inspector

Parse `readelf` output through a controlled adapter. Keep subprocess invocation isolated so tests can fixture command output.

Check:

- class;
- machine;
- SONAME;
- NEEDED;
- RPATH/RUNPATH;
- version definitions/requirements;
- symbol presence.

### 6D. Resolution engine

Explicitly model lookup classes:

- dynamic linker shared-library lookup;
- camlib lookup via `CAMLIBS`;
- iolib lookup via `IOLIBS`;
- explicit `dlopen`/application-defined search paths where known;
- executable/wrapper resolution.

Do not pretend these are one universal search algorithm.

### 6E. Baseline comparison

Compare candidate manifest/report with last known-good artifact set and categorize:

```text
added
removed
hash changed
provenance changed
resolution winner changed
shadowed competing artifact changed
ABI/symbol contract changed
```

## Acceptance

Create synthetic fixture trees for:

- known-good consistent stack;
- missing `pgphoto` wrapper;
- wrong architecture library;
- missing versioned symbol;
- stale shadowing copy selected;
- expected path missing;
- hash/provenance mismatch.

Each must fail for the intended reason.

---

# Phase 7 — firmware-patcher CI integration

## Goal

Make runtime verification a real gate rather than a standalone tool.

## Required patcher-side changes

The patcher should emit a runtime manifest containing exact expected component paths/hashes/provenance.

Then CI/pre-release runs:

```bash
polaris-harness runtime verify \
  --root <staged-image-root> \
  --manifest <runtime-manifest.json> \
  --json runtime-verification.json
```

## Gate policy

Release candidate fails on:

- missing managed artifact;
- wrong architecture/ABI;
- unresolved required dependency/symbol;
- loader winner differs from intended path;
- missing source provenance/hash;
- launcher points at nonexistent target.

Shadowed stale copies should initially be explicit warnings or policy-configurable failures until current firmware layout is fully catalogued, then tighten.

## Evidence boundary

Passing this gate means **package/runtime contract verified**, not physical-camera verified.

---

# Phase 8 — OpenPolaris CI integration

## Goal

Run selected harness scenarios against the real OpenPolaris client/protocol layer.

## Design requirement

OpenPolaris needs an injectable Polaris host/endpoint configuration suitable for tests. Avoid test-only production branches that alter protocol behavior.

## CI tiers

### Tier 1 — parser/state contract

Fast tests, possibly in-process OpenPolaris protocol component against saved fixtures.

### Tier 2 — external harness

Start `polaris-harness serve` as a separate process and point OpenPolaris integration tests at localhost.

### Tier 3 — physical E2E

Manual/hardware qualification only; never required for ordinary PR CI.

## Initial gate scenarios

- K-3 III delayed capture success;
- settings values present;
- settings values absent without stale carry-over;
- preview listener absent;
- preview accepted/no data;
- delayed valid preview;
- destructive operation client guard.

---

# Phase 9 — Recorded-contract growth

Every future physical bug/feature should ask at closure:

> Did this teach us a stable consumer/runtime contract worth preserving in the harness?

If yes:

1. preserve source evidence;
2. create/update evidence record;
3. add smallest scenario;
4. add regression assertion;
5. cross-link owning issue;
6. retain correct repository ownership.

Do not add harness scenarios merely for issue-count completeness.

---

# Phase 10 — Optional later capabilities

Only after the core is useful:

- pcap ingestion;
- deterministic fuzz/mutation around recorded frames;
- property-based framing tests;
- scenario matrix runner;
- Docker image for consumers;
- Windows/macOS packaging if OpenPolaris CI requires it;
- coverage report mapping physical evidence -> scenarios -> consumer tests;
- web UI for interactive trace exploration.

None of these should delay Phases 0–8.

---

# Dependency graph

```text
Phase 0 baseline
      |
      v
Phase 1 schemas/models
      |
      +--------------------+
      |                    |
      v                    v
Phase 2 protocol core   Phase 6 runtime verifier
      |                    |
      v                    v
Phase 3 preview         Phase 7 patcher CI
      |
      +----------+
      |          |
      v          v
Phase 4 trace   Phase 8 OpenPolaris integration foundation
      |
      v
Phase 5 physical scenarios
      |
      v
Phase 8 scenario gates
```

The runtime verifier can progress substantially in parallel with the protocol harness after Phase 1.

---

# Suggested agent allocation when free tokens are available

## Agent A — Foundation

Own Phase 0. Stop after conventions/CI/CLI skeleton are merged.

## Agent B — Schemas

Own Phase 1 scenario/evidence schemas and validation tests.

## Agent C — Protocol pure core

Own framing, matcher and pure state engine only.

## Agent D — Transport/integration

Own async TCP server and NDJSON ledger after C's interfaces exist.

## Agent E — Preview

Own preview endpoint lifecycle after scenario actions are stable.

## Agent F — Evidence ingestion

Own trace ingest/sanitize/normalize.

## Agent G — Runtime verification

Own runtime manifest/filesystem/ELF/resolution work; can run mostly parallel to C–F.

## Agent H — Physical scenarios

Own conversion of known K-3 III/K-1 II evidence into scenario data only after schemas + protocol core exist.

## Agent I — OpenPolaris integration

Works in OpenPolaris, consuming released/commit-pinned harness CLI.

## Agent J — firmware-patcher integration

Works in firmware-patcher, generating runtime manifests and invoking verifier.

---

# Review checkpoints

Before moving past each checkpoint:

## Checkpoint A — after Phase 1

Confirm schemas do not encode unproven protocol assumptions.

## Checkpoint B — after Phase 2

Confirm engine is generic and synthetic smoke fixture contains no Pentax-specific code.

## Checkpoint C — before first physical scenario merge

Confirm evidence record and source issue are complete enough for the claimed status.

## Checkpoint D — before patcher gate activation

Run verifier against a known-good historical or deliberately constructed compatible fixture and known-bad fixtures.

## Checkpoint E — before OpenPolaris gate activation

Confirm a harness failure cannot be mistaken in CI wording for physical camera incompatibility.

---

# Definition of done for the initial programme

The first programme is complete when all of the following are true:

1. harness package/CLI runs in CI;
2. scenario/evidence schemas are validated;
3. generic deterministic TCP/state engine exists;
4. preview lifecycle can be modelled;
5. K-3 III delayed capture is a physical evidence-backed scenario;
6. K-1 II vs K-3 III setting differential is represented without invented values;
7. runtime verifier detects the classes of packaging defect already encountered;
8. firmware-patcher can invoke runtime verification before hardware qualification;
9. OpenPolaris can run selected protocol scenarios in CI;
10. documentation/CI wording preserves the evidence hierarchy.