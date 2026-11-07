"""Three-node LangGraph verification pipeline for the AWS re:Invent Morning Briefing.

Workflow:
    lead (assign) -> researcher -> fact-checker -> lead (final verdict)

Verification rules live in `docs/confirm_rule.md` and are loaded dynamically on
every fact-check run. Before building `index.html` or `docs/results.md`, the
pipeline partitions results into `data/split_results.json` and enforces the
automated `pytest` safety gate (`tests/test_safety.py`).
"""

import asyncio
import atexit
from collections.abc import Mapping, Sequence
import csv
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, TypedDict
from urllib.parse import urlparse

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from langsmith import get_current_run_tree, traceable
from langsmith.wrappers import wrap_openai
from openai import APIStatusError, OpenAI

# Load environment variables from `.env` before reading runtime configuration.
load_dotenv()

# ---------------------------------------------------------------------------
# File paths & encoding constants
# ---------------------------------------------------------------------------
UTF8_ENCODING: str = "utf-8"
RULE_FILE: str = "docs/confirm_rule.md"
SNAPSHOT: str = os.environ.get("SNAPSHOT_FILE", "data/aws_official_source_snapshot.json")
CANDIDATES: str = os.environ.get("CANDIDATES_FILE", "data/aws_reinvent_candidate_items.csv")
RESULTS: str = "docs/results.md"
BRIEFING_HTML: str = "index.html"
SPLIT_FILE: str = "data/split_results.json"
SAFETY_TEST_PATH: str = "tests/test_safety.py"

# ---------------------------------------------------------------------------
# Domain, verdict, check-answer, and graph-stage constants
# ---------------------------------------------------------------------------
DEFAULT_AWS_DOMAIN: str = "aws.amazon.com"
NO_MATCH: str = "no match"

ANSWER_YES: str = "yes"
ANSWER_NO: str = "no"
ANSWER_NOT_ASKED: str = "not asked"

VERDICT_CONFIRMED: str = "confirmed"
VERDICT_REJECTED: str = "rejected"

STAGE_NEW: str = "new"
STAGE_ASSIGNED: str = "assigned"
STAGE_RESEARCHED: str = "researched"
STAGE_CHECKED: str = "checked"

NODE_LEAD: str = "lead"
NODE_RESEARCHER: str = "researcher"
NODE_FACT_CHECKER: str = "fact-checker"

# ---------------------------------------------------------------------------
# LLM, retrieval, and LangSmith configuration constants
# ---------------------------------------------------------------------------
DEFAULT_OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
MAX_LLM_ATTEMPTS: int = 6
LLM_RETRY_DELAY_SECONDS: float = 2.0
LLM_MAX_TOKENS: int = 300

MAX_SHARE_ATTEMPTS: int = 15
SHARE_RETRY_DELAY_SECONDS: float = 2.0
SHARE_URL_SELECT_FIELD: str = "SHARE_URL"
DEFAULT_LANGSMITH_PROJECT: str = "default"

# Pre-compiled regular expressions reused across retrieval and parsing steps.
WORD_TOKEN_RE: re.Pattern[str] = re.compile(r"\b[a-zA-Z0-9-]+\b")
SENTENCE_SPLIT_RE: re.Pattern[str] = re.compile(r"(?<=\.) (?=[A-Z])")
LAUNCH_STATUS_RE: re.Pattern[str] = re.compile(
    r"generally available|preview|now available", re.IGNORECASE
)
JSON_OBJECT_RE: re.Pattern[str] = re.compile(r"\{.*\}", re.DOTALL)
RULE_DOMAIN_RE: re.Pattern[str] = re.compile(r"source page on ([\w.]+)\?")
CHECK_PREFIX_RE: re.Pattern[str] = re.compile(r"^Check \d failed:\s*")

# Common English and AWS terms excluded when scoring candidate pages.
QUERY_STOPWORDS: frozenset[str] = frozenset({
    "amazon", "aws", "now", "and", "for", "with", "all", "is", "at", "in",
    "the", "a", "an", "to", "will", "are", "be", "by", "of", "on", "as",
    "or", "it", "its", "that", "this", "from", "into",
})

# Runtime state caches for LangSmith trace sharing.
RUNS: dict[str, Any] = {}  # candidate_id -> LangSmith RunTree
_PROJECT_IDS: dict[str, str] = {}  # project_name -> project UUID
_RUNNER = asyncio.Runner()
atexit.register(_RUNNER.close)


