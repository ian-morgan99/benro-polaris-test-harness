# Preview producer/listener harness

Issue #3 is implemented as an independent, synthetic preview endpoint. It
exists to keep the following outcomes separate in consumer tests:

| Outcome | What the client observes |
|---|---|
| `listener_absent` | TCP connection is refused because no listener is bound |
| `accepted_closed` | TCP connection is accepted, then closes before a frame |
| `accepted_no_data` | Connection remains open for the bounded idle period without bytes |
| `empty_frame` | A complete zero-length media frame is received |
| `invalid_frame` | A complete frame contains bytes that are not a JPEG |
| `delayed_jpeg` | A valid generated JPEG arrives after a controlled delay |
| `repeated_jpeg` | Two valid JPEG frames arrive as separate frames |

## Synthetic wire contract

The harness uses a four-byte big-endian length prefix followed by the media
payload. This is a test envelope, not a claim about an undocumented Polaris
preview wire format. It makes an explicit empty frame distinguishable from no
bytes. A real adapter must translate the observed Polaris endpoint into the
same outcome classes and retain the actual evidence separately.

`synthetic_jpeg()` returns a tiny embedded JPEG fixture. It is not a user image,
contains no camera metadata, and avoids an image-library dependency.

## Python usage

```python
import socket

from polaris_harness.preview import (
    PreviewMode,
    PreviewServer,
    read_preview_frame,
)

server = PreviewServer(PreviewMode.DELAYED_JPEG, delay_ms=50)
server.start()
try:
    with socket.create_connection((server.host, server.port), timeout=1) as client:
        result = read_preview_frame(client)
        assert result.outcome.value == "valid_jpeg"
finally:
    server.stop()
```

`PreviewServer.from_scenario()` accepts a schema-valid manifest with an
`endpoints.preview` block. The seven manifests under `scenarios/preview-*`
provide one stable contract per failure class.

## Qualification boundary

All current preview scenarios are `source: synthetic` and are safe for CI.
They prove only that a consumer can distinguish bounded transport/media
outcomes. They do not prove:

- that a physical Polaris preview listener uses this framing;
- that Stage-2, `pgphoto`, or libgphoto2 produces any particular preview;
- that a camera emits a valid JPEG; or
- that OpenPolaris handles these outcomes until an external consumer test is
  added.

Promotion to a physical scenario requires a sanitised trace, raw-artifact hash,
source layer, runtime/camera provenance, and explicit limitations.
