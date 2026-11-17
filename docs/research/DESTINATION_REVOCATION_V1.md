# Revocation at the destination with separate receipt recovery

[فارسی](DESTINATION_REVOCATION_V1_FA.md) · [Recorded traces](../../results/destination_revocation_v1.json) · [Prior delivery study](ARCHIVE_DELIVERY_V1.md)

The earlier delivery study concerned a notice of an already completed archive. In this study the queued operation represents release of a synthetic handoff packet whose dispatch permission can change. The initial ResumeCert decision admits the work to the source outbox. A separate destination authority determines whether an arrival may create the effect. A reader with independent reconciliation authority can recover a historical receipt after dispatch is revoked.

## Protocol and assurance boundary

The destination binds one immutable payload to one operation key. A dispatch carries its approval generation. The destination creates a new effect only if its current generation equals the carried generation and the approval is active. It checks approval and inserts the effect under the same SQLite write transaction. A unique operation key also makes an already accepted effect single-instance.

Revocation checks the expected generation, closes it and records the receipt visible at the fence in the same transaction. The result is immutable for that retired generation. A retry of the old revocation returns that result without closing a newer approval. A new explicit approval increments the generation again. It permits a new attempt with the same payload while leaving old in-flight attempts ineligible.

An empty ordinary lookup means the work is unresolved. After a recorded fence, an empty receipt observation supports the narrower conclusion that the operation had no recorded effect at that fence and the retired generation can no longer create one. The conclusion is scoped to one destination and one generation. An explicitly renewed generation may subsequently create the first effect. A receipt returned after revocation refers to a committed effect, and reading it creates no effect.

The destination transaction is the linearization point. The interval between the source requesting revocation and the destination applying it remains observable and operationally relevant. The two SQLite files, trusted caller roles and the guarded destination table are the boundaries of this prototype. Identity verification for remote callers, multiple destinations, automatic compensation and production transport are outside the experiment.

## Experiment and results

Four policies ran the same 14 scenarios for **56 executions**. The key-only and sender-recheck baselines deliberately share stable-key deduplication. Sender recheck verifies the destination before queuing and also stops new work when the sender knows of the revocation request. The two fenced policies share the same destination enforcement. The coupled lookup policy stops checking receipts after the source requests cancellation. The separate-lookup policy can read a prior receipt or an already-installed fence under its distinct read authority.

| Policy | New effects with stale or inactive destination approval | Previously recorded effects left unconfirmed | Confirmed effects | Confirmed absence at the fence | Unresolved |
| --- | ---: | ---: | ---: | ---: | ---: |
| Stable key | 5 | 0 | 13 | 0 | 1 |
| Sender recheck | 4 | 0 | 11 | 0 | 3 |
| Destination fence, coupled lookup | 0 | 4 | 5 | 0 | 9 |
| Destination fence, separate lookup | 0 | 0 | 9 | 4 | 1 |

A confirmed receipt measures whether the source reconciled what happened. The baseline receipt counts include effects that were created with stale approval. The first two columns are separate diagnostics rather than mutually exclusive outcomes. The last three partition the 14 executions for each policy.

For both fenced policies, two valid-at-destination effects arrived after revocation was requested but before the fence was installed. In one case the revocation control message was never delivered while the destination still processed the previously submitted request. In the separate-lookup policy's one unresolved case, there was no effect and no installed fence. The system kept that outcome unknown.

An independent event-only oracle reconstructed the authority transitions and fence observations for all 56 traces. A separate concurrent test races a genuine SQLite write with revocation across independent connections. If the write wins, the fence captures its receipt. If revocation wins, the write is blocked and absence is recorded. Additional tests cover rollback of a failed fence write, restoration after reopening SQLite, epoch reuse and replay, and payload mismatch.

## Research placement

Marla and Bhatnagar's October 2026 preprint separates permission to dispatch from permission to reconcile. Zhu and Wang's 2026 preprint formalizes provider-local fences in a broader system of delegated authority. Tong et al. combine authorization, reservation and certified absence with a delivery fence. The new artifact here is a bounded comparison inside ResumeCert of where the check runs, whether recovery has its own authority, and what can be proved before and after the destination observes the control. Generation fencing has a systems precedent in Chubby's sequencers. The IETF evidence document is an Internet-Draft. [Permission to Retry](https://arxiv.org/abs/2610.31916) · [Root-scoped quiescence](https://arxiv.org/abs/2609.21284) · [AID-Guard](https://arxiv.org/abs/2608.21159) · [Chubby](https://research.google/pubs/the-chubby-lock-service-for-loosely-coupled-distributed-systems/) · [IETF draft](https://datatracker.ietf.org/doc/draft-abak-agent-control-delivery-evidence/)

## Reproduce

Run with Python 3.12 from the repository root.

```bash
python -m unittest discover -s tests -p 'test_revoc*.py' -v
python scripts/run_destination_revocation.py --check results/destination_revocation_v1.json
```

Run the second command without `--check` to emit the complete timeline and summary JSON.
