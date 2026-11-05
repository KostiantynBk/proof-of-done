import json

import pytest
from pod.schemas import AuditClaim, CodeCitation, HistoryRecord, MinedRule, RuleQuote, TestCitation
from pod.verify.code_citations import verify_code_citation
from pod.verify.evidence import verify_claim
from pod.verify.history import (
    accept_rule,
    parse_ci_log,
    parse_git_log,
    parse_reviews_json,
    verify_record_quote,
)
from pod.verify.normalize import normalize_prose, numbers_in, strip_code_comment
from pod.verify.snapshot import Snapshot, is_test_file
from pod.verify.spec_quotes import verify_spec_quote
from pod.verify.test_citations import index_tests, verify_test_citation


def test_normalization_and_quotes():
    assert normalize_prose("“ﬁle”—ﬂow′") == '"file"-flow\''
    assert numbers_in("90 3 0.01 5,000.00")["5000.00"] == 1
    assert strip_code_comment('x = "# not comment" # comment') == 'x = "# not comment"'
    assert strip_code_comment('x = """# inside""" # trailing') == 'x = """# inside"""'
    assert strip_code_comment('x = "a\\"#inside" # comment') == 'x = "a\\"#inside"'
    spec = "# Heading\nThe ﬁle uses “quotes” in this example.\n"
    assert verify_spec_quote('The file uses "quotes" in this example.', spec).match == "normalized"
    assert verify_spec_quote("The ﬁle uses “quotes” in this example.", spec).locator == "# Heading"
    assert verify_spec_quote("The file ... this example is abbreviated", spec).match == "abridged"
    assert (
        verify_spec_quote(
            "Use ... to represent an omitted Python expression.",
            "Use ... to represent an omitted Python expression.",
        ).match
        == "exact"
    )
    assert (
        verify_spec_quote(
            "The wait is 90 days before approval.",
            "The wait is 90 days before approval.",
            statement="Wait 100 days",
        ).match
        == "number_mismatch"
    )
    assert verify_spec_quote("tiny", spec).match == "too_short"


def test_snapshot_and_code(tmp_path):
    content = "\n".join(
        [
            "long_call(value=12345)",
            "result = compute(",
            "    item,",
            "    value,",
            ")",
            "alpha = do_work(value)",
            "# in between",
            "",
            "beta = more_work(value)",
            "ellipsis_value = ...",
            *[""] * 20,
            "long_call(value=12345)",
        ]
    )
    (tmp_path / "src.py").write_text(content)
    (tmp_path / ".env").write_text("secret")
    snap = Snapshot(tmp_path)
    assert ".env" not in snap.files
    assert len(snap.tree_sha256()) == 64
    for bad in ("../other", "/absolute", "C:\\foo", "\\\\server\\x", ".env", "~/.env", "a\x00b"):
        assert snap.safe_path(bad) is None
    assert is_test_file("a/tests/foo.py") and is_test_file("a/foo_test.py")
    cases = [
        (1, "long_call(value=12345)", "exact", 1),
        (15, "long_call(value=12345)", "ambiguous", None),
        (3, "long_call(value=12345)", "relocated", 1),
        (1, "not_a_real_call(value)", "not_found", None),
        (2, "result = compute(item, value)", "exact", 2),
        (6, "alpha = do_work(value)\nbeta = more_work(value)", "exact", 6),
        (1, "# only a comment", "too_short", None),
        (1, "x = 1", "too_short", None),
        (10, "ellipsis_value = ...", "exact", 10),
    ]
    for line, snippet, match, actual in cases:
        result = verify_code_citation(CodeCitation(file="src.py", line=line, snippet=snippet), snap)
        assert (result.match, result.actual_line) == (match, actual)
        assert "actual_snippet" not in result.model_dump()
    assert (
        verify_code_citation(
            CodeCitation(file="missing.py", line=1, snippet="long_enough_call()"), snap
        ).match
        == "missing_file"
    )
    assert (
        verify_code_citation(
            CodeCitation(file="../evil", line=1, snippet="long_enough_call()"), snap
        ).match
        == "invalid_path"
    )


