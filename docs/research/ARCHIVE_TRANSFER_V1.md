# Exploratory transfer: revalidating evidence at commit

[فارسی](ARCHIVE_TRANSFER_V1_FA.md) · [Summary JSON](../../results/archive_transfer_v1.json) · [Complete traces](../../results/archive_transfer_v1_runs.jsonl.gz)

The confirmatory study asked whether the next action could be certified for a stable snapshot. This study asks whether an already verified allow still justifies recording an archive receipt after a second writer changes the database. The effect is one inert receipt in a temporary SQLite database. No real dataset or service is modified.

## Design

The fixture archives a dataset if its manifest is verified and either the curator has approved it or the restricted dataset is eligible for an open restricted lane. Each of the five fields is binary, including an annotation field that does not affect permission. The design enumerates all 32 starting states and every zero-, one-, or two-field flip, giving 512 schedules per method and budget. A writer on a separate connection commits the scheduled changes after the initial certificate and before the archive attempt. Every method receives the same greedy policy's observations for a given schedule and budget. The full SQL row used to represent the trusted initial snapshot is internal to the fixture. Only exposed fields count against the initial observation budget.

All methods require an independently verified allow for the original snapshot. The five variants are:

| Method | Commit check |
| --- | --- |
| Snapshot only | Use the originally valid certificate without a live guard |
| Revision | Require an unchanged row revision, including annotation changes |
| All evidence | Require all observed field values to match |
| Sufficient evidence | Require every value in at least one sufficient acquired subset to match |
| Full predicate | Evaluate the complete action precondition on current facts |

The sufficient-evidence guard enumerates inclusion-minimal subsets of already observed values. A subset is sufficient if all admissible worlds consistent with it permit the action. Holding the contract fixed, the current admissible state satisfies the predicate if any sufficient acquired subset still matches. This implication explains soundness under the modeled conditions. A valid current state may fail the guard when all original proofs are gone and a new proof would require unobserved values.

For the guarded methods, SQLite performs the check and the receipt insertion inside one write transaction. The read and the effect therefore share a writer lock. The one-row revision is incremented by fixture mutations. A separate replay test checks that a single request creates at most one receipt, and a lock test attempts a conflicting write after the guard reads. The initial certificate audit is trusted and the laboratory scope is one fresh database. [SQLite transaction and isolation semantics](https://sqlite.org/isolation.html)

## Results

The table shows budget four. For each method, 160 of 512 schedules had an initial allow certificate. Of those 160, 76 remained eligible at commit and 84 had lost permission.

| Method | Valid receipts of 76 | Invalid receipts of 84 | Eligible requests rejected | Total commit fields inspected |
| --- | ---: | ---: | ---: | ---: |
| Snapshot only | 76 | 84 | 0 | 0 |
| Revision | 10 | 0 | 66 | 160 |
| All evidence | 60 | 0 | 16 | 384 |
| Sufficient evidence | 64 | 0 | 12 | 352 |
| Full predicate | 76 | 0 | 0 | 640 |

Sufficient evidence recovered four valid receipts and checked 32 fewer field values than all-evidence matching. It checked 45% fewer field values than full predicate evaluation and rejected twelve eligible requests that full evaluation completed. At budgets two and three, the two evidence guards tied at 56 valid receipts and 256 inspected fields. Across all budgets, the unguarded policy wrote 212 invalid receipts. The four guarded methods wrote zero in the scheduled grid.

In traces 6893 and 6894, manifest verification was intact, curator approval changed from zero to one, and both restricted-route fields stayed at one. The archived action remained valid through the already-observed restricted route. Matching all observations rejected the request because curator approval changed. The sufficient guard kept that request. A separate arithmetic replay verified permissions, initial evidence equivalence and the persisted outcomes across all 7,680 traces. The published JSONL includes starting and commit states, budget, query transcript, original certificate, guard fields and receipt outcome.

## Research context and limits

Lyu et al. study selective revalidation of explicit decision conditions, including durable SQLite workflows. Their preprint already distinguishes state-version conflicts from decision conflicts. This experiment measures survival of a sufficient subset extracted from prior budgeted observations, using equal-evidence comparisons against full-transcript and full-predicate guards. [Lyu et al., arXiv:2609.08015, 2026](https://arxiv.org/abs/2609.08015)

Cordon stages effects and validates their lineage at a transaction boundary. Trofimov and Novikov distinguish external effects from confirmation observed by an agent. Both are 2026 preprints. These works motivate the local transaction and the independent check of persisted receipts. [Chen et al.](https://arxiv.org/abs/2606.17573) · [Trofimov and Novikov](https://arxiv.org/abs/2609.15397)

Optimistic concurrency control provides the established validation context for the revision baseline. The benchmark uses exhaustive synthetic schedules on SQLite, and field counts are a logical work measure. All rows fit in the same table. These counts carry no latency, lock-duration or I/O claim. The writer schedule covers one commit in the gap between certification and execution. Process crashes, distributed side effects, contract changes and model policies lie outside the design. This was an exploratory transfer study. [Kung and Robinson, ACM TODS](https://doi.org/10.1145/319566.319567)

## Reproduce

Use Python 3.12 from the repository root.

```bash
python -m unittest discover -s tests -p test_archive_transfer.py -v
python scripts/run_archive_transfer.py --check results
```

For fresh outputs use `python scripts/run_archive_transfer.py --record <new-directory>`. Results compare the decompressed trace bytes so gzip implementation headers do not affect reproducibility.
