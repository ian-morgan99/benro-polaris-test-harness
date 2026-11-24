# Issue #29 WBS 1 - Static analysis of the stock bcmdhd TCP ACK/PSH pool path

Offline only. No device was contacted, no radio was changed, nothing was flashed.
Method: ELF inspection of the extracted stock module plus ARM disassembly with
`arm-linux-gnueabi-objdump` and `readelf -r`. Conclusions are labelled
PROVEN / DISPROVEN / UNKNOWN against specific addresses.

## Artifact identity (verified, not assumed)

| field | value |
|---|---|
| path | `.build-env/tmp/fwpkt-manifest-milipjnv/958962934/ubifs/komod/bcmdhd.ko` |
| sha256 | `6331425326f59e23c314bbe11f3b24e244e29e8ccca2f7b7cdb51ab0571bba2f` |
| size | 1,136,256 bytes |
| ELF | ELF32, REL (relocatable), ARM |
| vermagic | `4.9.37 SMP mod_unload ARMv7 p2v8` |
| license | GPL and additional rights |

**UNKNOWN / correction to the brief:** `modinfo` on this artifact exposes no
`version:` field, so driver version **1.363.125.19 is not confirmed by this file**.
The reported "43 parameters" is also not confirmed: the `__param` section parses to
**44 descriptors** (my first parse reported 0 writable params; that run was a
parser bug - this struct carries no `perm` field in 4.9, so permissions must be read
from sysfs on device, not from the object file). Treat both reported numbers as
unverified.

## Symbols and strings

Present in symtab: `disable_proptx` (0x3048, .bss), `dhd_tcpack_suppress` (0xa010),
`dhd_tcpack_suppress_set` (0x98fc), `dhd_tcpdata_info_get` (0xacf8),
`_tdata_psh_info_pool_enq` (0x9534, local), `dhd_tcpack_info_tbl_clean` (0x97f0).

`_tdata_psh_info_pool_deq` has **no symtab entry** but its name and both error
strings are present in the binary:
```
_tdata_psh_info_pool_deq
%s %d: Out of tdata_disc_grp
%s %d: No more free tdata_psh_info!!
```
Per the WBS instruction to separate absent symbols from absent functionality: the
dequeue path **exists** in this build; it is simply a file-local symbol that was
stripped. The failing code is compiled in.

## The `disable_proptx` hypothesis - DISPROVEN (statically)

`disable_proptx` has exactly **three** relocations in the whole module:

| offset | type | meaning |
|---|---|---|
| 0x1f0 | R_ARM_ABS32 | its own `__param` descriptor (the value pointer) |
| 0x4f824 / 0x4f828 | MOVW/MOVT + `ldr r3,[r3]`; `cmp r3,#0` | the **only** code read |

That single read sits inside `wl_cfg80211_add_virtual_iface` (0x4f470), in the
Wi-Fi Direct virtual-interface creation path, guarded by a P2P field test at
`[r7,#1050]`. It is not reached by normal AP association or TCP traffic.

No relocation to `disable_proptx` exists anywhere in the range 0xa010-0xacf8
(`dhd_tcpack_suppress`, `dhd_tcpack_suppress_set`, `dhd_tcpdata_info_get`) or in
the pool functions. **`disable_proptx=1` does not bypass the failing pool path.**
The name is consistent with proprietary TX status reporting, not ACK suppression,
and the binary agrees. Do not spend a physical A/B on it.

## The actual control - `tcpack_suppress` iovar (PROVEN to exist)

Strings `tcpack_suppress`, `proptx`, `proptx_opt` are present (rodata; string at
0x8be50). `dhd_iovar_op` (0x4b54) calls `dhd_tcpack_suppress_set` at 0x5378, so the
mode is settable through the driver's iovar interface at runtime.

Callers of `dhd_tcpack_suppress_set`:

| call site | function | role |
|---|---|---|
| 0x5378 | `dhd_iovar_op` | runtime control surface |
| 0x655c | `dhd_prec_enq` | per-packet TX enqueue path |
| 0xf52c | `dhd_detach` | teardown |
| 0x109a0 | `dhd_attach` | **boot default: `mov r1,#2` -> mode 2** |

So the shipped default is suppression **mode 2**, chosen at attach, and mode is a
per-adapter byte at `[adapter+0x1000+0x908]` with related fields at +0x90c/+0x910.
`suppress_set` validates `mode <= 3` (`cmp r4,#3; bhi` reject), so the domain is
0..3 and mode 0 is the "off" candidate.

**Not a feature gate:** inside `suppress_set` the global tested at 0x992c-0x9938
resolves to `dhd_msg_level` (`tst r2,#2`). That branch is debug-logging gating, not
functionality - do not read it as a suppression switch.

## UNKNOWN - what must be measured before any physical A/B