class State(TypedDict, total=False):
    """Mutable state passed between nodes in the LangGraph verification workflow."""

    row: dict[str, str]
    stage: str  # new -> assigned -> researched -> checked
    page: dict[str, Any] | None
    quote: str
    answers: list[str]  # check 1, 2, 3: "yes", "no", or "not asked"
    whys: list[str]
    verdict: str
    reason: str


# ---------------------------------------------------------------------------
# Snapshot & LLM client initialization helpers
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def get_llm_client() -> OpenAI:
    """Create and cache the LangSmith-wrapped OpenAI-compatible client."""
    base_url = os.environ.get("OPENROUTER_BASE_URL", DEFAULT_OPENROUTER_BASE_URL)
    api_key = os.environ.get("OPENROUTER_API_KEY")
    # Custom gateways behind strict WAFs require a standard SDK User-Agent header.
    headers = {"user-agent": "openai-python/1.0.0"} if "brisnets.com" in base_url else None
    return wrap_openai(OpenAI(base_url=base_url, api_key=api_key, default_headers=headers))


@lru_cache(maxsize=1)
def load_snapshot_pages(snapshot_path: str = SNAPSHOT) -> list[dict[str, Any]]:
    """Load the official AWS source snapshot JSON from disk once per process."""
    raw_json = Path(snapshot_path).read_text(encoding=UTF8_ENCODING)
    return json.loads(raw_json)


@lru_cache(maxsize=1)
def get_pages_by_id(snapshot_path: str = SNAPSHOT) -> dict[str, dict[str, Any]]:
    """Index snapshot pages by `page_id` for O(1) lookup during research."""
    return {page["page_id"]: page for page in load_snapshot_pages(snapshot_path)}


# ---------------------------------------------------------------------------
# LLM request & JSON parsing helpers
# ---------------------------------------------------------------------------
def _build_extra_body(model_name: str) -> dict[str, Any] | None:
    """Disable chain-of-thought thinking tokens on DeepSeek models for deterministic JSON."""
    if "deepseek" in model_name.lower():
        return {"thinking": {"type": "disabled"}}
    return None


def _extract_message_content(response: Any) -> str:
    """Safely extract the first assistant message string from a chat completion."""
    if not response.choices:
        return ""
    message = response.choices[0].message
    if message and message.content:
        return str(message.content)
    return ""


def _format_api_status_error(exc: APIStatusError) -> str:
    """Extract a readable error message from an OpenAI `APIStatusError` payload."""
    if isinstance(exc.body, dict):
        nested_error = exc.body.get("error")
        if isinstance(nested_error, dict) and nested_error.get("message"):
            return str(nested_error["message"])
    return str(exc)


def _request_completion_once(system: str, user: str) -> str:
    """Execute a single temperature-0 chat completion against the configured model."""
    model_name = os.environ["OPENROUTER_MODEL"]
    response = get_llm_client().chat.completions.create(
        model=model_name,
        temperature=0,
        max_tokens=LLM_MAX_TOKENS,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        extra_body=_build_extra_body(model_name),
    )
    return _extract_message_content(response)


def ask(system: str, user: str) -> str:
    """Prompt the LLM with bounded retries on transient API or network errors."""
    for attempt in range(1, MAX_LLM_ATTEMPTS + 1):
        try:
            return _request_completion_once(system, user)
        except APIStatusError as exc:
            print(f"  API error {exc.status_code}: {_format_api_status_error(exc)}")
            if attempt == MAX_LLM_ATTEMPTS:
                raise
        except Exception as exc:
            print(f"  Request error: {exc}")
            if attempt == MAX_LLM_ATTEMPTS:
                raise
        time.sleep(LLM_RETRY_DELAY_SECONDS)
    return ""


def ask_json(system: str, user: str) -> dict[str, Any]:
    """Prompt the LLM and parse the first JSON object found in its reply."""
    match = JSON_OBJECT_RE.search(ask(system, user))
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


# ---------------------------------------------------------------------------
# Text tokenization, sentence splitting, and page tie-breaking helpers
# ---------------------------------------------------------------------------
def tokenize_words(text: str) -> set[str]:
    """Extract lowercase alphanumeric/hyphenated word tokens from `text`."""
    return set(WORD_TOKEN_RE.findall(text.lower()))


