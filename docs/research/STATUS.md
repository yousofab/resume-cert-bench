# Research status

## Available now

- The Persian research proposal in PDF and editable DOCX.
- The literature matrix and protocol that informed the proposal.
- A deterministic witness over eight constructed states, with its output and a verification command.
- [Simulator v1](SIMULATOR_V1.md): budgeted reads over fixed snapshots and independent verification. The [recorded fixtures](../../results/simulator_smoke_v1.json) match the standalone witness across 64 verified runs and cover allow, block, defer, and contradictory evidence.

## Planned study

| Stage | Work | Exit criterion |
| --- | --- | --- |
| Pilot | 24 matched pairs across three development families, plus independent controls | Check matching, answer leakage, and baseline budgets |
| Freeze | Lock contracts, policy settings, primary budgets, baselines, and held-out split | Record the versions and artifact digests before evaluation |
| Confirmatory study | 48 matched pairs across two held-out families | Report coverage, error denominators, read cost, and failure categories |
| Transfer | An independent workflow or a model-based read policy | Report separately as exploratory |

The action is fixed in the primary experiment. The verifier certifies a decision only under the declared state space, observation contract, and stable snapshot. The study does not measure end-to-end task completion, human cognition, or production safety.
