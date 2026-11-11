# ResumeCert

**Budgeted evidence for resuming agent workflows after external state changes.**

[فارسی](README.fa.md) · [Research proposal PDF](docs/proposal/ResumeCert_Research_Proposal_FA.pdf) · [Editable proposal](docs/proposal/ResumeCert_Research_Proposal_FA.docx) · [Academic acknowledgments](docs/ACKNOWLEDGMENTS.md)

An agent can remember the correct next step after an interruption even when that step is no longer allowed. ResumeCert asks which current facts an agent should reread, within a fixed budget, to certify that step as permitted or blocked. If both outcomes remain possible, it defers.

For example, a document may have a recorded approval but a newer version than the one approved. The proposed benchmark holds the action, checkpoint, and resumption message constant across matched scenarios and changes the precondition that governs the action. An independent verifier checks the decision, supporting observations, and read cost.

## Research status

| Item | Status |
|---|---|
| [Formal proposal](docs/proposal/ResumeCert_Research_Proposal_FA.pdf) | Available |
| [Research protocol](docs/research/RESEARCH_PROTOCOL_FA.md) and [primary source matrix](docs/research/EVIDENCE_MATRIX_FA.md) | Available; the formal proposal governs the planned design if details differ |
| [Exhaustive eight-state witness](proof/witness.py) | Reproducible |
| Matched benchmark, independent simulator verifier, and confirmatory comparison | Planned |

The witness uses three Boolean fields and eight constructed states. With two unit-cost reads, the best fixed field set certifies **4 of 8** states; an adaptive tree that reads the active route and then the corresponding approval certifies **8 of 8**. This is an exact result for the constructed example. No LLM or real workflow was evaluated.

## Reproduce

Python 3.12 is used for repository verification. No API key, GPU, or package installation is needed.

```bash
python proof/witness.py
python scripts/verify_repository.py
```

The verifier checks the recorded witness output and the SHA-256 hashes of both proposal files and both institutional marks. The same check is configured in GitHub Actions.

## Next milestone

Implement the action contract, the budgeted read interface, and an independent verifier for the 24 matched pilot pairs. Freeze the observation rules, baselines, budgets, and held-out design before running the confirmatory comparison. See the [research status and roadmap](docs/research/STATUS.md).

To cite this repository, use [CITATION.cff](CITATION.cff). The institutional marks are displayed in the [acknowledgments](docs/ACKNOWLEDGMENTS.md) for educational context.
