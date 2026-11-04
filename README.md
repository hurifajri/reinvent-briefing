# AWS re:Invent 2025 Morning Briefing

Live Briefing Page: **[https://hurifajri.github.io/reinvent-briefing/briefing.html](https://hurifajri.github.io/reinvent-briefing/briefing.html)** (also accessible at [https://hurifajri.github.io/reinvent-briefing/](https://hurifajri.github.io/reinvent-briefing/))

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

- **[briefing.html](file:///Users/hurifajri/Developer/Projects/Personal/portfolio/reinvent-briefing/briefing.html)**: The live, standalone morning briefing page hosted on GitHub Pages displaying the 5 candidate claims with Confirmed/Rejected badges, evidence quotes, reasons, and LangSmith traces.
- **[spotcheck.md](file:///Users/hurifajri/Developer/Projects/Personal/portfolio/reinvent-briefing/spotcheck.md)**: Human-audited fact-by-fact spot check verifying each Confirmed item against the refreshed AWS snapshot (`data/aws_official_source_snapshot_refresh.json`), citing the exact words backing each asserted fact.
- **[final_run_output.txt](file:///Users/hurifajri/Developer/Projects/Personal/portfolio/reinvent-briefing/final_run_output.txt)**: Terminal log demonstrating the clean holdout run, showing that `tests/test_safety.py` passed before `briefing.html` was built.
- **[checks.md](file:///Users/hurifajri/Developer/Projects/Personal/portfolio/reinvent-briefing/checks.md)**: Proof of safety gate functionality, showing both a passing run and a stopped run triggered by an intentional bad item.
- **[tests/test_safety.py](file:///Users/hurifajri/Developer/Projects/Personal/portfolio/reinvent-briefing/tests/test_safety.py)**: Automated pytest checks guarding the split between confirmed and rejected items.

---

## Pipeline Execution

```bash
# Setup
pip install -r requirements.txt

# Run the pipeline (evaluates holdout batch, runs safety gate, builds briefing.html)
python pipeline.py

# Run safety checks independently
pytest tests/test_safety.py -v
```

Environment variables needed in `.env`:
- `OPENROUTER_API_KEY`
- `OPENROUTER_MODEL`
- `LANGSMITH_API_KEY`
- `LANGSMITH_TRACING=true`
- `LANGSMITH_PROJECT=reinvent-briefing-audit`
- `LANGSMITH_ENDPOINT=https://aws.api.smith.langchain.com`
