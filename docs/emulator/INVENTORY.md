# Firmware/kernel/board inventory and acquisition map (issue #27, WBS 1.1–1.3)

**Generated:** 2026-11-05
**Repository:** `ian-morgan99/benro-polaris-test-harness`
**Branch:** `main` @ `30013d6`
**Source of truth for artifacts:** `PrivateResearch` (private, not on GitHub)

## WBS 1.1 — Located retained artifacts

### Stock firmware (user-supplied, not redistributed)

| Artifact | Path | Size | MD5 | SHA-256 | Version | Classification |
|---|---|---|---|---|---|---|
| Stock FwPkt.zip | `PrivateResearch/.astro-v94.TrArG1/firmware/FwPkt.zip` | 68,599,228 B | `90bdad511f556f25a2904ae9d2980102` | `f980fe5245a1f85b58d0c2db523402d1af72ddfff782acba99a4eadfdca54d2f` | not decoded from ZIP | proprietary, user-supplied, gitignored |

Notes:
- Supplied by equipment owner (Ian Morgan), not from a public Benro download endpoint.
- Pre-existing on build host on or before 2026-08-22.
- No second known-good copy available; treated as one-shot user-supplied input.
- `.gitignore` excludes `FwPkt*.zip`, `*.ubifs`, `*.bin` — never committed to GitHub.
- Provenance recorded in `PrivateResearch/.astro-v94.TrArG1/firmware/SOURCE.md`.

### Patched FwPkt builds (local only, gitignored)

| Build | Path | MD5 | libgphoto2 commit | patcher commit | Status |
|---|---|---|---|---|---|
| b-0823 (pentax-only) | `builds/2026-08-23/FwPkt.zip` | `25403283…1aca1837e` | `da8c33482` | pentax-only build | pentax-only |
| b-0827r2 (combined 720p60) | `builds/2026-08-27-combined-720p60/FwPkt.zip` | `e4a6a37d…ca8f45d4` (rebuild2) | `da8c33482` | layered on b-0823 + HDMI | combined (rebuild2) |
| b-0829 (libgphoto2-only repackage) | `builds/2026-08-29-libgphoto2-only/FwPkt.zip` | `25403283…1aca1837e` (=b-0823) | `da8c33482` | repackaged from b-0823 | libgphoto2-only repackage |
| b-0830 (padded-appfs) | `builds/2026-08-30-padded-appfs/FwPkt.zip` | `92da8883…b5fa22b94067` | `da8c33482` | padded-appfs experiment | hypothesis test |
| b-0905 (combined pentax256+hdmi720p60) | `builds/2026-09-05-combined-pentax256-hdmi720p60/FwPkt.zip` | `e6fe0c9c…40b474e6` | `91f4c9146` | patcher `1c2f9ce` + HDMI `b3aa306` | combined pentax256+hdmi |
| b-0907cand (K-1II/K-3III candidate) | `builds/2026-09-07-k1ii-k3iii-candidate/FwPkt.zip` | `955ec67a…40ec94e` | `6aa3e4e66` | patcher `af5b0d3` | K-1II/K-3III candidate |
| o-fixed (pre-iolibs) | `out/k1ii-k3iii-fixed/FwPkt.zip` | `dcfeb60c…4588148` | release 2.5.34 | patcher ~`fbf5539` era | fixed (pre-iolibs) |
| o-v3 (SUPERSEDED, vanilla libgphoto2) | installed on device Sep 7 | `5491835a…d5518018` | vanilla 2.5.34 (NOT fork) | patcher ~`f3dbff4`+`9e47a34` | v3 — SUPERSEDED |
| o-v4 (CORRECT fork build, installed baseline) | `out/k1ii-k3iii-fixed-v4-20260908/FwPkt.zip` | `d0674ef6…e7a8fd90` | `6aa3e4e66` (Pentax fork) | patcher main @ `a975933` era | v4 — installed baseline |
| o-v5 (SUPERSEDED by o-v5b) | `firmware-packets/o-v5/FwPkt.zip` (PrivateResearch) | `751250ce…edbc87a` | `0f0858087` | patcher main @ `2c52c40` | v5 — SUPERSEDED |

All patched builds are gitignored, live on disk/SMB/SD card only, and are not published to GitHub. Provenance registry is in `PrivateResearch/.astro-v94.TrArG1/docs/FWPKT-PROVENANCE-CONTRACT.md`.

### Extracted pgphoto.stage2ondisk (source binary for ARM regions)

| Artifact | Path | Size | SHA-256 | Firmware | Notes |
|---|---|---|---|---|---|
| `pgphoto.stage2ondisk` | `PrivateResearch/.astro-v94.TrArG1/out/o-v18m-k1ii-pgphoto-readiness-20261029/stage2-ondisk/ondisk/pgphoto.stage2ondisk` | 7,801,576 B | `8973d9486e0207446cc111234ed39e878676ce1f3edca2967a97db86c8d9eaac` | 6.0.0.54.90 | Extracted appfs; source binary for `captureBulbImage` region |

