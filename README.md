# re:Invent claim check
- Model: anthropic/claude-sonnet-4.5
- Answer: NOT REAL
- Trace: https://aws.smith.langchain.com/public/da5022b6-34c2-44ca-af59-e84c7832d22f/r/3a7f3b6c-c79b-43f9-ae3c-611dc87ef2b9?start_time=2026-10-19T09%3A38%3A27.356233Z
- Source check: the model found no official AWS page proving the claim ("There is no official AWS announcement page for this claim because it doesn't exist as a real launch"). It only pointed to the Amazon Braket page (aws.amazon.com/braket/) as AWS's actual quantum offering, and said a Hacker News third-party source is a strong sign the claim is speculative.

## Pipeline

`pipeline.py` is a LangGraph graph with three nodes: `lead`, `researcher`, `fact-checker`.
The fact-checker reads its rules from `docs/confirm_rule.md` on every run.

```
pip install -r requirements.txt
python pipeline.py        # runs all five candidates, writes results.md, one public LangSmith trace per candidate
python verify_results.py  # checks quotes, blanks, verdicts and trace links in results.md
```

Needs `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` and the `LANGSMITH_*` variables in `.env`.