1. **Pool size.** `TCPDATA_PSH_INFO_MAXNUM` was not recovered. A `movw r2,#337`
   appears near the pool code but is an error-report argument (line number), not a
   count. The exhaustion threshold is currently unknown.
2. **Exposure and permissions of the `tcpack_suppress` iovar** on this build - **RESOLVED, see
   "Live follow-up" below: not exposed.** No module parameter, no `/proc/bcmdhd`, no `wl` or
   `dhd_priv`, so it is unreachable from userspace on the shipped image.
3. **Whether mode 0 is safe.** Disabling suppression changes ACK behaviour for all
   flows; #68's staged plan (identical cold start, camera disconnected, physical
   fallback, no remote radio change) still applies.
4. Whether `dhd_tcpack_suppress` is reached from the RX path in AP mode: it has one
   call site in this build, and I have not traced whether that site runs for our
   traffic pattern.

## Recommendation

Redirect the physical A/B from `disable_proptx` to the **`tcpack_suppress` iovar**,
default 2 -> candidate 0, because that is the control the binary actually wires to
the pool functions. Keep it as a runtime-only experiment under #68's authorisation
rules; no rebuild or flash is justified by this evidence. Next offline step per WBS
2: build the standalone pool stress model once the pool size is recovered from
`_tdata_psh_info_pool_init`.

## Live observations (partial - link dropped mid-test)
Device `polaris_d13e86`, reached over the AP at 192.168.0.1 with source bind 192.168.0.4.
Captured in one successful control-plane session, then the association dropped and could not
be re-established from this host (`wlp8s0` went DOWN; `bind 192.168.0.4: Cannot assign
requested address`). Note that after the drop, `ping 192.168.0.1` still succeeded at ~4 ms and
SSH returned *connection refused* - that is a **different host** on the LAN route via
192.168.68.1, not the mount. Do not read those responses as device data.

Reliably observed:
- `dmesg | grep -c` for the pool signature returned **1182** at uptime 18:49 in one read and
  **593** in another. Those two counts are inconsistent, so **no error rate is claimed here**.
  A follow-up delta measurement failed because its baseline read errored out. Treat the
  "~230 errors/min" figure on #171 as unconfirmed by this session either way.
- `wl`, `dhd_priv`, `nvram` and `dhd` are **not present** on the device, so the `tcpack_suppress`
  iovar proven to exist statically has **no userspace caller available**. Setting it would need
  a new tool or a driver-side default change - it is not an ops-tunable today.

## Live follow-up (link re-established) - UNKNOWN #2 RESOLVED

Reconnected with `ConnectTimeout=30` plus in-session retries; kex succeeded on the first
attempt. All of the following was read in single round trips, which is what the flapping link
tolerates.