### ARM code regions (provenance-pinned, referenced by harness sidecars)

| Region | Sidecar | Region SHA-256 | Symbol | vaddr | Size | Mode | Source issue |
|---|---|---|---|---|---|---|---|
| `capture_image_with_Bulb` | `tests/fixtures/arm/stage2_region.json` | `23398c6efc7c737c1b2b91664a98b45a2b7bc01f97e704c3ccb4bf3fc4a618c3` | `capture_image_with_Bulb` | `0x101aac` | 2,176 B | ARM | patcher #251 |
| `captureBulbImage` | `tests/fixtures/arm/capture_bulb_image_region.json` | `9917d69fc9228d2234e9e735cd3ad3c102941ebfb501de3c91681a695517073e` | `captureBulbImage` | `0x20604` | 684 B | ARM | patcher #251 |

The `.bin` files themselves are **not** in the harness repo (gitignored). Only sidecars (coordinates + hashes) and `.placeholder` retrieval docs are tracked.

### Traces and evidence (PrivateResearch)

| Evidence dir | Contents | Relevance |
|---|---|---|
| `polaris-evidence/bulb40s-20261025/` | `pgphoto-wait-loop.txt`, `source-binary.sha256` (`3e340e9e…`) | `capture_image_with_Bulb` disassembly, hold loop, release point |
| `.astro-v94.TrArG1/docs/evidence/bulb40s-20261025/` | same | same |
| `.astro-v94.TrArG1/docs/evidence/issue-251-dispatch-20261030/` | dispatcher follow-up source + ARM tests | #251 dispatcher fix |
| `.astro-v94.TrArG1/docs/evidence/issue-251-harness-emulator-20261031/` | `arm-tests.txt`, `cancel-return.txt`, `harness-real-arm-probe.txt` | real ARM probe, cancel-return defect |
| `.astro-v94.TrArG1/docs/evidence/issue-251-recovery-20261030/` | build and source recovery evidence | #251 recovery |
| `.astro-v94.TrArG1/docs/evidence/k1ii-controls-20261103/` | `232-descriptor-parse.md` … `253-readiness.md`, `handoff/` | K-1 II control defects (attached to this PC) |
| `.astro-v94.TrArG1/docs/evidence/k1ii-controls-20261104/SUMMARY.md` | K-1 II summary | #233/#234/#235 live results |
| `.astro-v94.TrArG1/docs/evidence/rig-le-advertising-absent-20261027/SUMMARY.md` | BLE wake blocker | firmware defect, not host |
| `.astro-v94.TrArG1/docs/evidence/o-v18j-k1ii-mainline-20261028-r2/SUMMARY.md` | .87 deployed evidence | K-1 II partial Polaris evidence |
| `.astro-v94.TrArG1/docs/evidence/bulb40s-20261023/` … `bulb-trace-evidence-summary-20261012.md` | bulb traces | Bulb capture traces |
| `.astro-v94.TrArG1/docs/evidence/native-arm-toolchain-20261028/SUMMARY.md` | native ARM toolchain | QEMU/user-mode toolchain eval |

### Build/analysis tooling (PrivateResearch)

| Tool | Path | Purpose |
|---|---|---|
| `analyze_pgphoto.py` | `.astro-v94.TrArG1/container/analyze_pgphoto.py` | pgphoto binary analysis |
| `build_ptp2.sh` | `.astro-v94.TrArG1/container/build_ptp2.sh` | libgphoto2 ptp2 build |
| `patch.sh` | `.astro-v94.TrArG1/container/patch.sh` | FwPkt patching |
| `polaris_bulb_dispatch_patch.py` | `.astro-v94.TrArG1/container/polaris_bulb_dispatch_patch.py` | bulb dispatch patch |
| `polaris_bulb_poll_budget_patch.py` | `.astro-v94.TrArG1/container/polaris_bulb_poll_budget_patch.py` | bulb poll budget patch |
| `polestar_bulb_patch.py` | `.astro-v94.TrArG1/container/polestar_bulb_patch.py` | polestar bulb patch |
| `polestar_fwver_patch.py` | `.astro-v94.TrArG1/container/polestar_fwver_patch.py` | polestar fwver patch |
| `hdmi_geometry_patch.py` | `.astro-v94.TrArG1/container/hdmi_geometry_patch.py` | HDMI geometry patch |
| `extract_dead_site_bytes.py` | `.astro-v94.TrArG1/container/extract_dead_site_bytes.py` | dead site byte extraction |
| `gen_firmwareinfo.py` | `.astro-v94.TrArG1/container/gen_firmwareinfo.py` | firmwareInfo generation |
| `gen_ssh_hook.py` | `.astro-v94.TrArG1/container/gen_ssh_hook.py` | SSH hook generation |
| `dbg_patch.py` | `.astro-v94.TrArG1/container/dbg_patch.py` | debug patch |
| `build_fullstack.sh` | `.astro-v94.TrArG1/container/build_fullstack.sh` | full stack build |

