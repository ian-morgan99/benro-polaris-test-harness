# ARM Linux loader qualification (#28)

Use this optional, offline diagnostic alongside the runtime manifest gate (artifact
layout, hashes and ELF machine) and the Unicorn function contracts. The patcher
owns builds and binary changes. This gate owns dependency and loader evidence.

## Inputs and measured target

Use an empty private directory for each extraction. Verify source hashes before
extracting. Assemble the stock root volume with the app volume under guest
`/app`. Keep the extracted ELF files, images and complete loader traces private.

The retained stock pgphoto identified by SHA-256
`0308393632dd41d974bd1064e61942655131fe5392959a334cf24623d1bd4a62`
is ELF32 little-endian ARM EABI5, soft-float calling convention, Cortex-A7
ARMv7-A, VFPv4/NEON, interpreter `/lib/ld-linux.so.3`. Its libc requirements
include GLIBC_2.4 and GLIBC_2.7. The v115 root volume contains glibc 2.24.
The loader trace establishes its compiled search directories as
`/lib/a7_softfp_neon-vfpv4` and `/usr/lib/a7_softfp_neon-vfpv4`.
The first is a symlink to the target lib directory. These paths matter when
constructing a reduced guest root. Inspect each new release independently.

## Running the static and user-mode controls

Set ROOT to the assembled private filesystem, STOCK to the stock reference above
and STOCK_SHA to its expected digest. Set QEMU and IMAGE to the pinned static
emulator path and local container image ID for the Linux/amd64 test host. Use
Python 3.12+, GNU readelf, and an existing Docker installation. Provision tools
separately. Execution never pulls an image, installs tools or contacts a device.

    S=/app/lib/stage2
    abi() {
      polaris-harness arm-abi --root "$ROOT" --stock "$STOCK" --stock-sha256 "$STOCK_SHA" \
        --system-dir /lib/a7_softfp_neon-vfpv4 --system-dir /usr/lib/a7_softfp_neon-vfpv4 "$@"
    }
    abi /bin/busybox > busybox.json
    abi --library-dir "$S" --library-dir /app/lib "$S/pgphoto.stage2ondisk" \
      "$S/libpolaris_stage2.so" "$S/libgphoto2.so.6" "$S/libgphoto2_port.so.12" \
      "$S/libgphoto2/2.5.34/ptp2.so" "$S/libgphoto2/2.5.34/pentax.so" \
      "$S/libgphoto2_port/0.12.2/usb1.so" "$S/libgphoto2_port/0.12.2/usbscsi.so" > stage2.json
    user() {
      receipt="$1"; shift
      polaris-harness arm-user --root "$ROOT" --abi "$receipt" \
        --abi-sha256 "$(sha256sum "$receipt" | cut -d' ' -f1)" \
        --qemu "$QEMU" --qemu-sha256 "$QEMU_SHA" --image "$IMAGE" "$@"
    }
    user busybox.json --mode busybox-true > positive.json
    user stage2.json --mode loader-list --preload "$S/libpolaris_stage2.so" > package.json
    user stage2.json --mode loader-list --preload "$S/libpolaris_stage2.so" \
      --omit /lib/a7_softfp_neon-vfpv4/libtimers.so > missing-provider.json

Inspect and retain the static receipt with the verified input and source commit
record before promoting a candidate. The digest binds the two stages. It does
not authenticate a new firmware download. The user-mode stage checks each
included ELF against that receipt. A passing injected-failure control must name
the deliberately omitted provider in the real loader error. Exit codes 0, 1 and
2 indicate PASS, FAIL and NOT TESTED respectively. JSON includes native stderr,
stdout and exit status for executed controls.

Each run copies only the selected ELF closure to a disposable, read-only
container mount. It clears inherited guest environment, drops all capabilities,
runs as UID/GID 65534, disables networking and host device passthrough, and
limits CPU, memory, process count and elapsed time.

## Evidence and remaining layers

The static report includes ARM attributes, interpreter, RPATH/RUNPATH, direct
and transitive dependencies, version requirements and definitions by provider,
and per-object hashes. It models explicit loader paths for this target; it
rejects an unmodelled ld.so.cache. Symbol-version namespace presence alone does
not establish that every relocation or lazy symbol binds. The user-mode
loader check compares the selected main/preload providers to that report.
Plugin execution, shim constructors, IPC readiness, camera traffic and
watchdog behaviour require subsequent scenarios.

The pinned review of jmtanous/benro-dev-env was
`9d1e672fe90edd9c9177076717eb320eec9fcd86` (MIT). Its build-example and SDK
preparation informed the cross-run and hash verification approach; its tool
runner, provisioning and VM scripts informed the disposable guest design.
The implementation here was written independently. The reference RC release
ships source and checksums; its provisioning script computes kernel and disk
digests after building. They are not preverified harness guest inputs.

The next qualification level requires a locked generic ARM kernel, initramfs,
guest packages and base QCOW2, plus two fresh overlays with equivalent outcomes.
Keep guest creation separate from offline runs. Require no guest credentials,
restrict egress, and tie any listener/replay claim to the actual process and
candidate. System mode, guest service replay and physical acceptance remain
independent results. Generic ARM Linux cannot qualify HiSilicon peripherals,
Broadcom Wi-Fi, physical USB/PTP timing or camera behaviour.