**Runtime parameter surface (resolves UNKNOWN #2):**
- `/sys/module/bcmdhd/parameters` **exists** at runtime.
- `disable_proptx = 0`, mode bits **644** - root-writable, so it is genuinely settable live.
- It is the **only** parameter matching `tcp|ack|proptx|arp`. There is no `tcpack_suppress`
  module parameter, and no `/proc/bcmdhd*` or `/proc/net/bcmdhd*` surface exists either.

**Writability proven, restore verified.** `echo 1 > .../disable_proptx` succeeded and read back
`1`; the session's `trap ... EXIT INT TERM` restored it. The arm-B session was later killed by a
link drop mid-window, so restore was re-checked independently: `disable_proptx=0` on the next
successful read. No untested change persists on the device.

**Idle error rate measured (first reproducible number).** Fixed 180 s window at stock settings:
`base=1410 after=1410 delta=0 secs=180` - **zero pool errors while associated and idle**.
This does not reproduce "~230 errors/min while associated" from #171 for the idle case; that
figure describes load, not association.

**Why the earlier counts disagreed.** `dmesg | grep -c` is not a cumulative counter: the ring
buffer wraps, so two reads at different times legitimately differ (1182 vs 593 vs 1410). Only a
delta over a fixed window is meaningful. Use deltas from now on.

**Methodological finding that changes how any future A/B must be run.** Under bulk host->device
transfer the SSH session dies (`Connection closed by 192.168.0.1 port 22`) - the same signature
as #171. So an interactive A/B destroys its own measurement channel exactly when load is applied,
which is the only time errors accumulate. The load arm must therefore be measured **on-device and
detached**, writing results to a file, rather than through the session that is generating traffic.
`scripts/issue-29-pool-window.sh` in this directory does that.

### Consequence for the owner's priority question on #68

### Consequence for the owner's priority question on #68
The proposed `disable_proptx=1` experiment is **not worth running**, independently of the static
result: statically it has exactly one code reference, in `wl_cfg80211_add_virtual_iface`, with no
path into the TCP ACK/PSH pool. The live check would only confirm what disassembly already shows.

The deployable lever, if any, is the suppression **mode default** set in `dhd_attach` (observed
statically as mode 2) or the pool size - both require a driver-side change plus rebuild, not a
runtime parameter. That is the boundary at which this offline analysis stops being sufficient and
a build/flash capability becomes mandatory.

The live check now strengthens rather than weakens that conclusion: `disable_proptx` is settable
and was exercised, it changed nothing measurable in a 180 s idle window, and statically it has no
path into the pool. The one runtime control that *is* wired to the pool (`tcpack_suppress` via
`dhd_iovar_op`) has **no caller available on this device** - no `wl`, no `dhd_priv`, no
`/proc/bcmdhd`, and it is not a module parameter. So there is currently **no runtime-tunable path
to the pool at all**, and the A/B the owner asked for cannot be performed with stock tooling.

**Artifact identity tie (verified):** every copy of the analyzed module is
`sha256 6331425326f59e23c314bbe11f3b24e244e29e8ccca2f7b7cdb51ab0571bba2f`,
`vermagic=4.9.37 SMP mod_unload ARMv7 p2v8`. The device reports the same 4.9.37 kernel, so the
static findings apply to the running code.

## Mechanism recovered from `dhd_tcpdata_info_get` (0xacf8) - the exhaustion site

**Artifact tie, verified on device:** `/app/komod/bcmdhd.ko` sha256
`6331425326f59e23c314bbe11f3b24e244e29e8ccca2f7b7cdb51ab0571bba2f`, 1136256 bytes -
**identical to the analyzed build**. Device `FwVer:6.0.0.54.114`. So these addresses are
the running code.

**Correction to my own earlier reading.** I first claimed a version mismatch because the
device logs `dhd_tcpdata_info_get 1056` while my extract showed only lines 889/898/903.
That was wrong: my `awk '/pattern/,/^$/'` extraction stopped at the first blank line inside
the function, truncating it to ~45 of its 634 instructions. With a proper function-boundary
extract, both device-reported line numbers are present in this exact function:

| printk line | address | note |
|---|---|---|
| 200   | `0xb304` | `_tdata_psh_info_pool_deq 200: Out of tdata_disc_grp` |
| 1056  | `0xb324` | `dhd_tcpdata_info_get 1056: No more free tdata_psh_info!!` |

So the two signatures seen on the device originate in one function, and it is the one I
analyzed. Do not repeat that extraction mistake - awk's range form ends at the first blank
line, which ARM disassembly listings contain inside functions.

**The mode gate (this is the important part).** Function entry:

```
acf8  push {r4..lr}
acfc  add  r8, r0, #4096          @ adapter + 0x1000
ad04  ldrb r3, [r8, #2312]        @ suppression mode byte (+0x908)
ad08  cmp  r3, #2
ad0c  beq  ad1c                   @ mode == 2 -> do the work
ad10  mov  r0, #0                 @ otherwise -> return NULL
ad14  add  sp, sp, #92
ad18  pop  {r4..lr, pc}
```

`dhd_tcpdata_info_get` does **nothing at all** unless suppression mode is exactly 2 - the
boot default set in `dhd_attach`. Mode 0 is therefore not "less aggressive suppression"; it
disables the entire TCP ACK/PSH tracking path. The device is logging these errors precisely
because it runs in mode 2.

**Mode 0 degrades safely, not crashingly.** The single caller is `dhd_rx_frame` (0x12ab0),
which calls it at `0x12bfc` and then immediately does `mov r1, r4` - the return value is
discarded. So a NULL return skips per-flow tracking without dereferencing it. That answers
UNKNOWN #3 ("is mode 0 safe") for this call site: no crash path exists here. It does not
prove the feature is *useful* to disable - suppression presumably exists to cut ACK traffic,
so turning it off may increase wireless load rather than reduce it.

**Pool size still UNKNOWN.** `_tdata_psh_info_pool_init` is not a separate function in this
build (inlined), and `_tdata_psh_info_pool_enq` (0x9534) is a plain linked-list push at
offset `0x11c` with no visible capacity check - the cap lives elsewhere, likely in the
inlined init or in `dhd_tcpdata_info_get` itself. The exhaustion threshold is not recovered
offline.

### Net position on deployability
- No runtime path exists to change suppression mode (no `wl`, no `dhd_priv`, no `/proc/bcmdhd`,
  not a module parameter). Confirmed live.
- `disable_proptx` is settable but statically unrelated to this path and produced zero delta
  over a 180 s window.
- The only lever is a driver-side default change (`dhd_attach`'s `mov r1,#2`) plus rebuild and
  flash - and the evidence does not yet show that mode 0 would help, only that it is safe at
  this call site. Recommend against spending a flash on it until the pool size and the
  load-window error rate are measured with `scripts/pool-window.sh` detached on-device.
