import asyncio
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fakes_pipeline import ScriptedSandboxRunner
from fastapi.testclient import TestClient
from pod.app import BodyLimitMiddleware, create_app
from pod.config import Settings


@pytest.mark.asyncio
@pytest.mark.parametrize('declared', [True, False])
async def test_body_cap_before_app(declared):
    called = False
    reads = 0
    sent = []

    async def downstream(scope, receive, send):
        nonlocal called
        called = True

    chunks = iter([{'type': 'http.request', 'body': b'x' * 40000, 'more_body': True},
                   {'type': 'http.request', 'body': b'y' * 30000, 'more_body': False}])

    async def receive():
        nonlocal reads
        reads += 1
        return next(chunks)

    async def send(message):
        sent.append(message)

    headers = [(b'content-length', b'70000')] if declared else []
    await BodyLimitMiddleware(downstream)({'type': 'http', 'headers': headers}, receive, send)
    assert sent[0]['status'] == 413
    assert not called
    assert reads == (0 if declared else 2)


class BlockingRunner(ScriptedSandboxRunner):
    def __init__(self):
        super().__init__(lambda spec: ('passed', '', []))
        self.release = Event()
        self.started = 0
        self.leaked = 0

    async def reap_orphans(self):
        pass

    def leaked_count(self):
        return self.leaked

    async def run(self, spec):
        self.started += 1
        await asyncio.to_thread(self.release.wait, 5)
        return await super().run(spec)


def test_playground_cap_and_leaks(tmp_path, monkeypatch):
    import pod.app as module

    async def pipeline(*args, **kwargs):
        await asyncio.sleep(.1)

    monkeypatch.setattr(module, 'execute_run', pipeline)
    runner = BlockingRunner()
    app = create_app(Settings(data_dir=tmp_path), runner=runner)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.post, '/api/playground', json={'code': 'pass'}) for _ in range(2)]
        try:
            deadline = time.monotonic() + 5
            while runner.started < 2 and time.monotonic() < deadline:
                time.sleep(.01)
            assert runner.started == 2
            assert client.post('/api/playground', json={'code': 'pass'}).status_code == 429
            assert client.post('/api/runs', json={'project': 'demo:refundly'}).status_code == 200
            runner.leaked = 1
            assert client.get('/api/health').json()['sandbox_leaked'] == 1
            assert client.post('/api/playground', json={'code': 'pass'}).status_code == 503
            assert client.post('/api/runs', json={'project': 'demo:refundly'}).status_code == 503
        finally:
            runner.release.set()
        assert all(f.result(timeout=5).status_code == 200 for f in futures)


def test_extra_fields_and_lengths(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        assert client.post('/api/playground', json={'code': 'pass', 'unused': 'x'}).status_code == 422
        assert client.post('/api/playground', json={'preset': 'x' * 65}).status_code == 422
        assert client.post('/api/runs', json={'project': 'x' * 201}).status_code == 422
        assert client.post('/api/runs', json={'project': 'demo:refundly', 'unused': 1}).status_code == 422
        assert client.post('/api/runs/r/approvals/x', json={'approve': True, 'reviewer': 'x' * 61}).status_code == 422
        assert client.post('/api/runs/r/approvals/x', json={'approve': True, 'reviewer': 'x', 'extra': 1}).status_code == 422
        assert client.post('/api/runs', content=b'x' * 65537).status_code == 413


def test_rate_ip_cap_and_expiry(tmp_path):
    runner = BlockingRunner()
    runner.release.set()
    app = create_app(Settings(data_dir=tmp_path), runner=runner)
    with TestClient(app) as client:
        playground, runs = app.state.rate_limit_buckets
        now = time.monotonic()
        runs.update({f'client-{i}': deque([now]) for i in range(10000)})
        assert client.post('/api/playground', json={'code': 'pass'}).status_code == 429
        runs['client-0'] = deque([now - 3601])
        assert client.post('/api/playground', json={'code': 'pass'}).status_code == 200
        assert 'client-0' not in runs
        assert len(playground.keys() | runs.keys()) == 10000
