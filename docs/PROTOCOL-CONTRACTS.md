# Polaris protocol contract packs

The protocol contract pack records what is known about a Polaris command without
turning an inferred or destructive operation into an automatic test. The initial
pack is [`contracts/protocol/benro-9090-v1.json`](../contracts/protocol/benro-9090-v1.json).

Validate it with:

```bash
polaris-harness protocol validate \
  --contract contracts/protocol/benro-9090-v1.json
```

## Evidence boundary

Each entry has a safety class, confidence, status, exact request/response shape,
and a provenance reference. The current pack is **provisional**: it is derived
from the OpenPolaris protocol mapping and conflict audit, plus the issue evidence
listed in each entry. A contract-pack PASS means the data is internally
consistent; it does not qualify a firmware build or prove compatibility with a
physical camera. Physical requalification remains required whenever the runtime,
firmware, camera, or packet path changes.

The pack deliberately preserves unresolved commands as `unverified` or
`unknown`. They are useful research records but cannot be selected by the
default safe-probe set.

## Exact command identity

Lookups use the exact `(code, subtype)` pair. The harness does not infer a nearby
command from a sequential number or reuse a code across subtypes. For example,
`264` subtype `4` is the provisional asynchronous capture contract; looking up
`264` without subtype returns no contract. This prevents the historical camera
opcode collisions from becoming executable assumptions.

## Default safe set

The default safe set currently contains only physically observed, verified,
read-only commands:

- `286` camera state
- `770` file listing
- `775` SD status
- `778` battery capacity
- `780` device information
- `802` Wi-Fi-band read

`803` is a physically observed state write and is retained for documentation,
but is not an unattended probe. `789` subtype `2` is explicitly classified as
`media_destructive` and is never safe: the retained evidence records that a live
probe deleted user JPEGs. Camera setting/focus codes (`261`, `262`, `277`, and
`311`) and preview codes (`291`, `292`) remain withheld until their exact
wire-level behavior is captured and separately qualified.

The capture entry (`264`, subtype `4`) is asynchronous. Its first response is
not completion; consumers must wait for the final lifecycle/file obligation.

## Next evidence needed

- retain sanitised raw traces for each command and response, including camera
  model and firmware identity;
- qualify the same command pack against both K-3 III and K-1 II paths;
- add a consumer-side scenario that exercises only the default safe set;
- convert provisional capture and setting entries only after physical evidence
  and failure handling are retained;
- add CI validation of the pack and reject unknown command identities at the
  caller boundary.

Source references are retained in the pack, including the OpenPolaris live map
at commit `4076ce4`, the protocol-code conflict audit, and the linked patcher
issues.
