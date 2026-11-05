# Baseline Audit — Polaris Test Harness (issue #27, WBS 0.1)

**Generated:** 2026-11-05
**Repository:** `ian-morgan99/benro-polaris-test-harness`
**Branch:** `main` (tracking `origin/main`)
**Current commit:** `e486dc4` — `docs: add Layer 3 programme baseline audit (issue #27, WBS 0.1-1.3)`
**Remote:** `https://github.com/ian-morgan99/benro-polaris-test-harness` (fetch + push)
**Push status:** clean — no unpushed commits, no unpulled commits

## Scope

This baseline covers the harness repository only. PrivateResearch (firmware extracts, traces, provenance registry) and the firmware patcher are separate repositories referenced by hash/path where relevant.

## Test inventory

| Category | Count | Command | Real binaries? | Notes |
|---|---|---|---|---|
| Total tests | 462 passed, 2 skipped | `python3 -m pytest -q` | Mixed | See below |
| ARM emulator suite | `tests/test_arm_emulator.py` | `pytest tests/test_arm_emulator.py` | Synthetic + real region binaries | `stage2_region.bin`, `capture_bulb_image_region.bin` are gitignored proprietary firmware; sidecars only. Locally present with hashes matching sidecars. |
| ARM environment seeding | `tests/test_arm_environment_seeding.py` | `pytest tests/test_arm_environment_seeding.py` | Synthetic only | Fixture-free, tmp_path fixtures |
| ARM mode/skip | `tests/test_arm_mode_and_skip.py` | `pytest tests/test_arm_mode_and_skip.py` | Synthetic only | Thumb/ARM mis-decoding + SKIP semantics |
| ARM CPU state | `tests/test_arm_cpu_state.py` | `pytest tests/test_arm_cpu_state.py` | Synthetic only | FPEXC/VFP fault classification |
| Protocol contract | `tests/test_protocol_contract.py` | `pytest tests/test_protocol_contract.py` | Synthetic | `contracts/protocol/benro-9090-v1.json` |
| Camera transaction | `tests/test_camera_transaction.py` | `pytest tests/test_camera_transaction.py` | Synthetic | `CaptureTransactionGuard` |
| Camera personalities | `tests/test_camera_personalities.py` | `pytest tests/test_camera_personalities.py` | Synthetic | K-3 III / K-1 II provisional manifests |
| Session recovery | `tests/test_session_recovery.py` | `pytest tests/test_session_recovery.py` | Synthetic | `SessionGuard` |
| Preview harness | `tests/test_preview.py` | `pytest tests/test_preview.py` | Synthetic | `PreviewServer` seven modes |
| Async capture | `tests/test_async_capture.py` | `pytest tests/test_async_capture.py` | Synthetic | |
| Astro scenarios | `tests/test_astro_scenarios.py` | `pytest tests/test_astro_scenarios.py` | Synthetic | |
| Astro WebUI smoke | `tests/test_astro_webui_smoke.py` | `pytest tests/test_astro_webui_smoke.py` | Synthetic | Browser smoke test |
| Package verifier | `tests/test_package_verifier.py` | `pytest tests/test_package_verifier.py` | Synthetic | FwPkt verification |
| Packaging | `tests/test_packaging.py` | `pytest tests/test_packaging.py` | Synthetic | Wheel contents |
| Safety policy | `tests/test_safety_policy.py` | `pytest tests/test_safety_policy.py` | Synthetic | Fail-closed physical preflight |
| SD emulator | `tests/test_sd_emulator.py` | `pytest tests/test_sd_emulator.py` | Synthetic | FAT32 SD card image |
| Runtime verifier | `tests/test_runtime_verifier.py` | `pytest tests/test_runtime_verifier.py` | Synthetic | fs/ELF/loader/provenance |
| Basic functionality | `tests/test_basic_functionality.py` | `pytest tests/test_basic_functionality.py` | Synthetic | Clock + schema validation |
| Smoke | `tests/test_smoke.py` | `pytest tests/test_smoke.py` | Synthetic | Clock + schema validation |
| Integration | `tests/test_integration.py` | `pytest tests/test_integration.py` | Synthetic | |
| Evidence validation | `tests/test_evidence_validation.py` | `pytest tests/test_evidence_validation.py` | Synthetic | |
| Install simulation | `tests/test_install_simulation.py` | `pytest tests/test_install_simulation.py` | Synthetic | |
| Camera generation lifecycle | `tests/test_camera_generation_lifecycle.py` | `pytest tests/test_camera_generation_lifecycle.py` | Synthetic | |
| Issue templates | `tests/test_issue_templates.py` | `pytest tests/test_issue_templates.py` | Synthetic | GitHub issue form schema |
| Lifecycle partial | `tests/test_lifecycle_partial.py` | `pytest tests/test_lifecycle_partial.py` | Synthetic | Case 5 oracle regression test |