def sentences(body: str) -> list[str]:
    """Split snapshot body text into individual sentences."""
    return SENTENCE_SPLIT_RE.split(body)


def states_status(page: Mapping[str, Any]) -> bool:
    """Return True if the page body explicitly mentions GA or preview availability."""
    return bool(LAUNCH_STATUS_RE.search(str(page.get("body_text", ""))))


def tie_break(pages: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Select the canonical page: newest `published_date`, then status-bearing, then lowest `page_id`."""
    newest_date = max(page["published_date"] for page in pages)
    newest_pool = [page for page in pages if page["published_date"] == newest_date]
    return min(newest_pool, key=lambda page: (not states_status(page), page["page_id"]))


def resolve_sentence_span(span: Mapping[str, Any], sentence_count: int) -> tuple[int, int]:
    """Validate `{start, end}` sentence indices from LLM JSON, defaulting safely to `(0, 0)`."""
    try:
        start = int(span["start"])
        end = int(span["end"])
    except (KeyError, TypeError, ValueError):
        return 0, 0

    if 0 <= start <= end < sentence_count:
        return start, end
    return 0, 0


def get_candidate_pages(row: Mapping[str, str]) -> list[dict[str, Any]]:
    """Score snapshot pages against claim terms (title/service weighted 3x over body text)."""
    pages = load_snapshot_pages()
    query_text = f"{row.get('claimed_service', '')} {row.get('claim_text', '')}"
    query_words = tokenize_words(query_text) - QUERY_STOPWORDS

    scored_pages: list[tuple[int, dict[str, Any]]] = []
    for page in pages:
        header_words = tokenize_words(f"{page['service_name']} {page['title']}")
        body_words = tokenize_words(page["body_text"])
        score = len(query_words & header_words) * 3 + len(query_words & body_words)
        if score > 0:
            scored_pages.append((score, page))

    scored_pages.sort(key=lambda pair: pair[0], reverse=True)
    strong_matches = [page for score, page in scored_pages if score >= 3]
    if strong_matches:
        return strong_matches[:8]
    return [page for _, page in scored_pages[:5]] or pages


def load_rule(rule_path: str = RULE_FILE) -> str:
    """Read the core verification rule text from `docs/confirm_rule.md`."""
    text = Path(rule_path).read_text(encoding=UTF8_ENCODING)
    section_marker = "## Who does what"
    return text.split(section_marker)[0].strip() if section_marker in text else text


def _extract_required_domain(rule_text: str) -> str:
    """Parse the required official hostname (e.g. `aws.amazon.com`) from the rule file."""
    match = RULE_DOMAIN_RE.search(rule_text)
    return match.group(1) if match else DEFAULT_AWS_DOMAIN


# ---------------------------------------------------------------------------
# LangGraph agent nodes: lead, researcher, fact-checker
# ---------------------------------------------------------------------------
def lead(state: State) -> dict[str, Any]:
    """Route new claims to research and synthesize the final verdict after checking."""
    if state["stage"] == STAGE_NEW:
        print(f"  {NODE_LEAD}")
        return {"stage": STAGE_ASSIGNED}

    print(f"  {NODE_LEAD} (final verdict)")
    page = state.get("page")
    answers = state.get("answers") or []
    whys = state.get("whys") or []

    # Short-circuit when the researcher found no matching snapshot page.
    if not page:
        return {
            "verdict": VERDICT_REJECTED,
            "reason": "Check 1 failed: the snapshot has no page for this claim.",
            "answers": [ANSWER_NO, ANSWER_NOT_ASKED, ANSWER_NOT_ASKED],
            "quote": NO_MATCH,
        }

    # Confirm only when all three checks explicitly answered "yes".
    if all(answer == ANSWER_YES for answer in answers):
        return {"verdict": VERDICT_CONFIRMED, "reason": "All three checks are yes."}

    first_failed_idx = next(
        idx for idx, answer in enumerate(answers) if answer != ANSWER_YES
    )
    return {
        "verdict": VERDICT_REJECTED,
        "reason": f"Check {first_failed_idx + 1} failed: {whys[first_failed_idx]}",
    }


def researcher(state: State) -> dict[str, Any]:
    """Match a candidate claim to an official snapshot page and extract a verbatim quote span."""
    print(f"  {NODE_RESEARCHER}")
    row = state["row"]
    candidates = get_candidate_pages(row)
    listing = "\n".join(
        f'{page["page_id"]} | {page["service_name"]} | {page["title"]}'
        for page in candidates
    )

    picked_ids = ask_json(
        'You match a claim to AWS announcement pages. Reply only with JSON: {"page_ids": [...]}. '
        "List every page below that is an official announcement of the product, feature, or launch "
        "described in the claim. If none matches, use [].",
        f"Claim: {row['claim_text']}\n\nPages:\n{listing}",
    ).get("page_ids", [])

    pages_by_id = get_pages_by_id()
    matching_pages = [pages_by_id[pid] for pid in picked_ids if pid in pages_by_id]
    if not matching_pages:
        return {"stage": STAGE_RESEARCHED, "page": None, "quote": NO_MATCH}

    selected_page = tie_break(matching_pages)
    page_sentences = sentences(selected_page["body_text"])
    numbered_sentences = "\n".join(
        f"{idx}: {sentence}" for idx, sentence in enumerate(page_sentences)
    )

    span_reply = ask_json(
        'You pick a quote from one page to check against a claim. Reply only with JSON: {"start": n, "end": n}. '
        "Pick the span of consecutive sentences that addresses the facts asserted in the claim "
        "(product identity, status, features, and where/which regions or zones it is available). "
        "Ensure you include any sentences that specify the product's availability or regions/zones "
        "so the fact-checker can verify regional claims. Sentence numbers start at 0.",
        f"Claim: {row['claim_text']}\n\nSentences:\n{numbered_sentences}",
    )
    start, end = resolve_sentence_span(span_reply, len(page_sentences))
    quote = " ".join(page_sentences[start : end + 1])
    return {"stage": STAGE_RESEARCHED, "page": selected_page, "quote": quote}


def _evaluate_check_three(
    claim: str,
    page: Mapping[str, Any],
    quote: str,
    rule: str,
) -> tuple[str, str]:
    """Run LLM Check 3 to verify that `quote` backs every asserted fact in `claim`."""
    reply = ask_json(
        "You are the fact-checker. Follow this rule file. Answer check 3 only: "
        "Does the quote back up every fact in the claim?\n"
        "Check that every fact asserted in the claim is supported by the quote. "
        "If any asserted fact (such as purpose, status, or regional scope) is not backed by the quote, answer 'no'.\n"
        "Note: Extra details in the quote that are not in the claim do not invalidate the claim.\n"
        f'Reply only with JSON: {{"answer": "yes" or "no", "why": "one short sentence"}}.\n\n{rule}',
        f"Claim: {claim}\nPage: {page['page_id']} {page['url']}\nQuote: {quote}",
    )
    is_supported = str(reply.get("answer", "")).strip().lower() == ANSWER_YES
    raw_why = str(reply.get("why") or "the checker was unsure.")
    cleaned_why = CHECK_PREFIX_RE.sub("", raw_why)
    return (ANSWER_YES if is_supported else ANSWER_NO), cleaned_why


def fact_checker(state: State) -> dict[str, Any]:
    """Evaluate Check 1 (domain), Check 2 (verbatim quote), and Check 3 (full fact support)."""
    print(f"  {NODE_FACT_CHECKER}")
    rule = load_rule()
    page = state["page"] or {}
    quote = state["quote"]
    claim = state["row"]["claim_text"]

    # Check 1 (deterministic): verify the source URL is hosted on `aws.amazon.com`.
    required_domain = _extract_required_domain(rule)
    host = urlparse(page["url"]).hostname
    check1_ok = host == required_domain
    answer1 = ANSWER_YES if check1_ok else ANSWER_NO
    why1 = (
        f"the page is on {required_domain}."
        if check1_ok
        else f"the page host is {host}, not {required_domain}."
    )

    # Check 2 (deterministic): verify the quote appears word-for-word in `body_text`.
    check2_ok = bool(quote) and quote in page["body_text"]
    answer2 = ANSWER_YES if check2_ok else ANSWER_NO
    why2 = (
        "the quote is in the page word for word."
        if check2_ok
        else "the quote is not word for word in the page."
    )

    # Check 3 (LLM-judged): run only when both deterministic checks succeeded.
    if check1_ok and check2_ok:
        answer3, why3 = _evaluate_check_three(claim, page, quote, rule)
    else:
        answer3 = ANSWER_NOT_ASKED
        why3 = "an earlier check already failed."

    return {
        "stage": STAGE_CHECKED,
        "answers": [answer1, answer2, answer3],
        "whys": [why1, why2, why3],
    }


# ---------------------------------------------------------------------------
# Graph assembly & traced candidate execution
# ---------------------------------------------------------------------------
def after_lead(state: State) -> str:
    """Transition from `lead` to `researcher` on assignment, or terminate after final verdict."""
    return NODE_RESEARCHER if state["stage"] == STAGE_ASSIGNED else END


def after_researcher(state: State) -> str:
    """Skip `fact-checker` and return straight to `lead` when no snapshot page matched."""
    return NODE_FACT_CHECKER if state.get("page") else NODE_LEAD


def build_verification_graph():
    """Construct and compile the three-node LangGraph state machine."""
    builder = StateGraph(State)
    builder.add_node(NODE_LEAD, lead)
    builder.add_node(NODE_RESEARCHER, researcher)
    builder.add_node(NODE_FACT_CHECKER, fact_checker)
    builder.set_entry_point(NODE_LEAD)
    builder.add_conditional_edges(
        NODE_LEAD, after_lead, {NODE_RESEARCHER: NODE_RESEARCHER, END: END}
    )
    builder.add_conditional_edges(
        NODE_RESEARCHER,
        after_researcher,
        {NODE_FACT_CHECKER: NODE_FACT_CHECKER, NODE_LEAD: NODE_LEAD},
    )
    builder.add_edge(NODE_FACT_CHECKER, NODE_LEAD)
    return builder.compile()


graph = build_verification_graph()


@traceable(name="candidate_run")
def run_candidate(row: dict[str, str]) -> dict[str, Any]:
    """Execute the verification graph for one candidate row inside a LangSmith root trace."""
    RUNS[row["candidate_id"]] = get_current_run_tree()
    final_state = graph.invoke({"row": row, "stage": STAGE_NEW})
    return {key: value for key, value in final_state.items() if key != "row"}


# ---------------------------------------------------------------------------
# LangSmith SmithDB v2 public trace sharing helpers
# ---------------------------------------------------------------------------
async def _resolve_project_id(ls_client: Any, project_name: str) -> str:
    """Resolve and cache the LangSmith project UUID for `project_name`."""
    cached_id = _PROJECT_IDS.get(project_name)
    if cached_id:
        return cached_id

    project = await ls_client.aread_project(project_name=project_name)
    project_id = str(project.id)
    _PROJECT_IDS[project_name] = project_id
    return project_id


async def _retrieve_share_url(run: Any, project_id: str) -> str | None:
    """Fetch the deployment-aware public `share_url` for a run from SmithDB v2."""
    try:
        authenticated_run = await run.client.runs.retrieve(
            str(run.id),
            project_id=project_id,
            start_time=run.start_time,
            selects=[SHARE_URL_SELECT_FIELD],
        )
    except Exception:
        return None
    return authenticated_run.share_url or None


async def _try_share_once(run: Any, project_name: str) -> str | None:
    """Attempt once to publish `run` via SmithDB v2 and return its public `share_url`."""
    try:
        project_id = await _resolve_project_id(run.client, project_name)
    except Exception:
        return None

    try:
        share_resp = await run.client.runs.share.create(
            str(run.id),
            session_id=project_id,
            trace_id=str(run.trace_id),
        )
        if not share_resp.share_token:
            return None
    except Exception:
        # If the trace was already shared on an earlier attempt, fall through to retrieve its URL.
        return await _retrieve_share_url(run, project_id)

    return await _retrieve_share_url(run, project_id)


async def _share_async(run: Any) -> str:
    """Poll SmithDB until the flushed trace is ingested, shared, and yields a public URL."""
    project_name = run.session_name or os.environ.get(
        "LANGSMITH_PROJECT", DEFAULT_LANGSMITH_PROJECT
    )
    for _ in range(MAX_SHARE_ATTEMPTS):
        share_url = await _try_share_once(run, project_name)
        if share_url:
            return share_url
        await asyncio.sleep(SHARE_RETRY_DELAY_SECONDS)

    raise RuntimeError(f"could not share trace {run.trace_id}")


def share(run: Any) -> str:
    """Flush pending LangSmith events and return the public trace URL for `run`."""
    run.client.flush()
    return _RUNNER.run(_share_async(run))


# ---------------------------------------------------------------------------
# Partitioning, safety gate execution, and output builders
# ---------------------------------------------------------------------------
def cell(text: str) -> str:
    """Escape pipe characters so arbitrary text renders cleanly inside a Markdown table."""
    return text.replace("|", "\\|")


def lead_split_results(
    evaluated_items: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition evaluated items into `confirmed` and `rejected` lists for the safety gate."""
    confirmed: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for item in evaluated_items:
        entry: dict[str, Any] = {
            "candidate_id": item["candidate_id"],
            "claim": item["claim_text"],
            "source_url": item["source_url"],
            "url": item["source_url"],
            "trace_link": item["trace_link"],
            "trace": item["trace_link"],
        }
        if item.get("verdict") == VERDICT_CONFIRMED:
            entry["quote"] = item["quote"]
            confirmed.append(entry)
        else:
            entry["reason"] = item["reason"]
            rejected.append(entry)

    return confirmed, rejected


def save_split_results(
    confirmed: Sequence[Mapping[str, Any]],
    rejected: Sequence[Mapping[str, Any]],
    split_path: str = SPLIT_FILE,
) -> None:
    """Persist partitioned `confirmed` and `rejected` lists to `data/split_results.json`."""
    target = Path(split_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"confirmed": list(confirmed), "rejected": list(rejected)}, indent=2)
    target.write_text(f"{payload}\n", encoding=UTF8_ENCODING)


