# Camera transaction contracts

[`CaptureTransactionGuard`](../src/polaris_harness/camera_transaction.py)
models the consumer-visible camera lifecycle at the Polaris boundary. It does
not implement Pentax PTP or replace libgphoto2. Its purpose is to prevent a
partial or stale operation from being presented as a successful photograph.

## Required lifecycle

```text
request -> accepted -> output candidate -> transfer/publication
        -> restoration/read-back -> complete
```

An acknowledgement only enters `pending`; it is not completion. A second capture
is blocked while the first output obligation is unresolved. A candidate is
admitted only when its transaction ID is current, its creation time is after the
request, and its path is in the declared output set.

Transfer failure becomes `OUTCOME_UNKNOWN_TRANSFER_FAILED` and never schedules an
automatic shutter retry. Restore failure becomes
`CAPTURE_DEGRADED_RESTORE_FAILED`; it must be shown as degraded rather than
reported as a clean success. Old session generations cannot publish candidates
or complete the current capture.

`CameraCapabilityState` keeps connected state separate from vendor-control and
preview capability. A connected camera with a missing capability is degraded,
not silently retried and not treated as a transport failure.

## Test evidence

`tests/test_camera_transaction.py` covers stale/pre-existing candidates,
unexpected output, duplicate capture blocking, transfer failure, restore failure,
reconnect generation and capability degradation. The tests are deterministic and
do not claim physical camera compatibility.

The next integration step is to connect this contract to the external scenario
ledger and OpenPolaris capture/file-event consumer tests. Physical K-3 III and
K-1 II qualification still requires direct evidence for the actual runtime path.
