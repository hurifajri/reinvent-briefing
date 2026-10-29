# Agent Audit: re:Invent Claim Check Runs

This audit evaluates the agent decisions across all five candidate claims by comparing the LangSmith execution traces against the ground-truth rules in `docs/confirm_rule.md` and the official snapshot `data/aws_official_source_snapshot.json`.

---

## 1. Audit Summary & Root Cause Analysis

### Identified Agent Flaws & Root Causes

1. **Shortlist Candidate Search (Generalization & Zero Hardcoding)**:
   - **Flaw**: The original candidate retrieval used strict equality (`p["service_name"] == row["claimed_service"]`), dropping all pages when services were slightly mislabeled. A prior attempt introduced an ad-hoc branch checking specifically for `"quantum"` and `"braket"`. On an unseen holdout claim with a mislabeled service, any word overlap with irrelevant pages would still drop the real page because claim text never fed candidate scoring.
   - **General Fix**: Removed all hardcoded service branches. Implemented `get_candidate_pages()` which extracts non-stopword tokens from *both* `claimed_service` and `claim_text` and scores all 40 snapshot pages weighted across title, service name, and body text. The top-scoring pages form the shortlist for researcher evaluation.

2. **Quote Span Selection (`researcher`)**:
   - **Flaw in C-01**: In the initial run, the researcher was instructed to pick the "shortest run" of sentences. It returned only sentence 0 (`"Amazon Bedrock AgentCore is now generally available."`), omitting sentence 1 (`"AgentCore is an agentic platform to build, deploy and operate AI agents securely at scale..."`). That single sentence did not prove the claim's assertion `"for building and running AI agents at scale"`. The researcher under-selected the span, and the fact-checker incorrectly waved it through.
   - **Flaw in C-03**: In C-03, the claim asserts availability `"in all commercial AWS Regions"`. The researcher selected only sentence 0, omitting sentence 2 (`"P6e-GB200 UltraServers are available today in the Dallas Local Zone."`). The initial audit erroneously credited the fact-checker with detecting the Dallas Local Zone when the quote never contained that sentence.
   - **General Fix**: Updated the researcher span prompt to pick the span of consecutive sentences addressing all asserted dimensions—product identity, status, features, and where/which regions or zones the service is available.

3. **Fact-Checking Verification (`fact_checker`)**:
   - **Flaw**: Check 3 failed to catch under-supported claims (waving through C-01 despite missing purpose/scale evidence).
   - **General Fix**: Prompted Check 3 to verify that *every* fact asserted in the claim (purpose, status, regional scope) is supported by the quote alone, while noting that extra details in the quote do not invalidate the claim.

---

## 2. Audit Table (Five Candidates)

| Candidate | Claim Text | Original Verdict | Assessment | Agent at Fault & Cause | Fix Applied | Verdict After Fix | Confirmed Quote in Snapshot | New LangSmith Trace Link |
|---|---|---|---|---|---|---|---|---|
| **C-01** | Amazon Bedrock AgentCore is now generally available for building and running AI agents at scale. | confirmed | Wrong (Under-supported quote waved through) | **Researcher & Fact-checker**. Researcher picked only sentence 0 (`now generally available`), omitting sentence 1 (`to build, deploy and operate AI agents securely at scale`). Fact-checker waved it through on Check 3 even though the quote alone did not prove "building and running AI agents at scale". | Researcher prompt instructed to cover purpose and status; Fact-checker prompt checks all asserted facts. | confirmed | `"Amazon Bedrock AgentCore is now generally available. AgentCore is an agentic platform to build, deploy and operate AI agents securely at scale, using any framework and any foundation model. AgentCore adds support for VPC, AWS PrivateLink and AWS CloudFormation. Amazon Bedrock AgentCore is available in nine AWS Regions: US East (N. Virginia), US East (Ohio), US West (Oregon), Asia Pacific (Mumbai), Asia Pacific (Singapore), Asia Pacific (Sydney), Asia Pacific (Tokyo), Europe (Frankfurt) and Europe (Ireland)."` (Verified in AWS-SRC-019) | [Trace C-01](https://smith.langchain.com/public/d52f93c4-1b03-4402-9196-cd153ea59d59/r) |
| **C-02** | AWS will launch a managed quantum computing Region in us-east-1 at re:Invent 2025. | rejected | Right (Flawed Agent) | **Researcher**. Original code used exact service name matching `p["service_name"] == row["claimed_service"]`, dropping all pages before reading anything. It arrived at `no match` by string-mismatch accident. | Generalized `get_candidate_pages` to score tokens from `claimed_service` and `claim_text` equally without special-casing. Researcher reads candidates and genuinely concludes no official announcement exists (`no match`). | rejected | `no match` | [Trace C-02](https://smith.langchain.com/public/427070e0-ebae-44f2-83d4-64f5af6be7e9/r) |
| **C-03** | Amazon EC2 P6e-GB200 UltraServers accelerated by NVIDIA Grace Blackwell are now available in all commercial AWS Regions. | rejected | Right (Flawed Audit & Under-selected Quote) | **Researcher**. Researcher previously selected only sentence 0, omitting sentence 2 (`Dallas Local Zone`). The audit credited the fact-checker with catching Dallas Local Zone when the quote never contained that sentence. | Prompt researcher to include availability/regional sentences. Span now includes sentence 2. Fact-checker accurately rejects Check 3 because Dallas Local Zone does not support "all commercial AWS Regions". | rejected | N/A (Rejected on Check 3) | [Trace C-03](https://smith.langchain.com/public/63b654c9-19db-4d0b-8320-1042b042bc7d/r) |
| **C-04** | Amazon Aurora DSQL is now generally available as a serverless distributed SQL database with active-active high availability. | confirmed | Right | None. Researcher correctly resolved preview vs GA pages via date tie-break; Fact-checker verified all facts. | Generalized candidate scoring and span prompt preserved; execution verified end-to-end. | confirmed | `"Amazon Aurora DSQL is now generally available. Aurora DSQL is a serverless, distributed SQL database with virtually unlimited scale, active-active high availability, PostgreSQL compatibility and no infrastructure to manage."` (Verified in AWS-SRC-016) | [Trace C-04](https://smith.langchain.com/public/3c3e4ea8-cf6f-4e56-a728-284b405d9681/r) |
| **C-05** | Amazon S3 Vectors is now GA and lets you store and query vector embeddings natively in S3 at massive scale. | rejected | Right | None. Researcher correctly identified AWS-SRC-014; Fact-checker rejected because quote specifies "preview", which does not back "GA". | Fact-checker prompt checks status qualifiers (`preview` vs `GA`). | rejected | N/A (Rejected on Check 3) | [Trace C-05](https://smith.langchain.com/public/bbfe550e-5368-45b6-b52b-2125765f25f0/r) |

---

## 3. Quote Verification

Every confirmed quote in `results.md` and this audit was verified against `data/aws_official_source_snapshot.json`:
- **C-01 (`AWS-SRC-019`)**: The confirmed quote is an exact word-for-word substring of `AWS-SRC-019` body text, backing both GA status and scale/purpose.
- **C-04 (`AWS-SRC-016`)**: The confirmed quote is an exact word-for-word substring of `AWS-SRC-016` body text, backing GA, serverless distributed SQL, and active-active high availability.
- **C-02**: No announcement page exists in the snapshot; correctly marked `no match`.
