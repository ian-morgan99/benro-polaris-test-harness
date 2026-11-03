# Physical-target safety policy

The harness can run synthetic scenarios freely against its own test server. A
physical-target replay is a separate, fail-closed path. It is a policy preflight,
not a claim that a real Polaris is safe or compatible.

## Checks

Run a plan-only assessment before a physical replay:

```bash
polaris-harness safety check \
  --scenario scenarios/<scenario> \
  --target physical
```

The command emits JSON containing the selected operations, safety class, unlock
state, and errors. A non-zero result means the physical path must not proceed.
`replay --target physical` also performs this preflight and requires `--plan`
before it contacts the endpoint:

```bash
polaris-harness replay \
  --scenario scenarios/<scenario> \
  --host 127.0.0.1 --port 9090 \
  --target physical --plan
```

No category is unlocked by default. The only default-allowed class is
`read_only`. A mutating category must be independently selected with a repeated
`--allow` option, for example `--allow state_write`. Unlocking one category does
not unlock another. `unknown` is never unlockable.

## Scenario requirements

A physical scenario must include:

- non-synthetic source/status;
- `source_issue` and `evidence_id`;
- `safety.dry_run_plan: true`;
- `safety.ledger: required`;
- `safety.operator_abort: true`;
- one operation mapping for every request rule;
- an exact protocol-contract ID and matching safety class for every operation.

For `state_write` and `motion`, the operation must declare
`state_snapshot: true`, `restore: true`, and `readback: true`. Every mutating
operation must set `retry: false`; a timeout or transport failure must not
silently repeat capture, movement, deletion, or firmware actions.

For `media_destructive`, the operation must additionally declare
`disposable_fixture: true` and `exact_scope: true`. The contract pack permanently
keeps command `789` out of the default safe set.

## What this does not yet prove

The current gate does not perform physical execution, identify the target
hardware, provide a hardware emergency stop, or integrate a complete execution
ledger/restore controller. Those remain release/deployment gates. A PASS means
only that the scenario's declared physical safety contract is internally
consistent and explicitly unlocked. Physical testing still requires supervised
operation, target identity capture, bounded timeouts, and hardware-specific
acceptance.
