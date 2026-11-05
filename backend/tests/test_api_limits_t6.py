import asyncio

from fastapi.testclient import TestClient
from pod.app import create_app
from pod.config import Settings
from pod.store import Store


def test_budget_includes_recorded_run(tmp_path):
    settings = Settings(data_dir=tmp_path, daily_token_budget=1)
    store = Store(tmp_path / 'db')
    store.create_run('old', 'demo:refundly', status='done')
    (tmp_path / 'recorded-run-id').write_text('old')
    with TestClient(create_app(settings, store=store)) as client:
        response = client.post('/api/runs', json={'project': 'demo:refundly'})
        assert response.status_code == 429
        assert response.json()['recorded_run_id'] is None
        assert client.get('/api/runs/recorded').status_code == 404


def test_concurrency_and_rate(monkeypatch, tmp_path):
    import pod.app as module

    async def wait_run(*args, **kwargs):
        await asyncio.sleep(0.25)

    monkeypatch.setattr(module, 'execute_run', wait_run)
    settings = Settings(data_dir=tmp_path)
    store = Store(tmp_path / 'db')
    with TestClient(create_app(settings, store=store)) as client:
        assert client.post('/api/runs', json={'project': 'demo:refundly'}).status_code == 200
        assert client.post('/api/runs', json={'project': 'demo:refundly'}).status_code == 429
        import time
        time.sleep(.3)
        assert client.post('/api/runs', json={'project': 'demo:refundly'}).status_code == 200
        time.sleep(.3)
        assert client.post('/api/runs', json={'project': 'demo:refundly'}).status_code == 200
        time.sleep(.3)
        assert client.post('/api/runs', json={'project': 'demo:refundly'}).status_code == 429
