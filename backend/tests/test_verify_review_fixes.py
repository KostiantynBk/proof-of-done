"""Regression tests for the independent T3 verifier review."""

import pytest
from pod.schemas import CodeCitation, HistoryRecord, MinedRule, RuleQuote, TestCitation
from pod.verify.code_citations import verify_code_citation
from pod.verify.history import accept_rule, is_generic_advice
from pod.verify.normalize import normalize_prose, numbers_in
from pod.verify.snapshot import Snapshot
from pod.verify.spec_quotes import verify_spec_quote
from pod.verify.test_citations import index_tests, verify_test_citation


@pytest.mark.parametrize(
    ("file", "source", "snippet", "expected"),
    [
        (
            "app.py",
            '"""\nrefund_total = guarded_refund(amount)\n"""\nvalue = 3\n',
            "refund_total = guarded_refund(amount)",
            False,
        ),
        (
            "app.py",
            'example = "refund_total = guarded_refund(amount)"\n',
            "refund_total = guarded_refund(amount)",
            False,
        ),
        (
            "README.md",
            "refund_total = guarded_refund(amount)\n",
            "refund_total = guarded_refund(amount)",
            False,
        ),
        (
            "tests/test_app.py",
            "assert guarded_refund(amount) == 1\n",
            "assert guarded_refund(amount) == 1",
            False,
        ),
        ("app.py", 'def f():\n    return "no  refunds"\n', 'return "no refunds"', False),
        ("app.py", "first = call()\nsecond = call()\n", "first = call() second = call()", False),
        (
            "app.py",
            'result = call(\n    "# keep",\n    other,\n)\n',
            'result = call("# keep", other)',
            True,
        ),
        (
            "app.py",
            'value = """# not a comment\ninside"""\n',
            'value = """# not a comment\ninside"""',
            True,
        ),
        ("app.py", 'value = "a\\"#inside" # actual comment\n', 'value = "a\\"#inside"', True),
        ("app.py", "value = ...\n", "value = ...", True),
    ],
)
def test_implementation_only(tmp_path, file, source, snippet, expected):
    path = tmp_path / file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    result = verify_code_citation(
        CodeCitation(file=file, line=1, snippet=snippet), Snapshot(tmp_path)
    )
    assert result.verified is expected
    assert "actual_snippet" not in result.model_dump()


@pytest.mark.parametrize(
    ("quote", "statement", "valid"),
    [
        ("The permitted balance floor is -90 units.", "Balance floor is 90 units.", False),
        ("The permitted balance floor is 90 units.", "Balance floor is -90 units.", False),
        ("The permitted balance floor is ９０ units.", "Balance floor is 90 units.", True),
        ("The limit is 5,000.00 units for each account.", "The limit is 5000.00 units.", True),
        ("Thresholds are 90 and 90 for these accounts.", "Thresholds are 90 and 90.", True),
        ("The permitted balance floor is 90 units.", "Balance floor is 100 units.", False),
    ],
)
def test_number_fidelity(quote, statement, valid):
    assert verify_spec_quote(quote, quote, statement=statement).verified is valid


@pytest.mark.parametrize(
    ("text", "generic"),
    [
        ("Use descriptive\nnames", True),
        ("Keep functions small.", True),
        ("Add comments", True),
        ("Do not add comments containing customer secrets", False),
        ("Use descriptive names for customer-facing API fields", False),
    ],
)
def test_generic_advice_templates(text, generic):
    assert is_generic_advice(text) is generic


def test_unicode_normalization_and_multisets():
    assert normalize_prose("９０ ″wide″") == '90 "wide"'
    assert numbers_in("-90 +90 ９０ 5,000.00") == {"-90": 1, "90": 2, "5000.00": 1}
    assert not verify_spec_quote(
        "Limit 90 and 90 units apply here.",
        "Limit 90 and 90 units apply here.",
        statement="Use 90, 90 and 90 units.",
    ).verified


def test_in_root_symlinks_denied(tmp_path):
    (tmp_path / ".env").write_text("not a real secret")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_app.py").write_text("assert True")
    try:
        (tmp_path / "alias.py").symlink_to(tmp_path / ".env")
        (tmp_path / "other.py").symlink_to(tmp_path / "tests/test_app.py")
    except OSError:
        pytest.skip("Windows did not permit symlink creation")
    snap = Snapshot(tmp_path)
    assert snap.safe_path("alias.py") is None and snap.safe_path("other.py") is None
    assert "alias.py" not in snap.files and "other.py" not in snap.files


@pytest.mark.parametrize(
    ("kinds", "prs", "accepted", "units"),
    [
        (("ci", "ci"), (None, None), False, 0),
        (("review", "review"), (None, None), False, 0),
        (("commit", "commit"), (None, None), True, 2),
        (("review", "commit"), (10, 10), False, 1),
        (("review", "commit"), (10, 11), True, 2),
    ],
)
def test_independent_units(kinds, prs, accepted, units):
    text = "Always use approved helpers for monetary calculations."
    records = {
        str(i): HistoryRecord(record_id=str(i), kind=kind, pr=prs[i], text=text)
        for i, kind in enumerate(kinds)
    }
    rule = MinedRule(
        id="RULE-1",
        rule="Use monetary calculation helpers",
        rationale="test",
        quotes=[RuleQuote(record_id=str(i), quote=text) for i in range(2)],
    )
    item = accept_rule(rule, records)
    assert item.accepted is accepted
    assert item.distinct_prs == units


@pytest.mark.parametrize(
    ("runs", "expected", "flaky"),
    [
        ([], None, False),
        ([["passed"]], False, False),
        ([["passed"], ["passed"]], True, False),
        ([["passed"], ["failed"]], False, True),
        ([["failed"], ["passed"]], False, True),
        ([["skipped"], ["skipped"]], False, False),
        ([["failed"], ["failed"]], False, False),
        ([["passed"], []], False, False),
    ],
)
def test_baseline_gate_and_nested_parameter_identity(tmp_path, runs, expected, flaky):
    path = tmp_path / "tests/test_identity.py"
    path.parent.mkdir()
    path.write_text("class RefundTests:\n    class Nested:\n        def test_pay(self): pass\n")
    snap = Snapshot(tmp_path)
    node = "tests/test_identity.py::RefundTests::Nested::test_pay[a::b.c/[x]]"
    baselines = [[{"nodeid": node, "outcome": outcome} for outcome in run] for run in runs]
    citation = TestCitation(
        file="tests\\test_identity.py", test_name="RefundTests::Nested::test_pay[a::b.c/[x]]"
    )
    check = verify_test_citation(citation, snap, index_tests(snap), baselines)
    assert check.exists and check.passed is expected and check.flaky is flaky
    assert check.nodeids == ([node] if any(runs) else [])


def test_duplicate_case_report_is_not_trusted(tmp_path):
    (tmp_path / "test_a.py").write_text("def test_a(): pass\n")
    snap = Snapshot(tmp_path)
    case = {"nodeid": "test_a.py::test_a", "outcome": "passed"}
    check = verify_test_citation(
        TestCitation(file="test_a.py", test_name="test_a"),
        snap,
        index_tests(snap),
        [[case, case], [case]],
    )
    assert check.passed is False
