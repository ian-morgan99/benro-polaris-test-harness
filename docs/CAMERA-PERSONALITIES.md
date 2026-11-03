# Camera personality contracts

Issue #4 is represented by two separate, provisional personality manifests:

- `personalities/pentax-k3iii-settings-v1/personality.yaml` records the K-3 III
  positive-control observation that the Polaris path exposes setting values and
  choices. The actual values and writable round-trips remain `unknown`.
- `personalities/pentax-k1ii-settings-v1/personality.yaml` records that the
  equivalent values and choices were not visible in the observed Polaris path.
  It does not convert that observation into a libgphoto2, firmware, or
  OpenPolaris diagnosis.

Each setting has independent fields for current value, available choices, and
writability. `unknown` is retained when the evidence does not establish a value
or write contract; `observed: false` means that the field was not exposed in
this path. The K-1 II manifest never inherits K-3 III data.

The provenance is currently `derived-from-openpolaris-63`, not a retained direct
A/B evidence package. The manifests therefore remain `source: derived` and
`status: provisional`. Before physical qualification, retain separate evidence
for:

1. direct libgphoto2 plus physical camera;
2. Polaris-local without OpenPolaris; and
3. OpenPolaris.

That separation is required to distinguish a body/libgphoto2 issue from a
firmware/runtime issue or a client/UI issue.
