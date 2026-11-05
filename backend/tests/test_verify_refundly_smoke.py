import json
import re
from pathlib import Path

import pytest
from pod.schemas import MinedRule, RuleQuote
from pod.verify.history import accept_rule, parse_ci_log, parse_git_log, parse_reviews_json
from pod.verify.spec_quotes import verify_spec_quote

ROOT = Path(__file__).resolve().parents[2] / "samples" / "refundly"


def test_refundly_ground_truth_anchors():
    if not (ROOT / "ground_truth.json").exists():
        pytest.skip("sample not yet available")
    ground = json.loads((ROOT / "ground_truth.json").read_text(encoding="utf-8"))
    spec = (ROOT / "SPEC-v2.md").read_text(encoding="utf-8")
    sentences = re.findall(r"(?m)^## Requirement [^\n]+\n([^\n]+)", spec)
    assert len(sentences) == 8
    for sentence in sentences:
        assert verify_spec_quote(sentence, spec).match == "exact"
    for item in ground:
        if item["kind"] == "requirement":
            assert verify_spec_quote(item["anchor"], spec).match == "exact", item["key"]
    reviews = parse_reviews_json(ROOT / "history" / "reviews.json")
    records = reviews + parse_git_log(ROOT / "history" / "git-log.txt")
    for file in (ROOT / "history" / "ci").glob("*.txt"):
        records.extend(parse_ci_log(file))
    index = {r.record_id: r for r in records}
    partners = {
        "RULE-money": "review-002",
        "RULE-clock": "review-005",
        "RULE-audit": "review-007",
        "RULE-errors": "review-009",
    }
    rules = [item for item in ground if item["kind"] == "rule"]
    assert len(rules) == 4
    for item in rules:
        first = next(r for r in reviews if r.text == item["anchor"])
        second = index["review:" + partners[item["key"]]]
        rule = MinedRule(
            id=item["key"],
            rule=first.text,
            rationale="sample",
            quotes=[RuleQuote(record_id=r.record_id, quote=r.text) for r in (first, second)],
        )
        assert accept_rule(rule, index).accepted, item["key"]
