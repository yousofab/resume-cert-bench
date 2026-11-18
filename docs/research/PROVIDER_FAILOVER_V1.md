# Certified failover between independent providers

[فارسی](PROVIDER_FAILOVER_V1_FA.md) · [Recorded traces](../../results/provider_failover_v1.json) · [Prior destination revocation study](DESTINATION_REVOCATION_V1.md)

A workflow handed off to provider A may outlive the worker that submitted it. If A does not answer, a replacement worker might route the same operation to B. The two providers each deduplicate the same stable key in their own database. Each can still create an effect, giving two effects across the workflow. The fixture represents release of one synthetic archive handoff packet.

## Design

Three separate SQLite files hold the source workflow and two destination ledgers. The existing ResumeCert gate commits the local archive receipt and activates its outbox. The trusted coordinator enrolls one fixed pair of providers for that operation and payload. A provider-bound dispatch must address the current route. Receipt reconciliation records the provider along with the local effect ID.

Switching routes requires a permanent retirement recorded by the enrolled primary. The primary closes its current generation, records the existing effect or its absence, and disables every further grant for that operation in one transaction. A prior generation-only fence can be upgraded while it still reflects the current closed generation. Retrieving a retirement record after a lost reply gives the same result. If a current or retired primary receipt is available, recovery settles the original receipt. If retirement proves absence, the coordinator durably activates the one enrolled secondary. If neither receipt nor retirement evidence is available, it keeps the primary route and the outcome unresolved.

For the protected path, any write committed at the primary before retirement is observed by the retirement transaction, which excludes switching. An empty retirement precludes every later primary write under this operation key. The coordinator enables the secondary only after observing that empty retirement. A unique operation key gives the configured secondary at most one write across its own retries. These properties compose under the fixture assumptions. A crash between retirement and route transition delays progress and leaves the primary closed. A crash after route transition permits recovery using the persisted secondary assignment. This design uses independent SQLite stores, trusted administrative callers, stable provider identities and durable maintained records at both providers. The external network, provider authentication and storage loss are outside the tested boundary.

## Results

Each method ran the same 16 schedules for **64 executions**. The schedules include delayed primary arrival, lost primary and secondary replies, missing control evidence, generation regrant, restart, repeated recovery and eventual visibility of a previously inaccessible retirement. The runner reads the persisted destination ledgers and source status. An event-only test independently reconstructs the counts.

| Policy | Confirmed with exactly one effect | Duplicate across providers | Unresolved at end | Secondary effects |
| --- | ---: | ---: | ---: | ---: |
| Switch on timeout | 11 | 5 | 0 | 15 |
| Switch on empty lookup | 12 | 2 | 2 | 11 |
| Switch after a generation-only fence | 12 | 1 | 3 | 10 |
| Switch after empty permanent retirement | 11 | 0 | 5 | 8 |

For the protected path, 3 outcomes were confirmed at the primary, 8 at the secondary, and 5 remained unresolved. Of those 5, 3 had a primary effect that the source had not confirmed within the scheduled observations, and 2 had no effect. Those five are distinct from the duplicate count. The confirm and duplicate categories in the first two result columns, together with unresolved, partition these runs.

In the ordinary-fence counterexample, the primary closes generation 1 without an effect. The coordinator switches to B. A new approval on A creates generation 3, and A and B both commit. The protected path waits for permanent retirement. Once retirement is installed, the primary rejects regrant for that operation. A case with eventual visibility of retirement shows progress when durable evidence becomes readable.

The test suite includes a subprocess that exits after the primary commits retirement and before its result reaches the coordinator, a real SQLite race between primary apply and retirement, transaction rollback with an injected failure, restart after route transition, late arrivals and provider/payload binding.

## Research placement

AID-Guard already studies certified absence, provider fencing and one-successor transfer under specified provider contracts. Permission to Retry distinguishes authority to retry from ability to reconcile an earlier outcome. This experiment isolates the difference between a reopenable provider fence and transfer-safe retirement, and reports the safety–availability tradeoff across matched primary and secondary histories. The EC2 documentation gives a concrete example of why an idempotency token can have a narrower scope than an operation's fallback policy. The IETF document is an Internet-Draft. [AID-Guard](https://arxiv.org/abs/2608.21159) · [Permission to Retry](https://arxiv.org/abs/2610.31916) · [EC2 idempotency scope](https://docs.aws.amazon.com/ec2/latest/devguide/ec2-api-idempotency.html) · [IETF draft](https://www.ietf.org/archive/id/draft-abak-agent-control-delivery-evidence-02.html)

## Reproduce

With Python 3.12, run from the repository root.

```bash
python -m unittest discover -s tests -p 'test_provider_failover*.py' -v
python scripts/run_provider_failover.py --check results/provider_failover_v1.json
```

Run the second command without `--check` to emit complete traces. The fixture runs entirely on temporary SQLite databases.
