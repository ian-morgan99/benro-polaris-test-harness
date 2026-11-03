# Final FwPkt package verifier

The package verifier is the pre-flash boundary between a patcher build and the
runtime verifier. It checks the finished immutable `FwPkt.zip`; it does not build,
extract, mount, install, reboot, or contact Polaris.

```bash
polaris-harness package verify \
  --fwpkt FwPkt.zip \
  --manifest package-manifest.json \
  [--stock stock-FwPkt.zip]
```

The manifest is described by
[`schemas/package-manifest.schema.json`](../schemas/package-manifest.schema.json).
It must pin the final packet SHA-256, patcher commit, target profile, libgphoto2
source SHA/kind, and hashes for the managed packet members. A Pentax profile must
identify patched libgphoto2 provenance; an upstream/unknown source is rejected.

The verifier checks:

- one `FwPkt` root and no duplicate ZIP members;
- `FwVer` and `firmwareInfo`;
- every declared component's size and MD5 using the packet's own metadata;
- all expected firmware components;
- final packet SHA-256;
- managed-member SHA-256 values and optional appfs SHA-256;
- exact stock packet SHA-256 and declared unchanged-member drift.

It returns a machine-readable result with separate checks for packet hash, ZIP
layout, firmwareInfo, provenance, managed members, stock comparison and runtime
round-trip. Exit code `0` means package integrity/provenance passed; exit code
`5` means the package gate failed.

## Evidence boundary

A package PASS means only that the immutable archive and its declared provenance
are internally consistent. It does not mean the updater will accept it, the
firmware will boot, the runtime loader will select the intended libraries, a
camera works, or a physical Polaris test passed. `runtime_round_trip` is
explicitly `NOT_TESTED` here; appfs extraction, loader resolution and managed
Stage-2 runtime checks remain the runtime verifier's responsibility.
