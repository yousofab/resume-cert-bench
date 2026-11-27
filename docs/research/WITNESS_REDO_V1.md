# Recovering one witnessed provider transition

Evaluated 27 November 2026. [Persian report](WITNESS_REDO_V1_FA.md).

## Problem

ResumeCert began by checking authority with bounded observations. The recovery studies added historical receipts, permanent retirement before provider transfer, a retained provider authority and an independent state witness. In the [last stage](AUTHORITY_ROLLBACK_V1.md), the service correctly quarantined an old image. It also quarantined a process exit after advancing the witness and before committing SQLite. A digest alone cannot reconstruct the missing state.

This stage adds a typed, bounded redo record. It can reconstruct one already witnessed transition from its exact predecessor. This is a local recovery contract for the inert provider database. It assumes that effect rows and receipt numbering are part of the same deterministic SQLite transaction.

## Implementation

The witness compare-and-set records the next revision, successor digest, predecessor digest and canonical command in one witness transaction. The provider transaction then commits. Each provider and store retains at most the latest redo record. Reads and idempotent requests preserve it. Enrollment continues to use the earlier admission contract. An existing witness can gain the new table before switching to the redo service, and redo data becomes available after a subsequent state change.

An operator or recovery process calls `recover()` on an offline-restored image or after a crash.

1. Read the witness head and matching redo record from one snapshot. A currently matching revision and digest require no write.
2. Require the exact predecessor revision and state commitment and require the record to name the observed head. Replay the recorded register, apply or retire transition using local SQLite routines.
3. Check that the reconstructed state has the expected digest before updating the local revision and committing. Any mismatch rolls back. The witness head is unchanged by repair.

This preserves the evidence boundary. Normal requests and attestations still check freshness on every call. A repaired predecessor cannot reopen an already retired operation. The resulting receipt is for the inert database effect. Recovering a register transition reconstructs destination registration alone. Source reservation still goes through the original authorization and guard when normal service resumes.

The witness is assumed trusted, independently retained, correctly configured and reachable. Candidate restore remains offline, with the provider handles closed. The provider lock serializes reconstruction in one image. The result describes the witness head observed by that recovery. Subsequent admission checks use the then-current head. Schema changes or replay differences that change the computed successor digest cause quarantine.

## Prior work and new evidence

| Primary source | Use in this stage |
| --- | --- |
| [Memoir](https://www.microsoft.com/en-us/research/publication/memoir-practical-state-continuity-for-protected-modules/), IEEE S&P 2011 | Protected request history and deterministic replay motivate adding reconstructible input to the state witness. |
| [ARIES](https://research.ibm.com/publications/aries-a-transaction-recovery-method-supporting-fine-granularity-locking-and-partial-rollbacks-using-write-ahead-logging), ACM TODS 1992 | Write-ahead recovery provides the established transaction context. The prototype has a deliberately narrower, single retained transition. |
| [Exercised, Rarely Recovered](https://arxiv.org/html/2611.27090v1), preprint dated 20 November 2026 | Public reports of partial and failed agent recovery motivate concrete crash cut points and separate scoring of service recovery and task completion. |

The contribution is their integration with the existing provider and source evidence boundary, an executable comparison against digest-only admission, and an external-effect control. It does not introduce a new general database recovery algorithm.

## Results

The two policies share the same authority, source, guard and freshness checks. `digest_only` checks the current state and quarantines any mismatch. `one_transition_redo` adds verified repair. Twelve constructed schedules run per policy.

| Policy | Confirmed with exactly one effect row across destinations | Unresolved | Primary current at end | Transitions repaired |
| --- | ---: | ---: | ---: | ---: |
| Digest-only witness | 2 | 10 | 1 | 0 |
| Witness with redo | 8 | 4 | 8 | 7 |

A task can remain confirmed at the secondary while a stale primary is quarantined. The separate current-primary metric captures this case. The full policy repairs effect, retirement and registration gaps. It also repairs a one-generation restore after acknowledgment, preserves the existing receipt, and tolerates a second interruption during repair. The evaluator inspects materialized rows after each schedule step. A row reconstructed after loss of a previously committed image is a physical reinsertion of the same logical SQLite state.

The four unresolved schedules have a two-revision gap, divergent predecessor, modified redo command or unavailable witness. The trusted witness still has to lie outside the rollback domain. The joint rollback control from the previous study remains supported by existing regression tests.

### External-effect control

Two additional histories use a separate SQLite table as an opaque non-idempotent sink. One starts with no external effect, the other already has one. The provider state observed before repair and the retained witness head match between histories. A blind test resend produces one and two sink entries respectively. The redo implementation never touches the sink. This illustrates the need for independent outcome evidence or provider-side idempotency when an effect falls outside the transaction.

## Verification and limits

The repository suite contains 103 tests. The new tests include a real subprocess exit after witness commit, simultaneous recovery attempts, and journal-write failure that rolls back the witness head. A result mismatch is checked after local replay, and the test confirms no partial retirement was committed. The schedule traces and counters are independently recounted.

This is a single-host deterministic SQLite study. It neither measures performance at production scale nor simulates hardware loss, concurrent online restore, or a witness that returns false state. The retained command includes the operation payload. A deployment must account for its storage and retention as it does for the provider state. No multi-revision replay is claimed.

```bash
python -m unittest discover -s tests -v
python scripts/run_witness_redo.py --check results/witness_redo_v1.json
```

[Implementation](../../resumecert/authority_redo.py) · [Runner](../../scripts/run_witness_redo.py) · [Traces and control](../../results/witness_redo_v1.json)
