# Recovery after an uncertain archive notification

[فارسی](ARCHIVE_DELIVERY_V1_FA.md) · [Frozen case outcomes](../../results/archive_delivery_v1.json) · [Previous repair study](ARCHIVE_REPAIR_V1.md)

## Workflow

A confirmed archive operation can require a separate notification. If the destination writes the notification and the worker stops before saving the reply, the recovered worker sees a pending operation. A timeout also occurs when no write was made or the first attempt is still in flight. Those possibilities require different treatment when the destination does not deduplicate.

This prototype uses two on-disk SQLite databases. In the source, a verified archive decision inserts a receipt and makes its prepared notification pending in one transaction. A worker delivers the pending record. In the destination, the immutable notice identity and the inbox effect share one transaction. Repeated requests with the same identity and payload receive the original effect ID. The source marks the operation sent after it accepts a matching receipt. A key or payload conflict leaves the source record pending.

The source authorization covers the archive commit. The notice describes an archive that already occurred, so the decision to record that historical notice survives a later state change. Withdrawable dispatch authority, authorization to disclose content, and separate reconciliation authority require their own rules. The fixture uses trusted application code, a stable namespace with unique request IDs, and only a synthetic notice. The destination keeps its deduplication records throughout the retry horizon.

## Design and results

The deterministic grid crosses eight schedules, four recovery methods, and caps of zero, one and two recovery cycles, producing **96 executable cases**. The first attempt is outside the recovery cap. The schedules cover normal operation, loss before the first write, lost acknowledgement, late first commit, duplicate transport delivery, a stale lookup, a persistent outage and two lost acknowledgements. The stale read and transport timings are injected. Each write runs through SQLite and receipt counts come from the persisted destination ledger after delayed attempts settle.

The four conditions are stop on an unknown outcome, retry blindly, look up the effect before retrying, and retry with a stable key and atomic destination deduplication. The first three use the same unkeyed destination behavior. The keyed condition changes the destination contract as well as the recovery policy.

| Method with up to two recovery cycles | Exactly one effect | Duplicate effect | Missing effect | Source still pending |
| --- | ---: | ---: | ---: | ---: |
| Stop on unknown | 5 | 1 | 2 | 6 |
| Blind retry | 2 | 5 | 1 | 1 |
| Lookup then retry | 4 | 3 | 1 | 1 |
| Stable key and atomic deduplication | 7 | 0 | 1 | 1 |

A source record can remain pending even when its effect exists. The last column is a source observation and can overlap the first three outcome columns. In the late-commit case, a lookup sees no existing effect, the retry succeeds, and the first request arrives afterward. The unkeyed destination records two effects. With the stable key, both receive the same effect ID. The persistent outage still prevents completion. It leaves the operation pending.

A separate regression test exits a real child process immediately after the destination transaction commits. A replacement connection delivers the pending operation, obtains the original receipt and records completion with exactly one inbox row. Other tests cover concurrent destination writers, rollback of source enqueue, refusal to release a notice for an unapproved archive, a repaired archive decision, mismatched payloads and mismatched receipts.

## Relation to recent work

Li's 2026 LIMBO preprint compares agents and destination contracts under failures including late commits and duplicate delivery. It shows why verification alone cannot guarantee exactly-once effects for unbounded in-flight requests. Marla and Bhatnagar's October 2026 preprint separates fresh dispatch authority from reconciliation for effects that may already exist. ResumeCert uses these distinctions to connect its pre-commit evidence repair to a deliberately narrower post-commit notification with a real source and destination ledger. The transactional outbox and receiver idempotency are established patterns. [LIMBO](https://arxiv.org/abs/2609.29095) · [Permission to Retry](https://arxiv.org/abs/2610.31916) · [AWS transactional outbox](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html)

The safety claim is at most one effect for a stable key within this store's retention and transaction boundary. Completion requires a delivered attempt and successful confirmation. A provider with expiring deduplication needs a retry horizon compatible with its retention. Real provider integration, a network benchmark, production authorization changes and model behavior are outside this prototype. [Stripe idempotency documentation](https://docs.stripe.com/api/idempotent_requests)

## Reproduce

Run the following from the repository root with Python 3.12.

```bash
python -m unittest discover -s tests -p test_effect_delivery.py -v
python scripts/run_archive_delivery.py --check results/archive_delivery_v1.json
python scripts/run_archive_delivery.py --traces
```

The check compares each case's persisted receipt count, status and operation counts with the independently constructed reference table. The trace command exposes the order of transport calls, lookups, observed replies and late effects. No notification is sent outside the two temporary SQLite databases.
