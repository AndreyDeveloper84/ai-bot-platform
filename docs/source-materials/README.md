# Source materials

This directory is intentionally a pointer, not a copy. The canonical source docs live in `mysite/docs/arch/` because the formula_tela repo is the historical home of all design context. Copying them here would create drift and a maintenance burden for zero benefit.

## What's in `mysite/docs/arch/`

```
mysite/docs/arch/
├── 01_system_architecture.pdf
├── 02_tool_contracts.pdf
├── 03_data_model.pdf
├── 04_safety_policy.pdf
├── 05_mvp_scope.pdf
├── PHASE0_DESIGN.md                ← canonical full design (v2, 9.7K words)
├── compass_artifact_wf-…_text_markdown.md   ← skill catalog (26 skills)
├── deep-research-report.md
├── deep-research-report (1).md
└── deep-research-report (2).md
```

| File | What it is | When to read |
|---|---|---|
| `PHASE0_DESIGN.md` | The canonical design doc for the 22-week Phase 0 plan. 13 sections + 6 ADRs + 14 risks. | Before any architecture decision. Use [`docs/architecture.md`](../architecture.md) for the daily condensed view. |
| `01_system_architecture.pdf` | Component diagrams + service boundaries from initial design phase. | When you need a picture, not prose. |
| `02_tool_contracts.pdf` | Tool layer contracts (YClients, Catalog, KB, Reminders…) | Implementing a new tool in `apps/tools/`. |
| `03_data_model.pdf` | Original data model sketches before they were normalised in `PHASE0_DESIGN.md §3`. | Historical record. The text version in `PHASE0_DESIGN.md §3` supersedes. |
| `04_safety_policy.pdf` | Safety policies — rate limits, content filters, prompt-injection defences. | Before working in `apps/orchestrator/safety/`. |
| `05_mvp_scope.pdf` | What's in / out of MVP scope. | When negotiating Phase 1 features. |
| `compass_artifact_…markdown.md` | 26-skill compass catalogue (22 functional + 4 infrastructure). | Designing a new skill in `apps/skills/`. |
| `deep-research-report.md` (×3) | Deep-research notes that fed PHASE0_DESIGN.md. | Verifying a citation or chasing a "why this way?" question. |

## How to access

The mysite repo lives at `github.com/AndreyDeveloper84/formula_tela`. PDFs are checked in; clone the repo or open files directly on GitHub.

Local dev:

```powershell
git clone git@github.com:AndreyDeveloper84/formula_tela.git
# Source materials at: formula_tela/mysite/docs/arch/
```

## When source materials change

If a substantive design update happens (new ADR, scope shift, deferred work moving back in), update both:

1. The canonical source in `mysite/docs/arch/PHASE0_DESIGN.md` (or open a new ADR).
2. The condensed `ai-bot-platform/docs/architecture.md` to keep the daily reference accurate.

Don't fork PDFs into this repo. They're large binary blobs with no diff readability — kept once, in `mysite/`.
