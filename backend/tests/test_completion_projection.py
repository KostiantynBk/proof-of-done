import copy
import json

import pytest
from pod.config import Settings
from pod.pipeline.context import RunContext, RunState
from pod.pipeline.export import export_all
from pod.pipeline.summary import build_summary, completeness_summary
from pod.schemas import GapTestResult, Item, ItemVerdict, ProposedTest
from pod.store import Store


def make_state():
    items = [Item(id=f'REQ-0{i}', kind='requirement', title='item', statement='requirement',
                  source_checks=[], source_verified=True) for i in range(1, 5)]
    return RunState(requirements=items, verdicts={i.id: ItemVerdict(item_id=i.id, kind=i.kind, verdict='WEAK')
                                                   for i in items})


def test_clean_run_has_no_partial_work():
    projection = completeness_summary(make_state(), [])
    assert projection == {'component_failures': 0, 'skipped_gaps': [], 'guard_decisions': []}


def test_historical_projection_and_redaction(tmp_path):
    store = Store(tmp_path / 'db')
    store.create_run('r', 'demo:example', status='done')
    state = make_state()
    state.errors = ['PRIVATE-RAW-ERROR'] * 3
    store.append_event('r', 'gap_skipped', {'item_id': 'REQ-01', 'reason': 'LLMError'})
    store.append_event('r', 'gap_skipped', {'item_id': 'REQ-02', 'reason': 'six item limit'})
    store.append_event('r', 'gap_skipped', {'item_id': 'REQ-03', 'reason': 'PRIVATE-RAW-REASON'})
    store.append_event('r', 'gap_skipped', {'item_id': 'REQ-99 PRIVATE', 'reason': 'PRIVATE'})
    store.append_event('r', 'stage_finished', {'stage': 'S1', 'status': 'failed', 'error': 'PRIVATE-RAW-ERROR'})
    state.gap_results['REQ-04'] = GapTestResult(item_id='REQ-04', file='tests/test_pod_req_04.py',
                                               passes_on_original=False, reveals_violation=True, approved=False)
    store.put_artifact('r', 'state.json', state.model_dump_json())
    summary = build_summary(store, 'r')
    c = summary['completeness']
    assert c['component_failures'] == 3 and len(c['skipped_gaps']) == 4
    assert [x['reason'] for x in c['skipped_gaps']] == ['model response failed', 'six item limit',
                                                         'other failure', 'other failure']
    assert c['skipped_gaps'][-1]['item_id'] is None
    assert c['guard_decisions'] == [{'item_id': 'REQ-04', 'decision': 'rejected', 'original': 'failed'}]
    assert 'PRIVATE' not in json.dumps(c) + json.dumps(summary['stages'])


def test_t14_like_decision_counts():
    state = make_state()
    for index, item in enumerate(state.items):
        state.gap_results[item.id] = GapTestResult(item_id=item.id, file='tests/x.py',
                                                  passes_on_original=index != 3,
                                                  reveals_violation=index == 3,
                                                  approved=index < 2 if index != 2 else None)
    result = completeness_summary(state, [])['guard_decisions']
    assert [x['decision'] for x in result] == ['approved', 'approved', 'pending', 'rejected']
    assert result[-1]['original'] == 'failed'


def test_six_decisions_and_projection_do_not_mutate_inputs():
    state = make_state()
    state.requirements.extend(
        Item(id=f'REQ-0{i}', kind='requirement', title='item', statement='requirement',
             source_checks=[], source_verified=True) for i in (5, 6)
    )
    for index, item in enumerate(state.items):
        state.gap_results[item.id] = GapTestResult(item_id=item.id, file='tests/x.py',
                                                  passes_on_original=index != 5,
                                                  reveals_violation=index == 5, approved=index < 2)
    events = [{'type': 'gap_skipped', 'payload': {'item_id': 'REQ-05', 'reason': 'six item limit'}}]
    original_state, original_events = state.model_dump(mode='json'), copy.deepcopy(events)
    decisions = completeness_summary(state, events)['guard_decisions']
    assert [sum(d['decision'] == kind for d in decisions) for kind in ('approved', 'rejected', 'pending')] == [2, 4, 0]
    assert state.model_dump(mode='json') == original_state
    assert events == original_events


@pytest.mark.asyncio
async def test_clean_report_does_not_assert_guard_failed(tmp_path):
    store = Store(tmp_path / 'db')
    store.create_run('r', 'demo:example', status='done')
    ctx = RunContext('r', tmp_path / 'project', '', [], Settings(data_dir=tmp_path), None, None, store, make_state())
    await export_all(ctx)
    report = store.get_artifact('r', 'report.md')['content']
    assert 'Component failure entries: 0' in report
    assert 'Generated guard failed on original' not in report


@pytest.mark.asyncio
async def test_report_rejected_guard_is_candidate_and_no_raw_errors(tmp_path):
    store = Store(tmp_path / 'db')
    store.create_run('r', 'demo:example', status='done')
    state = make_state()
    state.errors = ['PRIVATE-RAW-ERROR']
    for item_id in ('REQ-01', 'REQ-02', 'REQ-03', 'REQ-04'):
        store.append_event('r', 'gap_skipped', {'item_id': item_id, 'reason': 'six item limit'})
    state.gap_proposals['REQ-01'] = ProposedTest(item_id='REQ-01', file='tests/test_pod_req_01.py',
                                                content='def test_x(): pass\n', explanation='x')
    state.gap_results['REQ-01'] = GapTestResult(item_id='REQ-01', file='tests/test_pod_req_01.py',
                                                passes_on_original=False, reveals_violation=True, approved=False)
    ctx = RunContext('r', tmp_path / 'project', '', [], Settings(data_dir=tmp_path), None, None, store, state)
    await export_all(ctx)
    report = store.get_artifact('r', 'report.md')['content']
    assert 'Component failure entries: 1' in report and 'Skipped gap attempts: 4' in report
    assert 'approved 0, rejected 1, pending 0' in report
    assert 'REQ-01: rejected; original failed' in report
    assert 'Generated guard failed on original; may be a test defect, reviewer assessment required.' in report
    assert 'equivalent or off-target edits can survive' in report
    assert 'PRIVATE-RAW-ERROR' not in report
    receipt = json.loads(store.get_artifact('r', 'receipts.json')['content'])
    assert receipt['run']['id'] == 'r'
