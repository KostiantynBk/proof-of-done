import pytest
from pod.config import Settings
from pod.llm import FakeLLM
from pod.pipeline.broadcast import Broadcaster
from pod.pipeline.run import execute_run
from pod.store import Store


@pytest.mark.asyncio
async def test_setup_failure_is_terminal(tmp_path):
    settings = Settings(data_dir=tmp_path)
    (tmp_path / 'runs').write_text('not a directory')
    store = Store(tmp_path / 'db')
    store.create_run('r', 'demo:refundly', status='queued')
    await execute_run('r', 'demo:refundly', settings=settings, llm=FakeLLM(), sandbox=object(),
                      store=store, broadcaster=Broadcaster())
    assert store.get_run('r')['status'] == 'failed'
    assert [e['type'] for e in store.list_events('r')] == ['error', 'run_finished']


@pytest.mark.asyncio
async def test_stage_failure_does_not_publish_exception(tmp_path, monkeypatch):
    import pod.pipeline.run as module
    settings = Settings(data_dir=tmp_path)
    store = Store(tmp_path / 'db')
    store.create_run('r', 'demo:refundly', status='queued')

    async def fail(ctx, project):
        async with ctx.stage('S0', 'Ingest'):
            raise ValueError('dummy-private-path-and-secret-sentinel')

    monkeypatch.setattr(module, 'prepare_inputs', fail)
    await execute_run('r', 'demo:refundly', settings=settings, llm=FakeLLM(), sandbox=object(),
                      store=store, broadcaster=Broadcaster())
    assert store.get_run('r')['status'] == 'failed'
    assert 'dummy-private-path-and-secret-sentinel' not in str(store.list_events('r'))
    assert 'dummy-private-path-and-secret-sentinel' not in str(store.get_artifact('r', 'state.json'))