def run_safety_gate(test_path: str = SAFETY_TEST_PATH) -> None:
    """Execute `pytest` safety checks and abort the build immediately if any check fails."""
    print(f"\nRunning safety gate ({test_path})...")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", test_path, "-v"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        if proc.stderr:
            print(proc.stderr)
        print(f"\n[SAFETY GATE HALTED] {test_path} check failed!")
        print("Pipeline stopped before building results.")
        sys.exit(1)

    print(proc.stdout.strip())
    print("\n[SAFETY GATE PASSED] All safety checks passed successfully.")


# Standalone CSS stylesheet embedded into the generated `index.html` briefing page.
BRIEFING_CSS: str = """    :root {
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
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: var(--bg);
      color: var(--text);
      line-height: 1.6;
      padding: 2.5rem 1rem;
    }
    .container {
      max-width: 900px;
      margin: 0 auto;
    }
    header {
      margin-bottom: 2.5rem;
      border-bottom: 1px solid var(--border);
      padding-bottom: 1.5rem;
    }
    h1 {
      font-size: 2.25rem;
      font-weight: 800;
      color: #0f172a;
      letter-spacing: -0.025em;
      margin-bottom: 0.5rem;
    }
    .subtitle {
      color: var(--text-muted);
      font-size: 1.05rem;
      margin-bottom: 1.25rem;
    }
    .meta-bar {
      display: flex;
      flex-wrap: wrap;
      gap: 1.5rem;
      font-size: 0.9rem;
      color: var(--text-muted);
      background: #f1f5f9;
      padding: 0.75rem 1rem;
      border-radius: 6px;
    }
    .claim-card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1.5rem;
      margin-bottom: 1.5rem;
      box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .claim-card.badge-confirmed {
      border-left: 5px solid #10b981;
    }
    .claim-card.badge-rejected {
      border-left: 5px solid #ef4444;
    }
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1rem;
    }
    .claim-id {
      font-weight: 700;
      font-size: 0.95rem;
      color: var(--text-muted);
      letter-spacing: 0.05em;
    }
    .badge {
      display: inline-block;
      padding: 0.25rem 0.75rem;
      border-radius: 9999px;
      font-size: 0.85rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .badge-confirmed {
      background: var(--confirmed-bg);
      color: var(--confirmed-text);
      border: 1px solid var(--confirmed-border);
    }
    .badge-rejected {
      background: var(--rejected-bg);
      color: var(--rejected-text);
      border: 1px solid var(--rejected-border);
    }
    .claim-text {
      font-size: 1.15rem;
      font-weight: 600;
      color: #1e293b;
      margin-bottom: 1rem;
    }
    .evidence {
      background: #f8fafc;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 1rem;
      margin-bottom: 1rem;
      font-size: 0.95rem;
    }
    .evidence-row {
      margin-bottom: 0.5rem;
      word-break: break-all;
    }
    .evidence-row a {
      color: var(--link);
      text-decoration: none;
    }
    .evidence-row a:hover {
      text-decoration: underline;
    }
    .quote-box {
      margin-top: 0.5rem;
      border-left: 3px solid #0ea5e9;
      padding-left: 0.75rem;
      color: #334155;
      font-style: italic;
    }
    .reason-box {
      color: #991b1b;
    }
    .card-footer {
      display: flex;
      justify-content: flex-end;
      padding-top: 0.75rem;
      border-top: 1px solid #f1f5f9;
    }
    .trace-link {
      color: var(--link);
      font-size: 0.9rem;
      font-weight: 500;
      text-decoration: none;
    }
    .trace-link:hover {
      text-decoration: underline;
    }"""


