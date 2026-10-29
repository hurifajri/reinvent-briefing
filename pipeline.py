"""Three-node LangGraph pipeline: lead -> researcher -> fact-checker -> lead.

Rules live in docs/confirm_rule.md. The fact-checker reads that file on every run.
"""
import csv, json, os, re, sys, time
from typing import Optional, TypedDict
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

from langgraph.graph import END, StateGraph
from langsmith import get_current_run_tree, traceable
from langsmith.wrappers import wrap_openai
from openai import APIStatusError, OpenAI

RULE_FILE = "docs/confirm_rule.md"
SNAPSHOT = "data/aws_official_source_snapshot.json"
CANDIDATES = "data/aws_reinvent_candidate_items.csv"
RESULTS = "results.md"

client = wrap_openai(OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.environ["OPENROUTER_API_KEY"]))
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
    for attempt in range(4):
        try:
            resp = client.chat.completions.create(
                model=os.environ["OPENROUTER_MODEL"],
                temperature=0,
                max_tokens=200,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
            return resp.choices[0].message.content or ""
        except APIStatusError as e:
            if e.status_code != 402 or attempt == 3:
                raise
            wait = int(e.response.headers.get("Retry-After", 120))
            print(f"  OpenRouter 402, retrying in {wait}s")
            time.sleep(wait)


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


def researcher(state: State) -> dict:
    print("  researcher")
    row = state["row"]
    shortlist = [p for p in PAGES if p["service_name"] == row["claimed_service"]]
    if not shortlist:
        return {"stage": "researched", "page": None, "quote": "no match"}
    listing = "\n".join(f'{p["page_id"]} | {p["title"]} | {p["body_text"]}' for p in shortlist)
    picked = ask_json(
        "You match a claim to AWS pages. Reply only with JSON: {\"page_ids\": [...]}. "
        "List every page below that is about the same product or feature as the claim. Use [] if none is.",
        f"Claim: {row['claim_text']}\n\nPages:\n{listing}",
    ).get("page_ids", [])
    candidates = [BY_ID[i] for i in picked if i in BY_ID and BY_ID[i] in shortlist]
    if not candidates:
        return {"stage": "researched", "page": None, "quote": "no match"}
    page = tie_break(candidates)
    sents = sentences(page["body_text"])
    numbered = "\n".join(f"{i}: {s}" for i, s in enumerate(sents))
    span = ask_json(
        "You pick a quote from one page. Reply only with JSON: {\"start\": n, \"end\": n}. "
        "Pick the shortest run of consecutive sentences that says what the page states about the claim's "
        "status and availability. Sentence numbers start at 0.",
        f"Claim: {row['claim_text']}\n\nSentences:\n{numbered}",
    )
    try:
        start, end = int(span["start"]), int(span["end"])
        assert 0 <= start <= end < len(sents)
    except (KeyError, ValueError, TypeError, AssertionError):
        start, end = 0, 0
    return {"stage": "researched", "page": page, "quote": " ".join(sents[start:end + 1])}


def load_rule() -> str:
    return open(RULE_FILE, encoding="utf-8").read()


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
            f"You are the fact-checker. Follow this rule file. Answer check 3 only.\n"
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
            token = urlparse(run.client.share_run(run.trace_id)).path
            break
        except Exception:
            time.sleep(2)
    else:
        raise RuntimeError(f"could not share trace {run.trace_id}")
    # share_run builds the link on smith.langchain.com; use the host of the configured endpoint instead
    app = os.environ.get("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com").replace(".api.", ".").replace("//api.", "//")
    return app.rstrip("/") + token


def cell(text: str) -> str:
    return text.replace("|", "\\|")


def main(ids: list) -> None:
    rows = [r for r in csv.DictReader(open(CANDIDATES, encoding="utf-8")) if not ids or r["candidate_id"] in ids]
    lines = [
        "# Results",
        "",
        "| Candidate | Claim | Page | Quote | Check 1 | Check 2 | Check 3 | Verdict | Reason | Trace |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        print(f"== {row['candidate_id']} ==")
        f = run_candidate(row)
        url = share(RUNS[row["candidate_id"]])
        page = f["page"]["page_id"] if f.get("page") else "no match"
        lines.append("| " + " | ".join(cell(x) for x in [
            row["candidate_id"], row["claim_text"], page, f["quote"] or "no match",
            *f["answers"], f["verdict"], f["reason"], f"[trace]({url})",
        ]) + " |")
        print(f"  -> {f['verdict']}: {f['reason']}")
    if not ids:
        open(RESULTS, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        print(f"wrote {RESULTS}")


if __name__ == "__main__":
    main(sys.argv[1:])
