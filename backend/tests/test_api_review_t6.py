import asyncio

from fakes_pipeline import ScriptedSandboxRunner
from fastapi.testclient import TestClient
from pod.app import create_app
from pod.config import Settings
from pod.demo_script import demo_llm
from pod.pipeline.broadcast import Broadcaster
from pod.pipeline.run import execute_run
from pod.store import Store


def make_run(tmp_path, *, baseline_failed=False, unscored=False):
    settings = Settings(data_dir=tmp_path)
    store = Store(tmp_path / 'db')
    store.create_run('r', 'demo:refundly', status='queued')
    baseline = '<testcase file="tests/test_refunds.py" classname="tests.test_refunds" name="test_audit_record_exists"/>'
    guard = '<testcase file="tests/test_pod_req_03.py" classname="tests.test_pod_req_03" name="test_audit_fields">{}</testcase>'

    def script(spec):
        if unscored and spec.purpose.endswith('-B1'):
            return 'timeout_killed', '', []
        if unscored and spec.purpose.endswith('-B2'):
            return 'error', '', []
        if spec.purpose == 'baseline:1' and baseline_failed:
            other = '<testcase file="tests/test_refunds.py" name="test_old_order_requires_approval"><failure/></testcase>'
            return 'tests_failed', '<testsuite>' + baseline + other + '</testsuite>', []
        if spec.purpose.startswith('gap_test:'):
            assert 'test_audit_fields' in spec.files['tests/test_pod_req_03.py']
            assert 'event.reason' in spec.files['tests/test_pod_req_03.py']
            if spec.purpose.count(':') == 2:
                assert 'refundly/refunds.py' in spec.files
                assert 'amount, reason, order.order_id)' not in spec.files['refundly/refunds.py']
                return 'tests_failed', '<testsuite>' + baseline + guard.format('<failure message="assertion"/>') + '</testsuite>', []
            return 'passed', '<testsuite>' + baseline + guard.format('') + '</testsuite>', []
        return 'passed', '<testsuite>' + baseline + '</testsuite>', []

    runner = ScriptedSandboxRunner(script)
    llm = demo_llm()
    if unscored:
        llm.responses['saboteur'][1] = {'item_id': 'REQ-03', 'mutations': [
            {'file': 'refundly/refunds.py', 'find': 'attempts < 3', 'replace': 'attempts < 2', 'description': 'x'},
            {'file': 'refundly/refunds.py', 'find': 'attempts == 3', 'replace': 'attempts == 2', 'description': 'y'}]}
    state = asyncio.run(execute_run('r', 'demo:refundly', settings=settings, llm=llm, sandbox=runner,
                                    store=store, broadcaster=Broadcaster()))
    assert state.gap_results['REQ-03'].kills_survivors
    return settings, store


def test_valid_simulated_recording_discloses_provenance(tmp_path):
    settings, store = make_run(tmp_path)
    (tmp_path / 'recorded-run-id').write_text('r')
    with TestClient(create_app(settings, store=store)) as client:
        assert client.get('/api/runs/recorded').json() == {'run_id': 'r', 'simulated': True}


def test_approval_is_bound_and_atomic(tmp_path):
    settings, store = make_run(tmp_path)
    with TestClient(create_app(settings, store=store)) as client:
        response = client.post('/api/runs/r/approvals/REQ-03', json={'approve': True, 'reviewer': 'Reviewer'})
        assert response.status_code == 200, response.text
        assert response.json()['verdict']['verdict'] == 'PROVEN'
        assert response.json()['verdict']['mutations_killed'] == 3
        assert all(r['outcome'] == 'killed' for r in response.json()['results'])
        assert client.get('/api/runs/r').json()['items'][0]['mutations']['survived'] == 0
        assert 'test_audit_fields' in client.get('/api/runs/r/export/report.md').text
        replay = client.get('/api/runs/r/events').text
        assert '"type": "signoff"' in replay


def test_excluded_mutations_do_not_block_three_kills(tmp_path):
    settings, store = make_run(tmp_path, unscored=True)
    with TestClient(create_app(settings, store=store)) as client:
        response = client.post('/api/runs/r/approvals/REQ-03', json={'approve': True, 'reviewer': 'Reviewer'})
        assert response.status_code == 200, response.text
        verdict = response.json()['verdict']
        assert verdict['verdict'] == 'PROVEN'
        assert verdict['mutations_killed'] == 3
        assert verdict['mutations_timeout'] == 1
        assert verdict['mutations_invalid'] == 1


def test_failed_baseline_cannot_promote(tmp_path):
    settings, store = make_run(tmp_path, baseline_failed=True)
    with TestClient(create_app(settings, store=store)) as client:
        response = client.post('/api/runs/r/approvals/REQ-03', json={'approve': True, 'reviewer': 'Reviewer'})
        assert response.status_code == 409
        assert client.get('/api/runs/r').json()['items'][0]['verdict'] == 'WEAK'


def test_export_failure_preserves_retry(tmp_path, monkeypatch):
    import pod.app as module
    settings, store = make_run(tmp_path)
    real_export = module.export_all

    async def fail(ctx):
        raise OSError('private path sentinel')

    with TestClient(create_app(settings, store=store)) as client:
        before = store.get_artifact('r', 'state.json')['sha256']
        monkeypatch.setattr(module, 'export_all', fail)
        response = client.post('/api/runs/r/approvals/REQ-03', json={'approve': True, 'reviewer': 'Reviewer'})
        assert response.status_code == 503
        assert store.get_artifact('r', 'state.json')['sha256'] == before
        assert not any(e['type'] == 'signoff' for e in store.list_events('r'))
        monkeypatch.setattr(module, 'export_all', real_export)
        assert client.post('/api/runs/r/approvals/REQ-03', json={'approve': True, 'reviewer': 'Reviewer'}).status_code == 200


def test_publication_failure_rolls_back(tmp_path):
    settings, store = make_run(tmp_path)
    with store._lock, store._db:
        store._db.execute("CREATE TRIGGER fail_patch BEFORE INSERT ON artifacts WHEN NEW.name='patch.diff' "
                          "BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    before_state = store.get_artifact('r', 'state.json')['sha256']
    before_patch = store.get_artifact('r', 'patch.diff')['sha256']
    with TestClient(create_app(settings, store=store)) as client:
        response = client.post('/api/runs/r/approvals/REQ-03', json={'approve': True, 'reviewer': 'Reviewer'})
        assert response.status_code == 503
        assert store.get_artifact('r', 'state.json')['sha256'] == before_state
        assert store.get_artifact('r', 'patch.diff')['sha256'] == before_patch
        assert not any(e['type'] == 'signoff' for e in store.list_events('r'))
        with store._lock, store._db:
            store._db.execute('DROP TRIGGER fail_patch')
        assert client.post('/api/runs/r/approvals/REQ-03', json={'approve': True, 'reviewer': 'Reviewer'}).status_code == 200


def test_approval_rejects_stale_proposal(tmp_path):
    settings, store = make_run(tmp_path)
    from pod.pipeline.summary import load_state
    state = load_state(store, 'r')
    state.gap_proposals['REQ-03'].content += '# changed\n'
    store.put_artifact('r', 'state.json', state.model_dump_json())
    with TestClient(create_app(settings, store=store)) as client:
        response = client.post('/api/runs/r/approvals/REQ-03', json={'approve': True, 'reviewer': 'Reviewer'})
        assert response.status_code == 409
        assert not any(e['type'] == 'signoff' for e in store.list_events('r'))
