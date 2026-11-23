# Resolving unknown outcomes with purpose-bound provider evidence

Date: 2026-11-23. Exploratory deterministic SQLite study. [Persian report](TERMINAL_EVIDENCE_V1_FA.md).

## Problem, added contract, and contribution

The [open-tail study](OPEN_TAIL_V1.md) kept a claim unknown when its positive receipt was lost. It also exhibited identical logical recovery views with zero or one historical effect. This stage supplies **new authoritative information** from an independently retained provider system of record. It does not infer an answer from the old view.

The provider must retain its operation identity, effect receipt, and permanent retirement state through the recovery horizon. Its terminalization transaction must serialize with effect creation and block later delivery of a retired operation. An issuer backed by that contract can attest either a recorded effect or terminal absence. A point-in-time empty lookup provides a weaker observation, even when authenticated.

The contribution is an executable purpose/scope verifier connected to ResumeCert's existing independent claims and approved failover route, with portable evidence, source-restore recovery, adversarial evidence fixtures, and a replayable additional-information witness. Terminalization, fencing, HMACs, and exact-action binding are established mechanisms. No new cryptographic primitive or general exactly-once system is claimed.

## Sources reviewed

Primary sources were opened on 2026-11-23. Preprints and Internet-Drafts are distinguished from standards and peer-reviewed findings.

