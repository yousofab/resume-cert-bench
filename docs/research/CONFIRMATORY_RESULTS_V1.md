# ResumeCert confirmatory results v1

[فارسی](CONFIRMATORY_RESULTS_V1_FA.md) · [Registered protocol](CONFIRMATORY_PROTOCOL_V1.md) · [Complete summary](../../results/confirmatory_v1.json) · [Run traces (JSONL gzip)](../../results/confirmatory_v1_runs.jsonl.gz)

## Primary result

At two unit-cost reads per snapshot, greedy adaptive and optimized fixed both certified **11/96 (11.46%)** on the two held-out synthetic workflow families. The methods certified the same 11 snapshots. The registered contrast was **0/96 (0 percentage points)**, which meets the prespecified **no pooled gain** rule. Each policy used 184 units in total (1.92 per snapshot), certified one allow and ten blocks, and certified both arms of zero matched pairs.

| Family | Snapshots | Both | Adaptive only | Fixed only | Neither | Coverage difference | Units per policy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Quorum release | 48 | 2 | 0 | 0 | 46 | 0/48 | 96 |
| Capacity reservation | 48 | 9 | 0 | 0 | 39 | 0/48 | 88 |
| Pooled | 96 | 11 | 0 | 0 | 85 | 0/96 | 184 |

The quorum certificates were one allow and one block. All nine capacity certificates were blocks. In each policy, 47 of 48 actually allowed snapshots were deferred. Each policy had zero raw false allows, zero raw false blocks, zero rejected claims, and zero reads that left the compatible-world count unchanged.

For each policy, 71 of the 85 deferrals had a case-specific minimum evidence cost above budget. The other 14 had some affordable certifying subset when chosen with hindsight. This lower bound uses the actual state to select the subset and is a diagnostic for evidence availability. The other registered strata give 4/48 pivotal and 7/48 benign certificates, and 8/48 low-text and 3/48 high-text certificates for each policy. Text level is a matching factor in the design.

## Secondary results

Each budget row uses the same 96 snapshots. Entries give certified snapshots and total read units.

| Read budget | Optimized fixed | Greedy adaptive | Exact prior tree | Seeded random |
| ---: | ---: | ---: | ---: | ---: |
| 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| 1 | 8 / 96 | 8 / 96 | 8 / 48 | 0 / 96 |
| 2 | 11 / 184 | 11 / 184 | 12 / 157 | 3 / 192 |
| 3 | 30 / 269 | 31 / 269 | 37 / 261 | 10 / 285 |
| 4 | 68 / 335 | 68 / 334 | 68 / 334 | 27 / 371 |

The exact tree optimizes coverage and then cost under the uniform prior over admissible worlds. The empirical distribution of the synthetic suite can differ from that prior.

| Budget-two cost regime | Optimized fixed | Greedy adaptive | Exact prior tree | Minimum cost above budget |
| --- | ---: | ---: | ---: | ---: |
| Unit-cost singleton reads | 11 / 184 | 11 / 184 | 12 / 157 | 71/96 |
| Additional unit-cost two-field read | 30 / 184 | 30 / 184 | 37 / 189 | 49/96 |
| One singleton repriced from one to two | 8 / 96 | 8 / 184 | 8 / 96 | 84/96 |

With grouping, fixed and greedy gained 19 certificates at the same total cost. The tree's seven additional certificates over those methods all came from capacity reservation (27 versus 20). With unequal prices, fixed matched greedy coverage at 88 fewer units.

The checkpoint policy made 24 false allows per 48 blocked snapshots and 24 false blocks per 48 allowed snapshots at every budget in the primary grid. The verifier rejected all 96 checkpoint claims at each budget, including the 48 correct guesses without sufficient evidence. Always-defer produced 96 verified deferrals at each of those budgets. In the four unchanged-after-interruption controls, fixed and greedy each certified 0/4 at budget zero and 1/4 at budget two. All four trusted-current controls certified at zero cost with both main policies. The outside-budget reference certified 96/96 by reading every singleton once, spending 528 units.

### Exploratory trace inspection

At the primary budget in capacity reservation, both methods read service status first, then capacity when the first read left the decision open. In quorum release, both used vote A and the threshold in all 48 snapshots, in opposite orders. This observation describes how the tie arose in this run. It was inspected after the registered outcome was computed.

## Audit and reproduction

The run used the [frozen commit](https://github.com/yousofab/resume-cert-bench/commit/09b77a40880045f6c8f85d0ddf90c241635d7a61) and the [execution implementation recorded before outcomes](https://github.com/yousofab/resume-cert-bench/commit/5f1d997d83b9691a21e494a9cc4cd78fb583d0ce). All 17 pinned files and the fixture digest matched before and after execution. The sweep completed 2,880 budget-grid cells, 96 controls, 576 cost-sensitivity cells, and 96 full-read references. The 192 primary policy runs are included in the grid. Every planned cell was retained. A separate arithmetic check replayed the 3,648 traces against the vote and capacity rules and checked the primary four-cell counts and per-case hindsight costs.

With Python 3.12, run from the repository root.

```bash
python scripts/verify_freeze.py
python -m unittest discover -s tests -v
python scripts/run_confirmatory.py --check results
```

The last command regenerates the summary and compares the complete uncompressed JSONL stream with the recorded trace. To record a new sweep, run `python scripts/run_confirmatory.py --record /path/to/new-directory`. Each trace includes the scoped context, ledger, submitted certificate, verifier verdict, and arithmetic truth. Failed record attempts retain a partial trace when available and an error report.

This is a descriptive result for a deterministic synthetic suite of 48 matched pairs with some repeated live states. The state space and tool prices are declared, the snapshots are stable during a run, and the policies are algorithms without an LLM. The estimate applies to these scenarios and assumptions. The study has no population inference or production workflow evaluation.
