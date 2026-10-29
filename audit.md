# Agent Audit: re:Invent Claim Check Runs

This audit evaluates the agent decisions across all five candidate claims by comparing the LangSmith execution traces against the ground-truth rules in `docs/confirm_rule.md` and the snapshot `data/aws_official_source_snapshot.json`.

---

## 1. Audit Summary & Root Cause Analysis

### Identified Agent Flaws
1. **Researcher Agent (`researcher`)**:
   - **Flaw**: The original researcher implementation gated candidate page retrieval on strict string equality: `shortlist = [p for p in PAGES if p["service_name"] == row["claimed_service"]]`.
   - **Impact on C-02**: In C-02, `claimed_service` is `"AWS (quantum Region)"`. Because no snapshot page has that exact service name, the shortlist was completely empty. The researcher returned `"no match"` without reading or evaluating any snapshot pages (such as AWS-SRC-021 Amazon Braket). While C-02's final verdict was `rejected` (matching the rule card), the verdict was reached by accident due to a brittle string mismatch rather than actual verification. On holdout claims with slight service mislabeling (e.g. `"EC2"` vs `"Amazon EC2"`), this would cause a false `"no match"` rejection.
   - **General Fix**: Replaced the exact string filter with keyword-based candidate discovery (`get_candidate_pages`) that extracts terms from both `claimed_service` and `claim_text`, falling back to full catalog evaluation. Candidate announcements are presented to the model, allowing semantic inspection. For C-02, the model now actively inspects candidate quantum pages (e.g. AWS-SRC-021 Braket program sets) and genuinely concludes that none announces a managed quantum Region.

2. **Fact-Checker Agent (`fact_checker`)**:
   - **Refinement**: Explicitly instructed the model on Check 3 to scrutinize scope and status qualifiers (`all`, `now`, `generally available` vs `preview`), while clarifying that extra details in the quote do not invalidate the claim.

---

## 2. Audit Table (Five Candidates)

| Candidate | Claim Text | Original Verdict | Assessment | Agent at Fault & Cause | Fix Applied | Verdict After Fix | Confirmed Quote in Snapshot | New LangSmith Trace Link |
|---|---|---|---|---|---|---|---|---|
| **C-01** | Amazon Bedrock AgentCore is now generally available for building and running AI agents at scale. | confirmed | Right | None. Researcher matched AWS-SRC-019; Fact-checker confirmed GA status. | Candidate search expanded to flexible keyword matching. | confirmed | `"Amazon Bedrock AgentCore is now generally available."` (Verified in AWS-SRC-019) | [Trace C-01](https://aws.smith.langchain.com/public/380caf68-947c-46fe-af91-ca5f66908efe/r) |
| **C-02** | AWS will launch a managed quantum computing Region in us-east-1 at re:Invent 2025. | rejected | Right (Flawed Agent) | **Researcher**. The initial researcher used `p["service_name"] == row["claimed_service"]`, dropping all pages before reading anything. It arrived at the correct verdict only by coincidence. | Researcher now inspects candidate quantum pages semantically; correctly returns empty match after reading. | rejected | `no match` | [Trace C-02](https://aws.smith.langchain.com/public/d4956d1d-9f59-4bba-b67f-691181870bc6/r) |
| **C-03** | Amazon EC2 P6e-GB200 UltraServers accelerated by NVIDIA Grace Blackwell are now available in all commercial AWS Regions. | rejected | Right | None. Researcher matched AWS-SRC-017; Fact-checker caught scope mismatch on "all commercial AWS Regions" (page specifies Dallas Local Zone). | Researcher widened to general candidate matching; Fact-checker verifies regional scope. | rejected | N/A (Rejected on Check 3) | [Trace C-03](https://aws.smith.langchain.com/public/6b15a953-3d66-4fea-9a50-b85911171990/r) |
| **C-04** | Amazon Aurora DSQL is now generally available as a serverless distributed SQL database with active-active high availability. | confirmed | Right | None. Researcher correctly resolved multiple pages (preview AWS-SRC-002 vs GA AWS-SRC-016) via date tie-break; Fact-checker confirmed all facts. | Researcher tie-break preserved; token consumption optimized for reliable execution. | confirmed | `"Amazon Aurora DSQL is now generally available. Aurora DSQL is a serverless, distributed SQL database with virtually unlimited scale, active-active high availability, PostgreSQL compatibility and no infrastructure to manage."` (Verified in AWS-SRC-016) | [Trace C-04](https://aws.smith.langchain.com/public/2e4dc933-111d-4201-ab2d-f5753661d9d8/r) |
| **C-05** | Amazon S3 Vectors is now GA and lets you store and query vector embeddings natively in S3 at massive scale. | rejected | Right | None. Researcher matched AWS-SRC-014; Fact-checker rejected because quote specifies "preview", which does not back "GA". | Fact-checker prompt reinforces status verification (`preview` vs `GA`). | rejected | N/A (Rejected on Check 3) | [Trace C-05](https://aws.smith.langchain.com/public/7ddf2428-d04e-4f8e-926c-a6c5777fdc73/r) |

---

## 3. Quote Verification

Every confirmed quote in `results.md` and this audit was checked against `data/aws_official_source_snapshot.json`:
- **C-01 (`AWS-SRC-019`)**: `"Amazon Bedrock AgentCore is now generally available."` is an exact word-for-word substring of `body_text`.
- **C-04 (`AWS-SRC-016`)**: `"Amazon Aurora DSQL is now generally available. Aurora DSQL is a serverless, distributed SQL database with virtually unlimited scale, active-active high availability, PostgreSQL compatibility and no infrastructure to manage."` is an exact word-for-word substring of `body_text`.
- **C-02**: No snapshot page exists; correctly marked `no match` (no empty cells).
