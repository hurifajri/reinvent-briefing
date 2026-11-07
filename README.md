# AWS re:Invent 2025 Morning Briefing

Live Briefing Page: **[https://hurifajri.github.io/reinvent-briefing/](https://hurifajri.github.io/reinvent-briefing/)**

---

## Overview

This repository powers an automated, verified morning briefing for AWS Solutions Architects. Before architects share announcements with customers, every candidate claim must prove itself against official AWS documentation.

To prevent rumors and blog posts from ever reaching customers, the pipeline enforces strict safety gates and verification checks before publishing:
1. **Source Verification:** Every confirmed claim must originate strictly from an official `aws.amazon.com` domain.
2. **Verbatim Evidence:** Every confirmed claim must be supported word-for-word by an exact quote from the official AWS documentation snapshot.
3. **Automated Safety Gate (`pytest`):** Automatic tests in `tests/test_safety.py` validate the partition integrity before the briefing page is built. If any check fails, the pipeline halts immediately without publishing.
4. **Public Traces:** Every evaluation run retains a public, permanent LangSmith trace link that can be audited by anyone without authentication.

---

## Key Repository Artifacts

- **[index.html](index.html)**: The live, standalone morning briefing page hosted on GitHub Pages displaying the 5 candidate claims with Confirmed/Rejected badges, evidence quotes, reasons, and LangSmith traces.
- **[docs/spotcheck.md](docs/spotcheck.md)**: Human-audited fact-by-fact spot check verifying each Confirmed item against the official AWS snapshot (`data/aws_official_source_snapshot.json`), citing the exact words backing each asserted fact.
- **[docs/final_run_output.txt](docs/final_run_output.txt)**: Terminal log demonstrating the clean run, showing that `tests/test_safety.py` passed before the briefing page was built.
- **[docs/checks.md](docs/checks.md)**: Proof of safety gate functionality, showing both a passing run and a stopped run triggered by an intentional bad item.
- **[docs/results.md](docs/results.md)**: Markdown summary table of evaluated candidate claims, check results, verdicts, reasons, and public LangSmith traces.
- **[docs/audit.md](docs/audit.md)**: Initial pipeline trace audit against `docs/confirm_rule.md`.
- **[tests/test_safety.py](tests/test_safety.py)**: Automated pytest checks guarding the split between confirmed and rejected items.

---

## Pipeline Execution

```bash
# Setup
pip install -r requirements.txt

# Run the pipeline (evaluates holdout batch, runs safety gate, builds index.html)
python pipeline.py

# Run safety checks independently
pytest tests/test_safety.py -v
```

Environment variables needed in `.env` (see [`.env.example`](.env.example)):
- `OPENROUTER_BASE_URL`
- `OPENROUTER_API_KEY`
- `OPENROUTER_MODEL`
- `LANGSMITH_API_KEY`
- `LANGSMITH_TRACING=true`
- `LANGSMITH_PROJECT`
- `LANGSMITH_ENDPOINT`
