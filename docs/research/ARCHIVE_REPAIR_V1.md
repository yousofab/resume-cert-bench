# Budgeted repair of durable archive certificates

[فارسی](ARCHIVE_REPAIR_V1_FA.md) · [Summary JSON](../../results/archive_repair_v1.json) · [Full traces](../../results/archive_repair_v1_runs.jsonl.gz) · [Previous SQLite transfer](ARCHIVE_TRANSFER_V1.md)

## Problem and implementation

A queued archive operation can survive a restart while its reason for proceeding changes. In the previous experiment, matching a sufficient subset of the old evidence allowed 64 of 76 still-eligible requests. This implementation tries to recover the other requests under a hard budget on fresh field values. It writes a local SQLite archive receipt.

1. Before the pause, the existing greedy policy reads at most four field values. ResumeCert verifies the resulting decision and saves the observed values and their field epochs in a durable checkpoint.
2. A separate connection applies changes. SQLite triggers advance the epoch of every changed value, including each step of a change-and-restore sequence.
3. A new connection opens a write transaction. The reuse methods query the epochs for observed fields, retain only values with matching epochs, and optionally acquire fresh values. A new certificate is constructed over the retained values and the fresh transcript.
4. An allow and its receipt insertion share the same transaction. The database enforces a single receipt per request. A definitive block or an unresolved decision leaves no receipt. The experiment independently reads the outcome from another connection and verifies the certificate against the simulated workflow state as stored by SQLite.

Let *K* be the union of retained values and the fresh transcript. The authorization criterion is that the set of admissible worlds consistent with *K* is nonempty and every such world permits the action. For this fixture the current world is admissible. It agrees with retained values by the trigger-backed epoch invariant, and with fresh values by transaction isolation. A committed allow therefore implies current permission under the local assumptions. Tests cover writer exclusion during a read-and-write transaction, failure while inserting a receipt followed by retry, identity reuse, and an intervening change whose original value is restored. [SQLite isolation](https://sqlite.org/isolation.html) · [SQLite triggers](https://www.sqlite.org/lang_createtrigger.html)

## Evaluation design

All methods share the same checkpoint policy, workflow contract and singleton read catalog. The grid crosses 32 starting states with 16 schedules of at most one external commit and five two-commit schedules that flip then restore one field. It includes five fresh-read budgets (0 through 4) and six methods, for **20,160 runs**. The 512 ordinary schedules and 160 restored-value schedules are reported separately. A repair is attempted only when the original checkpoint has a verified allow. In the ordinary group that yields 160 cases per method and budget, including 76 still allowed and 84 revoked. In the restore group it yields 50 allowed cases.

- Reuse only validates epochs and reasons over surviving values.
- Fresh start uses either an optimized fixed order or an exact decision tree and reads within the new budget. It carries forward the existence of a verified allow as an entry condition and discards the earlier values.
- Repair uses the same fixed and exact planners or the existing greedy policy, initialized with the epoch-validated values. Both exact trees optimize expected definitive certification under a uniform prior over worlds consistent with the values they are allowed to use. They do not optimize separately for the frequency of this case grid.

Each case runs on persisted SQLite state. The initial, mutating and resuming connections open in successive phases. At every budget the three repair planners tied in aggregate for this particular contract. The two fresh-start planners also tied.

## Measured results

Each cell here counts durable valid receipts in the 76 eligible ordinary cases. No method wrote any receipt in a revoked case.

| Fresh-read cap | Reuse only | Fresh start, exact tree | Repair, exact tree |
| ---: | ---: | ---: | ---: |
| 0 | 64 | 0 | 64 |
| 1 | 64 | 0 | 68 |
| 2 | 64 | 64 | 68 |
| 3 | 64 | 64 | 76 |
| 4 | 64 | 76 | 76 |

With a cap of three, repair committed every eligible request and returned a definitive block on all 84 revoked cases. Fresh start at that cap certified 72 blocks and returned 24 defers. The same 76 commits and 84 blocks required a cap of four for fresh start. Across that equal-outcome comparison, repair used **144 fresh values plus 384 epoch checks**, while fresh start used **340 fresh values and zero epoch checks**.

If a fresh value costs one unit and an epoch check costs λ units, repair costs 144 + 384λ and fresh start costs 340. The equality is λ = 196/384 ≈ 0.5104. At equal per-item prices the measured proxy favors fresh start, 340 versus 528. These counts exclude initial checkpoint construction, common scope/receipt lookups, writes, lock time, CPU and I/O. Epochs are retrieved in one SQL query per verified initial allow for a method that reuses evidence. The study reports logical counts and a cost sensitivity, with no latency estimate.

For the restored-value schedules, retaining only epoch-current observations yielded **28/50** receipts. Repair with one new read per request recovered **50/50**. It made 22 fresh value reads and 120 epoch checks in total. A value returning to its earlier contents does not erase its committed intermediate change. In this contract a fresh read can prove the value again.

In traces `14197`, `14199` and `14202`, the restricted processing route closes after checkpointing while curator approval is granted. The manifest evidence remains valid. Reuse-only defers. Fresh start spends its sole read confirming the manifest and defers. Repair spends its sole fresh read checking curator approval and commits.

## Literature and scope

Lyu et al. already implement selective revalidation and require target-side binding between validation and effect. Mnemosyne treats repair outputs as proposals that must pass deterministic admission. CoAgent studies concurrency with agent repairs at the operation or plan level. In ResumeCert the proposed action stays fixed, authorized evidence is retained by field, and repaired coverage is measured under explicit budgets against fresh planning. The benchmark reuses the earlier synthetic archive contract, so these comparisons are exploratory. [Lyu et al., 2026](https://arxiv.org/abs/2609.08015) · [Chang and Geng, 2026](https://arxiv.org/abs/2607.00269) · [CoAgent, 2026](https://arxiv.org/abs/2606.15376)

Pakhomov and Nijkamp's November 2026 preprint emphasizes executing interventions after restoring state and verifying the relevant equivalence. Here the matched property is limited to the materialized initial approval evidence and the same scheduled database transition across methods. Every branch executes its own SQLite writes. This experiment does not estimate a language model's dependence on its history. Zong et al. describe expected-cost decision diagram optimization, whereas this study evaluates definitive decisions under a hard fresh-read limit. [Executed counterfactuals, November 2026](https://arxiv.org/abs/2611.05650) · [Cost-optimal decision diagrams](https://arxiv.org/abs/2606.24672)

The 2026 research items above are cited as public preprints. The complete SQLite fixture assumes trusted runtime code, trigger-governed facts, fixed contract semantics, and a local effect in the same database. It uses artificial data. Production deployment, network effects, LLM decisions and process crashes during commit are outside the evaluation.

## Reproduce

Run with Python 3.12 from the repository root.

```bash
python -m unittest discover -s tests -p test_archive_repair.py -v
python scripts/run_archive_repair.py --check results
```

Use `--record <new-directory>` to regenerate the summary and gzip-compressed JSONL traces. The byte comparison uses the uncompressed JSONL. Each trace has the initial evidence, the external writer schedule, the observed epoch comparisons, the fresh read ledger, the new decision and whether a separate connection found the receipt.
