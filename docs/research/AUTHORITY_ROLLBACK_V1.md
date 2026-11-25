# Verifying provider authority after rollback

Evaluated 26 November 2026. [Persian report](AUTHORITY_ROLLBACK_V1_FA.md).

## Continuity with ResumeCert

The original simulator certified decisions under a bounded observation contract. The lifecycle studies then required evidence that survived the transition to an effect. Permanent retirement made switching destinations safe while the predecessor remained closed. Terminal attestations carried that fact to the caller, and the [retained provider authority](PROVIDER_AUTHORITY_V1.md) kept it outside projection restores. The last shared-restore control still produced a second effect when the provider authority itself returned to an older image with the same identity.

This stage makes state freshness a condition for serving from an authority image. A healthy current image, including a separately saved copy of its exact state, remains admissible. The mechanism detects a missing current image and stops that provider's protected service. Recovering the missing rows requires the matching image or another separately justified recovery mechanism.

## Protocol

[WitnessedAuthority](../../resumecert/witnessed_authority.py) binds each provider and store identity to a revision and SHA-256 commitment in a separately retained SQLite witness. The commitment covers the schema and a canonical encoding of the rows in the provider identity, authority identity, authorization, effect, fence, and permanent retirement tables. The revision is compared separately. The tables and encoding belong to this controlled fixture.

Before using an authority, the service takes its SQLite writer lock and checks the candidate revision and commitment. The same check covers reservation, effect insertion, attestation issuance, projection refresh, and explicit image verification. A stale image therefore cannot issue a fresh terminal absence attestation. Pure reads, existing-effect retries, and repeated terminalization leave the witness unchanged.

A state-changing operation is staged in the provider transaction. The witness then updates its head from exactly the observed revision and commitment to the next revision and candidate commitment in a separate durable transaction. Only after that compare-and-set succeeds does the provider update its local revision and commit. The response leaves the service after provider commit. Concurrent different transitions prepared against the same witness head cannot both commit through this path. In the unit test, two provider copies compete with the same witness and one provider transaction rolls back.

The check prevents serving from a predecessor after an acknowledged state change as long as the witness survives and all supported calls use the verified path. It trusts the configured paths, schema and issuer. Enrollment provisions an unused authority. An apparent recovery candidate may never be used to initialize a new head. Restore offline with provider connections closed and check the candidate before reopening traffic. Every protected call rechecks the witness.

## Why a crash can require quarantine

The witness advances first. If the process stops before the provider commits, the witness can point at a state that has never appeared in a committed image. Two schedules persist the same gate observation, including both fingerprints, both revisions and source state.

| History | Retained witness | Available provider image | Actual historical effects |
| --- | --- | --- | ---: |
| Witness committed and provider transaction failed | Proposed state at revision 2 | Revision 1 | 0 |
| Witness and provider both committed, then the provider was restored | Same state at revision 2 | Same revision-1 image | 1 |

The full policy quarantines both histories. Reopening revision 1 solely because the provider image is empty would also reopen the second history. The runner injects a revision-write failure at this boundary. A separate real process-exit test terminates after witness commit and confirms the provider rollback and quarantine. A separate matched case resumes when the verified current backup is restored.

## Literature and scope of contribution

| Primary source | Relevance |
| --- | --- |
| Parno et al., [Memoir](https://www.microsoft.com/en-us/research/publication/memoir-practical-state-continuity-for-protected-modules/), IEEE S&P 2011 | Uses independently protected request history and safe replay to tackle state continuity across crashes. This fixture uses explicit admission and exhibits the crash window where richer recovery evidence would be needed. |
| Tong et al., [AID-Guard](https://arxiv.org/html/2608.21159v1), 2026 preprint | Specifies that terminal state must be retained through the recovery horizon. The new comparison makes failure of that assumption observable for a controlled provider. |
| Hapuarachchi, [Exercised, Rarely Recovered](https://arxiv.org/html/2611.27090v1), 20 November 2026 preprint | Studies public reports of agent recovery. It motivates scoring completed work separately from defensive stops. Its sample of reports is distinct from the controlled SQLite schedules here. |

Revision checks, commitments, external witnesses, and compare-and-set are established mechanisms. The contribution is their integration with ResumeCert's provider effect and terminal evidence path, a matched sibling-image control, and the reproduced ambiguity at the two-store commit boundary.

## Experiment

[The runner](../../scripts/run_authority_rollback.py) pairs 13 constructed schedules with three comparison policies. All share the independent witness bookkeeping. The identity-only ablation ignores freshness. The revision-only ablation compares the number. The full policy compares the revision and content commitment. An unavailable witness therefore blocks all three variants by design. Twelve schedules preserve the witness. The 13th separately rolls back the witness and authority.

| Check | Exactly one historical effect and source confirmed | Duplicate | Unresolved |
| --- | ---: | ---: | ---: |
| Identity | 5 | 6 | 1 |
| Identity and revision | 8 | 2 | 2 |
| Identity, revision and commitment | 10 | 0 | 2 |

Each column counts schedules. The two revision-only duplicate histories use different images at the same revision. In one the legitimate history retired the primary and reached the secondary. In the other the primary already recorded an effect. The sibling reached the same revision through a different authorization update. The full policy refuses both. The unresolved full-policy cases have one unconfirmed historical effect after loss of the current image and zero effects after the witness-ahead failure.

All variants produce a duplicate when both the witness and provider return to their earlier state. The witness must remain outside the restore domain. This test covers loss or replacement of trusted files in the stated fixture. It does not establish a hardware root, cross-host consensus, or recovery from a malicious or jointly restored witness. The full scan reads and sorts all covered rows at each check, so incremental commitment, checkpoint cadence, throughput and physical fault tolerance remain unmeasured.

The effect is an inert SQLite insert and its receipt shares the transaction. Provider-state backups use SQLite's backup API and are restored with closed connections. The scorer records successful creations outside the restore domain. The runner does not simulate an evaluator crash in the scoring gap. A separate trace test recounts all 39 runs, checks the same-revision controls and compares the equal gate observations.

## Reproduction

```bash
python -m unittest discover -s tests -v
python scripts/run_authority_rollback.py --check results/authority_rollback_v1.json
```

[Full results and trace](../../results/authority_rollback_v1.json).