### Missing / not yet located

| Item | Status | Notes |
|---|---|---|
| Kernel image (zImage/Image) | not located | No kernel boot evidence found; QEMU system-mode feasibility unproven (#25) |
| Device tree (.dtb) | not located | |
| Bootloader | not located | |
| Kernel modules | not located | |
| Root filesystem image (appfs.ubifs) | partial | Inside stock FwPkt.zip; not extracted separately in located inventory |
| Dynamic linker / libc | partial | Inside pgphoto.stage2ondisk dependency closure; not separately inventoried |
| `/dev` nodes, sysfs/proc paths for Polaris board | not documented | Required for WBS 1.4 |
| SoC identification | not documented | Required for QEMU system-mode feasibility (#2.5) |

## WBS 1.2 — CPU/ABI determination

### pgphoto.stage2ondisk (7,801,576 B)

From `tests/fixtures/arm/stage2_region.json` sidecar:
- ELF flags: `0x5000200` — Version5 EABI, soft-float ABI
- Architecture: ARM (NOT Thumb) — confirmed by `harness-real-arm-probe.txt`: "ELF flags 0x5000200 Version5 EABI soft-float = ARM mode, NOT Thumb"
- Symbol `capture_image_with_Bulb` at vaddr `0x101aac`, size 2,176 B
- Region SHA-256: `23398c6efc7c737c1b2b91664a98b45a2b7bc01f97e704c3ccb4bf3fc4a618c3`
- Load address: `0x101aac` (must load at original vaddr; PC-relative literal pools)
- `.got`: `0x2f7000-0x2f7674`, `.data`: `0x2f7678-0x3117f4`, `.bss`: `0x3117f8-0x37cea4`
- `code_address`/`code_size`: `0x101000` / 2,605,056 (one page-aligned range spanning artifact + globals)
- Max instructions: 5,000
- Stub profile: `lifecycle`, command: `capture_image_with_Bulb`

### captureBulbImage region (684 B)

From `tests/fixtures/arm/capture_bulb_image_region.json` sidecar:
- Mode: ARM
- Symbol `captureBulbImage` at vaddr `0x20604`, size 684 B
- Region SHA-256: `9917d69fc9228d2234e9e735cd3ad3c102941ebfb501de3c91681a695517073e`
- Source binary SHA-256: `8973d9486e0207446cc111234ed39e878676ce1f3edca2967a97db86c8d9eaac`
- Firmware version: 6.0.0.54.90
- Load address: `0x20604` (must load at original vaddr)
- `code_address`/`code_size`: `0x20000` / 3,526,656 (one range from .text through end of .bss)
- Max instructions: 5,000
- Stub profile: `lifecycle`, command: `captureBulbImage`

### Known emulation result (real ARM code, 2026-11-05)

From `tests/test_arm_emulator.py` run against provenance-pinned `stage2_region.bin` (SHA-256 `23398c6efc7c737c1b2b91664a98b45a2b7bc01f97e704c3ccb4bf3fc4a618c3`) with `POLARIS_FIRMWARE_ROOT` set to the retained `pgphoto.stage2ondisk`:

- **Emulator:** Unicorn 2.1.4, ARM mode (not Thumb), FPEXC.EN enabled
- **Binary:** `pgphoto.stage2ondisk`, ELF32 ARM EABI5 soft-float, entry `0x15ed8`, interpreter `/lib/ld-linux.so.3`
- **Source binary SHA-256:** `8973d9486e0207446cc111234ed39e878676ce1f3edca2967a97db86c8d9eaac`
- **Case 4 (stop failure preserves primary error):** PASS — 154 instructions, 17 stub calls from original `pgphoto.stage2ondisk` code
- **Cases 1-3, 5-9:** execute real ARM instructions (12-154 instructions, up to 190 stub calls) then correctly report `not_evaluated` for edges the region doesn't declare, or timeout on the wait loop without `event_return_value=-52`
- **VFP/NEON:** Previously recorded as "unsupported_instruction" at `0x1002` with `instructions_executed: 0` — this was an uninitialised-FPU artifact, not an engine limit. Setting FPEXC.EN bit 30 lets VFP instructions execute correctly (per AGENTS.md precedent, commit 712b996).

The earlier probe recorded `result: unsupported_instruction, instructions_executed: 0, unsupported_at: 0x1002` — that was with FPEXC.EN cleared (Unicorn's default), which rejects every VFP encoding. It is not evidence that QEMU can boot the stock kernel, nor that the engine cannot decode VFP.

## WBS 1.3 — Linux boot and services dependency graph

### What is known

| Item | Value | Source |
|---|---|---|
| Firmware version running on attached Polaris | `6.0.0.54.70` (checked 2026-10-25) | `CURRENT-STATE.md` |
| Live `polestar_app` SHA-256 | `a5e58ffd76670cf9bdc76560a859d2e037ca4320d37bb3c2d93d482cf459ac02` | `CURRENT-STATE.md` |
| `SP_GimbalSetAxsi` address | `0x000636e8` (emits MCU `J` frame) | `CURRENT-STATE.md` |
| pgphoto process | runs in Stage-2 runtime, owns camera lifecycle | `POLARIS-RUNTIME-CONTRACT.md` |
| TCP port 9090 | Polaris command port | harness docs |
| Astro Web UI | served by Polaris runtime | `ASTRO_WEBUI.md` |
| USB camera | Pentax K-1 II (`25fb:0183`) or K-3 III (`25fb:0189`) | `AGENT-PROMPTS.md`, `CAMERA-PERSONALITIES.md` |
| BLE wake | blocked — rig never advertises LE | `rig-le-advertising-absent-20261027/SUMMARY.md` |

### What is NOT known (explicit unknowns)

| Unknown | Why it matters | Where to resolve |
|---|---|---|
| SoC model | Determines QEMU system-mode board compatibility | WBS 1.4 / #2.5 |
| Kernel version/config | Needed for system-call surface mapping | WBS 1.4 |
| Device tree bindings | Needed for peripheral emulation | WBS 1.4 |
| Bootloader | Needed for full-system boot feasibility | WBS 2.5 |
| `/dev` nodes, sysfs/proc paths, ioctl numbers | Device abstraction boundary | WBS 4.1/4.5 |
| Init entrypoints and service startup order | Process environment reconstruction | WBS 3.2 |
| Mount layout, environment variables, watchdogs | Virtual filesystem fidelity | WBS 3.1 |
| pgphoto IPC and port usage | Service dependency graph | WBS 1.3 / #2.4 |
| Dynamic linker and library load paths | ELF dependency closure | WBS 1.5 |

### Dependency graph (partial, to be completed in WBS 1.4)

```
firmware (FwPkt.zip)
  ├── appfs.ubifs (root/app filesystem)
  │     ├── /app/pgphoto.stage2ondisk  (main runtime, ELF ARM soft-float)
  │     ├── /app/polestar_app          (UI/app server)
  │     ├── libgphoto2_port.so.12
  │     ├── libgphoto2.so.6
  │     ├── camlibs/ (ptp2, pentax, ...)
  │     ├── iolibs/ (usb1, ...)
  │     └── loader / dynamic linker
  ├── kernel (not located)
  ├── device tree (not located)
  └── bootloader (not located)

pgphoto.stage2ondisk at runtime:
  ├── loads libgphoto2_port + camlibs + iolibs
  ├── opens USB device (Pentax K-1 II / K-3 III)
  ├── binds TCP port 9090 (Polaris command protocol)
  ├── spawns watchdog / restart supervisor
  └── exposes capture / settings / live view / mount interfaces
```

### Acquisition gap plan

| Missing artifact | Recovery path | Blocker? |
|---|---|---|
| Kernel image | Not in located inventory; may be inside FwPkt.zip appfs or separate partition | Yes for QEMU system mode (#2.5) |
| Device tree | Not located | Yes for peripheral emulation (#1.4) |
| Bootloader | Not located | Yes for full-system boot (#2.5) |
| Root filesystem | Inside stock FwPkt.zip; extractable read-only | No — can extract from FwPkt.zip |
| Dynamic linker/libc | Inside pgphoto dependency closure; extractable from stage2-ondisk | No |

Never require privileged device changes or speculative flashing. If unobtainable, design a marked adapter and reduce claimed coverage.

## Gates

- **G1:** signed-off inventory and executable/dependency/device graphs with hashes and explicit unknowns; at least one original executable available locally for controlled launch.
  - `pgphoto.stage2ondisk` (7.8 MB) is available locally in PrivateResearch — satisfies "at least one original executable."
  - Kernel/device tree/bootloader not located — explicit unknowns recorded above.

## Next

Proceed to WBS 2 (architecture spike and executable proof of life): evaluate `qemu-arm` Linux user mode against the ARM soft-float ABI, build a synthetic ARM smoke binary, and attempt to launch `pgphoto.stage2ondisk` in an isolated filesystem with stubbed device boundaries.
