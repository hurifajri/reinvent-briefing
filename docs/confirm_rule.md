# Confirmed means

A claim is judged only against the AWS snapshot in `data/aws-official-source-snapshot-2025-11-03.json`.
It is **confirmed** only if every answer below is yes.

1. Is the source page on aws.amazon.com? (yes/no)
2. Is the quote copied exactly from that page? (yes/no)
3. Does the quote back up every fact in the claim, not most of it? (yes/no)
   "Preview" does not back "generally available". One Region does not back "all Regions".
   If the page proves only part of the claim, the answer is no.

## Reject on any "no"

If any answer is no, the claim is **rejected**, with one line that names the check that failed
(for example "Check 3 failed: ..."). If the fact-checker is unsure, the answer is no.
Checks are answered in order, and the first "no" decides the reason.

## Who does what

| Agent | Gets | Gives back |
|---|---|---|
| Lead | The claim | The final verdict, confirmed or rejected, with a one-line reason. Sends the claim to the researcher, then the claim, page and quote to the fact-checker. If the researcher says "no match", the lead rejects the claim itself and does not call the fact-checker. |
| Researcher | The claim | The matching snapshot page (`page_id`, `url`) and the passage copied word for word from the page's `body_text`, or "no match" |
| Fact-checker | The claim, the page and the quote | The three yes/no answers, in order, each with a short why |

"No match" means the snapshot has no page for this claim. A page about a related service does not count as a match.
The lead rejects with "Check 1 failed: ..." because there is no source page on aws.amazon.com to check.

## Worked examples

Quotes are copied from the snapshot without changing, adding or dropping any word.

### C-01: confirmed

Claim: "Amazon Bedrock AgentCore is now generally available for building and running AI agents at scale."

Page: AWS-SRC-019, https://aws.amazon.com/about-aws/whats-new/2025/10/amazon-bedrock-agentcore-available/

Quote: "Amazon Bedrock AgentCore is now generally available. AgentCore is an agentic platform to build, deploy and operate AI agents securely at scale, using any framework and any foundation model."

| Check | Answer |
|---|---|
| 1. Page on aws.amazon.com | Yes |
| 2. Quote copied exactly | Yes |
| 3. Quote backs every fact | Yes. It says generally available, and building, deploying and operating AI agents at scale. |

Verdict: confirmed.

### C-02: rejected

Claim: "AWS will launch a managed quantum computing Region in us-east-1 at re:Invent 2025."

Page: none. The researcher found no snapshot page about a quantum Region and answered "no match". The closest page, AWS-SRC-021 (Amazon Braket program sets), is about running quantum tasks in existing Regions, not a new quantum computing Region, so it is not a match.

Quote: none.

| Check | Answer |
|---|---|
| 1. Page on aws.amazon.com | No. There is no source page for this claim. |
| 2. Quote copied exactly | Not asked. Check 1 already failed. |
| 3. Quote backs every fact | Not asked. Check 1 already failed. |

Verdict: rejected. Reason: "Check 1 failed: the snapshot has no page for a quantum computing Region."

### C-03: rejected

Claim: "Amazon EC2 P6e-GB200 UltraServers accelerated by NVIDIA Grace Blackwell are now available in all commercial AWS Regions."

Page: AWS-SRC-017, https://aws.amazon.com/about-aws/whats-new/2025/07/amazon-ec2-p6e-gb200-ultraservers-available/

Quote: "P6e-GB200 UltraServers are available through EC2 Capacity Blocks for ML in the Dallas Local Zone (us-east-1-dfw-2a), an extension of the US East (N. Virginia) Region."

| Check | Answer |
|---|---|
| 1. Page on aws.amazon.com | Yes |
| 2. Quote copied exactly | Yes |
| 3. Quote backs every fact | No. The page names one Local Zone, not all commercial Regions. |

Verdict: rejected. Reason: "Check 3 failed: the page says one Dallas Local Zone, the claim says all commercial AWS Regions."

### C-05: rejected

Claim: "Amazon S3 Vectors is now GA and lets you store and query vector embeddings natively in S3 at massive scale."

Page: AWS-SRC-015, https://aws.amazon.com/s3/features/vectors/

Quote: "Amazon S3 Vectors is in preview and is available in five AWS Regions."

| Check | Answer |
|---|---|
| 1. Page on aws.amazon.com | Yes |
| 2. Quote copied exactly | Yes |
| 3. Quote backs every fact | No. The page says preview, the claim says GA. |

Verdict: rejected. Reason: "Check 3 failed: the page says S3 Vectors is in preview, the claim says it is generally available."
