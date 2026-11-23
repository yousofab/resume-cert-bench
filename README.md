# ResumeCert

**Budgeted evidence for resuming agent workflows after external state changes.**

[فارسی](README.fa.md) · [Research proposal PDF](docs/proposal/ResumeCert_Research_Proposal_FA.pdf) · [Editable proposal](docs/proposal/ResumeCert_Research_Proposal_FA.docx) · [Academic acknowledgments](docs/ACKNOWLEDGMENTS.md)

An agent can remember the correct next step after an interruption even when that step is no longer allowed. ResumeCert asks which current facts an agent should reread, within a fixed budget, to certify that step as permitted or blocked. If both outcomes remain possible, it defers.

For example, a document may have a recorded approval but a newer version than the one approved. The proposed benchmark holds the action, checkpoint, and resumption message constant across matched scenarios and changes the precondition that governs the action. An independent verifier checks the decision, supporting observations, and read cost.

## Research status

| Item | Status |
|---|---|
| [Formal proposal](docs/proposal/ResumeCert_Research_Proposal_FA.pdf) | Available |
| [Research protocol](docs/research/RESEARCH_PROTOCOL_FA.md) and [primary source matrix](docs/research/EVIDENCE_MATRIX_FA.md) | Available; the formal proposal governs the planned design if details differ |
| [Exhaustive eight-state witness](proof/witness.py) | Reproducible |
| [Simulator and independent verifier](docs/research/SIMULATOR_V1.md) | Available; [64 witness runs and decision fixtures](results/simulator_smoke_v1.json) reproduced |
| [Matched development pilot](docs/research/MATCHED_PILOT_V1.md) | Available; 24 pairs, three synthetic families, and 12 controls |
| [Confirmatory protocol and freeze](docs/research/CONFIRMATORY_PROTOCOL_V1.md) | Recorded before evaluation; 48 pairs in two new families |
| [Held-out confirmatory comparison](docs/research/CONFIRMATORY_RESULTS_V1.md) | Complete; 11/96 for each method at two reads |

The witness uses three Boolean fields and eight constructed states. With two unit-cost reads, the best fixed field set certifies **4 of 8** states; an adaptive tree that reads the active route and then the corresponding approval certifies **8 of 8**. This is an exact result for the constructed example. No LLM or real workflow was evaluated.

## Development pilot

At two unit reads per case, greedy adaptive reads certify **22/48** cases versus **19/48** for an optimized fixed order at the same total cost. With a cheap grouped read, both reach **32/48**. The [pilot report](docs/research/MATCHED_PILOT_V1.md) separates the gains, the cost of observations, and the cases where evidence is insufficient.

![ResumeCert pilot coverage across read budgets](results/matched_pilot_coverage_v1.svg)

## Confirmatory result

At two unit-cost reads, greedy adaptive and optimized fixed certified the same **11/96** snapshots in the two held-out families. Each spent 184 units. The registered interpretation is **no pooled gain**. Adding a unit-cost grouped query raised both to **30/96** at the same total cost. See the [report](docs/research/CONFIRMATORY_RESULTS_V1.md) and [full run traces](results/confirmatory_v1_runs.jsonl.gz).

## Exploratory SQLite transfer

An archive receipt experiment checks whether a verified snapshot certificate still authorizes the action after another writer changes the database. At budget four, atomic sufficient-evidence revalidation wrote **64 valid and zero invalid receipts**, compared with **60 valid and zero invalid** for matching all evidence. Full predicate evaluation wrote **76 valid and zero invalid**. [Study and complete traces](docs/research/ARCHIVE_TRANSFER_V1.md)

## Durable checkpoint repair

After a SQLite writer changes the authorization route, ResumeCert validates field epochs and reads just enough new evidence to rebuild the decision. In the exploratory archive fixture, a three-read cap recovered **76/76** eligible requests versus **64/76** for a fresh start at the same cap. The [study](docs/research/ARCHIVE_REPAIR_V1.md) also accounts for the cost of epoch checks.

## Delivery after an uncertain acknowledgement

A two-store SQLite experiment connects verified archive commits to a durable outbox and a keyed destination receipt. With two recovery cycles, stable deduplication completed 7 of 8 schedules with one effect each. The persistent outage left one pending. [Design and reproduction](docs/research/ARCHIVE_DELIVERY_V1.md)

## Destination revocation and receipt recovery

A queued synthetic handoff can lose its dispatch authority before arriving. The [study](docs/research/DESTINATION_REVOCATION_V1.md) compares a destination-side generation fence with sender checks and shows how an independent receipt lookup can recover an existing effect after revocation.

## Failover between independent providers

Two destinations may each honor the same idempotency key and still produce two effects. The [64-run study](docs/research/PROVIDER_FAILOVER_V1.md) evaluates the tradeoff of waiting for proof of no effect and permanent primary retirement before enabling a secondary.

## Retention after cleanup and restore

Deleting old recovery records can admit a delayed request again. The [54-run study](docs/research/RETENTION_V1.md) retains a compact independent closure boundary before cleanup. It also demonstrates the remaining duplicate risk for unclosed operations whose recovery image is restored backwards.

## Recovery of the unclosed tail

A receipt cache still has a gap between target commit and receipt persistence. The [63-run study](docs/research/OPEN_TAIL_V1.md) adds an independent pre-effect claim, prevents replay after provider-image loss, and reports the unresolved work it leaves behind. Rolling back the independent guard remains a failing boundary control.

## Resolving unknown outcomes with provider evidence

An authenticated empty lookup can precede a late effect. The [60-run study](docs/research/TERMINAL_EVIDENCE_V1.md) verifies evidence scope and purpose before confirming an effect or enabling the approved secondary. The contract requires independently retained terminal provider state; a provider-rollback control demonstrates the limit.

## Reproduce

Python 3.12 is used for repository verification. No API key, GPU, or package installation is needed.

```bash
python proof/witness.py
python scripts/verify_repository.py
python -m unittest discover -s tests -v
python -m resumecert demo --check results/simulator_smoke_v1.json
python -m resumecert pilot --check results/matched_pilot_v1.json
python scripts/verify_freeze.py
python scripts/run_confirmatory.py --check results
python scripts/run_archive_transfer.py --check results
python scripts/run_archive_repair.py --check results
python scripts/run_archive_delivery.py --check results/archive_delivery_v1.json
python scripts/run_destination_revocation.py --check results/destination_revocation_v1.json
python scripts/run_provider_failover.py --check results/provider_failover_v1.json
python scripts/run_retention.py --check results/retention_v1.json
python scripts/run_open_tail.py --check results/open_tail_v1.json
python scripts/run_terminal_evidence.py --check results/terminal_evidence_v1.json
```

GitHub Actions runs the reproduction and unit checks. The artifact check preserves the recorded witness and the proposal and institutional mark digests; the simulator checks cover the read budget and evidence verification.

## Next milestone

The SQLite transfer and durable checkpoint repair studies are complete. A model-based read policy remains a potential exploratory study. See the [research status and roadmap](docs/research/STATUS.md).

To cite this repository, use [CITATION.cff](CITATION.cff). The institutional marks are displayed in the [acknowledgments](docs/ACKNOWLEDGMENTS.md) for educational context.