| Source | Relevant point | Use and boundary |
| --- | --- | --- |
| Tong, Dai, and Guo, [AID-Guard](https://arxiv.org/html/2608.21159v1), August 2026, preprint | Successor transfer requires terminal provider evidence, a predecessor fence, and retention across recovery. | This is direct prior art for the recovery rule. We test a narrower SQLite composition, not their implementation. |
| Schrock, [Action Evidence Boundary, draft-07](https://www.ietf.org/archive/id/draft-schrock-action-evidence-boundary-07.html), 25 September 2026, Internet-Draft, work in progress | Authenticated point-in-time absence does not prevent a live dispatcher from delivering later; verification must affirm the evidence's purpose and attempt. | Motivates separate checks for authentication, exact scope, and terminal meaning. The draft is not an RFC or an adopted standard. |
| Jiapeng Li, [Checking Retry Transparency in Agent Tools](https://arxiv.org/html/2611.23307v1), 11 November 2026, preprint | An old attempt can invalidate a later acknowledged result. Blocking and completion must be reported separately. | The benchmark includes late primary arrival after secondary acknowledgement and reports unresolved cases separately. It does not replicate the paper's tool models. |
| Song Xu, [Idempotency Is Not Recovery](https://arxiv.org/html/2611.23353v1), 17 November 2026, preprint | Separates effects, outcome knowledge, and evidence-persistence failures. | Extends the previous stage with a declared authoritative evidence channel; no direct numerical comparison is made. |

## Stores and execution boundary

Each run has a source archive/outbox and its fixed primary/secondary route, an independent guard with attempt claims and accepted evidence, a restorable local primary projection, and two separately persisted provider databases. All source notices first pass the existing archive authorization verifier.

The local projection has no surviving effect receipt and initially reports `unknown` through the previous open-tail API. A reserved dispatch creates its independent claim before the actual provider call. The actual inert handoff is created in the provider database using the existing destination authority and permanent-retirement implementation. The provider databases are the systems of record for this experiment, not read-access to the previous evaluator's private history.

The benchmark reads those provider effect tables independently for scoring. In the rollback control, the primary has no effect before restoration; restoration removes its empty retirement state, and a later first primary effect coexists with the secondary effect. No previously committed provider effect is erased from the scoring history by that restore.

## Evidence protocol

[`terminal_evidence.py`](../../resumecert/terminal_evidence.py) defines canonical JSON evidence with a provider-pinned HMAC. Fields bind protocol version, provider identity, operation key, payload digest, attempt generation, kind, purpose, and the relevant receipt or closed generation.

| Kind | Purpose | Required fact | Permitted recovery |
| --- | --- | --- | --- |
| `recorded` | `outcome` | Matching positive provider receipt | Confirm the active provider's historical effect |
| `lookup_empty` | `observation` | No matching effect observed at that lookup | Remain unknown; no successor permission |
| `retired_empty` | `terminal` | Permanent operation retirement committed with no prior effect | Enable the source-approved secondary |

The issuer obtains terminal evidence by calling the provider's atomic permanent-retirement operation. A race with an effect yields either a positive receipt or a permanently closed empty operation. The old dispatcher retains its original generation and cannot create a late effect after the empty retirement.

Verification checks canonical encoding and field shape, the provider-pinned MAC key, operation/payload/provider binding, the independent claim's generation, the kind/purpose combination, and the closed-generation relation. A valid MAC does not authorize a mismatched operation, another generation, or an observation presented as terminal authority. The verifier does not prove that an issuer tells the truth about its backend.

The consumer receives the source, guard, evidence, and pinned keys. It makes no live backend query. A certificate collected before the issuer channel becomes unavailable can therefore still be consumed. The simulation's offline flag affects evidence collection; an already dispatched primary request may still arrive.

Accepted evidence is committed to the independent guard before the source route or acknowledgement changes. Positive evidence also seals the corresponding independent claim in that guard transaction. A source crash leaves a replayable immutable decision. `recover_saved` authenticates and binds it again to reconstruct source progress after restart. Conflicting evidence cannot overwrite a previously accepted terminal decision.

A negative primary decision enables exactly the secondary enrolled by source authorization. A negative secondary decision stops empty; it does not authorize a tertiary destination or reopen the primary. Attempt claims are preserved. Accepted evidence records are retained without a new compaction API in this stage.

## Compared policies and results

[`run_terminal_evidence.py`](../../scripts/run_terminal_evidence.py) evaluates 20 fixed schedules under three same-team policies, for 60 runs. Nineteen schedules preserve the declared durable provider contract. One intentionally rolls the provider back before its empty retirement as a boundary control. The study is exploratory, developed alongside the implementation, with no preregistered or statistical prevalence claim.

All policies verify authentication and scope. The hold policy accepts positive results but waits on negative evidence. The authenticated-lookup comparator also releases on a properly authenticated empty lookup. The full policy requires terminal evidence for that release. The unsafe comparator calls a deliberately weakened private path; the public `accept` function always requires terminal meaning.

| Policy | One effect, confirmed, in 19 supported schedules | Duplicate runs | Unresolved |
| --- | ---: | ---: | ---: |
| Hold negative evidence | 3 | 0 | 16 |
| Release on authenticated lookup absence | 9 | 2 | 8 |
| Bound terminal evidence | 9 | 0 | 10 |

The full policy completes six more schedules than holding all negative evidence. It avoids the two late-delivery duplicates caused by releasing on an authenticated observation. Its ten unresolved cases include seven with no effect after rejected evidence and three with an unconfirmed primary effect. These are constructed schedules, not production rates or independent sampled tasks.

Malformed/tampered or valid-MAC misbound controls cover the operation key, payload, provider, generation, purpose, and closed generation. Other schedules cover receipt loss, delayed arrival, source-image restore, offline evidence collection, repeated evidence consumption, repeated secondary delivery, and a lost secondary acknowledgement. Canonical envelopes and MACs are included in the result for independent checking. Every key in this benchmark is explicitly public fixture material.

**Provider rollback control.** A genuine empty-retirement certificate remains authenticated after the provider image is rolled back, but the restored provider can accept the old call again. Both releasing policies then produce two effects. The hold policy remains unresolved with one primary effect. Across all 20 schedules, the full policy therefore has one duplicate run, the authenticated-lookup policy has three, and the hold policy has none. Authentication cannot compensate for a provider that loses its terminal state.

## Additional-information witness

The result records paired legacy views for an empty primary and an already-committed primary. The local projection, independent attempt claim, and source outbox are identical; the old recovery API reports `unknown` in both. The new provider evidence is `retired_empty` in the first history and `recorded` in the second. The full policy completes one effect at the secondary in the first case and confirms the primary's existing effect in the second.

This explicitly shows what changed from the prior indistinguishability witness. The provider supplies an additional observable contract; no decision rule extracts missing history from the old state alone. If that provider evidence is unavailable or invalid, recovery remains unresolved.

## Validation

Twelve new checks bring the repository suite to 82 tests. They include a real subprocess `os._exit(83)` after evidence commits in the guard but before source route commit, a concurrent effect/terminalization race using separate SQLite connections, evidence-storage failure, missing projection receipts, secondary reply loss, conflicting evidence, pinned-key failures, canonical encoding, and purpose/scope mutation checks.

A separate trace checker recounts provider effects after each event, checks acknowledged providers, independently recomputes fixture HMACs and operation/body bindings, and verifies the paired legacy-view witness. The complete 60-run JSON is reproduced by CI alongside all earlier stages.

## Trust and measurement limits

The HMAC prototype uses public deterministic test keys to exercise verification logic. It is not a credential deployment, a public-key signature system, or nonrepudiation: any party holding a shared MAC key can generate a tag. Issuer honesty, correct backend-to-evidence mapping, pinned-key integrity, stable provider identity, and retained terminal state are trusted assumptions. A correctly shaped lie by a trusted issuer may pass verification, especially before any contradictory fact has been retained.

The real provider API must implement terminalization and preserve it throughout the recovery horizon. A provider offering only an eventually consistent or ordinary negative lookup does not meet that contract. The rollback control demonstrates this boundary. The guard and source schema are trusted; arbitrary modification or guard rollback is outside the guarantee.

The effects are inert local SQLite records. No live LLM, network transport fault injection, SaaS deployment, actual notification, Byzantine provider, general multi-host system, arbitrary power loss, throughput, latency, or physical storage measurement is included. The new provider contract is stronger than the previous projection-only experiment and must not be attributed to an arbitrary external API.

## Reproduction

```bash
python -m unittest discover -s tests -v
python scripts/run_terminal_evidence.py --check results/terminal_evidence_v1.json
```

[`terminal_evidence_v1.json`](../../results/terminal_evidence_v1.json) contains all schedules, evidence envelopes, event states, scoring facts, and the additional-information witness.
