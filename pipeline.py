"""Three-node LangGraph pipeline: lead -> researcher -> fact-checker -> lead.

Rules live in docs/confirm_rule.md. The fact-checker reads that file on every run.
"""
import csv, json, os, re, subprocess, sys, time
from typing import Optional, TypedDict
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

from langgraph.graph import END, StateGraph
from langsmith import get_current_run_tree, traceable
from langsmith.wrappers import wrap_openai
from openai import APIStatusError, OpenAI

RULE_FILE = "docs/confirm_rule.md"
SNAPSHOT = os.environ.get("SNAPSHOT_FILE", "data/aws_official_source_snapshot_refresh.json" if os.path.exists("data/aws_official_source_snapshot_refresh.json") else "data/aws_official_source_snapshot.json")
CANDIDATES = os.environ.get("CANDIDATES_FILE", "data/aws_reinvent_candidate_items_holdout.csv" if os.path.exists("data/aws_reinvent_candidate_items_holdout.csv") else "data/aws_reinvent_candidate_items.csv")
RESULTS = "results.md"
BRIEFING_HTML = "briefing.html"
SPLIT_FILE = "data/split_results.json"

base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
api_key = os.environ.get("OPENROUTER_API_KEY")
headers = {"user-agent": "openai-python/1.0.0"} if "brisnets.com" in base_url else None
client = wrap_openai(OpenAI(base_url=base_url, api_key=api_key, default_headers=headers))
PAGES = json.load(open(SNAPSHOT, encoding="utf-8"))
BY_ID = {p["page_id"]: p for p in PAGES}
RUNS = {}  # candidate_id -> LangSmith run tree, used to share each trace


class State(TypedDict, total=False):
    row: dict
    stage: str  # new -> assigned -> researched -> checked
    page: Optional[dict]
    quote: str
    answers: list  # check 1, 2, 3: "yes", "no" or "not asked"
    whys: list
    verdict: str
    reason: str


def ask(system: str, user: str) -> str:
    for attempt in range(6):
        try:
            extra = {"thinking": {"type": "disabled"}} if "deepseek" in os.environ.get("OPENROUTER_MODEL", "").lower() else {}
            resp = client.chat.completions.create(
                model=os.environ["OPENROUTER_MODEL"],
                temperature=0,
                max_tokens=300,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                extra_body=extra if extra else None,
            )
            choice = resp.choices[0] if resp.choices else None
            if choice and choice.message and choice.message.content:
                return choice.message.content
            return ""
        except APIStatusError as e:
            msg = e.body.get("error", {}).get("message") if isinstance(e.body, dict) else str(e)
            print(f"  API error {e.status_code}: {msg}")
            if attempt == 5:
                raise
            time.sleep(2)
        except Exception as e:
            print(f"  Request error: {e}")
            if attempt == 5:
                raise
            time.sleep(2)
    return ""


def ask_json(system: str, user: str) -> dict:
    match = re.search(r"\{.*\}", ask(system, user), re.S)
    try:
        return json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        return {}


def sentences(body: str) -> list:
    return re.split(r"(?<=\.) (?=[A-Z])", body)


def states_status(page: dict) -> bool:
    return bool(re.search(r"generally available|preview|now available", page["body_text"], re.I))


def tie_break(pages: list) -> dict:
    """Newest published_date, then a page that states a status, then lowest page_id."""
    newest = max(p["published_date"] for p in pages)
    pool = [p for p in pages if p["published_date"] == newest]
    return min(pool, key=lambda p: (not states_status(p), p["page_id"]))


def lead(state: State) -> dict:
    if state["stage"] == "new":
        print("  lead")
        return {"stage": "assigned"}
    print("  lead (final verdict)")
    page, answers, whys = state.get("page"), state.get("answers"), state.get("whys")
    if not page:
        return {"verdict": "rejected", "reason": "Check 1 failed: the snapshot has no page for this claim.",
                "answers": ["no", "not asked", "not asked"], "quote": "no match"}
    if all(a == "yes" for a in answers):
        return {"verdict": "confirmed", "reason": "All three checks are yes."}
    n = answers.index(next(a for a in answers if a != "yes")) + 1
    return {"verdict": "rejected", "reason": f"Check {n} failed: {whys[n - 1]}"}


