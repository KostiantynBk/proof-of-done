import json

import pytest
from fastapi.testclient import TestClient
from pod.app import create_app
from pod.config import Settings
from pod.pipeline.context import RunState
from pod.schemas import GapTestResult, Item, ItemVerdict, ProposedTest
from pod.store import Store


@pytest.mark.parametrize('approved', [True, False])
def test_approval_and_sse(tmp_path, approved):
    store = Store(tmp_path / 'db')
    store.create_run('r', 'demo:refundly', status='awaiting_approval')
    state = RunState(requirements=[Item(id='REQ-01', kind='requirement', title='x', statement='x',
                                       source_checks=[], source_verified=True)],
                     verdicts={'REQ-01': ItemVerdict(item_id='REQ-01', kind='requirement', verdict='WEAK')},
                     gap_proposals={'REQ-01': ProposedTest(item_id='REQ-01', file='tests/test_pod_req_01.py',
                                                            content='def test_x(): pass\n', explanation='x')},
                     gap_results={'REQ-01': GapTestResult(item_id='REQ-01', file='tests/test_pod_req_01.py',
                                                          passes_on_original=True)})
    store.put_artifact('r', 'state.json', state.model_dump_json())
    first = store.append_event('r', 'first', {})
    second = store.append_event('r', 'run_finished', {'status': 'awaiting_approval'})
    with TestClient(create_app(Settings(data_dir=tmp_path), store=store)) as client:
        assert client.post('/api/runs/r/approvals/missing', json={'approve': True, 'reviewer': 'a'}).status_code == 404
        assert client.post('/api/runs/r/approvals/REQ-01', json={'approve': True, 'reviewer': ' '}).status_code == 422
        assert client.get('/api/runs/r/events?after=-1').status_code == 400
        assert client.get('/api/runs/r/events', headers={'Last-Event-ID': '99999999999999999999999'}).status_code == 400
        stream = client.get(f'/api/runs/r/events?after={first["seq"]}')
        assert f'id: {second["seq"]}\ndata: ' in stream.text
        assert stream.text.endswith('\n\n')
        assert '"type": "first"' not in stream.text
        assert not any(line.startswith('event:') for line in stream.text.splitlines())
        response = client.post('/api/runs/r/approvals/REQ-01', json={'approve': approved, 'reviewer': 'human'})
        if approved:
            assert response.status_code == 409  # synthetic state has no proof binding
            return
        assert response.status_code == 200
        assert response.json()['gap']['result']['approved'] is approved
        patch = client.get('/api/runs/r/export/patch.diff').text
        assert ('test_pod_req_01.py' in patch) is approved
        assert any(e['type'] == 'signoff' for e in store.list_events('r'))
        assert client.get('/api/missing').status_code == 404
        assert client.get('/runs/r').status_code == 200
        receipt = json.loads(client.get('/api/runs/r/export/receipts.json').content)
        assert receipt['signoffs'][0]['reviewer'] == 'human'