def _has_contradicting_aws_quote(url: str, quote: str) -> bool:
    """Return True when a rejected item examined a real AWS page and extracted a quote."""
    if not url or DEFAULT_AWS_DOMAIN not in url:
        return False
    return bool(quote) and quote != NO_MATCH


def _render_evidence_html(item: Mapping[str, Any], is_confirmed: bool) -> str:
    """Render either the supporting quote block or the rejection explanation block."""
    if is_confirmed:
        url = item["source_url"]
        quote = item["quote"]
        return f"""
            <div class="evidence">
              <div class="evidence-row"><strong>Official Source:</strong> <a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a></div>
              <div class="quote-box"><blockquote>&ldquo;{quote}&rdquo;</blockquote></div>
            </div>
            """

    reason = item["reason"]
    url = str(item.get("source_url") or "")
    quote = str(item.get("quote") or "")
    evidence_rows = [f'<div class="reason-box"><strong>Rejection Reason:</strong> {reason}</div>']

    if _has_contradicting_aws_quote(url, quote):
        evidence_rows.append(
            f'<div class="evidence-row" style="margin-top: 0.75rem;"><strong>Examined Official Page:</strong> '
            f'<a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a></div>'
        )
        evidence_rows.append(
            '<div class="quote-box" style="border-left-color: #ef4444; color: #7f1d1d; background: #fef2f2; '
            'padding: 0.5rem 0.75rem; border-radius: 4px; margin-top: 0.5rem;">'
            f"<strong>Contradicting Quote from Page:</strong><blockquote>&ldquo;{quote}&rdquo;</blockquote></div>"
        )

    return f"""
            <div class="evidence">
              {"".join(evidence_rows)}
            </div>
            """


