#!/usr/bin/env bash
# Offline assembly from two verified Debian packages and a provisioned tools image.
set -euo pipefail
umask 022
if [[ $# != 4 ]]; then echo "usage: $0 KERNEL.deb BUSYBOX.deb TOOL_IMAGE_ID NEW_BUNDLE_DIR" >&2; exit 2; fi
kernel=$(realpath "$1")
busybox=$(realpath "$2")
image=$3
here=$(cd "$(dirname "$0")" && pwd)
[[ $image =~ ^sha256:[a-f0-9]{64}$ && $(docker image inspect --format '{{.Id}}' "$image") == "$image" ]]
check() { echo "$1  $2" | sha256sum --check --status; }
# A changed transitive tools package requires a new provisioning receipt.
inventory=$(docker run --pull=never --rm --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges --user 65534:65534 --entrypoint /usr/bin/dpkg-query \
    "$image" -W | sha256sum | cut -d' ' -f1)
[[ $inventory == 560a8fdfaf05bd4e21e9fb7fee90528a6f207ca7664ff84089b45e28ce53eb92 ]] || {
    echo "tools package inventory differs from the qualified lock" >&2; exit 1;
}
check 6e64a25739d2f19b03e8bb0166a8c35582e9d83592f3f68e3305209733f49417 "$kernel"
check 2f68dbeaebeda49f3cde34ac421d5add85fb1c837ff7c4a4971945340d9d66ff "$busybox"
mkdir "$4"
out=$(realpath "$4")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
dpkg-deb -x "$kernel" "$work/kernel"
dpkg-deb -x "$busybox" "$work/tools"
r="$work/initramfs"
mkdir -p "$r"/{bin,dev,proc,modules}
install -m755 "$work/tools/bin/busybox" "$r/bin/busybox"
install -m755 "$here/init" "$r/init"
modules="$work/kernel/lib/modules/6.1.0-56-armmp/kernel/drivers"
install -m644 "$modules/block/virtio_blk.ko" "$modules/virtio/virtio_mmio.ko" "$r/modules/"
install -m644 "$work/kernel/boot/vmlinuz-6.1.0-56-armmp" "$out/kernel"
(cd "$r" && find . -exec touch -h -d @0 {} + && find . -print0 | LC_ALL=C sort -z | cpio --null -o -H newc --reproducible --owner=0:0 | gzip -n > "$out/initramfs.gz")
# Stream the disk image out; the tools container never receives a writable host mount.
docker run --pull=never --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
    --user 65534:65534 --tmpfs /state:size=16m,mode=1777 --pids-limit 12 --memory 128m \
    --entrypoint /bin/sh "$image" -ec 'truncate -s 4M /state/raw
    printf %s POLARIS-ABI-DISK | dd of=/state/raw conv=notrunc status=none
    qemu-img convert -f raw -O qcow2 /state/raw /state/base
    cat /state/base' > "$out/base.qcow2"
python3 - "$out" "$image" <<'PY'
import hashlib, json, sys
from pathlib import Path
out = Path(sys.argv[1])
lock = {"schema": 1, "image": sys.argv[2], "sha256": {
    name: hashlib.sha256((out/name).read_bytes()).hexdigest()
    for name in ("kernel","initramfs.gz","base.qcow2")}}
(out/"lock.json").write_text(json.dumps(lock, sort_keys=True, indent=2)+"\n")
PY
sha256sum "$out/lock.json"
