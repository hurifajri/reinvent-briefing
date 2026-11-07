# Safety Gate Checks

This file records the execution outputs of the pipeline safety gate, demonstrating one passing run and one stopped run halted by a planted bad item.

---

## 1. Passing Run

The baseline pipeline run evaluated all five candidates, verified source requirements, passed both pytest safety checks, built results.md, and printed the confirmed and rejected lists.

```
== C-01 ==
  lead
  researcher
  fact-checker
  lead (final verdict)
  -> confirmed: All three checks are yes.
== C-02 ==
  lead
  researcher
  lead (final verdict)
  -> rejected: Check 1 failed: the snapshot has no page for this claim.
== C-03 ==
  lead
  researcher
  fact-checker
  lead (final verdict)
  -> rejected: Check 3 failed: The quote says availability only in the Dallas Local Zone, not all commercial AWS Regions.
== C-04 ==
  lead
  researcher
  fact-checker
  lead (final verdict)
  -> confirmed: All three checks are yes.
== C-05 ==
  lead
  researcher
  fact-checker
  lead (final verdict)
  -> rejected: Check 3 failed: The quote says 'preview', not GA, so the claim's status is not backed.

Running safety gate (tests/test_safety.py)...
============================= test session starts ==============================
platform darwin -- Python 3.9.6, pytest-8.4.2, pluggy-1.6.0 -- /Users/hurifajri/Developer/Projects/Personal/portfolio/reinvent-briefing/.venv/bin/python
cachedir: .pytest_cache
rootdir: /Users/hurifajri/Developer/Projects/Personal/portfolio/reinvent-briefing
plugins: anyio-4.12.1, langsmith-0.4.37
collecting ... collected 2 items

tests/test_safety.py::test_confirmed_items_have_aws_url_and_quote PASSED [ 50%]
tests/test_safety.py::test_rejected_items_have_reason PASSED             [100%]

============================== 2 passed in 0.01s ===============================

[SAFETY GATE PASSED] All safety checks passed successfully.
wrote results.md

================== CONFIRMED LIST ==================
[C-01] Amazon Bedrock AgentCore is now generally available for building and running AI agents at scale.
  Source URL: https://aws.amazon.com/about-aws/whats-new/2025/10/amazon-bedrock-agentcore-available/
  Quote:      Amazon Bedrock AgentCore is now generally available. AgentCore is an agentic platform to build, deploy and operate AI agents securely at scale, using any framework and any foundation model. AgentCore adds support for VPC, AWS PrivateLink and AWS CloudFormation. Amazon Bedrock AgentCore is available in nine AWS Regions: US East (N. Virginia), US East (Ohio), US West (Oregon), Asia Pacific (Mumbai), Asia Pacific (Singapore), Asia Pacific (Sydney), Asia Pacific (Tokyo), Europe (Frankfurt) and Europe (Ireland).
  Trace Link: https://aws.smith.langchain.com/public/49ef9509-6372-4676-bc77-a360306446f3/r

[C-04] Amazon Aurora DSQL is now generally available as a serverless distributed SQL database with active-active high availability.
  Source URL: https://aws.amazon.com/about-aws/whats-new/2025/05/amazon-aurora-dsql-generally-available/
  Quote:      Amazon Aurora DSQL is now generally available. Aurora DSQL is a serverless, distributed SQL database with virtually unlimited scale, active-active high availability, PostgreSQL compatibility and no infrastructure to manage.
  Trace Link: https://aws.smith.langchain.com/public/cb2997af-1284-498a-a9f4-a863d538fcc8/r

================== REJECTED LIST ==================
[C-02] AWS will launch a managed quantum computing Region in us-east-1 at re:Invent 2025.
  Source URL: https://news.ycombinator.com/
  Reason:     Check 1 failed: the snapshot has no page for this claim.
  Trace Link: https://aws.smith.langchain.com/public/3cfecdff-204c-4743-8f77-c7ac1f1d93cb/r

[C-03] Amazon EC2 P6e-GB200 UltraServers accelerated by NVIDIA Grace Blackwell are now available in all commercial AWS Regions.
  Source URL: https://aws.amazon.com/about-aws/whats-new/2025/07/amazon-ec2-p6e-gb200-ultraservers-available/
  Reason:     Check 3 failed: The quote says availability only in the Dallas Local Zone, not all commercial AWS Regions.
  Trace Link: https://aws.smith.langchain.com/public/83cf6175-30d6-4c0e-830b-7964d2370174/r

[C-05] Amazon S3 Vectors is now GA and lets you store and query vector embeddings natively in S3 at massive scale.
  Source URL: https://aws.amazon.com/about-aws/whats-new/2025/07/amazon-s3-vectors-preview-native-support-storing-querying-vectors/
  Reason:     Check 3 failed: The quote says 'preview', not GA, so the claim's status is not backed.
  Trace Link: https://aws.smith.langchain.com/public/89329ad7-b9e7-4ab3-8e80-c508709b8c58/r
```

---

## 2. Stopped Run (Planted Bad Item)

A non-AWS blog URL was planted into a confirmed item on purpose, causing test_confirmed_items_have_aws_url_and_quote to fail and halting pipeline.py before building any results.

```
== C-01 ==
  lead
  researcher
  fact-checker
  lead (final verdict)
  -> confirmed: All three checks are yes.

Running safety gate (tests/test_safety.py)...
============================= test session starts ==============================
platform darwin -- Python 3.9.6, pytest-8.4.2, pluggy-1.6.0 -- /Users/hurifajri/Developer/Projects/Personal/portfolio/reinvent-briefing/.venv/bin/python
cachedir: .pytest_cache
rootdir: /Users/hurifajri/Developer/Projects/Personal/portfolio/reinvent-briefing
plugins: anyio-4.12.1, langsmith-0.4.37
collecting ... collected 2 items

tests/test_safety.py::test_confirmed_items_have_aws_url_and_quote FAILED [ 50%]
tests/test_safety.py::test_rejected_items_have_reason PASSED             [100%]

=================================== FAILURES ===================================
_________________ test_confirmed_items_have_aws_url_and_quote __________________

    def test_confirmed_items_have_aws_url_and_quote():
        data = load_split_results()
        confirmed = data.get("confirmed", [])
        for item in confirmed:
            cid = item.get("candidate_id", "unknown")
            url = item.get("source_url") or item.get("url") or ""
            host = urlparse(url).hostname or ""
>           assert host == "aws.amazon.com" or host.endswith(".aws.amazon.com"), (
                f"Confirmed item {cid} lacks an aws.amazon.com URL: got '{url}'"
            )
E           AssertionError: Confirmed item C-01 lacks an aws.amazon.com URL: got 'https://techblog.example.com/2025/10/amazon-bedrock-agentcore'
E           assert ('techblog.example.com' == 'aws.amazon.com'
E             
E             - aws.amazon.com
E             + techblog.example.com or False)
E            +  where False = <built-in method endswith of str object at 0x109731b70>('.aws.amazon.com')
E            +    where <built-in method endswith of str object at 0x109731b70> = 'techblog.example.com'.endswith

tests/test_safety.py:23: AssertionError
=========================== short test summary info ============================
FAILED tests/test_safety.py::test_confirmed_items_have_aws_url_and_quote - AssertionError: Confirmed item C-01 lacks an aws.amazon.com URL: got 'https://techblog.example.com/2025/10/amazon-bedrock-agentcore'
========================= 1 failed, 1 passed in 0.04s ==========================


[SAFETY GATE HALTED] tests/test_safety.py check failed!
Pipeline stopped before building results.
```
