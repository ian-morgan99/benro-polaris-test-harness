#!/usr/bin/env bash
# Offline v115 startup differential. Evidence directory must be new.
set -euo pipefail
umask 077
if [[ $# != 4 ]]; then echo "usage: $0 ROOTFS ABI_JSON GUEST_BUNDLE NEW_EVIDENCE_DIR" >&2; exit 2; fi
here=$(cd "$(dirname "$0")" && pwd)
root=$(realpath "$1"); abi=$(realpath "$2"); bundle=$(realpath "$3")
mkdir "$4"; out=$(realpath "$4")
echo "1d893a99f14b7c5865de6449ffeae490cfe4e1694d8ffb2f5d925e72ac956d94  $bundle/lock.json" | sha256sum -c --status
image=$(jq -r .image "$bundle/lock.json")
[[ $(docker image inspect --format '{{.Id}}' "$image") == "$image" ]]
echo "$(jq -r .sha256.kernel "$bundle/lock.json")  $bundle/kernel" | sha256sum -c --status
for arm in closure legacy; do
    args=()
    if [[ $arm == legacy ]]; then args+=(--legacy-ports); fi
    python3 "$here/pgphoto-prepare.py" --root "$root" --abi "$abi" --bundle "$bundle" --output "$out/$arm.gz" "${args[@]}"
    chmod 444 "$out/$arm.gz"
    sha256sum "$out/$arm.gz" >> "$out/hashes.txt"
    docker run --pull=never --rm --read-only --network none --cap-drop ALL --security-opt no-new-privileges \
      --pids-limit 32 --user 65534:65534 --cpus 1 --memory 384m \
      --mount "type=bind,src=$bundle/kernel,dst=/kernel,readonly" --mount "type=bind,src=$out/$arm.gz,dst=/guest,readonly" \
      --entrypoint /usr/bin/timeout "$image" -k 2 45 /usr/bin/qemu-system-arm \
      -accel tcg -machine virt,highmem=off -cpu cortex-a7 -smp 1 -m 192 -nodefaults -display none \
      -monitor none -serial stdio -nic none -no-reboot -kernel /kernel -initrd /guest \
      -append 'console=ttyAMA0 rdinit=/init quiet panic=1' > "$out/$arm.log" 2>&1
    grep -q '^GUEST_FINISHED' "$out/$arm.log"
done
python3 "$here/pgphoto-result.py" "$out" > "$out/result.json"