def get_candidate_pages(row: dict) -> list:
    """Find candidate pages by scoring terms from both claimed_service and claim_text against page text."""
    stopwords = {
        "amazon", "aws", "now", "and", "for", "with", "all", "is", "at", "in", "the", "a", "an", "to",
        "will", "are", "be", "by", "of", "on", "as", "or", "it", "its", "that", "this", "from", "into"
    }
    query = (row.get("claimed_service", "") + " " + row.get("claim_text", "")).lower()
    query_words = set(re.findall(r"\b[a-zA-Z0-9-]+\b", query)) - stopwords

    scored = []
    for p in PAGES:
        ts_words = set(re.findall(r"\b[a-zA-Z0-9-]+\b", (p["service_name"] + " " + p["title"]).lower()))
        b_words = set(re.findall(r"\b[a-zA-Z0-9-]+\b", p["body_text"].lower()))
        score = len(query_words & ts_words) * 3 + len(query_words & b_words)
        if score > 0:
            scored.append((score, p))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = [p for s, p in scored if s >= 3]
    return top[:8] if top else [p for s, p in scored[:5]] or PAGES


def researcher(state: State) -> dict:
    print("  researcher")
    row = state["row"]
    candidates = get_candidate_pages(row)
    listing = "\n".join(f'{p["page_id"]} | {p["service_name"]} | {p["title"]}' for p in candidates)
    picked = ask_json(
        "You match a claim to AWS announcement pages. Reply only with JSON: {\"page_ids\": [...]}. "
        "List every page below that is an official announcement of the product, feature, or launch described in the claim. If none matches, use [].",
        f"Claim: {row['claim_text']}\n\nPages:\n{listing}",
    ).get("page_ids", [])
    matching = [BY_ID[i] for i in picked if i in BY_ID]
    if not matching:
        return {"stage": "researched", "page": None, "quote": "no match"}
    page = tie_break(matching)
    sents = sentences(page["body_text"])
    numbered = "\n".join(f"{i}: {s}" for i, s in enumerate(sents))
    span = ask_json(
        "You pick a quote from one page to check against a claim. Reply only with JSON: {\"start\": n, \"end\": n}. "
        "Pick the span of consecutive sentences that addresses the facts asserted in the claim (product identity, status, features, and where/which regions or zones it is available). "
        "Ensure you include any sentences that specify the product's availability or regions/zones so the fact-checker can verify regional claims. Sentence numbers start at 0.",
        f"Claim: {row['claim_text']}\n\nSentences:\n{numbered}",
    )
    try:
        start, end = int(span["start"]), int(span["end"])
        assert 0 <= start <= end < len(sents)
    except (KeyError, ValueError, TypeError, AssertionError):
        start, end = 0, 0
    return {"stage": "researched", "page": page, "quote": " ".join(sents[start:end + 1])}


def load_rule() -> str:
    text = open(RULE_FILE, encoding="utf-8").read()
    return text.split("## Who does what")[0].strip() if "## Who does what" in text else text


def fact_checker(state: State) -> dict:
    print("  fact-checker")
    rule, page, quote, claim = load_rule(), state["page"], state["quote"], state["row"]["claim_text"]
    answers, whys = [], []

    domain = re.search(r"source page on ([\w.]+)\?", rule).group(1)
    host = urlparse(page["url"]).hostname
    ok1 = host == domain
    answers.append("yes" if ok1 else "no")
    whys.append(f"the page host is {host}, not {domain}." if not ok1 else f"the page is on {domain}.")

    ok2 = bool(quote) and quote in page["body_text"]
    answers.append("yes" if ok2 else "no")
    whys.append("the quote is not word for word in the page." if not ok2 else "the quote is in the page word for word.")

    if ok1 and ok2:
        out = ask_json(
            f"You are the fact-checker. Follow this rule file. Answer check 3 only: Does the quote back up every fact in the claim?\n"
            f"Check that every fact asserted in the claim is supported by the quote. If any asserted fact (such as purpose, status, or regional scope) is not backed by the quote, answer 'no'.\n"
            f"Note: Extra details in the quote that are not in the claim do not invalidate the claim.\n"
            f"Reply only with JSON: {{\"answer\": \"yes\" or \"no\", \"why\": \"one short sentence\"}}.\n\n{rule}",
            f"Claim: {claim}\nPage: {page['page_id']} {page['url']}\nQuote: {quote}",
        )
        ok3 = str(out.get("answer", "")).strip().lower() == "yes"
        answers.append("yes" if ok3 else "no")
        whys.append(re.sub(r"^Check \d failed:\s*", "", str(out.get("why") or "the checker was unsure.")))
    else:
        answers.append("not asked")
        whys.append("an earlier check already failed.")
    return {"stage": "checked", "answers": answers, "whys": whys}