**Tracked test files:** 48 (see `git ls-files tests/`)

## ARM emulator — real vs synthetic binaries

### Synthetic (checked in, runnable on any clone)

| Fixture | SHA-256 binding | Mode | Entry | Stub entries |
|---|---|---|---|---|
| `tiny_fixture.bin` | synthetic | Thumb | 0x1000 | none |
| `aapcs_fixture.bin` | synthetic | ARM | — | AAPCS marshalling |
| `arm_mode_fixture.bin` | synthetic | ARM/Thumb | — | mode selection |
| `illegal_opcode_fixture.bin` | synthetic | — | — | unsupported instruction |
| `infinite_loop_fixture.bin` | synthetic | — | — | instruction limit |
| `stub_call_fixture.bin` | synthetic | — | — | stub call recording |
| `lifecycle_probe_fixture.bin` | synthetic | ARM | 0x1000 | stop, close |

### Gitignored proprietary firmware (present locally, NOT on GitHub)

These are extracted read-only from `PrivateResearch` and are **never committed**. Each has a committed `.json` sidecar (coordinates + hashes only) and a `.placeholder` documenting retrieval. The `.bin` files exist locally with hashes matching their sidecars:

| Fixture | Sidecar | Placeholder | Source binary | Region SHA-256 | Local? |
|---|---|---|---|---|---|
| `stage2_region.bin` | `stage2_region.json` | `stage2_region.bin.placeholder` | `pgphoto.stage2ondisk` (v90-era) | `23398c6efc7c737c1b2b91664a98b45a2b7bc01f97e704c3ccb4bf3fc4a618c3` | Yes |
| `capture_bulb_image_region.bin` | `capture_bulb_image_region.json` | `capture_bulb_image_region.bin.placeholder` | `pgphoto.stage2ondisk` 6.0.0.54.90 | `9917d69fc9228d2234e9e735cd3ad3c102941ebfb501de3c91681a695517073e` | Yes |

**Consequence:** On a fresh public clone, any lifecycle case requiring these region binaries reports SKIP (not FAIL) because `FixtureMetadata.path` is None. Locally, the binaries are present and real ARM execution runs against them. The CI ARM gate (`--cov-fail-under=80`) measures only the synthetic-input paths (`test_arm_emulator.py`, `test_arm_environment_seeding.py`, `test_arm_mode_and_skip.py`) and stays enforced on every runner.

### Real ARM execution results (verified 2026-11-05)

Running against the provenance-pinned `stage2_region.bin` (SHA-256 `23398c6e…`) with `POLARIS_FIRMWARE_ROOT` pointing at the retained `pgphoto.stage2ondisk`:

- **Emulator:** Unicorn 2.1.4, ARM mode (not Thumb), FPEXC.EN enabled (VFP/NEON accepted)
- **Binary:** `pgphoto.stage2ondisk`, ELF32 ARM EABI5 soft-float, entry `0x15ed8`, interpreter `/lib/ld-linux.so.3`
- **Source binary SHA-256:** `8973d9486e0207446cc111234ed39e878676ce1f3edca2967a97db86c8d9eaac`
- **Case 4 (stop failure preserves primary error):** PASS — 154 instructions, 17 stub calls from original `pgphoto.stage2ondisk` code
- **Cases 1-3, 5-9:** execute real ARM instructions (12-154 instructions, up to 190 stub calls) then correctly report `not_evaluated` for edges the region doesn't declare, or timeout on the wait loop without `event_return_value=-52`
- **VFP/NEON:** Previously recorded as "unsupported_instruction" at `0x1002` with `instructions_executed: 0` — this was an uninitialised-FPU artifact, not an engine limit. Setting FPEXC.EN bit 30 lets VFP instructions execute correctly (per AGENTS.md precedent, commit 712b996).

## Missing private fixtures / evidence

The following are referenced by sidecar but their `.bin` bytes are **not** committed to this repo (gitignored):

- `tests/fixtures/arm/stage2_region.bin` — provenance-pinned `capture_image_with_Bulb` region, SHA-256 `23398c6efc7c737c1b2b91664a98b45a2b7bc01f97e704c3ccb4bf3fc4a618c3`, extracted from `pgphoto.stage2ondisk` in `PrivateResearch`. Present locally.
- `tests/fixtures/arm/capture_bulb_image_region.bin` — `captureBulbImage` region, SHA-256 `9917d69fc9228d2234e9e735cd3ad3c102941ebfb501de3c91681a695517073e`, source binary retained at `PrivateResearch firmware-packets/6.0.0.54.90--o-v18m-k1ii-pgphoto-readiness-20261029/extracted_appfs/stage2-ondisk/ondisk/pgphoto.stage2ondisk`. Present locally.

