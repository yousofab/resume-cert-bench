# Bounded recovery of witnessed provider state

The [previous stage](WITNESS_REDO_V1.md) recovers one witnessed SQLite change. An offline backup can lag by several writes even though the independent witness has survived. This experiment measures how many such backups can serve again, and whether a failed replay can leave a candidate partly updated.

## Recovery contract

Provision the redo-chain table on an independently retained witness with `create_redo_chain`. Use `BoundedRedoAuthority` with the same pinned provider and store identity and a positive integer capacity `k`. Each effective change advances the witness head, appends a typed command with its predecessor and successor digests, and prunes commits older than the latest `k` revisions in one witness transaction. Retention is per provider and store. A failed prune rolls back the append and the head advance.

The operator restores the provider image offline and runs `recover()` before serving. A single witness read snapshot supplies the head and retained records. A candidate already matching the observed head returns zero replays. For a stale candidate, the recorded revisions must cover the missing interval, the first predecessor must match the restored digest, and the linked digests must lead to the observed head. Each register, apply or permanent-retire command runs through the same SQLite transition as on the normal path. The reconstructed digest is verified after each command. One provider transaction commits all replays and the final revision. On interruption or mismatch, the candidate keeps its original logical state. Concurrent recovery calls on the same provider file serialize on the provider write lock. Normal calls keep checking the current witness head.

For a complete, deterministic retained suffix, replay follows by induction. The restored state supplies the first predecessor. Every checked transition supplies the next predecessor, until the final state equals the observed head. A missing record breaks this argument even if the numerical lag fits the configured capacity.

## Experiment

The matched comparison uses the actual one-transition implementation and two chain capacities. Each case creates two synthetic source requests, registers, applies and permanently retires each on the primary, and restores exactly one offline backup taken at a revision from 0 through 6. Source and guard are held outside the provider restore. A successful run must match the original head, reproduce both signed receipt objects, confirm both requests through the source and reject both retired dispatches. A repeat recovery must do zero work.

| Policy | Repaired stale backups (of 6) | Ready backups (of 7) | Quarantined | Command bytes retained at revision 6 |
| --- | ---: | ---: | ---: | ---: |
| Previous one-transition redo | 1 | 2 | 5 | 187 |
| Chain, capacity 2 | 2 | 3 | 4 | 373 |
| Chain, capacity 4 | 4 | 5 | 2 | 752 |

The byte count covers the serialized commands for this fixture. The witness database also stores digests, identities, revisions and SQLite pages. The runs enumerate backup offsets and provide coverage counts for this schedule.

Four additional cases restore revision 3 with a witness at revision 6. A missing record and a broken predecessor digest at revision 5 are rejected. A syntactically valid retirement substituted for the recorded apply at revision 5 fails the recomputed successor check after replay has begun. A separate process exits immediately after verifying its second replayed transition inside the recovery transaction. In all four cases the candidate reopens at the same revision and digest, with the same effect and retirement counts. After removing the injected faults, each run replays all three changes and confirms both source requests.

## Prior work and scope

- ARIES provides the established redo context, including repeating logged history during recovery. ResumeCert uses typed application transitions, independent state commitments and a single local recovery transaction. [Mohan et al., ACM TODS 1992](https://www.cs.cmu.edu/~15849g/readings/mohan92.pdf)
- Raft describes the space and replay trade-off of growing logs and uses snapshots for compaction. Here a configured suffix sets the supported lag for a known offline backup. [Ongaro and Ousterhout, USENIX ATC 2014, §7](https://raft.github.io/raft.pdf)
- Ananke uses process-failure injection to check restoration in live applications. This study tests a narrower second interruption inside SQLite replay and compares state before retry. [Liu et al., FAST 2025](https://www.usenix.org/conference/fast25/presentation/liu-jing)
- XLL validates committed-log replay across several failure points in TiKV and measures the storage cost of redundant writes. This study records retained command bytes and keeps the witness independent of the provider image. [Shawger et al., NSDI 2026](https://www.usenix.org/conference/nsdi26/presentation/shawger)
- The November 2026 preprint distinguishes full from partial recovery in a collection of public agent incident reports. That distinction motivates checking both the state and the resulting source confirmations here. [Hapuarachchi, arXiv preprint](https://arxiv.org/html/2611.27090v1)

The configuration expresses a number of changes per provider. A backup policy has to relate that number to the maximum writes between durable usable backups. An existing witness can adopt the new table from a verified current image. The next state change starts populating history, so earlier one-step records are outside the new chain. Increasing capacity preserves more future records. Reducing it limits what recovery can admit at once and prunes excess on the next effective write.

These are local SQLite effects under a trusted and reachable witness with offline restores. The earlier [external-effect control](WITNESS_REDO_V1.md#external-effect-control) still applies when an email, payment or other effect commits outside the transaction. Tests cover one replay process exit, exact digest mismatches, concurrent recovery and a failed prune. Hardware failures, adversarial witnesses and production throughput are outside this experiment. Each replay hashes the declared provider state. The retained commands carry the payload, so their retention inherits its data-handling requirements.

```bash
python scripts/run_authority_redo_chain.py --check results/authority_redo_chain_v1.json
python -m unittest discover -s tests -p 'test_authority_redo*.py' -v
```

[Implementation](../../resumecert/authority_redo_chain.py) · [Runner](../../scripts/run_authority_redo_chain.py) · [Traces](../../results/authority_redo_chain_v1.json) · [Persian report](AUTHORITY_REDO_CHAIN_V1_FA.md)