def _render_claim_card(item: Mapping[str, Any]) -> str:
    """Render one `<article>` card for a confirmed or rejected candidate claim."""
    is_confirmed = item["verdict"] == VERDICT_CONFIRMED
    badge_class = "badge-confirmed" if is_confirmed else "badge-rejected"
    badge_text = "Confirmed" if is_confirmed else "Rejected"
    detail_html = _render_evidence_html(item, is_confirmed)

    return f"""
        <article class="claim-card {badge_class}">
          <div class="card-header">
            <span class="claim-id">{item["candidate_id"]}</span>
            <span class="badge {badge_class}">{badge_text}</span>
          </div>
          <p class="claim-text">{item["claim_text"]}</p>
          {detail_html}
          <div class="card-footer">
            <a href="{item["trace_link"]}" target="_blank" rel="noopener noreferrer" class="trace-link">View Public LangSmith Trace &rarr;</a>
          </div>
        </article>
        """


def build_briefing_html(
    evaluated_items: Sequence[Mapping[str, Any]],
    confirmed: Sequence[Mapping[str, Any]],
    rejected: Sequence[Mapping[str, Any]],
    output_path: str = BRIEFING_HTML,
) -> None:
    """Build the standalone Morning Briefing HTML page for Solutions Architects."""
    items_rendered = "\n".join(_render_claim_card(item) for item in evaluated_items)
    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AWS re:Invent 2025 Morning Briefing</title>
  <style>
{BRIEFING_CSS}
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
        <span><strong>Safety Gate:</strong> Passed ({SAFETY_TEST_PATH})</span>
      </div>
    </header>
    <main>
      {items_rendered}
    </main>
  </div>
