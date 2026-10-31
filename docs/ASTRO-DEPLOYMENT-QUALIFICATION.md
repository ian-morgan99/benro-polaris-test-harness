# Astro deployment qualification

This document defines how the Benro Polaris Test Harness will qualify the
Astro plate-solving deployment from the first software artifact through the
final photograph.

It is a test and evidence plan, not a claim that the harness emulates a
camera, Polaris firmware, or a physical mount. The harness must remain
deterministic and must not invent body-specific protocol behavior.

## Qualification boundary

The harness owns the reproducible software contracts around:

~~~
session state -> capture publication -> solve admission
             -> calibration/drift decisions -> package/install evidence
~~~

Physical camera behavior, the actual Polaris runtime, motor response, and
photographic quality remain higher evidence levels. A harness PASS means that
the tested consumer handles the recorded or explicitly synthetic contract; it
does not prove physical support.

Until a physical trace is recorded and linked, every new Astro scenario must
carry:

    source: synthetic

Synthetic scenarios are useful for negative paths and state-machine tests, but
must not be described as a camera personality or used to claim hardware
compatibility.

## Scenario packages

Implement the following as the smallest useful scenario families. Each family
must have a schema-valid manifest, deterministic input, expected outcome, and
NDJSON execution ledger.

### astro-session-boundary-v1

Cover:

- entry and exit from Astro mode;
- creation of a new session identifier;
- rejection of stale target, pose, frame, and capture events;
- camera reconnect with a new identity/generation;
- duplicate events and reused filenames;
- cancellation while a solve or movement is pending.

The consumer must not apply an observation from an earlier session to the
current target.

### astro-capture-publication-v1

Cover:

- completion event before the file;
- file before the completion event;
- delayed publication;
- stable, valid JPEG;
- partial or invalid JPEG;
- a file that is still changing;
- duplicate completion;
- timeout, retry, and missing-file behavior;
- power-loss simulation during local publication.

The scenario must assert file identity using the available contract fields:
path, size, modification time, and content hash where the contract exposes
them. It must test that an arbitrary newest JPEG is never substituted for a
missing event-matched file.

### astro-solve-admission-v1

Cover:

- accepted high-confidence solve;
- low match count;
- high residual or poor fit;
- wrong or absent RA/Dec hint;
- missing focal length or sensor geometry;
- solver timeout and process failure;
- native/reference disagreement;
- catalogue/index manifest mismatch.

The result must be accepted only when finite, internally consistent, and above
the configured confidence gates. A rejected solve must not update target,
calibration, EXIF, or mount state.

### astro-calibration-model-v1

Cover:

- three well-spaced calibration points;
- duplicate or poorly conditioned points;
- fourth-point hold-out validation;
- stale timestamps and stale mount poses;
- RA wrap at 0/360 degrees;
- coordinate-frame mismatch;
- invalid or non-finite model parameters.

The fourth point is a validation observation in this scenario family. It must
not be treated as proof of motor-rate accuracy.

### astro-drift-measurement-v1

Cover:

- timed repeated solves;
- static spatial offset versus a time-dependent residual slope;
- roll/field-rotation measurement;
- insufficient samples;
- out-of-order frames;
- a result that explicitly reports
  rate_control: unavailable.

The scenario must prove that measurement can be recorded without authorising a
motor-speed command. Fine rate control requires a separately evidenced
firmware/API contract and a later physical qualification.

### astro-firmware-install-v1

Cover the installer and verifier with disposable package fixtures:

- valid candidate tree and manifest;
- ZIP-only staging rejected;
- component hash mismatch rejected;
- wrong build identity rejected;
- camera-busy preflight rejected;
- insufficient free space rejected;
- AP link flap without an uptime reset rejected;
- successful reboot followed by post-boot identity verification;
- missing last-known-good rollback package rejected;
- interrupted staging leaves no installed claim.

The fixture must assert that the release path stages an extracted FwPkt tree
and validates its identity before reboot. It must not execute captured
firmware payloads.

### astro-offset-provenance-v1

Cover:

- exact app and gimbal hashes accepted;
- mismatched app/gimbal pair rejected;
- stale or missing offset-table entry rejected;
- source, binary, and ledger provenance missing;
- speculative code-cave/default-slot proposals remain analysis-only;
- the runtime adapter emits no AC 4A command.

This family is a provenance and fail-closed test, not an endorsement of a
binary patch or an offset on an unverified firmware build.

## Execution ledger

Every Astro scenario run must record enough information to reproduce and
classify the result:

~~~
scenario_id/version
harness revision
consumer revision
patcher commit and branch, when applicable
firmware candidate/build ID
FwPkt SHA-256 and install-manifest hash
app/gimbal component hashes
camera model/firmware/USB identity, when applicable
JPEG input/output hashes and file identity
solver/catalogue/index identity
focal-length and sensor-size source
RA/Dec/roll/scale/confidence
mount action and whether it was dry-run
failure code and user-facing reason
~~~

