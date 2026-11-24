# Provider authority that survives projection restore

Evaluated 24 November 2026. [Persian report](PROVIDER_AUTHORITY_V1_FA.md).

## Problem and design

The [terminal-evidence study](TERMINAL_EVIDENCE_V1.md) produced a duplicate after the primary provider returned to an image from before its permanent retirement. The old certificate stayed authentic while its enforcement disappeared. A second failure is possible when an effect commits, the reply disappears, and a backup restore erases the evidence before a separate copy is made. The restored provider may then report empty and permit a second effect.

This stage separates a provider's recoverable projection from its system of record. The source still uses its verified outbox, fixed provider pair, independent attempt claim, and purpose-bound evidence consumer. Both providers now have a retained SQLite authority that owns effect creation and permanent retirement. The effect row is itself the durable receipt for this inert action. Effect insertion, its receipt, and the authority check share one transaction. The retirement transaction shares the same writer order and either captures the existing effect or permanently disables insertion for the operation.

[ProviderAuthority](../../resumecert/provider_authority.py) opens the configured store without creating a replacement file and checks the pinned provider and store identifiers. Application, dispatch reservation, and evidence issuance use that store. The projection holds read-only material derived from the provider in the application sense. It can be refreshed or rebuilt, and it supplies no authority. Refresh serializes its observation and materialization using the authority writer lock, which can add contention. A stale, missing, or unwritable projection does not change the result of a separate apply or issue call. Authenticated evidence retains the previous scoped HMAC format.

With the authority retained, two insertions for one key at the same provider would violate the unique operation-key constraint. In a cross-provider transfer, the secondary can be selected only after the primary's empty retirement. The primary applies and retires in one serialization order. If apply wins, the primary can attest its stored effect. If empty retirement wins, all later primary arrivals are denied. Restoring the source projection or the provider view preserves these facts while the authoritative store and independent source guard survive. This reasoning covers the fixed route and stable operation identity in the fixture.

## Primary references

| Source | Relevance |
| --- | --- |
| [AID-Guard](https://arxiv.org/html/2608.21159v1), August 2026 preprint | Specifies retention of terminal provider state across the recovery horizon. This stage exercises an implementation of that requirement in the ResumeCert SQLite fixture. |
| [Idempotency Is Not Recovery](https://arxiv.org/html/2611.23353v2), preprint revised 20 November 2026 | Separates effect creation, outcome knowledge, and evidence persistence under injected faults. The matched experiment here compares recovery domains and the gap after provider commit. |
| [RIFL](https://web.stanford.edu/~ouster/cgi-bin/papers/rifl.pdf), SOSP 2015 | Makes completion records durable with operation mutations and accessible on retry. The analogous storage assumption is explicit in the retained authority. |

The contribution is an executable comparison of these design choices, with a rebuildable projection and a shared-restore boundary control. The study builds on established atomic completion and fencing mechanisms.

## Matched experiment

[The runner](../../scripts/run_provider_authority.py) applies three policies to 17 fixed schedules each. Sixteen schedules retain the independently managed authority or evidence copy. A separate control rewinds that storage where present. The cases exercise lost replies, commits and retirement, repeated restores, both providers, source restore, an unavailable projection, and transient or persistent authority outage. Outage events are deterministic fixtures. The projection-unavailable scenario physically removes its path.

| Policy | Exactly one effect and confirmed | Duplicate | Unresolved |
| --- | ---: | ---: | ---: |
| Provider image only | 4 | 8 | 4 |
| Independent evidence copied after commit | 10 | 4 | 2 |
| Retained provider authority | 15 | 0 | 1 |

Counts are across 16 supported schedules per policy for the first operation. All duplicate cases are settled by the source, so the table partitions the 16 cases. The additional 17th schedule produces a duplicate under each policy and is reported separately. This is an exploratory comparison of constructed histories on a single host.

The evidence-copy comparator durably copies positive receipts and empty retirement certificates before returning them to the caller. It consults that copy before replay, so a previously completed copy protects across projection restore. The comparator still has a gap between committing an effect and saving the independent receipt. A restored image can both replay an effect and incorrectly attest empty retirement during that gap. The retained authority recovers the positive receipt already committed with the effect. It also reconstructs an empty retirement after a crash before the certificate reaches the source.

The retained-authority policy leaves one operation unresolved when the store stays unavailable after an effect. A separate schedule confirms an unrelated authorized operation after projection restore while the original primary path remains retired. Another schedule rebuilds a stale projection from unresolved to recorded without creating an effect.

**Shared-restore control.** After terminal proof and secondary commit, the control restores the primary's authority itself to before retirement. The provider and store IDs still match. The old primary arrival creates a second effect. Keeping an identity pinned detects a substituted store ID. An old copy of the same authority is outside the retention contract and requires a separate anti-rollback or replication mechanism.

## Validation and scope

The benchmark takes genuine SQLite backup images and restores them offline. The scorer records historical insertions outside the restore operation by inspecting the effect state before application. Its evidence is private to the benchmark and uses sequential events. It does not share the subprocess fault window in the tests. A trace test independently recounts effect creation, checks source settlement, and verifies the stale projection and boundary controls.

The 89-test suite includes real subprocess exit after an effect commit and after retirement without communicating the response, an apply-versus-retire race with independent connections, missing and substituted authority stores, and projection write failure. The two subprocess cuts inspect durable store state directly.

The fixture requires exclusive mediation of supported actions through the retained authority and trusted provider configuration and schema. The controlled effect is an inert SQLite row. Applying this design to an irreversible external action requires that action's own authoritative transaction or an equivalent provider contract. It cannot be obtained by adding a database alongside an arbitrary external call. Replication, consensus, storage cost, production throughput, power loss, live model behavior, and real provider integrations are outside the measured scope.

```bash
python -m unittest discover -s tests -v
python scripts/run_provider_authority.py --check results/provider_authority_v1.json
```

[Complete results and event traces](../../results/provider_authority_v1.json).
