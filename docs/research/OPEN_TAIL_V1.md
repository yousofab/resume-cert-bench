# Open-tail recovery with independently retained attempt claims

Date: 2026-11-22. Exploratory local SQLite experiment. [Persian report](OPEN_TAIL_V1_FA.md).

## Problem and contribution

The [retention study](RETENTION_V1.md) protected closed source prefixes. Its unclosed-provider-restore control still produced two historical effects under every policy. A provider image can lose a recent receipt while the previously performed handoff remains part of history.

This stage adds a provider-qualified claim before every new target effect INSERT and retains that claim outside the provider backup. A receipt can be sealed in the independent store after target commit. A claim without a surviving positive receipt blocks a new attempt and reports an unknown outcome. The implementation composes this open-tail ledger with the earlier irreversible prefix closure, which permits later reclamation.

Write-ahead records, stable identities, cached results, and conservative uncertainty are established systems ideas. The contribution here is an executable integration and a controlled comparison that isolates the postcommit evidence gap, including failed first attempts and raw delayed calls. No new general exactly-once primitive is claimed.

## Literature inspected

Sources were inspected on 2026-11-22. These implementations are not external comparison baselines in our experiment.

| Source | Relevant evidence and limit | Design consequence |
| --- | --- | --- |
| Zheng et al., [ACRFence](https://arxiv.org/html/2603.20625v1), March 2026 arXiv manuscript; abstract page lists CoDAIM workshop 2026 | Studies regenerated calls after checkpoint restore and proposes semantic replay or fork. Its discussion says the mitigation itself was not implemented in that evaluation. | Bind this experiment to the original exact operation identity; do not equate a receipt cache with complete restore safety. Semantic intent classification is outside our implementation. |
| Xu, [Idempotency Is Not Recovery](https://arxiv.org/html/2611.23353v1), 17 November 2026, preprint | Separates repeated effects, unresolved outcomes, and evidence-persistence costs in a controlled runtime. Full experimental code and records are not publicly deposited with the manuscript. | Report safety and unresolved work separately, including unknowns where no effect occurred. No direct numerical comparison or claimed replication is made. |
| Tong, Dai, and Guo, [AID-Guard](https://arxiv.org/html/2608.21159v1), August 2026, preprint | Retains ambiguous reservations and conditions successor permission on terminal evidence and fencing. | An unresolved claim grants no no-effect result and no replacement permission. |
| Lee et al., [RIFL](https://web.stanford.edu/~ouster/cgi-bin/papers/rifl.pdf), SOSP 2015 | Couples completion records to operations under durable-storage assumptions and handles retry rendezvous and reclamation. | Restoring a weaker recovery projection requires independently retained information; our separate claim and receipt commits expose an uncertainty window. |

## Protocol and proof boundary

[`open_tail.py`](../../resumecert/open_tail.py) extends the existing retention fixture. Trusted source authorization and the canonical namespace/sequence operation key remain the starting contract. Claims are keyed by `(provider_id, operation_key)` and bind stream, sequence, payload digest, and accepted generation. This is a same-provider recovery guarantee. A separate authorized route/failover protocol remains necessary across different provider identities.

The provider has a SQL trigger on every new `handoff_effects` row. It calls a configured trusted function that opens a fresh independent guard transaction. That transaction checks the current closure anchor and atomically inserts the unique claim. The callback permits the target INSERT only after this independent commit succeeds. An existing claim cannot grant another insertion. Provider rollback does not roll back the independent claim.

After the target transaction commits, the adapter seals its matching positive receipt in the guard. This update cannot change claim identity, accepted generation, or a previously recorded receipt. Cached positive results can then be recovered without recreating a row in an older provider image. A local historical receipt can also resolve an unsealed claim when that receipt survives.

| Cutpoint | Independently retained fact | Recovery behavior |
| --- | --- | --- |
| Claim write failed before commit | No claim | Target INSERT fails; a later first attempt can proceed |
| Claim committed; target rolled back or process exited before target commit | Attempt may have occurred | No new insertion; unknown without an effect in the evaluator's history |
| Target committed; receipt not sealed | Attempt may have occurred | Recover and seal a surviving positive target receipt, or remain unknown after its image is lost |
| Matching positive receipt sealed | Historical effect established | Return the provider-qualified historical receipt without another target INSERT |
| Closed prefix reclaimed | Independent anchor rejects the old sequence | Report forgotten and stop new admission |

For one provider and operation key, each new effect INSERT requires a successful unique independent claim insertion. While the sequence is open, its claim cannot be deleted. Once deletion is permitted, the same guard transaction domain already contains a closure anchor that prevents a replacement claim. Thus two protected effect creations for that pair would require two successful unique claim insertions during an open interval. The implementation denies that history under the declared storage and adapter assumptions. This is a scoped reasoning argument, accompanied by bounded tests, rather than a mechanized proof over arbitrary deployments.

Fresh guard connections avoid explicit or implicit stale WAL read snapshots. A connection lacking the claim function cannot insert target effects. The configured trigger also covers a delayed call through the earlier destination adapter, which can bypass a higher-level postcommit cache.

The prefix-closing operation first uses the previous durable anchor/cleanup protocol, then removes independent claims below the closed boundary. A crash after anchor advancement can retain redundant records but grants no retry. Closure still withdraws permission from unfinished work and does not mark the source outbox complete.

## Experiment

[`run_open_tail.py`](../../scripts/run_open_tail.py) evaluates 21 schedules under three same-team policies, for 63 runs. Twenty schedules satisfy the independently retained guard contract. One explicitly rolls both guard and provider backwards as a boundary control. The design was developed with the implementation and is exploratory, without preregistration, random sampling, confidence intervals, or production-rate estimates.

The policies share the existing archive authorization and destination logic. The prefix-only comparator has no open-tail evidence. The postcommit comparator caches a positive receipt after the target commits, and checks it on ordinary adapter retries. Both deliberately remove the new pre-effect SQL claim requirement. The full adapter retains the pre-effect claim and the postcommit receipt. These are mechanism ablations, not benchmarked implementations of ACRFence, RIFL, or other frameworks.

| Policy | Duplicate runs in 20 supported schedules | Exactly one effect and known result | Unknown with one effect | Unknown with zero effects | Forgotten |
| --- | ---: | ---: | ---: | ---: | ---: |
| Prefix floor only | 10 | 8 | 0 | 0 | 2 |
| Postcommit receipt cache | 7 | 11 | 0 | 0 | 2 |
| Write-ahead claim | 0 | 10 | 6 | 2 | 2 |

These columns partition each policy's 20 supported runs. A duplicate run has more than one historical creation for the first operation, regardless of what the source eventually believes. Every policy also duplicates the first operation in the separate guard-rollback control, so the all-21 duplicate totals are 11, 8, and 1 respectively.

The completed-restore schedules show why cached positive receipts help. The unsealed-restore and failed-seal schedules expose the remaining gap in a postcommit-only cache. A raw delayed arrival can bypass the cache check; the claim trigger still blocks a second INSERT. The historical-receipt/ID-reuse schedule checks operation binding when an old image reuses a local receipt number for another operation.

The full policy's eight unknowns must remain visible. Six have a historical effect whose surviving positive receipt was lost before sealing. Two have no effect because the target transaction failed after the claim committed. No automatic retry clears those two claims. A different authorized operation still completes in the dedicated independence schedule. Resolving a blocked claim requires additional authoritative reconciliation or explicit operator handling under an expanded contract. This stage does not infer absence from an empty restored database.

**Indistinguishable-history witness.** The result records the entire logical recovery view for `failed_effect_restored` and `unsealed_restored` under the full policy. Provider identity, stream floors and bindings, authority, target rows and fences, independent anchors and claims, and source outbox rows are identical. The evaluator's separate historical count is zero in the first case and one in the second. A recovery decision based only on this declared view cannot certify historical absence in both histories. Retrying can duplicate the second history; refusing can leave the first unfinished. Additional authoritative evidence is required to remove that ambiguity. This bounded witness is checked independently of the service code.

## Validation and measurement

Thirteen new checks cover real process exits before target commit and after target commit/before sealing, both retained and restored receipt paths, claim-write and seal-write failure, raw adapter replay, immutable binding, prefix reclamation, stale implicit WAL reads, the indistinguishable-history witness, and a concurrent race between two copied provider images sharing one guard. In that bounded race, only one image creates a new effect for the key; the other recovers a receipt or stays unknown. This is not a multi-host or general split-brain evaluation.

The complete repository unit suite has 70 tests. CI also reproduces the new 63-run JSON alongside the earlier studies. A separate trace test recounts historical creations, checks operation-bound receipts against that history, verifies source acknowledgement states, and preserves the failing boundary control.

The evaluator observes newly committed target rows before/after each event and saves them in a separate SQLite oracle that provider restoration does not overwrite. It also observes a committed target row when subsequent receipt sealing throws. Policies cannot query this oracle. Real process-exit tests independently inspect the surviving SQLite facts; the 63-run harness itself uses deterministic exception/response cutpoints. The same-team oracle is not an external business service, and a crash between effect commit and oracle recording is not evaluated by that harness.

The new claim adds an independent commit before the target and receipt sealing adds another afterwards. One claim row remains for each attempted provider/operation pair above the closed prefix. An unresolved old operation can therefore retain state indefinitely if its retry permission must remain open. Physical storage, latency, throughput, live-model behavior, and live SaaS effects were not measured.

## Assumptions and limits

The guard must survive provider restoration with its contents and identity intact. Its uniqueness and commit semantics are trusted. Schema/function bypass, arbitrary database editing, backup images predating installation of the trigger, regenerated operation identities for the same human intent, and rollback of the guard invalidate the contract. The boundary control demonstrates the latter explicitly. The fixture is not a consensus or hardware anti-rollback service.

Only the declared inert SQLite effect is covered. Returning a historical receipt does not reconstruct lost business data or undo a previously committed downstream action. Source completion requires a matching positive receipt; unknown and forgotten outcomes remain pending. No automatic successor transfer, compensation, expiry, or no-effect certificate is introduced.

## Reproduction

```bash
python -m unittest discover -s tests -v
python scripts/run_open_tail.py --check results/open_tail_v1.json
```

[`open_tail_v1.json`](../../results/open_tail_v1.json) includes every event, observed creation, claim-row count, and source completion state.
