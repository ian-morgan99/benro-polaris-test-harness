# K-3 III asynchronous capture contract

This provisional derived scenario preserves the observed sequence recorded in
validator issue #2: command `264` is acknowledged, an intermediate `-1005`
arrives later, and the final local image is the completion obligation. The
timing values are bounded replay annotations, not universal timing guarantees.

The source is marked `derived` because the current repository retains the issue
reference and sequence but not a sanitised raw physical trace. This scenario
must not be used to claim physical camera qualification until that evidence is
retained and hashed. The `-1005` interpretation is local to this sequence and
must not be applied globally to other bodies or operations.
