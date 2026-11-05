import json

import pytest
from fakes_pipeline import ScriptedSandboxRunner
from pod.config import Settings
from pod.demo_script import demo_llm
from pod.pipeline.broadcast import Broadcaster
from pod.pipeline.export import digest
from pod.pipeline.gaps import apply_gap
from pod.pipeline.run import execute_run
from pod.pipeline.summary import build_summary
from pod.schemas import GapTestResult, ItemVerdict
from pod.store import Store


@pytest.mark.asyncio
async def test_full_fake_run(tmp_path):
    settings = Settings(data_dir=tmp_path)
    store = Store(tmp_path / 'db')
    store.create_run('r', 'demo:refundly', status='queued')
    xml = ('<testsuite><testcase file="tests/test_refunds.py" classname="tests.test_refunds" '
           'name="test_audit_record_exists"/></testsuite>')
    gap_xml = ('<testsuite><testcase file="tests/test_pod_req_03.py" classname="tests.test_pod_req_03" '
               'name="test_audit_fields"/></testsuite>')
    runner = ScriptedSandboxRunner(lambda spec: ('passed', gap_xml if spec.purpose.startswith('gap_test:') else xml, []))
    state = await execute_run('r', 'demo:refundly', settings=settings, llm=demo_llm(),
                              sandbox=runner, store=store, broadcaster=Broadcaster())
    summary = build_summary(store, 'r')
    assert summary['status'] == 'awaiting_approval', (state.errors, summary)
    assert all(stage['status'] in ('done', 'skipped') for stage in summary['stages'])
    assert store.verify_chain('r')
    assert any(e['type'] == 'approval_needed' for e in store.list_events('r'))
    assert state.gap_results['REQ-03'].passes_on_original
    receipt = json.loads(store.get_artifact('r', 'receipts.json')['content'])
    assert receipt.pop('manifest_sha256') == digest(receipt)


def test_apply_gap_branches():
    verdict = ItemVerdict(item_id='REQ-01', kind='requirement', verdict='WEAK', mutations_killed=2)
    result = GapTestResult(item_id='REQ-01', file='tests/test_pod_req_01.py',
                           passes_on_original=True, kills_survivors=['M1'], approved=True)
    # A bare verdict/result cannot establish proof: the endpoint requires the bound context gate.
    assert apply_gap(verdict, result).verdict == 'WEAK'
    assert apply_gap(verdict, result.model_copy(update={'approved': False})).verdict == 'WEAK'
    assert apply_gap(verdict, result.model_copy(update={'reveals_violation': True})).verdict == 'WEAK'