Unknown values must remain unknown. A missing provenance field is a failure
for a release-gated artifact, not an invitation to infer it from a filename.

Do not commit private camera-card metadata, credentials, or unsanitised
physical traces. A physical scenario must link its evidence record, observed
layer, source issue, and limitations, then receive a new scenario version when
the observed contract changes.

## Test stages

### 1. Schema and unit checks

Run on every change:

~~~
python -m pytest tests/ -q
polaris-harness validate <changed-scenario-or-directory>
~~~

Include negative tests for malformed manifests, unknown failure codes,
non-finite values, missing hashes, stale session IDs, and unsafe commands.

### 2. Protocol and state-machine checks

Use virtual time for deterministic tests and real TCP only for a smoke test.
Assert split/coalesced frames, unexpected requests, duplicate events,
timeouts, cancellation, and state transitions. The pure scenario engine must
remain independent of sockets.

### 3. Runtime/package checks

Run the read-only runtime verifier against a synthetic extracted package and
assert separate results for:

- ELF dynamic linker lookup;
- CAMLIBS camlib selection;
- IOLIBS port backend selection;
- explicit application search paths;
- executable/wrapper target resolution;
- provenance and component identity.

Do not collapse these into a single “library found” assertion.

### 4. Patcher integration

Consume only a candidate artifact produced by the firmware patcher’s
build-only path. The harness must verify:

- repository, ref, commit, and clean/dirty policy;
- exact app/gimbal input hashes;
- firmware-specific offset ledger;
- source and licence/provenance manifest;
- extracted FwPkt tree and final package hash;
- build ID and required runtime files.

The harness must not create, patch, or mutate the candidate firmware tree.

### 5. Install simulation

Exercise the full installer state machine without a Polaris:

~~~
not-staged
-> staged-unverified
-> staged-verified
-> reboot-uncertain
-> installed-verified
~~~

Inject interrupted transfers, low space, hash mismatch, camera busy state,
link flap, absent reboot, stale post-boot identity, and unavailable device.
The only successful terminal state is installed-verified; an AP disconnect
alone is never a success signal.

### 6. Consumer replay

Replay the scenario families against the OpenPolaris/consumer adapter using
dry-run mount actions. Assert that:

- accepted results update only the current session;
- rejected results cannot update target, calibration, EXIF, or motor state;
- repeated GOTO operations are serialised;
- capture completion is matched to the correct local file;
- cancellation stops pending work;
- user-facing status distinguishes waiting, solving, rejected, applied, and
  failed states.

### 7. Promotion to physical evidence

Promote a synthetic scenario only after the corresponding physical behavior
has been observed and sanitised. Preserve the synthetic version, add the
physical evidence record, increment the scenario version, and update consumer
tests intentionally. Never replace a physical trace with a more convenient
synthetic response.

## Acceptance gates

The harness portion is complete only when:

1. all scenario manifests validate;
2. the full harness suite passes;
3. every failure injection produces a bounded, user-visible, non-destructive
   result;
4. no rejected solve can reach a mount or EXIF action;
5. package/install verification is hash- and provenance-bound;
6. all ledgers are reproducible and sanitised;
7. the limitations are visible in the test report.

The following remain outside harness acceptance and require physical evidence:

- a real camera emits the completion event and local JPEG;
- Polaris loads the intended runtime after installation;
- the solve is accurate on the chosen camera/lens/sky;
- a three-point model improves pointing;
- a fourth point supports a drift estimate;
- motor-speed adjustment improves tracking without oscillation;
- the final photograph is centred, unrotated, and usable.

## Current baseline

The plan is an additional safety layer, not a completed release test. The
initial baseline exposed a missing declared Unicorn dependency and placeholder
schema validation. Those blockers have now been addressed locally:

- full harness suite: 142 passed;
- ARM emulation runs with the declared Unicorn 2.1.4 dependency;
- CLI schema validation passes all 12 current scenario/evidence manifests;
- seven Astro scenario packages and executable contract tests are present;
- the real loopback TCP scenario smoke test passes.

Therefore the harness is still not a release gate. All Astro scenarios are
synthetic contracts, and no harness result proves a physical camera, Polaris
runtime, motor, or final photograph. Physical promotion and the patcher/device
gates remain explicit prerequisites.

## Suggested implementation order

1. Add schemas and common ledger fields without changing the protocol engine.
2. Add astro-session-boundary-v1 and astro-capture-publication-v1 with
   synthetic fixtures.
3. Add solver-admission and calibration/drift decision fixtures.
4. Add package/install and offset-provenance fixtures to the runtime verifier
   boundary.
5. Wire the patcher build artifact as an external, hash-pinned test input.
6. Add consumer replay with dry-run mount actions.
7. Record the first physical camera trace and promote only the smallest
   matching scenario.
8. Re-run the entire evidence ladder before any supervised Polaris install.

This ordering keeps the harness useful immediately while preventing a green
synthetic test suite from being mistaken for a safe firmware deployment.