Do not infer contents from filenames. Do not commit proprietary firmware blobs to GitHub.

## Commands (baseline, host-only, no hardware)

```bash
# Install (development)
python3 -m pip install -e .[dev]

# Full test suite
python3 -m pytest -q

# ARM lifecycle suite (CI gate)
polaris-harness arm-emulate --suite

# ARM emulator unit tests with coverage
pytest tests/test_arm_emulator.py \
       tests/test_arm_environment_seeding.py \
       tests/test_arm_mode_and_skip.py -v --tb=short \
  --cov=src/polaris_harness/arm_emulator \
  --cov-report=term \
  --cov-report=xml:coverage-arm.xml \
  --cov-fail-under=80

# Tiny fixture end-to-end (synthetic, no firmware)
polaris-harness arm-emulate \
  --fixture tiny_fixture \
  --output /tmp/arm-result.json \
  --command capture_image_with_Bulb

# Validate all scenario files
python -c "
from polaris_harness.validation import validate_scenario_file
from pathlib import Path
for d in Path('scenarios').iterdir():
    f = d / 'scenario.yaml'
    if f.exists():
        errors = validate_scenario_file(f)
        assert not errors, (d.name, errors)
print('All scenario files valid')
"

# CLI
polaris-harness --version
polaris-harness --help
```

## Missing private fixtures / evidence

The following are referenced by sidecar but their `.bin` bytes are **not** in this repo:

- `tests/fixtures/arm/stage2_region.bin` — provenance-pinned `capture_image_with_Bulb` region, SHA-256 `23398c6efc7c737c1b2b91664a98b45a2b7bc01f97e704c3ccb4bf3fc4a618c3`, extracted from `pgphoto.stage2ondisk` in `PrivateResearch`.
- `tests/fixtures/arm/capture_bulb_image_region.bin` — `captureBulbImage` region, SHA-256 `9917d69fc9228d2234e9e735cd3ad3c102941ebfb501de3c91681a695517073e`, source binary retained at `PrivateResearch firmware-packets/6.0.0.54.90--o-v18m-k1ii-pgphoto-readiness-20261029/extracted_appfs/stage2-ondisk/ondisk/pgphoto.stage2ondisk`.

Do not infer contents from filenames. Do not commit proprietary firmware blobs to GitHub.

## Evidence directories (PrivateResearch)

Referenced by the harness but not duplicated here:

- `PrivateResearch/.astro-v94.TrArG1/docs/evidence/bulb40s-20261025/` — `pgphoto-wait-loop.txt`, `source-binary.sha256` (`3e340e9e03744bdc75ba2f3479999abb092468c05e5e27915530ae0cbb99789e`)
- `PrivateResearch/.astro-v94.TrArG1/docs/evidence/issue-251-dispatch-20261030/` — dispatcher follow-up source + ARM tests
- `PrivateResearch/.astro-v94.TrArG1/docs/evidence/issue-251-harness-emulator-20261031/` — `arm-tests.txt`, `cancel-return.txt`, `harness-real-arm-probe.txt`
- `PrivateResearch/.astro-v94.TrArG1/docs/evidence/issue-251-recovery-20261030/` — build and source recovery evidence
- `PrivateResearch/.astro-v94.TrArG1/docs/evidence/k1ii-controls-20261103/` — K-1 II control defects (232–253)
- `PrivateResearch/polaris-evidence/bulb40s-20261025/` — capture_image_with_Bulb disassembly

## Do-not-assume list

Per WBS 0.1: do not assume an issue marked closed is actually fixed. The following issues are referenced as open/blocking in this baseline:

- #15 ARM user-space emulation — in progress, VFP/NEON gap
- #18 v90 `capture_image_with_Bulb` at 0x101aac — open, stops at VFP/NEON
- #24 SD emulator FAT32 fidelity — open
- #26 CI gate inert — open
- #251 deferred timed cancellation — open, release-blocking (firmware-patcher)

## Next (WBS 0.2)

Create `docs/emulator/CAPABILITY-MATRIX.md` with columns: capability, real executable(s), required kernel/device interface, observed hardware evidence, virtual implementation, test, confidence, owner, status, limitation. Initial rows: startup, `pgphoto`, Stage-2, camera discovery, PTP/USB, Live View, settings, Manual capture, timed/untimed Bulb, cancellation, reconnect, image transfer, motor movement, mount state, TCP port 9090, Astro Web UI, SD deployment, restart/reflash.
