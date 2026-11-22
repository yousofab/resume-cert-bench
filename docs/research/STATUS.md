# Research status

## Available now

- The Persian research proposal in PDF and editable DOCX.
- The literature matrix and protocol that informed the proposal.
- A deterministic witness over eight constructed states, with its output and a verification command.
- [Simulator v1](SIMULATOR_V1.md): budgeted reads over fixed snapshots and independent verification. The [recorded fixtures](../../results/simulator_smoke_v1.json) match the standalone witness across 64 verified runs and cover allow, block, defer, and contradictory evidence.
- [Matched development pilot](MATCHED_PILOT_V1.md): 24 pairs across three synthetic families, 12 separate controls, and a grouped-read sensitivity.
- [Confirmatory protocol freeze](CONFIRMATORY_PROTOCOL_V1.md): 48 pairs across two new families, eight separate controls, and source and case commitments.
- [Confirmatory results](CONFIRMATORY_RESULTS_V1.md): the registered contrast was 0/96 at two unit reads. Each main policy certified 11 of 96 snapshots at a total cost of 184 units. The report includes sensitivity and control outcomes and links to the full traces.

- [Exploratory archive transfer](ARCHIVE_TRANSFER_V1.md) measures certificate validity at the commit boundary with a second SQLite connection and 7,680 deterministic trials.

- [Budgeted archive repair](ARCHIVE_REPAIR_V1.md) compares epoch-validated evidence reuse with fresh planning over 20,160 SQLite trials and includes cost sensitivity.

- [Archive notification recovery](ARCHIVE_DELIVERY_V1.md) checks source authorization, destination deduplication and unresolved delivery under eight local fault schedules.

- [Destination revocation](DESTINATION_REVOCATION_V1.md) compares commit-time fencing, sender rechecks and separate receipt recovery in 56 executable cases.

- [Provider failover](PROVIDER_FAILOVER_V1.md) compares reopenable fences and permanent retirement across 64 runs with two independent destinations.

- [Retention and restore](RETENTION_V1.md) evaluates independent prefix closure across 54 runs. It preserves closed-prefix rejection after metadata cleanup and offline provider-image restore, and includes a failing unclosed-tail control.

- [Open-tail recovery](OPEN_TAIL_V1.md) evaluates independently retained write-ahead claims in 63 runs. It prevents new same-provider effects for claimed operations after recovery-image loss, while preserving unresolved outcomes and a failing guard-rollback control.

## Planned study

| Stage | Work | Exit criterion |
| --- | --- | --- |
| Model policy | Compare an LLM read policy with the deterministic reference policies | Report separately as exploratory |

The action is fixed in the primary experiment. The verifier certifies a decision only under the declared state space, observation contract, and stable snapshot. The study does not measure end-to-end task completion, human cognition, or production safety.
