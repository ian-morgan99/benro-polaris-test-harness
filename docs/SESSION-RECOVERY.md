# Session and transport recovery contracts

The recovery model covers consumer-visible session behavior without pretending to
emulate Bluetooth, Android NetworkManager, or a physical Polaris access point.
The reusable model is [`SessionGuard`](../src/polaris_harness/session_recovery.py);
the synthetic fixtures are under `scenarios/recovery-*/`.

## Invariants

- only one session owner is active at a time;
- every reconnect increments a session generation;
- pending work from a closed generation is discarded;
- late progress/completion from an old generation is rejected;
- duplicate requests in one generation are rejected;
- cancellation removes the operation without an automatic retry;
- no-progress timeout becomes `OUTCOME_UNKNOWN_NO_PROGRESS`;
- a progressing operation is allowed to exceed the no-progress interval, but a
  separate safety ceiling becomes `OUTCOME_UNKNOWN_SAFETY_CEILING`;
- a timeout or disconnect never implies that capture, movement, or deletion is
  safe to repeat.

## Fixture coverage

- `recovery-request-timeout-v1`: accepted request with no response and explicit
  unknown terminal outcome;
- `recovery-late-event-after-reconnect-v1`: stale completion from generation N
  cannot satisfy generation N+1;
- `recovery-progress-aware-operation-v1`: progress resets the stall deadline,
  while the absolute safety ceiling remains bounded.

The fixtures are synthetic and schema-valid. They are consumer robustness
contracts, not physical AP or camera evidence.

## Remaining transport work

The current `ScenarioService` can serve deterministic request/response fixtures,
but it does not yet provide a complete session-aware external ledger, reconnect
controller, cancellation propagation, or physical AP recovery. Those belong in
the next OpenPolaris integration stage. Physical qualification still needs
observed disconnect/reconnect traces and a supervised hardware window.
