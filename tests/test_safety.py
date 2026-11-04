import json
import os
from urllib.parse import urlparse
import pytest

SPLIT_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "split_results.json")


def load_split_results():
    if not os.path.exists(SPLIT_FILE):
        pytest.fail(f"Split results file not found: {SPLIT_FILE}. Run pipeline.py first.")
    with open(SPLIT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def test_confirmed_items_have_aws_url_and_quote():
    data = load_split_results()
    confirmed = data.get("confirmed", [])
    for item in confirmed:
        cid = item.get("candidate_id", "unknown")
        url = item.get("source_url") or item.get("url") or ""
        host = urlparse(url).hostname or ""
        assert host == "aws.amazon.com" or host.endswith(".aws.amazon.com"), (
            f"Confirmed item {cid} lacks an aws.amazon.com URL: got '{url}'"
        )
        quote = item.get("quote") or ""
        assert quote.strip() and quote != "no match", (
            f"Confirmed item {cid} lacks a valid quote: got '{quote}'"
        )


def test_rejected_items_have_reason():
    data = load_split_results()
    rejected = data.get("rejected", [])
    for item in rejected:
        cid = item.get("candidate_id", "unknown")
        reason = item.get("reason") or ""
        assert reason.strip(), (
            f"Rejected item {cid} has no stated reason."
        )
