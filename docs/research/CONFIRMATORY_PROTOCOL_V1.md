# ResumeCert · Confirmatory protocol v1

[فارسی](CONFIRMATORY_PROTOCOL_V1_FA.md) · [Machine-readable plan](../../study/confirmatory-v1/plan.json) · [Freeze manifest](../../study/confirmatory-v1/manifest.json) · [Development pilot](MATCHED_PILOT_V1.md)

**Status:** prospective specification after the development pilot. Fixture construction and integrity checks are complete. No policy has been evaluated on these held-out snapshots. This is a public Git record of the protocol, with no claim of third-party registration or blind custody.

## 1. Primary question and decision rule

At a budget of two unit-cost single-field reads per snapshot, does the unchanged greedy adaptive policy certify more decisions than the optimized fixed-order policy? The primary metric is coverage of correct definitive certificates accepted by the independent verifier. A correct but unsupported guess, a verified defer, and a rejected claim each contribute zero coverage.

The fixed order is chosen per contract and budget before seeing an observation. It maximizes expected certification coverage under a uniform prior over admissible worlds, breaks coverage ties by lower expected cost, then uses catalog order. The greedy policy chooses an affordable read by expected impurity reduction per unit cost; its tie order is lower cost then catalog order. Both use the same public contract, context, catalog, budget, and snapshots. Their exact implementations are pinned in the manifest. Neither policy is fitted to held-out frequencies or allowed to access the case generator.

For each of 96 snapshots, compare the adaptive (A) and fixed (F) indicators. Report the four cells: both certify, A only, F only, neither. The pooled contrast is `(A only − F only) / 96`, with the same contrast separately in each family over 48 snapshots. Report accepted allows and blocks separately, total spent units, and pairs with both arms certified.

| Observed contrast | Prespecified interpretation |
| --- | --- |
| Positive in both families | Consistent support on this synthetic suite |
| Positive pooled and nonpositive in at least one family | Mixed; report the family differences |
| Zero or negative pooled | No pooled gain; report the signed difference and family results |

The pilot informed the choice of budget two and comparator. The confirmatory estimand is descriptive for this fixed synthetic suite. No significance test, population confidence interval, or generalization from two families is planned.

## 2. Frozen split and pairing

| Split | Families | Use |
| --- | --- | --- |
| Development | Report release, dispatch, and registration | Prior method development; excluded from the primary denominator |
| Held-out 1 | Quorum: sum of four binary votes meets a current threshold in {1, 2, 3, 4} | 24 pairs |
| Held-out 2 | Capacity: service is open and capacity minus committed units meets request size | 24 pairs |

For capacity, admissibility requires committed units not to exceed capacity. Both contracts include an annotation revision irrelevant to the precondition. Each pair shares the prior state, action, checkpoint text, interruption boundary, resumption message, empty initial current facts, catalog, and budget. One arm changes a single precondition-relevant field by one and flips the decision; the other changes only the annotation revision by one and preserves it. Each family includes six pairs in each prior-permission × low/high surface-text cell. Starting structural states and unordered pivotal edges are unique within each family. The deterministic selector balances pivotal fields subject to the eligibility rules. Some live states can recur; the cases are not independent random draws. Surface text is a matching factor; no language model or causal text effect is tested.

The generator derives candidate ranks and opaque scope IDs from a seed tied to the public development commit. No policy uses IDs as observations. Contracts and the generator are public and can reconstruct the cases; this prevents post-outcome edits from passing the commitment check but does not hide the cases from an outside reader. The design audit compares each contract and its admissibility rules with a separate arithmetic specification across all admissible states and checks all pairs and controls. The manifest records the deterministic case digest before any policy runs.

## 3. Secondary outcomes and controls

- Run the pinned checkpoint, always-defer, seeded random, optimized fixed, greedy adaptive and exact prior tree policies at budgets 0–4. Only the primary contrast above determines the primary conclusion. The exact tree is optimal for the declared prior, which need not match the empirical case frequencies.
- Run eight separate controls: one previously permitted and one previously blocked case per family in each of the unchanged-after-interruption and uninterrupted-with-trusted-current-facts conditions. Use budgets 0 and 2. Compare them separately from the primary pairs.
- At budget two, compare fixed, greedy and exact tree with (i) one additional unit-cost two-field read or (ii) an isolated increase in one singleton price from one to two. The grouped read covers votes A and B or capacity and committed units; the repriced field is quorum threshold or capacity. Keep the same cases and inference rule. No free change timestamp is exposed to any method.
- Read all singleton fields once on each primary snapshot as an outside-budget reference. Record the oracle truth and verifier outcome separately, along with all queries and their costs.
- Report raw false allows per actually blocked case and raw false blocks per actually allowed case, plus both counts per all snapshots. Count rejected certificates per submitted claim and verified deferrals of allowed actions per actually allowed case. A read is non-informative for the state space if it leaves the compatible-world count unchanged. Report read cost as total and units per snapshot; empty denominators are undefined.
- Report the per-case hindsight floor (cheapest certifying subset selected with the case known) and the count for which the floor exceeds the budget. A missed case with an affordable hindsight proof is not automatically a policy error. Separate it from an invalid transcript, wrong raw guess, or verified defer.
- Publish results by family, arm and surface at the primary budget, paired discordance, and complete verified run traces. Secondary results are descriptive and cannot change the prespecified primary interpretation.

## 4. Run gates and amendments

Verify the frozen commit and the manifest before running. Hold the source and case digests constant throughout one complete sweep. Expected run cells: 2,880 primary-regime cells (including the 192 primary contrast records), 96 controls, 576 sensitivity cells, and 96 full-read references. A fixture mismatch, engine failure, missing or duplicate cell, or invalid audit for a read-based method stops the sweep; preserve the failed attempt with a reason. No case can be excluded for its outcome. Use the pinned primary reducer for the final table. Report any repair, affected files, and whether outcomes were seen. Commit amendments before viewing affected results; analyses changed after viewing are exploratory. Do not overwrite this freeze silently.

```bash
python scripts/verify_freeze.py
python -m unittest discover -s tests -v
```

The pre-analysis separation follows Nosek et al., *The preregistration revolution*, PNAS (2018), [doi:10.1073/pnas.1708274114](https://doi.org/10.1073/pnas.1708274114). The protocol uses a public versioned commitment and a separate outcome phase. It does not apply a reusable holdout mechanism or make inferential claims about production environments.
