---
name: polaris-test-harness
description: Use the Benro Polaris test-harness repository to turn verified device evidence into deterministic scenarios, run protocol/runtime regressions, and route failures to OpenPolaris, the firmware patcher, or libgphoto2. Use for harness authoring, evidence ingestion, replay, fault injection, runtime-manifest checks, or consumer CI integration; not as proof of physical-camera support.
---

# Polaris test harness

Work in the `benro-polaris-test-harness` repository. Before changing it, read `AGENTS.md` and the documents it mandates. Use `USER-GUIDE.md` for supported commands and `docs/SCENARIO-FORMAT.md` for persisted contracts.

## Choose the job

- Model recorded Polaris protocol behavior with a versioned evidence record and scenario.
- Model malformed, delayed, missing, or split traffic that was not physically observed as a clearly labelled synthetic fault.
- Check packaged paths, hashes, ELF compatibility, or loader selection with a runtime manifest and the read-only verifier.
- Test a consumer using a loopback endpoint on an ephemeral port, retaining the harness NDJSON ledger with the consumer result.

Do not emulate internal camera/PTP behavior when only the Polaris-facing result was observed.

## Evidence gate

For a physical scenario, locate the raw evidence and owning GitHub issue first. Record camera firmware and USB mode when known, Polaris/FwPkt provenance when relevant, libgphoto2 SHA when relevant, observation date and layer, exact source path or issue link, limitations, and whether conclusions are confirmed or inferred.

Sanitize credentials, private paths, irrelevant network identifiers, and media metadata. Preserve raw framing bytes. Never change an old physical scenario merely to satisfy a consumer; version it when newer evidence changes the contract.

Interpret results using the ownership ladder:

```text
harness PASS = consumer handles the recorded contract
direct camera FAIL = candidate libgphoto2 issue
direct PASS, Polaris-local FAIL = patcher/runtime issue
direct and Polaris-local PASS, OpenPolaris FAIL = OpenPolaris issue
```

## Implement and verify

Keep transport, framing, matching, scenario state/actions, ledger, and runtime verification separated as described in `docs/ARCHITECTURE.md`. Exact matching is the default. TCP tests must cover split and coalesced frames when applicable; timing tests should use virtual time.

Use the repository environment when present:

```bash
.venv/bin/python -m pytest -q
.venv/bin/polaris-harness --help
```

Otherwise follow the documented isolated setup. Validate every new scenario, evidence, personality, or runtime manifest through the public validator or CLI, then run focused tests and the complete suite.

For consumer integration, record the exact harness commit, scenario id/version, endpoint and clock mode, consumer commit and command, exit result, and ledger path. Passing replay does not remove the physical qualification gate from a firmware release.

## Report and route

Report the contract, evidence provenance, tests, and what the result does and does not prove. Log the underlying defect in its owning repository and cross-link the harness scenario. Leave unknown camera values unknown and stop if completing the scenario would require invented behavior.