</body>
</html>
"""
    Path(output_path).write_text(full_html, encoding=UTF8_ENCODING)
    print(f"built {output_path}")


def print_split_lists(
    confirmed: Sequence[Mapping[str, Any]],
    rejected: Sequence[Mapping[str, Any]],
) -> None:
    """Print human-readable Confirmed and Rejected summaries to stdout."""
    print("\n================== CONFIRMED LIST ==================")
    for item in confirmed:
        print(f"[{item['candidate_id']}] {item['claim']}")
        print(f"  Source URL: {item['source_url']}")
        print(f"  Quote:      {item['quote']}")
        print(f"  Trace Link: {item['trace_link']}")
        print()

    print("================== REJECTED LIST ==================")
    for item in rejected:
        print(f"[{item['candidate_id']}] {item['claim']}")
        print(f"  Source URL: {item['source_url']}")
        print(f"  Reason:     {item['reason']}")
        print(f"  Trace Link: {item['trace_link']}")
        print()


# ---------------------------------------------------------------------------
# CLI entry point & batch orchestration
# ---------------------------------------------------------------------------
def _load_candidate_rows(
    selected_ids: Sequence[str],
    candidates_path: str = CANDIDATES,
) -> list[dict[str, str]]:
    """Read candidate rows from CSV, optionally filtering to `selected_ids`."""
    id_filter = set(selected_ids)
    with Path(candidates_path).open(encoding=UTF8_ENCODING, newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        if not id_filter:
            return list(reader)
        return [row for row in reader if row["candidate_id"] in id_filter]


def _evaluate_single_candidate(row: dict[str, str]) -> tuple[dict[str, Any], str]:
    """Run the verification graph for `row`, share its trace, and build its result record."""
    candidate_id = row["candidate_id"]
    print(f"== {candidate_id} ==")

    outcome = run_candidate(row)
    trace_url = share(RUNS[candidate_id])

    matched_page = outcome.get("page")
    page_id = matched_page["page_id"] if matched_page else NO_MATCH
    source_url = matched_page["url"] if matched_page else row.get("origin_source_url", NO_MATCH)
    quote = outcome.get("quote") or NO_MATCH
    verdict = outcome.get("verdict", VERDICT_REJECTED)
    reason = outcome.get("reason", "")

    evaluated_item: dict[str, Any] = {
        "candidate_id": candidate_id,
        "claim_text": row["claim_text"],
        "source_url": source_url,
        "quote": quote,
        "verdict": verdict,
        "reason": reason,
        "trace_link": trace_url,
    }

    markdown_cells = [
        candidate_id,
        row["claim_text"],
        page_id,
        quote,
        *outcome["answers"],
        verdict,
        reason,
        f"[trace]({trace_url})",
    ]
    markdown_row = "| " + " | ".join(cell(value) for value in markdown_cells) + " |"
    print(f"  -> {verdict}: {reason}")
    return evaluated_item, markdown_row


def main(ids: Sequence[str]) -> None:
    """Evaluate candidate claims, enforce the safety gate, and build briefing artifacts."""
    rows = _load_candidate_rows(ids)
    markdown_lines = [
        "# Results",
        "",
        "| Candidate | Claim | Page | Quote | Check 1 | Check 2 | Check 3 | Verdict | Reason | Trace |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]

    evaluated_items: list[dict[str, Any]] = []
    for row in rows:
        item, md_row = _evaluate_single_candidate(row)
        evaluated_items.append(item)
        markdown_lines.append(md_row)
        time.sleep(LLM_RETRY_DELAY_SECONDS)

    # Partition evaluated claims into confirmed and rejected lists.
    confirmed, rejected = lead_split_results(evaluated_items)
    save_split_results(confirmed, rejected)

    # Halt immediately before building output artifacts if any safety invariant fails.
    run_safety_gate()

    # Build the live HTML briefing page and Markdown results summary.
    build_briefing_html(evaluated_items, confirmed, rejected)
    if not ids:
        results_path = Path(RESULTS)
        results_path.parent.mkdir(parents=True, exist_ok=True)
        results_path.write_text("\n".join(markdown_lines) + "\n", encoding=UTF8_ENCODING)
        print(f"wrote {RESULTS}")

    print_split_lists(confirmed, rejected)


if __name__ == "__main__":
    main(sys.argv[1:])
