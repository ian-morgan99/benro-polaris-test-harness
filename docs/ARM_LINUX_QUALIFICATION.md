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


## Generic ARM system boot and disk isolation

The optional `arm-system` command boots a public Debian ARMv7 kernel and a
BusyBox initramfs under QEMU TCG on a generic Cortex-A7 machine. A synthetic
4 MiB disk provides a fixed header and a sector reserved for state. Each fresh
boot verifies that sector is zero, writes a per-boot challenge, reads it back
and powers down. A deliberate reboot of the first overlay receives a second
challenge and must report the **previous** challenge from disk. A separate
container must start clean again. The runner checks the immutable base hash
afterwards. JSON contains the pinned inputs, serial transcripts and exit status.

Provision separately on a Linux/amd64 host with Docker, bash, dpkg-deb, GNU
cpio, gzip and Python 3.12. The build uses an empty context. Keep the two
public ARM packages and tools image locally for subsequent offline runs.
The preparation script checks the complete installed tools package inventory
and both ARM package digests. If Debian rotates a pinned version, retrieve the
matching signed archive or qualify a revised lock explicitly.

    mkdir -p build/arm-system
    work=$(realpath build/arm-system)
    docker build --iidfile "$work/image.id" - < scripts/arm-system/Dockerfile
    image=$(cat "$work/image.id")
    id=$(docker create "$image" /bin/true)
    docker cp "$id:/inputs" "$work/inputs"
    docker rm "$id"
    scripts/arm-system/prepare.sh "$work"/inputs/linux-image-6*.deb \
      "$work"/inputs/busybox*.deb "$image" "$work/bundle"

Retain and review the printed lock digest with its bundle. Run offline with
`polaris-harness arm-system --bundle "$work/bundle" --lock-sha256 "$approved_digest"`.
The runner checks the image ID and each input digest before boot. Both QEMU
and the tools container have networking disabled. Each container runs as UID
65534 with no capabilities, a read-only root, three read-only file mounts, a
private tmpfs overlay, and CPU, memory, process and time limits. No KVM,
USB devices, guest credentials, port forwarding or host bridges are required.

The 2026-11-24 receipt records two independently assembled bundles and six
boots. That overlay scenario uses public guest inputs only.

## Packaged v115 pgphoto system startup

From a validator environment with the package installed, run the following from
the checkout. ROOT is the private extracted rootfs with appfs under /app.
ABI is the v115 stage2.json receipt from the user-mode qualification above.
BUNDLE is the locked generic ARM guest bundle recorded in the system receipt.
Use an unused evidence path on a local private filesystem.

    scripts/arm-system/run-pgphoto.sh "$ROOT" "$ABI" "$BUNDLE" build/v115-pgphoto-run

Two fresh RAM-only guests run the same packaged executable from /app/bin with
no arguments, the wrapper's Stage-2 environment and no optional SD overrides.
The supervisor and polestar_app are absent. Only loopback is available. The
first guest contains the audited ELF closure; the second also contains the
12 shipped files from /app/lib/libgphoto2_port/0.12.0. Their hashes were
independently extracted from the v115 appfs. Both generated initramfs files
contain private firmware. Keep the evidence directory private. Publish only
the receipt, hashes and reviewed observations.

Read result.json alongside closure.log and legacy.log. A passing startup
requires the Stage-2 slots, both IPC queues, a TCP 8080 socket inode matching
a pgphoto file descriptor, an HTTP 200 multipart stream response from that
listener, the expected camera-init result for the selected arm, and exit
after SIGTERM. A multipart response establishes HTTP readiness. Frame
delivery requires a separate test. The guest runs a direct executable launch;
it cannot qualify wrapper supervision, polestar_app command handling or
physical camera behaviour. Next probe: replay a previously recorded
read-only IPC request into the real service's no-camera state, matching its
queue type, response framing and result to the existing evidence contract.