def test_symlink_escape(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.write_text("private source")
    try:
        (tmp_path / "escape.py").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    assert Snapshot(tmp_path).safe_path("escape.py") is None


def test_history_independence_and_parsers(tmp_path):
    text = "Always use approved helpers for monetary calculations."
    records = {
        f"review:{n}": HistoryRecord(
            record_id=f"review:{n}", kind="review", pr=pr, author=f"person{n}", text=text
        )
        for n, pr in [(1, 1), (2, 1), (3, 2)]
    }

    def rule(ids, title="Use approved monetary calculation helpers"):
        return MinedRule(
            id="RULE-01",
            rule=title,
            rationale="test",
            quotes=[RuleQuote(record_id=f"review:{n}", quote=text) for n in ids],
        )

    assert not accept_rule(rule([1, 2]), records).accepted
    accepted = accept_rule(rule([1, 3]), records)
    assert accepted.accepted and accepted.distinct_prs == 2 and accepted.recurrence == 2
    assert (
        accept_rule(rule([1, 3], "Use descriptive names"), records).drop_reason == "generic_advice"
    )
    assert verify_record_quote(text, "unknown", records).match == "wrong_record"
    assert verify_record_quote(text + " " + text, "review:1", records).match == "not_found"
    reviews = tmp_path / "reviews.json"
    reviews.write_text(json.dumps([{"id": 4, "pr": 3, "body": text}]))
    assert parse_reviews_json(reviews)[0].record_id == "review:4"
    log = tmp_path / "git.txt"
    log.write_text("commit abcdef123456\n    Implement something (#123)\n")
    assert parse_git_log(log)[0].pr == 123
    ci = tmp_path / "ci.txt"
    ci.write_text("\n".join(str(x) for x in range(45)))
    assert [r.record_id for r in parse_ci_log(ci)] == [
        "ci:ci.txt#L1-L20",
        "ci:ci.txt#L21-L40",
        "ci:ci.txt#L41-L45",
    ]


def test_test_citations_and_claim(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text(
        "def test_a(): pass\nclass TestGroup:\n    def test_b(self): pass\n"
    )
    (tmp_path / "src.py").write_text("value = calculate()\n")
    snap = Snapshot(tmp_path)
    index = index_tests(snap)

    def case(node, outcome):
        return {
            "nodeid": "tests/test_a.py::" + node,
            "file": "tests/test_a.py",
            "name": node,
            "outcome": outcome,
            "message": "",
        }

    run = [
        case("test_a[1]", "passed"),
        case("test_a[2]", "failed"),
        case("TestGroup::test_b", "passed"),
    ]
    for name in ["TestGroup::test_b", "TestGroup.test_b", "tests/test_a.py::TestGroup::test_b"]:
        check = verify_test_citation(
            TestCitation(file="tests/test_a.py", test_name=name), snap, index, [run, run]
        )
        assert check.exists and check.passed and not check.flaky
    c = TestCitation(file="tests/test_a.py", test_name="test_a")
    assert verify_test_citation(c, snap, index, [run, run]).passed is False
    flaky_run = [case("test_a[1]", "failed"), *run[1:]]
    assert verify_test_citation(c, snap, index, [run, flaky_run]).flaky
    assert verify_test_citation(
        TestCitation(file="tests/test_a.py", test_name="test_a[1]"), snap, index, [run, run]
    ).passed
    assert verify_test_citation(c, snap, index, []).passed is None
    claim = AuditClaim(
        item_id="REQ-1",
        ai_status="covered",
        reason="claimed",
        code_evidence=[CodeCitation(file="src.py", line=1, snippet="value = calculate()")],
        test_evidence=[c],
    )
    original = claim.model_dump()
    report = verify_claim(claim, snap, index, [run, run])
    assert report.has_verified_code and not report.guarding_tests and claim.model_dump() == original
