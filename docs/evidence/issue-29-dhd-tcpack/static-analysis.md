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
2. **Exposure and permissions of the `tcpack_suppress` iovar** on this build
   (`/proc/net/<iface>/iovar`, `dhd_priv`, or private ioctl) - requires read-only
   device inventory, which #68 owns.
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
