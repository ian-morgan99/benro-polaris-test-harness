# Ownership and Failure Isolation

This document defines where a failure belongs and how the Benro Polaris Test Harness may be used without obscuring root cause.

## Governing rule

An issue belongs in a repository only when its reproduction can be demonstrated from that repository's own code/test harness plus the hardware/API it directly owns.

The test harness may model a failure from another repository, but **modelling is not ownership**.

## Repository boundaries

### libgphoto2

Owns actual camera/library behaviour.

A defect belongs here only when it reproduces from a clean libgphoto2 checkout with the physical camera directly attached, using `gphoto2 --debug` or a minimal source-repo harness.

OpenPolaris, pgphoto, Stage-2, Polaris firmware and this test harness must not be required to reproduce a libgphoto2 defect.

### benro-polaris-firmware-patcher

Owns firmware construction and the device runtime integration surface:

- FwPkt construction and provenance;
- Stage-2 packaging;
- pgphoto lifecycle;
- watchdog/restart behaviour;
- loader paths and environment;
- `libgphoto2`/`libgphoto2_port`/camlib/iolib integration;
- device-side protocol handlers where the failure is within the firmware/runtime.

Where practical, reproduce these failures directly on Polaris without OpenPolaris.

### OpenPolaris

Owns:

- external Polaris protocol framing and command mapping;
- parser behaviour;
- UI/state;
- optimistic write handling;
- client-side async state machines;
- preview transport handling;
- destructive-command guards;
- client platform behaviour;
- E2E qualification orchestration and attribution.

A camera-facing feature is not automatically a libgphoto2 concern. If the problem is that OpenPolaris sends the wrong Polaris opcode or misrepresents state, it belongs in OpenPolaris.

### benro-polaris-test-harness

Owns only:

- executable representations of evidenced contracts;
- trace replay;
- deterministic protocol servers;
- synthetic fault injection;
- runtime/package validation fixtures;
- cross-repo regression fixtures.

It does not own the underlying defect.

## Standard isolation experiment

For a camera-visible failure, run the smallest applicable sequence:

```text
A. Direct source-repo test
   clean libgphoto2 SHA + camera directly attached

B. Polaris-local test
   same camera + same intended libgphoto2 SHA/runtime, no OpenPolaris

C. OpenPolaris E2E
   OpenPolaris + Polaris + camera

D. Harness replay
   deterministic regression of the already-evidenced contract
```

Interpretation:

```text
A FAIL + B FAIL at the same underlying PTP operation
  => strong libgphoto2 ownership.

A PASS + B FAIL
  => firmware-patcher/runtime/Stage-2/session/environment ownership.

A FAIL + B PASS
  => libgphoto2 problem may be masked/compensated by the appliance.

A PASS + B PASS + C FAIL
  => OpenPolaris ownership.

D FAIL after A/B/C evidence was encoded correctly
  => regression in the harness or the consumer under test, not new camera evidence.
```

## Compare the first lower-level divergence

Do not classify ownership merely because two paths return the same final error code.

For example, `-6`, `-2`, `-1005`, `0xa008` or another high-level status can arise from different underlying operations.

Compare:

- first differing PTP operation;
- first differing response code;
- first differing state transition;
- first differing loader/path resolution;
- first missing protocol frame;
- first ownership/process difference.

If the underlying divergence differs, split the issues rather than forcing one root cause.

## Direct libgphoto2 evidence requirements

Record at minimum:

- `git rev-parse HEAD`;
- dirty/clean state;
- camera model and firmware;
- USB VID:PID/interface identity;
- `gphoto2 --version`;
- loader evidence proving the intended just-built camlib/iolib is used;
- fresh process and camera/session state;
- full `gphoto2 --debug` trace for the failing operation;
- shell/GP return code;
- first failing PTP transaction/response;
- repeated run after power cycle where meaningful.

A Polaris/OpenPolaris trace can support discovery, but it is not source-repo proof.

## Polaris-local evidence requirements

Record at minimum:

- Polaris firmware version;
- patcher SHA;
- FwPkt SHA-256/build identifier;
- embedded libgphoto2 source SHA;
- `/app/bin/gphoto2 --version` where available;
- `CAMLIBS`, `IOLIBS`, `LD_LIBRARY_PATH`, `LD_PRELOAD` and Stage-2 vars;
- pgphoto PID/process ownership;
- listener/port state;
- actual loaded library paths where determinable;
- runtime logs/trace;
- camera USB identity and camera state;
- whether pgphoto was stopped or competing for the camera.

Direct SSH mutation of runtime files is non-qualifying. Restore/install a canonical immutable FwPkt before release evidence is collected.

## OpenPolaris E2E evidence requirements

Record:

- OpenPolaris SHA/version;
- patcher SHA;
- FwPkt SHA/build identifier;
- embedded libgphoto2 SHA;
- camera model/firmware/mode;
- exact request/response frames;
- expected and observed UI/state;
- timing for async operations;
- whether the same operation succeeds Polaris-local;
- owning issue for each failure.

## Harness evidence requirements

Every physical replay scenario must point back to evidence collected at A, B or C.

Every synthetic scenario must say `source: synthetic`.

The harness must never fill in unknown fields by guessing from another body, firmware, opcode family or client assumption.

## Example: K-1 II vs K-3 III setting values

Observed client-level differential:

```text
K-3 III: setting values/options visible.
K-1 II: equivalent setting values/options not currently visible.
```

Required isolation:

```text
A. Direct libgphoto2 + K-1 II
   Are ISO/shutter/aperture/WB current values and choices present?

B. Polaris-local + K-1 II
   Are those values exposed through the device protocol/runtime?

C. OpenPolaris
   Are the returned values parsed and displayed?
```

Ownership:

```text
A missing -> candidate libgphoto2 issue.
A present, B missing -> firmware/runtime issue.
A present, B present, C missing -> OpenPolaris issue.
```

K-3 III is a positive-control body, not proof that K-1 II must use an identical camera protocol path.

## Closure rule

A harness regression test may be added to prevent recurrence, but the owning issue closes only when the owning layer's reproduction passes at its own boundary.

Examples:

- libgphoto2 issue: direct physical camera reproducer passes after fix;
- patcher issue: canonical FwPkt/runtime reproducer passes after fix;
- OpenPolaris issue: correct device/replay contract is handled and physical E2E is rerun where required.

Do not close a lower-layer issue solely because an upper-layer app appears to work.
