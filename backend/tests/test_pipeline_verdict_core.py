from pathlib import Path

import pytest
from pod.pipeline.render import render_files
from pod.pipeline.sabotage import validate_mutation
from pod.pipeline.verdict import compute_verdict
from pod.schemas import (
    AuditClaim,
    CodeCitation,
    CodeCitationCheck,
    EvidenceReport,
    Item,
    Mutation,
    MutationResult,
    TestCitation,
    TestCitationCheck,
)
from pod.verify.snapshot import Snapshot

ROOT = Path(__file__).resolve().parents[2] / 'samples' / 'refundly'


def item(verified=True):
    return Item(id='REQ-01', kind='requirement', title='cap', statement='cap', source_checks=[],
                source_verified=verified)


def evidence(*, passed=True, flaky=False, code=True):
    citation = CodeCitation(file='refundly/refunds.py', line=36, snippet='if amount <= Decimal("0.00"):')
    test = TestCitation(file='tests/test_refunds.py', test_name='test_cap_allows_exact_remaining')
    return EvidenceReport(item_id='REQ-01', claim=AuditClaim(item_id='REQ-01', ai_status='covered',
        reason='claim'), code_checks=[CodeCitationCheck(citation=citation, match='exact', verified=code)],
        test_checks=[TestCitationCheck(citation=test, exists=True, passed=passed, flaky=flaky,
                                      nodeids=['tests/test_refunds.py::test_cap_allows_exact_remaining'])])


def result(n, outcome):
    return MutationResult(mutation_id=f'REQ-01-T{n}', item_id='REQ-01', origin='targeted', outcome=outcome)


@pytest.mark.parametrize(('verified', 'ev', 'outcomes', 'baseline', 'expected'), [
    (False, evidence(), [], True, 'UNVERIFIED'),
    (True, None, [], True, 'UNTESTED'),
    (True, evidence(flaky=True), ['killed'] * 3, True, 'UNTESTED'),
    (True, evidence(code=False), ['killed'] * 3, True, 'UNTESTED'),
    (True, evidence(), ['killed'] * 2, True, 'INCONCLUSIVE'),
    (True, evidence(), ['killed'] * 3, True, 'PROVEN'),
    (True, evidence(), ['killed'] * 5 + ['survived'], True, 'WEAK'),
    (True, evidence(), ['killed'] * 3, False, 'INCONCLUSIVE'),
    (True, evidence(), ['killed'] * 3 + ['timeout'], True, 'PROVEN'),  # timeout unscored, not a kill
    (True, evidence(), ['killed'] * 2 + ['timeout', 'invalid'], True, 'INCONCLUSIVE'),  # only 2 kills
    (True, evidence(), ['killed'] * 3 + ['invalid', 'survived'], True, 'WEAK'),
])
def test_ladder(verified, ev, outcomes, baseline, expected):
    run = [{'nodeid': 'tests/test_refunds.py::test_cap_allows_exact_remaining', 'outcome': 'passed'}]
    verdict = compute_verdict(item(verified), ev, [result(i, x) for i, x in enumerate(outcomes)], baseline, [run, run])
    assert verdict.verdict == expected


def test_violated_takes_precedence():
    node = 'tests/test_refunds.py::test_cap_allows_exact_remaining'
    run = [{'nodeid': node, 'outcome': 'failed'}]
    assert compute_verdict(item(), evidence(passed=False), [], False, [run, run]).verdict == 'VIOLATED'


def mutation(file, find, replace):
    return Mutation(id='REQ-01-T1', item_id='REQ-01', origin='targeted', file=file, find=find,
                    replace=replace, description='test')


def test_validation_cases():
    snap = Snapshot(ROOT)
    file = 'refundly/refunds.py'
    find = 'if amount <= Decimal("0.00"): '
    find = find.rstrip()
    seen = set()
    valid = mutation(file, find, 'if amount < Decimal("0.00"):')
    assert validate_mutation(snap, valid, seen).valid
    assert validate_mutation(snap, valid, seen).reject_reason == 'duplicate'
    cases = [
        (mutation('tests/test_refunds.py', 'def', 'no'), 'test_file'),
        (mutation('tests/conftest.py', 'def', 'no'), 'test_file'),
        (mutation('../bad.py', 'a', 'b'), 'invalid_path'),
        (mutation(file, 'amount', 'x'), 'find_not_unique'),
        (mutation(file, 'absent-string', 'x'), 'find_not_found'),
        (mutation(file, find, find), 'unchanged'),
        (mutation(file, find, 'if :'), 'syntax_error'),
        (mutation(file, '"""Refund orchestration for a single in-memory service instance."""',
                  '"""Refund orchestration for a single in-memory service instance.""" # hi'),
         'comment_or_whitespace_only'),
    ]
    for candidate, expected in cases:
        assert validate_mutation(snap, candidate, set()).reject_reason == expected


def test_render_blind_and_lines():
    snap = Snapshot(ROOT)
    rendered = render_files(snap, include_tests=False)
    assert '=== tests/' not in rendered and 'conftest.py' not in rendered
    assert '   1 | """Refund orchestration' in rendered
    assert '=== pytest.ini ===' in rendered
    assert '[... file truncated ...]' in render_files(snap, include_tests=False, max_chars=1000)
