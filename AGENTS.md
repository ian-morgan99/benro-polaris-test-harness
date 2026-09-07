# AGENTS.md

## Purpose

This repository is designed for work by multiple independent coding agents. This file is normative guidance for any agent modifying it.

Read before coding:

1. `README.md`
2. `docs/ARCHITECTURE.md`
3. `docs/IMPLEMENTATION-PLAN.md`
4. `docs/SCENARIO-FORMAT.md`
5. `docs/OWNERSHIP-AND-ISOLATION.md`

If an issue-specific instruction conflicts with these documents, stop and surface the conflict rather than silently changing the architecture.

## Non-negotiable evidence rule

**Recorded physical behaviour -> harness contract -> consumer regression test.**

Never reverse this flow.

Do not edit a physical scenario because a consumer implementation expects different behavior unless new physical evidence justifies a new scenario version.

A harness PASS is never proof of physical camera support.

## Repository boundary rule

This repo owns the harness implementation and fixtures, not the underlying defects being modelled.

- direct physical camera/libgphoto2 defects belong in `ian-morgan99/libgphoto2` when independently reproducible there;
- Stage-2/pgphoto/loader/firmware packaging defects belong in `ian-morgan99/benro-polaris-firmware-patcher`;
- client/protocol/UI/state defects belong in `ian-morgan99/OpenPolaris`;
- this repo may preserve a deterministic regression representation of any of those once evidenced.

Do not add appliance-specific workarounds to libgphoto2 through this project.

## Implementation target

Unless an approved issue changes it, use:

- Python 3.12+;
- src-layout package `polaris_harness`;
- one external CLI: `polaris-harness`;
- pytest for tests;
- JSON Schema for persisted contracts;
- YAML or JSON scenario authoring validated against the schema;
- NDJSON for execution ledgers.

Do not introduce a second implementation language for convenience.

## Dependency discipline

Prefer standard library. Every new runtime dependency must have a concrete requirement and be added in the PR description.

Do not add heavyweight web/server frameworks for a small deterministic TCP harness without explicit architectural approval.

## Architectural separation

Keep these boundaries explicit:

```text
transport -> framing -> matcher -> scenario engine -> actions
                               |
                               -> execution ledger

runtime manifest -> filesystem/ELF/resolution verifier -> report
```

The pure scenario engine must not depend on sockets.

The transport must not contain Pentax/K-3 III/K-1 II semantics.

The runtime verifier must not depend on the protocol server.

## Protocol rules

- TCP reads are byte streams, not messages.
- Always handle split frames and coalesced frames.
- Preserve raw bytes before parsing/normalization.
- Unexpected input fails a scenario unless explicitly allowed.
- Exact matching is the default for physical evidence.
- Wildcards/regexes require an explanation of why the ignored field is non-semantic.
- Never guess sequential opcodes or infer a body-specific value domain from another camera.

## Timing rules

Implement virtual and real clocks.

Unit/scenario tests should use virtual time where possible. External consumer integration may use real time.

Do not make CI correctness depend on exact wall-clock millisecond scheduling.

## Scenario rules

Every physical scenario must:

- validate against the current scenario schema;
- link an evidence record;
- name its source issue;
- identify observed layer;
- state limitations;
- keep unknown values unknown;
- contain no invented camera behavior.

Every synthetic scenario must clearly say `source: synthetic`.

Do not call synthetic data a camera personality unless it is clearly namespaced as test-only synthetic behavior.

## Evidence changes

When new hardware evidence contradicts an existing physical scenario:

1. preserve the old scenario/version when useful for backwards compatibility;
2. add/link the new evidence;
3. increment scenario version;
4. explain the changed contract;
5. update consumer tests intentionally.

Never silently rewrite history.

## Runtime verifier rules

The verifier is read-only.

It must distinguish:

- ELF dynamic linker lookup;
- CAMLIBS camlib selection;
- IOLIBS port backend selection;
- explicit application `dlopen`/search-path behavior where known;
- executable/wrapper target resolution.

Do not collapse them into one fake loader algorithm.

Report both selected and shadowed candidate paths where possible.

Missing provenance is a failure for a release-gated managed artifact; do not infer provenance from filenames.

## Testing requirements

A behavioral code change requires tests.

At minimum, protocol core tests should cover:

- split frame;
- coalesced frames;
- partial trailing frame;
- exact request match;
- informative mismatch;
- unexpected request failure;
- deterministic state transition;
- virtual-time scheduling;
- real TCP smoke test.

Runtime verifier changes need positive and negative fixtures.

## Safety

- bind loopback by default;
- non-loopback binding must be explicit;
- never execute captured payloads;
- do not mutate firmware trees under verification;
- do not include credentials/private paths/user media metadata in committed traces;
- destructive protocol tests should use synthetic/disposable state.

## Scope discipline

Do not implement future roadmap items merely because they are interesting.

In particular, avoid until specifically required:

- full camera/PTP emulation;
- speculative protocol opcode maps;
- pcap parser support without a real source capture needing it;
- web dashboard;
- Docker/service orchestration;
- fuzzing framework;
- broad refactors in unrelated components.

## PR discipline

Each PR should identify:

- issue/work package;
- architectural layer changed;
- persisted schema changes, if any;
- evidence/scenario changes, if any;
- tests added;
- external dependency added, if any;
- compatibility/migration consequence;
- what the PR explicitly does not prove.

A schema-changing PR should not be mixed with unrelated protocol behavior unless unavoidable.

## Handoff discipline

When stopping work, leave an issue/PR comment with:

- exact commit/branch;
- completed acceptance items;
- failing tests or unresolved decisions;
- next smallest task;
- any assumptions that still require physical evidence.

Do not leave only a narrative like "mostly done".

## Stop conditions

Stop and request architectural review rather than guessing if:

- physical traces contradict each other and provenance cannot explain why;
- a scenario requires inventing an unknown body-specific response;
- implementation requires changing the evidence hierarchy;
- the protocol boundary appears materially different from the documented model;
- a runtime search path cannot be represented accurately without new evidence;
- a proposed change would cause harness PASS to be used as physical-camera qualification.
