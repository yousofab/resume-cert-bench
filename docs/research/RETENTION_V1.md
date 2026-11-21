# Retention after irreversible prefix closure

Date: 2026-11-21. Exploratory, constructed SQLite study. [Persian report](RETENTION_V1_FA.md).

## Problem and measured contribution

A delayed authorized handoff can arrive after its deduplication record or permanent retirement has been deleted. Restoring an older provider recovery image can also restore an old permission. The previous [failover study](PROVIDER_FAILOVER_V1.md) required permanent retirement to survive; this study makes that retention obligation explicit.

The implementation closes a numbered source prefix before reclaiming its per-operation metadata. A compact closure anchor survives independently of the provider image. This applies established sequence-watermark and admission-fencing ideas to ResumeCert's existing authorization and retirement fixture. The measured contribution is the executable comparison, crash cutpoint, restore counterexamples, and explicit recovery boundary. No new general exactly-once algorithm or production guarantee is claimed.

## Evidence reviewed

Sources were opened on 2026-11-21. The first two are recent preprints. Reading their manuscripts does not reproduce their experiments.

| Source | Relevant finding or contract | Consequence here |
| --- | --- | --- |
| Song Xu, [Idempotency Is Not Recovery](https://arxiv.org/html/2611.23353v1), 17 November 2026, preprint | Separates repeated effects, orphaned effects, and unresolved outcomes under controlled tool faults. Only the manuscript is deposited with the preprint. | Count committed target events separately from recovery knowledge; no direct performance comparison is possible from the public manuscript alone. |
| Tong, Dai, and Guo, [AID-Guard](https://arxiv.org/html/2608.21159v1), August 2026, preprint | Successor transfer depends on terminal no-effect evidence and a predecessor fence retained through the recovery horizon. | Deleting or rolling back the fence is a separate threat to the previous stage's assumptions. |
| Lee et al., [RIFL](https://web.stanford.edu/~ouster/cgi-bin/papers/rifl.pdf), SOSP 2015, DOI 10.1145/2815400.2815416 | Completion-record reclamation is tied to acknowledgements and leases. The server checks validity so an expired client cannot retry after collection; stale outcomes can remain ambiguous. | Retention safety requires rejecting old requests after collection. This principle is established prior work. |
| [Stripe API v1 idempotent requests](https://docs.stripe.com/api/idempotent_requests), official documentation | Keys may be pruned after at least 24 hours; reuse after pruning generates a new request. | A cache retention duration alone does not establish permanent retry protection. This benchmark does not call Stripe. |

## Contract and implementation

[`retention.py`](../../resumecert/retention.py) adds a trusted administrative closure operation. Each stream is the stable source namespace already used by `prepare_notice`. Its positive sequence is the archive request ID. A `StreamNotice` verifies the existing canonical operation key as SHA-256 of the encoded protocol name, namespace, and sequence. The same old key cannot be relabelled as a newer sequence through this API, subject to the usual hash assumption. A newly authorized namespace or request ID represents a different operation and remains an issuer responsibility.

Each provider retains a local `closed_through` value. A separately stored guard retains another value for the same provider and stream. The effective floor is their maximum. Registration and effect creation require `sequence > effective_floor`. SQL triggers also protect the original authority insertion, regrant, and effect insertion paths. A connection without the configured guard function cannot use those mutation paths.

`close_prefix(provider, guard, stream, through)` means the trusted operator irreversibly withdraws permission for **every** sequence at or below `through`, including unseen or unfinished work. It is not an inference that every such operation completed. An automatic caller would need an additional verified policy for choosing that boundary.

The operation holds the provider's write transaction while committing the independent anchor first. It then updates the local floor and deletes the selected bindings, authority rows, effect-recovery rows, fences, and permanent retirements in the provider transaction. The independent anchor is already durable if that second transaction fails.

| Cutpoint | State after recovery | Admission behavior |
| --- | --- | --- |
| Before independent anchor commit | No new closure is promised | Existing authority applies |
| After anchor commit, before provider cleanup commit | Old rows may remain; independent prefix is closed | Old requests are rejected |
| After cleanup commit | Per-operation recovery projection is removed | Old requests are rejected |
| Old provider image restored offline | Local floor may rewind; independent anchor survives | Old closed-prefix requests are rejected |
| Guard unavailable or missing | Current floor cannot be established | Admission stops |
| Guard and provider image both restored backwards | Both copies can forget closure | The co-restored comparator demonstrates duplicates |

Guard reads use a fresh read-only connection to the durable guard file. An explicit old read transaction is rejected. A pending WAL SELECT can retain an implicit snapshot while Python reports `in_transaction == False`; a separate test checks that this cannot hide the latest closure.

`lookup` reports `recorded`, `retained_empty`, `unregistered`, or `forgotten`. A closed prefix yields `forgotten` even when a restored image contains older rows. This grants no no-effect certificate and cannot justify a successor. The existing source outbox is not marked complete by prefix closure.

## Experiment and results

[`run_retention.py`](../../scripts/run_retention.py) evaluates 18 named schedules under three same-team policies, for 54 runs. Fourteen schedules request prefix closure. Four controls retain their recovery window. The design is exploratory and was developed alongside the implementation, without preregistration or a statistical sampling claim.

The deletion comparator discards per-operation metadata without advancing either floor. The co-restored comparator uses the same closure implementation, but restores the guard alongside an older provider image. The independent comparator preserves the guard outside that restore domain. Source authorization uses the existing archive verifier and outbox. One schedule composes real primary retirement, source route transfer, and secondary application before primary cleanup and restoration.

| Policy | Duplicate runs in 14 closure schedules | Duplicate runs across all 18 schedules | Lookups explicitly forgotten |
| --- | ---: | ---: | ---: |
| Delete metadata only | 9 | 10 | 0 |
| Co-restored floor | 4 | 5 | 9 |
| Independent floor | 0 | 1 | 13 |

Each duplicate-run count means more than one historical committed creation for the first operation. It is not the total count of extra effects or a production failure rate. In the repeated-restore counterexample, the two simpler policies each create that operation three times. In the failover/restore counterexample, each simpler policy records one secondary effect and one later primary effect; the independent-floor policy records just the secondary effect.

The independent policy deliberately reports forgotten outcomes both when one effect previously occurred and when none occurred. In the `unseen_sequence_after_close` schedule it blocks an authorized but unfinished operation. This is a visible availability cost of the operator's closure decision.

**Boundary control.** In `unclosed_provider_restore_control`, all three policies produce two effects. Its operation was never covered by a durable closure. A current floor cannot protect unclosed deduplication records lost during restore. Recovery of that tail requires a separately established quarantine, reconciliation, or stronger durable ledger before resuming. The result therefore supports closed-prefix retention safety only.

## Measurement and validation

After each completed apply, the evaluator queries the target table before and after and appends any newly committed creation to a separate SQLite observation database. Policies cannot read that database, and provider restoration does not restore it. Historical observations continue to count after recovery rows are removed. This models an irreversible handoff whose downstream effect survives replacement of the recovery projection; the fixture performs no external notification or transfer.

The observer is same-team instrumentation, not an independently implemented external service. It does not test a crash between target commit and observer recording. The real subprocess termination instead targets the anchor-committed/provider-cleanup-uncommitted cutpoint. The recorded scenario named `close_fail` injects a SQLite exception at that cutpoint, and the separate unit test uses `os._exit(31)` there.

There are ten new checks, including trace recounting, a real process exit, concurrent apply/closure with separate connections, unavailable or stale guard access, identity and payload substitution, old provider-image restore, and compaction of 256 completed operations. The compaction test removes 768 per-operation rows, leaving one local boundary and one independent anchor for the stream, plus constant schema/provider metadata. This counts recovery rows; it does not measure physical bytes, business-data retention, oracle growth, or throughput. Total retained boundaries grow with the number of enrolled provider/stream pairs.

The existing destination functions were factored into internal transaction helpers so the retention adapter can share the original authority and effect logic. Their public behavior remains covered by the prior tests and result reproduction.

## Scope

The guard is a trusted, independently retained local SQLite store, with monotonic SQL updates. It is not a consensus service or a cryptographic anti-rollback device. Rolling it back, replacing its identity, bypassing the trusted adapter/schema, or operating concurrent cloned provider images breaks the declared assumptions. Restoration is offline and leaves a single active provider image. New provider connections must configure their guard before mutations. Storage errors stop admission.

The experiment exercises real local SQLite transactions and selected process/concurrency faults. It measures constructed inert effects, with fixed notices and no live model, human participant, SaaS integration, arbitrary power failure, split-brain, or multi-host network evaluation. The oldest unclosed operation can keep a prefix from being safely retired if its retry permission must be preserved.

## Reproduction

```bash
python -m unittest discover -s tests -v
python scripts/run_retention.py --check results/retention_v1.json
```

The complete machine-readable result is [`retention_v1.json`](../../results/retention_v1.json). CI reproduces this result alongside all earlier stages.