def after_lead(state: State) -> str:
    return "researcher" if state["stage"] == "assigned" else END


def after_researcher(state: State) -> str:
    return "fact-checker" if state["page"] else "lead"


builder = StateGraph(State)
builder.add_node("lead", lead)
builder.add_node("researcher", researcher)
builder.add_node("fact-checker", fact_checker)
builder.set_entry_point("lead")
builder.add_conditional_edges("lead", after_lead, {"researcher": "researcher", END: END})
builder.add_conditional_edges("researcher", after_researcher, {"fact-checker": "fact-checker", "lead": "lead"})
builder.add_edge("fact-checker", "lead")
graph = builder.compile()


@traceable(name="candidate_run")
def run_candidate(row: dict) -> dict:
    RUNS[row["candidate_id"]] = get_current_run_tree()
    final = graph.invoke({"row": row, "stage": "new"})
    return {k: v for k, v in final.items() if k != "row"}


def share(run) -> str:
    run.client.flush()
    for _ in range(15):
        try:
            shared_url = run.client.share_run(run.trace_id)
            token = urlparse(shared_url).path
            break
        except Exception:
            try:
                shared_url = run.client.read_run_shared_link(run.trace_id)
                token = urlparse(shared_url).path
                break
            except Exception:
                time.sleep(2)
    else:
        raise RuntimeError(f"could not share trace {run.trace_id}")
    endpoint = os.environ.get("LANGSMITH_ENDPOINT", "")
    host = "https://aws.smith.langchain.com" if "aws" in endpoint else "https://smith.langchain.com"
    return host + token


def cell(text: str) -> str:
    return text.replace("|", "\\|")


def lead_split_results(evaluated_items: list) -> tuple[list, list]:
    """Last lead step: gathers results into a confirmed list and a rejected list.
    
    Each item in both lists keeps its source URL, its quote or reason, and its trace link.
    """
    confirmed = []
    rejected = []
    for item in evaluated_items:
        entry = {
            "candidate_id": item["candidate_id"],
            "claim": item["claim_text"],
            "source_url": item["source_url"],
            "url": item["source_url"],
            "trace_link": item["trace_link"],
            "trace": item["trace_link"],
        }
        if item.get("verdict") == "confirmed":
            entry["quote"] = item["quote"]
            confirmed.append(entry)
        else:
            entry["reason"] = item["reason"]
            rejected.append(entry)
    return confirmed, rejected


def save_split_results(confirmed: list, rejected: list) -> None:
    os.makedirs(os.path.dirname(SPLIT_FILE), exist_ok=True)
    with open(SPLIT_FILE, "w", encoding="utf-8") as f:
        json.dump({"confirmed": confirmed, "rejected": rejected}, f, indent=2)


def run_safety_gate() -> None:
    """Runs tests/test_safety.py via pytest before building anything.
    
    If the check fails, halts the pipeline run with a message.
    """
    print("\nRunning safety gate (tests/test_safety.py)...")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_safety.py", "-v"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        if proc.stderr:
            print(proc.stderr)
        print("\n[SAFETY GATE HALTED] tests/test_safety.py check failed!")
        print("Pipeline stopped before building results.")
        sys.exit(1)
    print(proc.stdout.strip())
    print("\n[SAFETY GATE PASSED] All safety checks passed successfully.")


