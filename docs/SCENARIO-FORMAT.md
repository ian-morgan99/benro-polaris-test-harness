# Scenario and Evidence Format

This document defines the minimum structure for deterministic scenarios in the Benro Polaris Test Harness.

The goal is to make every scenario auditable back to physical evidence or to an explicitly-labelled synthetic fault.

## Scenario identity

Each scenario should have a stable directory name and manifest, for example:

```text
scenarios/k3iii-capture-delayed-success/
  scenario.yaml
  README.md
  requests.ndjson
  responses.ndjson
  assertions.yaml
```

The representation may evolve, but the metadata contract below is mandatory.

## Mandatory manifest fields

```yaml
id: k3iii-capture-delayed-success
version: 1
title: K-3 III capture reports intermediate negative status before successful file delivery
source: physical
status: confirmed
observed_layer: polaris-protocol
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
source_issue: ian-morgan99/benro-polaris-firmware-patcher#37
observed_date: 2026-09-07
limitations:
  - Timing is representative of the recorded run, not a universal camera guarantee.
  - This scenario does not prove physical camera compatibility.
```

Unknown values must be written as `unknown` or omitted only where the schema explicitly permits it. Never infer missing provenance.

## `source`

Allowed conceptual values:

- `physical` — behaviour directly observed from real hardware;
- `synthetic` — deliberately invented fault/edge case;
- `derived` — deterministic reduction of multiple physical traces where every transformation is documented.

A `synthetic` scenario must never use wording such as "observed", "camera does", or "hardware behaviour".

## `status`

Recommended values:

- `confirmed` — repeated or otherwise sufficiently evidenced physical contract;
- `provisional` — physical observation exists but provenance/repetition is incomplete;
- `synthetic` — nonphysical injected case;
- `superseded` — retained for historical regression but replaced by stronger evidence.

CI should normally use confirmed and synthetic scenarios. Provisional scenarios may be useful diagnostically but should not silently become release gates.

## Events

Events should preserve ordering and enough timing to exercise async behaviour without baking in unnecessary nondeterminism.

Example:

```yaml
events:
  - at_ms: 0
    direction: client_to_device
    channel: command
    payload: "1&264&4&state:1;bulb:0;c:-1;#"

  - at_ms: 12
    direction: device_to_client
    channel: command
    payload: "264@state:1#"

  - at_ms: 2380
    direction: device_to_client
    channel: command
    payload: "264@state:-1005#"
    semantics: transient

  - at_ms: 3500
    direction: device_to_client
    channel: media
    event: image_ready
```

Tests should generally assert bounded ranges rather than exact millisecond values unless exact timing itself is the contract.

## Assertions

Assertions describe the consumer behaviour expected from the recorded contract.

For the async capture example:

```yaml
assertions:
  - capture_enters_pending_after_ack
  - transient_minus_1005_is_not_terminal
  - second_shutter_is_blocked_while_pending
  - exactly_one_completion_is_emitted
  - completion_requires_final_image_or_terminal_success
```

Assertions must not smuggle in unobserved camera semantics. For example, if we only know that `-1005` was transient in one successful capture sequence, assert that the client does not make it terminal in that scenario; do not globally redefine `-1005` for every body and operation without evidence.

## Camera personalities

A personality is a reusable set of evidenced responses/capabilities for one body/path.

Recommended layout:

```text
personalities/pentax-k3iii/
  manifest.yaml
  settings.yaml
  capture.yaml
  preview.yaml

personalities/pentax-k1ii/
  manifest.yaml
  settings.yaml
  ...
```

A personality may be incomplete. Unknown behaviour must remain unknown.

Do not derive K-1 II behaviour from K-3 III simply because both are Pentax.

## Positive controls and differentials

Some of the highest-value scenarios compare two evidenced behaviours.

Example: current setting enumeration differential.

```yaml
id: pentax-setting-enumeration-differential
cases:
  - personality: pentax-k3iii
    expected: setting_values_visible
  - personality: pentax-k1ii
    expected: setting_values_absent_in_current_polaris_path
```

This scenario ensures the client handles the observed difference. It does not determine why the difference exists; root-cause ownership comes from the direct/libgphoto2 -> Polaris-local -> OpenPolaris isolation process.

## Trace ingestion

Raw trace ingestion should follow this pipeline:

```text
physical log/pcap/protocol capture
  -> preserve immutable raw evidence externally or under evidence policy
  -> sanitise credentials/private paths/unrelated metadata
  -> normalize framing without altering semantics
  -> attach manifest with provenance
  -> replay against parser/server
  -> add explicit assertions
```

Any normalisation that changes bytes, timing, field order or framing must be documented.

## Runtime/package scenarios

Runtime scenarios may describe a fake `/app` tree and expected loader resolution.

Example assertions:

```yaml
runtime_assertions:
  - /app/lib/stage2/libgphoto2.so.6 is expected_arm_abi
  - /app/lib/stage2/libgphoto2_port.so.12 exports required_versioned_symbols
  - /app/lib/stage2/libgphoto2/2.5.34/ptp2.so matches intended source provenance
  - /app/lib/stage2/libgphoto2_port/0.12.2/usb1.so resolves DT_NEEDED
  - stock lookup-path copies are present where the real runtime requires them
  - wrapper environment resolves intended copies, not stale stock/stub files
```

These tests validate package/runtime assumptions only. They do not prove a physical camera works.

## Destructive operations

Synthetic storage/media state should be the default for delete/protect/reset/destructive protocol tests.

A destructive physical trace may be preserved if it already exists and contains no sensitive material, but reproducing deletion on real user media is not a requirement for harness development.

## Scenario change control

When a scenario based on physical evidence changes:

1. identify the new physical evidence;
2. link it;
3. explain why the previous contract was incomplete/wrong;
4. increment the scenario version;
5. retain old scenario/version when needed to test backward compatibility;
6. update consumer expectations deliberately rather than silently changing fixtures.

A consumer test failure must never be 'fixed' by editing the scenario to match the consumer unless new evidence justifies the scenario change.

## Minimum README for each physical scenario

Each physical scenario README should answer:

1. What was physically observed?
2. On exactly what camera/firmware/runtime/source SHA?
3. At which layer was it observed?
4. What does the harness reproduce?
5. What does the harness deliberately not claim?
6. Which owning issue records the root cause/fix?
7. What should a consumer do when this scenario occurs?
