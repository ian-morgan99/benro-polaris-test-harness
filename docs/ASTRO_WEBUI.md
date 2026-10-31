# Astro WebUI Harness Server (Issue #20)

This document describes the Astro WebUI harness server component added to address
[issue #20](https://github.com/ian-morgan99/benro-polaris-test-harness/issues/20).

## Purpose

Extend the harness to host and exercise the actual Polaris Astro WebUI contract
in a disposable, dry-run environment. The current harness web interface is a
separate simulation/evidence page and does not serve the Astro SPA or implement
its control API.

This component is related to umbrella issue [#14](https://github.com/ian-morgan99/benro-polaris-test-harness/issues/14)
and must not weaken the evidence boundary: a harness result proves only the
synthetic client/server contract, never physical Polaris or camera support.

## Scope

### In scope

- An explicit Astro-WebUI harness profile/command that serves approved Astro
  static assets from a pinned, recorded source path or fixture.
- Recording of UI asset commit, source repository, licence/provenance, and
  SHA-256 manifest. No Benro Connect APK or decompiled assets are copied into
  the public harness.
- Implementation of only the documented, synthetic Astro API surface needed by
  the UI, including state, location, mount status, capture, solve, target/GOTO,
  tracking, EXIF and diagnostics as applicable.
- All motion and firmware actions are dry-run only; the harness must never
  contact or mutate a Polaris.
- Schema-backed synthetic scenarios for: startup, camera-not-ready, capture
  publication, accepted solve, rejected solve, GOTO serialisation, stale
  session, EXIF opt-in, and unavailable rate control.
- A browser/client smoke test proving that the served SPA loads its JavaScript
  and CSS, reaches the synthetic API, displays bounded status/error states, and
  cannot apply a rejected solve or unavailable rate command.
- A route/API compatibility report so missing or extra endpoints are visible
  rather than silently ignored.

### Out of scope

- Physical camera or Polaris qualification.
- Firmware installation or protocol mutation.
- Reimplementing Benro Connect or redistributing its APK/decompiled source.
- Treating a browser smoke pass as proof that the native Polaris `polaris-httpd`
  runtime works.

## CLI Usage

### Start the Astro WebUI server

```bash
polaris-harness astro-webui --profile synthetic
```

### Options

| Option | Description | Default |
|--------|-------------|---------|
| `--profile` | Profile name (only `synthetic` supported) | `synthetic` |
| `--host` | Host to bind to | `127.0.0.1` |
| `--port` | Port to bind to (0 = ephemeral) | `0` |

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success / server started |
| 2 | Invalid CLI/config/schema |
| 7 | Internal harness error |

## Synthetic Astro API

All endpoints are dry-run only — they never contact or mutate a physical Polaris.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/astro/state` | GET | Return current synthetic camera/mount state |
| `/api/astro/capture` | POST | Start a synthetic capture |
| `/api/astro/solve` | POST | Run synthetic platesolve (accept/reject) |
| `/api/astro/goto` | POST | Slew to RA/Dec target |
| `/api/astro/tracking` | POST | Toggle tracking |
| `/api/astro/exif` | POST | Opt in/out of EXIF |
| `/api/astro/rate-control` | POST | Rate control — always 403 (unavailable) |
| `/api/astro/reset` | POST | Reset synthetic session state |
| `/api/astro/manifest` | GET | Return asset provenance manifest |
| `/api/astro/compatibility` | GET | Return route/API compatibility report |

## Synthetic Scenarios

All scenarios are labelled `source: synthetic` and do not claim physical
Polaris or camera support:

| Scenario ID | Description |
|-------------|-------------|
| `astro-startup` | Verify the Astro WebUI loads and initializes with default state |
| `astro-camera-not-ready` | Verify capture is rejected when camera is not ready |
| `astro-capture-publication` | Verify a successful capture publishes a capture_id |
| `astro-solve-accepted` | Verify a valid solve returns ok with confidence |
| `astro-solve-rejected` | Verify a rejected solve produces a visible reason and zero mount/EXIF actions |
| `astro-goto-serialisation` | Verify GOTO accepts RA/Dec and returns slewing state |
| `astro-stale-session` | Verify session reset produces a new session_id |
| `astro-exif-opt-in` | Verify EXIF opt-in returns ok |
| `astro-rate-control-unavailable` | Verify rate control returns 403 unless separately qualified |

## Asset Provenance

Static assets are checked in under `tests/fixtures/astro/` with a provenance
manifest (`astro_assets.json`) recording:

- UI asset commit
- Source repository
- Licence/provenance (GPL-3.0-only)
- SHA-256 manifest for each asset

No Benro Connect APK or decompiled assets are included.

## Evidence Boundary

A harness PASS proves only that the consumer correctly handles the synthetic
Astro WebUI contract. It does **not** prove:

- Physical camera or Polaris support
- That the native Polaris `polaris-httpd` runtime works
- Firmware installation or protocol mutation correctness

## Relationship to Existing Web Interface

The existing `web_interface.py` provides a separate simulation/evidence upload
page. The Astro WebUI server (`astro_webui/`) is a distinct component serving
the Astro SPA and its synthetic control API. They are independent and must not
be coupled.

## CI Integration

The Astro WebUI server can be started in CI with:

```bash
polaris-harness astro-webui --profile synthetic --host 127.0.0.1 --port 8080
```

Missing Flask support is a hard failure, not a silent skip.
