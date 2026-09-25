# Synthetic camera generation lifecycle

This scenario is deliberately synthetic. It encodes the Benro-facing contract
required by firmware-patcher issues #146 and #149: ending camera generation 1
does not reset mount state, a late generation-1 completion cannot satisfy
generation 2, generation 2 can admit fresh work, and cancellation releases its
owned operation without ending the camera session.

It does not emulate Pentax/PTP internals and does not prove physical reconnect,
capture, or cancellation behaviour.
