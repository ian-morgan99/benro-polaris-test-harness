# Capability Matrix — Polaris Test Harness (issue #27, WBS 0.2)

**Generated:** 2026-11-05
**Repository:** `ian-morgan99/benro-polaris-test-harness`
**Branch:** `main` @ `30013d6`

## Conventions

- **Real executable(s):** original Polaris/user-space binaries exercised, or `synthetic` if none.
- **Required kernel/device interface:** the real interface the capability needs (USB, `/dev` node, ioctl, socket, etc.) or `none` for pure synthetic.
- **Observed hardware evidence:** link to retained trace/evidence, or `none` if synthetic-only.
- **Virtual implementation:** what the harness models/replays, or `none`.
- **Test:** test file(s) covering it.
- **Confidence:** `high` / `medium` / `low` / `none`.
- **Owner:** responsible repo/person.
- **Status:** `implemented` / `partial` / `blocked` / `not-started`.
- **Limitation:** explicit known gap.

| Capability | Real executable(s) | Required kernel/device interface | Observed hardware evidence | Virtual implementation | Test | Confidence | Owner | Status | Limitation |
|---|---|---|---|---|---|---|---|---|---|
| Startup | none | none | none | synthetic scenario engine | `tests/test_basic_functionality.py`, `tests/test_smoke.py` | high | harness | implemented | synthetic only |
| `pgphoto` | `pgphoto.stage2ondisk` (gitignored, local only) | loader, `/app` path, dynamic linker | `PrivateResearch polaris-evidence/bulb40s-20261025/` | ARM user-space emulation (Unicorn), stubbed camera/libgphoto/OS/timer | `tests/test_arm_emulator.py` (lifecycle cases) | medium | harness + patcher | partial | region `.bin` not on GitHub; VFP/NEON gap resolved via FPEXC.EN (was misdiagnosed as engine limit) |
| Stage-2 | `pgphoto.stage2ondisk` | loader, ELF/ABI, `.got`/`.data` seeding | `PrivateResearch .astro-v94.TrArG1/docs/evidence/issue-251-harness-emulator-20261031/` | ARM user-space emulation, stub entries | `tests/test_arm_emulator.py` | medium | harness + patcher | partial | proprietary binary not public; deferred timed cancellation defect (#251) |
| Camera discovery | none | USB host controller, device enumeration | none | synthetic PTP/camera personality | `tests/test_camera_personalities.py` | none | harness | not-started | no USB host simulation |
| PTP/USB | none | USB device node, libusb/ioctl | `PrivateResearch polaris-evidence/` | synthetic fault injection only | `tests/test_sd_emulator.py` (USB UART gate) | none | harness | not-started | no real USB transport |
| Live View | none | preview socket/TCP | none | `PreviewServer` seven synthetic modes | `tests/test_preview.py` | medium | harness | implemented | synthetic envelope, not real Polaris wire format |
| Settings | none | camera property get/set | `PrivateResearch polaris-evidence/k1ii-controls-20261103/` | provisional K-3 III / K-1 II personalities | `tests/test_camera_personalities.py` | low | harness | partial | `source: derived`, `status: provisional` |
| Manual capture | none | camera capture command | none | `CaptureTransactionGuard` contract | `tests/test_camera_transaction.py` | medium | harness | implemented | synthetic, no physical camera |
| Timed Bulb | `capture_image_with_Bulb` region (gitignored) | timer, camera stop/cancel edge | `PrivateResearch polaris-evidence/bulb40s-20261025/` | ARM emulation + stubbed timer/camera | `tests/test_lifecycle_partial.py` (case 5) | low | harness + patcher | partial | cancel edge not fail-closed (#251 open defect) |
| Untimed Bulb | none | timer | none | synthetic | `tests/test_lifecycle_partial.py` | none | harness | not-started | |
| Cancellation | `cancel_timer` camera edge | camera cancel command | none | stubbed `cancel_timer` boundary | `tests/test_lifecycle_partial.py` | medium | harness | implemented | synthetic stub, not real camera |
| Reconnect | none | session generation, TCP reconnect | none | `SessionGuard` | `tests/test_session_recovery.py` | medium | harness | implemented | synthetic session model |
| Image transfer | none | file retrieval socket | none | synthetic output publication | `tests/test_camera_transaction.py` | medium | harness | implemented | no real image bytes |
| Motor movement | none | mount serial/USB/IPC | none | none | none | none | harness | not-started | motor boundary not yet identified from evidence |
| Mount state | none | mount query interface | none | none | none | none | harness | not-started | |
| TCP port 9090 | none | loopback TCP | none | deterministic TCP server | `tests/test_astro_webui_smoke.py` | high | harness | implemented | loopback only, no host exposure |
| Astro Web UI | none | loopback HTTP | none | synthetic Astro API + static assets | `tests/test_astro_webui_smoke.py` | medium | harness | implemented | dry-run only, never contacts Polaris |
| SD deployment | none | FAT32 block device | none | `SDEmulator` FAT32 image | `tests/test_sd_emulator.py` | low | harness | partial | FAT32 fidelity defects (#24) |
| Restart/reflash | none | firmware update path | none | package verifier (read-only) | `tests/test_package_verifier.py` | medium | harness | implemented | verifier does not flash or reboot |

## Gates referenced

- **G0:** baseline, coverage ledger, safety boundaries, runnable host-only smoke test exist; no unsupported claim of virtual hardware coverage.
- **G1:** signed-off inventory and executable/dependency/device graphs with hashes and explicit unknowns; at least one original executable available locally for controlled launch.
- **G2:** at least one genuine Polaris ELF executes meaningful instructions with correct loader; `pgphoto` startup blockers enumerated.
- **G3:** virtual boot reaches stable idle with at least one original Polaris application process.
- **G4:** original applications exercise camera enumeration, simulated capture, image retrieval, mount command, network exchange through explicit adapters.
- **G5:** end-to-end virtual session: discovery → settings → capture → transfer → mount → shutdown, with passing negative-path suite.
- **G6:** documented fidelity metrics against held-out hardware traces.
- **G7 / Layer 3 done:** real Polaris user-space components start from pinned images; end-to-end scenarios repeatable; unsupported behavior visible; physical qualification separate.

## Open items from this matrix

1. No real USB host controller simulation — camera discovery, PTP/USB, motor movement, mount state are `not-started`.
2. `pgphoto`/Stage-2 real-binary execution is `partial` — blocked on proprietary `.bin` not being on GitHub and VFP/NEON gap (#18).
3. Settings personalities are `derived`/`provisional` — need direct libgphoto2 + physical camera evidence (K-1 II attached to this PC per `PrivateResearch/.astro-v94.TrArG1/docs/evidence/k1ii-controls-20261103/handoff/AGENT-PROMPTS.md`).
4. SD deployment has FAT32 fidelity defects (#24).
5. CI gate is inert (#26) — Actions disabled; ARM coverage threshold fails without private fixtures.

## Next (WBS 1.1)

Inventory retained artifacts in `PrivateResearch` and patcher build outputs: firmware packet ZIPs, extracted kernel/root/app filesystem images, bootloader, device trees, modules, scripts, executable ELF files, dynamic linker and libraries. Record path, size, SHA-256, firmware version, extraction method, retention location, access classification and missing items.
