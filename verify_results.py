"""Checks results.md against the snapshot and the requirements. Exits non-zero on any failure."""
import json, re, sys

snap = {p["page_id"]: p for p in json.load(open("data/aws_official_source_snapshot.json", encoding="utf-8"))}
lines = [l for l in open("results.md", encoding="utf-8").read().splitlines() if l.startswith("| C-")]
rows = [[c.strip() for c in re.split(r"(?<!\\)\|", l)[1:-1]] for l in lines]
errors = []

if len(rows) != 5:
    errors.append(f"expected 5 rows, got {len(rows)}")
traces = [re.search(r"\((https?://[^)]+)\)", r[9]) for r in rows]
if not all(traces) or len({t.group(1) for t in traces if t}) != len(rows):
    errors.append("every row needs its own distinct trace link")
for r in rows:
    cid, claim, page, quote, c1, c2, c3, verdict, reason, _ = r
    if any(not c for c in r):
        errors.append(f"{cid}: blank cell")
    if page == "no match":
        if quote != "no match":
            errors.append(f"{cid}: no page but quote is not 'no match'")
    else:
        if page not in snap or quote.replace("\\|", "|") not in snap[page]["body_text"]:
            errors.append(f"{cid}: quote not word for word in {page}")
    expected = "confirmed" if (c1, c2, c3) == ("yes",) * 3 else "rejected"
    if verdict != expected:
        errors.append(f"{cid}: verdict {verdict} does not match checks {(c1, c2, c3)}")
    if verdict == "rejected" and not re.match(r"Check [123] failed: ", reason):
        errors.append(f"{cid}: reason does not name the failed check")

print("\n".join(errors) if errors else f"ok: {len(rows)} rows, quotes exact, no blanks, {len(rows)} distinct traces")

# Verify audit.md
audit_lines = [l for l in open("audit.md", encoding="utf-8").read().splitlines() if re.match(r"^\|\s*\*\*C-", l)]
if len(audit_lines) != 5:
    errors.append(f"audit.md: expected 5 candidate rows, got {len(audit_lines)}")
audit_traces = re.findall(r"\((https://aws\.smith\.langchain\.com/public/[^)]+)\)", open("audit.md").read())
if len(audit_traces) != 5 or len(set(audit_traces)) != 5:
    errors.append(f"audit.md: expected 5 distinct trace links, got {len(set(audit_traces))}")

if errors:
    print("ERRORS:", errors)
    sys.exit(1)
print("ok: audit.md has 5 rows and 5 distinct trace links")
sys.exit(0)