def build_briefing_html(evaluated_items: list, confirmed: list, rejected: list) -> None:
    """Builds the live morning briefing HTML page for solutions architects."""
    html_items = []
    for item in evaluated_items:
        cid = item["candidate_id"]
        claim = item["claim_text"]
        verdict = item["verdict"]
        trace = item["trace_link"]
        is_confirmed = verdict == "confirmed"
        badge_class = "badge-confirmed" if is_confirmed else "badge-rejected"
        badge_text = "Confirmed" if is_confirmed else "Rejected"

        if is_confirmed:
            url = item["source_url"]
            quote = item["quote"]
            detail_html = f"""
            <div class="evidence">
              <div class="evidence-row"><strong>Official Source:</strong> <a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a></div>
              <div class="quote-box"><blockquote>&ldquo;{quote}&rdquo;</blockquote></div>
            </div>
            """
        else:
            reason = item["reason"]
            url = item.get("source_url", "")
            quote = item.get("quote", "")
            has_matching_page = url and "aws.amazon.com" in url and quote and quote != "no match"

            evidence_rows = [f'<div class="reason-box"><strong>Rejection Reason:</strong> {reason}</div>']
            if has_matching_page:
                evidence_rows.append(f'<div class="evidence-row" style="margin-top: 0.75rem;"><strong>Examined Official Page:</strong> <a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a></div>')
                evidence_rows.append(f'<div class="quote-box" style="border-left-color: #ef4444; color: #7f1d1d; background: #fef2f2; padding: 0.5rem 0.75rem; border-radius: 4px; margin-top: 0.5rem;"><strong>Contradicting Quote from Page:</strong><blockquote>&ldquo;{quote}&rdquo;</blockquote></div>')

            detail_html = f"""
            <div class="evidence">
              {"".join(evidence_rows)}
            </div>
            """

        item_card = f"""
        <article class="claim-card {badge_class}">
          <div class="card-header">
            <span class="claim-id">{cid}</span>
            <span class="badge {badge_class}">{badge_text}</span>
          </div>
          <p class="claim-text">{claim}</p>
          {detail_html}
          <div class="card-footer">
            <a href="{trace}" target="_blank" rel="noopener noreferrer" class="trace-link">View Public LangSmith Trace &rarr;</a>
          </div>
        </article>
        """
        html_items.append(item_card)

    items_rendered = "\n".join(html_items)
    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AWS re:Invent 2025 Morning Briefing</title>
  <style>
    :root {{
      --bg: #f8fafc;
      --card-bg: #ffffff;
      --text: #0f172a;
      --text-muted: #475569;
      --border: #e2e8f0;
      --confirmed-bg: #ecfdf5;
      --confirmed-border: #a7f3d0;
      --confirmed-text: #065f46;
      --rejected-bg: #fef2f2;
      --rejected-border: #fecaca;
      --rejected-text: #991b1b;
      --link: #2563eb;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: var(--bg);
      color: var(--text);
      line-height: 1.6;
      padding: 2.5rem 1rem;
    }}
    .container {{
      max-width: 900px;
      margin: 0 auto;
    }}
    header {{
      margin-bottom: 2.5rem;
      border-bottom: 1px solid var(--border);
      padding-bottom: 1.5rem;
    }}
    h1 {{
      font-size: 2.25rem;
      font-weight: 800;
      color: #0f172a;
      letter-spacing: -0.025em;
      margin-bottom: 0.5rem;
    }}
    .subtitle {{
      color: var(--text-muted);
      font-size: 1.05rem;
      margin-bottom: 1.25rem;
    }}
    .meta-bar {{
      display: flex;
      flex-wrap: wrap;
      gap: 1.5rem;
      font-size: 0.9rem;
      color: var(--text-muted);
      background: #f1f5f9;
      padding: 0.75rem 1rem;
      border-radius: 6px;
    }}
    .claim-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1.5rem;
      margin-bottom: 1.5rem;
      box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }}
    .claim-card.badge-confirmed {{
      border-left: 5px solid #10b981;
    }}
    .claim-card.badge-rejected {{
      border-left: 5px solid #ef4444;
    }}
    .card-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1rem;
    }}
    .claim-id {{
      font-weight: 700;
      font-size: 0.95rem;
      color: var(--text-muted);
      letter-spacing: 0.05em;
    }}
    .badge {{
      display: inline-block;
      padding: 0.25rem 0.75rem;
      border-radius: 9999px;
      font-size: 0.85rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    .badge-confirmed {{
      background: var(--confirmed-bg);
      color: var(--confirmed-text);
      border: 1px solid var(--confirmed-border);
    }}
    .badge-rejected {{
      background: var(--rejected-bg);
      color: var(--rejected-text);
      border: 1px solid var(--rejected-border);
    }}
    .claim-text {{
      font-size: 1.15rem;
      font-weight: 600;
      color: #1e293b;
      margin-bottom: 1rem;
    }}
    .evidence {{
      background: #f8fafc;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 1rem;
      margin-bottom: 1rem;
      font-size: 0.95rem;
    }}
    .evidence-row {{
      margin-bottom: 0.5rem;
      word-break: break-all;
    }}
    .evidence-row a {{
      color: var(--link);
      text-decoration: none;
    }}
    .evidence-row a:hover {{
      text-decoration: underline;
    }}
    .quote-box {{
      margin-top: 0.5rem;
      border-left: 3px solid #0ea5e9;
      padding-left: 0.75rem;
      color: #334155;
      font-style: italic;
    }}
    .reason-box {{
      color: #991b1b;
    }}
    .card-footer {{
      display: flex;
      justify-content: flex-end;
      padding-top: 0.75rem;
      border-top: 1px solid #f1f5f9;
    }}
    .trace-link {{
      color: var(--link);
      font-size: 0.9rem;
      font-weight: 500;
      text-decoration: none;
    }}
    .trace-link:hover {{
      text-decoration: underline;
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>AWS re:Invent 2025 Morning Briefing</h1>
      <p class="subtitle">Verified Launch Intelligence for Solutions Architects &mdash; Solutions architects will only repeat an item if it proves itself on this page.</p>
      <div class="meta-bar">
        <span><strong>Batch:</strong> 3 November 2025 Holdout</span>
        <span><strong>Status:</strong> {len(confirmed)} Confirmed &bull; {len(rejected)} Rejected</span>
        <span><strong>Safety Gate:</strong> Passed (tests/test_safety.py)</span>
      </div>
    </header>
    <main>
      {items_rendered}
    </main>
  </div>
</body>
</html>
"""
    with open(BRIEFING_HTML, "w", encoding="utf-8") as f:
        f.write(full_html)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(full_html)
    print(f"built {BRIEFING_HTML}")


def print_split_lists(confirmed: list, rejected: list) -> None:
    print("\n================== CONFIRMED LIST ==================")
    for c in confirmed:
        print(f"[{c['candidate_id']}] {c['claim']}")
        print(f"  Source URL: {c['source_url']}")
        print(f"  Quote:      {c['quote']}")
        print(f"  Trace Link: {c['trace_link']}")
        print()
    print("================== REJECTED LIST ==================")
    for r in rejected:
        print(f"[{r['candidate_id']}] {r['claim']}")
        print(f"  Source URL: {r['source_url']}")
        print(f"  Reason:     {r['reason']}")
        print(f"  Trace Link: {r['trace_link']}")
        print()


def main(ids: list) -> None:
    rows = [r for r in csv.DictReader(open(CANDIDATES, encoding="utf-8")) if not ids or r["candidate_id"] in ids]
    lines = [
        "# Results",
        "",
        "| Candidate | Claim | Page | Quote | Check 1 | Check 2 | Check 3 | Verdict | Reason | Trace |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    evaluated_items = []
    for row in rows:
        print(f"== {row['candidate_id']} ==")
        f = run_candidate(row)
        url = share(RUNS[row["candidate_id"]])
        page = f["page"]["page_id"] if f.get("page") else "no match"
        source_url = f["page"]["url"] if f.get("page") else row.get("origin_source_url", "no match")
        evaluated_items.append({
            "candidate_id": row["candidate_id"],
            "claim_text": row["claim_text"],
            "source_url": source_url,
            "quote": f.get("quote", "no match"),
            "verdict": f.get("verdict", "rejected"),
            "reason": f.get("reason", ""),
            "trace_link": url,
        })
        lines.append("| " + " | ".join(cell(x) for x in [
            row["candidate_id"], row["claim_text"], page, f["quote"] or "no match",
            *f["answers"], f["verdict"], f["reason"], f"[trace]({url})",
        ]) + " |")
        print(f"  -> {f['verdict']}: {f['reason']}")
        time.sleep(2)

    # Lead step: split results into confirmed and rejected lists
    confirmed, rejected = lead_split_results(evaluated_items)
    save_split_results(confirmed, rejected)

    # Safety gate: runs tests/test_safety.py and stops before building anything if it fails
    run_safety_gate()

    # Build briefing.html
    build_briefing_html(evaluated_items, confirmed, rejected)

    if not ids:
        open(RESULTS, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        print(f"wrote {RESULTS}")

    # Print confirmed and rejected lists at the end
    print_split_lists(confirmed, rejected)


if __name__ == "__main__":
    main(sys.argv[1:])
