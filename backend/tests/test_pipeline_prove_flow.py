from pathlib import Path

import pytest
from fakes_pipeline import ScriptedSandboxRunner
from pod.config import Settings
from pod.llm import FakeLLM
from pod.pipeline.audit import audit_items
from pod.pipeline.baseline import run_baseline
from pod.pipeline.context import RunContext
from pod.pipeline.prove import prove_mutations
from pod.pipeline.sabotage import sabotage_items
from pod.pipeline.verdict import judge
from pod.sandbox.junit import parse_junit
from pod.sandbox.models import TelemetryEvent
from pod.schemas import Item, Mutation
from pod.store import Store
from pod.verify.snapshot import Snapshot

ROOT = Path(__file__).resolve().parents[2] / 'samples' / 'refundly'
NODE = 'test_cap_allows_exact_remaining'
XML_PASS = (f'<testsuite><testcase file="tests/test_refunds.py" '
            f'classname="tests.test_refunds" name="{NODE}"/></testsuite>')
XML_FAIL = (f'<testsuite><testcase file="tests/test_refunds.py" '
            f'classname="tests.test_refunds" name="{NODE}"><failure/></testcase></testsuite>')


def make_ctx(tmp_path, script, llm=None):
    return RunContext(run_id='test', project_dir=ROOT, spec_text='', history=[], settings=Settings(),
                      llm=llm or FakeLLM(), sandbox=ScriptedSandboxRunner(script),
                      store=Store(tmp_path / 'db.sqlite'))


def claim(snippet='if amount <= Decimal("0.00"):'):
    return {'item_id': 'wrong', 'ai_status': 'covered', 'reason': 'test',
            'code_evidence': [{'file': 'refundly/refunds.py', 'line': 36, 'snippet': snippet}],
            'test_evidence': [{'file': 'tests/test_refunds.py', 'test_name': NODE}]}


def proposal(find, replace):
    return {'file': 'refundly/refunds.py', 'find': find, 'replace': replace, 'description': 'break'}


@pytest.mark.asyncio
async def test_end_to_end(tmp_path):
    pairs = [('<= Decimal("0.00")', '< Decimal("0.00")'),
             ('> order.captured - order.refunded', '>= order.captured - order.refunded'),
             ('attempts < 3', 'attempts < 2'),
             ('attempts == 3', 'attempts == 2'),
             ('timedelta(days=90)', 'timedelta(days=91)'),
             ('amount = cents(amount)', 'amount = amount')]
    llm = FakeLLM({'auditor': [claim()], 'saboteur': [
        {'item_id': 'REQ-01', 'mutations': [proposal(*p) for p in pairs[:3]]},
        {'item_id': 'REQ-01', 'mutations': [proposal(*p) for p in pairs[3:]]}]})
    telemetry = TelemetryEvent(type='honeytoken_read', detail='fake canary', ts='now')

    originals = (ROOT / 'refundly' / 'refunds.py').read_text(encoding='utf-8')
    expected = {originals.replace(find, replace, 1) for find, replace in pairs}

    def script(spec):
        if spec.purpose.startswith('baseline'):
            assert not spec.files
            return 'passed', XML_PASS, [telemetry]
        assert spec.files and set(spec.files) == {'refundly/refunds.py'}
        assert spec.files['refundly/refunds.py'] in expected
        assert spec.files['refundly/refunds.py'] != originals
        return 'tests_failed', XML_FAIL, []

    ctx = make_ctx(tmp_path, script, llm)
    ctx.state.requirements = [Item(id='REQ-01', kind='requirement', title='cap', statement='cap',
                                   source_checks=[], source_verified=True)]
    await run_baseline(ctx)
    await audit_items(ctx)
    await sabotage_items(ctx)
    await prove_mutations(ctx)
    await judge(ctx)
    assert ctx.state.evidence['REQ-01'].has_verified_code
    assert ctx.state.evidence['REQ-01'].guarding_tests
    assert len(ctx.state.results) == 5
    assert all(r.killed_by_cited for r in ctx.state.results.values())
    assert ctx.state.verdicts['REQ-01'].verdict == 'PROVEN'
    events = ctx.store.list_events('test')
    assert len([e for e in events if e['type'] == 'containment_event']) == 2
    assert ctx.store.verify_chain('test')
    # A changed current manifest must not inherit earlier successful receipts.
    ctx.state.mutations['REQ-01'][0] = ctx.state.mutations['REQ-01'][0].model_copy(
        update={'replace': 'different'})
    await judge(ctx)
    assert ctx.state.verdicts['REQ-01'].verdict == 'INCONCLUSIVE'
    # Regenerating nothing must not resurrect older IDs or results either.
    ctx.state.mutations['REQ-01'] = []
    await prove_mutations(ctx)
    await judge(ctx)
    assert ctx.state.verdicts['REQ-01'].verdict == 'INCONCLUSIVE'


@pytest.mark.asyncio
@pytest.mark.parametrize(('sandbox_outcome', 'xml', 'expected'), [
    ('passed', XML_PASS, 'survived'), ('timeout_killed', '', 'timeout'),
    ('oom_killed', '', 'invalid'), ('error', '', 'invalid'),
    ('tests_failed', XML_FAIL, 'killed'), ('tests_failed', '', 'invalid'),
    ('tests_failed', '<broken', 'invalid'), ('tests_failed', XML_PASS, 'invalid'),
    ('tests_failed', XML_FAIL.replace(NODE, 'test_ghost'), 'invalid'),
    ('tests_failed', XML_FAIL.replace('<failure/>', '<error message="ImportError"/>'), 'invalid'),
    ('tests_failed', XML_FAIL.replace('<failure/>', '<failure message="Timeout"/>'), 'killed'),
])
async def test_classification(tmp_path, sandbox_outcome, xml, expected):
    ctx = make_ctx(tmp_path, lambda spec: (sandbox_outcome, xml, []))
    m = Mutation(id='REQ-01-T1', item_id='REQ-01', origin='targeted', file='refundly/refunds.py',
                 find='attempts < 3', replace='attempts < 2', description='test', valid=True)
    ctx.state.mutations[m.item_id] = [m]
    ctx.state.tree_sha256 = Snapshot(ROOT).tree_sha256()
    ctx.state.baseline_junit = [parse_junit(XML_PASS), parse_junit(XML_PASS)]
    await prove_mutations(ctx)
    assert ctx.state.results[m.id].outcome == expected


@pytest.mark.asyncio
async def test_fake_citation_is_not_verified(tmp_path):
    ctx = make_ctx(tmp_path, lambda spec: ('passed', XML_PASS, []),
                   FakeLLM({'auditor': [claim('invented citation that does not exist')]}))
    ctx.state.requirements = [Item(id='REQ-01', kind='requirement', title='x', statement='x',
                                   source_checks=[], source_verified=True)]
    await run_baseline(ctx)
    await audit_items(ctx)
    assert not ctx.state.evidence['REQ-01'].has_verified_code
