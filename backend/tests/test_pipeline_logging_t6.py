import io
import json
import logging

import pytest
from pod.config import Settings
from pod.pipeline.broadcast import Broadcaster, pod_logger
from pod.pipeline.run import execute_run
from pod.pipeline.summary import build_summary
from pod.store import Store


@pytest.mark.asyncio
@pytest.mark.parametrize('debug', [False, True])
async def test_per_item_redacted_server_logs_and_private_debug(tmp_path, monkeypatch, debug):
    import pod.pipeline.run as module

    # The fixture is an explicitly fake credential, never a real API key.
    fake_token = 'A' * 40
    monkeypatch.setenv('DEMO_TEST_TOKEN', fake_token)
    monkeypatch.setenv('POD_DEBUG_ERRORS', '1' if debug else '0')

    class Failure:
        async def chat(self, role, messages, **kwargs):
            raise RuntimeError(f'auditor failure sentinel {fake_token}\nAuthorization: Bearer shortsecret')

    async def fake_ingest(ctx, project):
        async with ctx.stage('S0', 'Ingest'):
            for item in ('REQ-01', 'REQ-02'):
                messages = [{'role': 'user', 'content': json.dumps({'item': f'{item}: statement'})}]
                try:
                    await ctx.llm_call('auditor', messages)
                except RuntimeError:
                    ctx.state.errors.append(f'audit {item}: auditor failure sentinel {fake_token}')
            raise RuntimeError(f'failure sentinel {fake_token}')

    monkeypatch.setattr(module, 'prepare_inputs', fake_ingest)
    settings = Settings(data_dir=tmp_path)
    store = Store(tmp_path / 'db')
    store.create_run('r', 'demo:refundly', status='queued')
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = pod_logger()
    logger.addHandler(handler)
    try:
        await execute_run('r', 'demo:refundly', settings=settings, llm=Failure(), sandbox=object(),
                          store=store, broadcaster=Broadcaster())
    finally:
        logger.removeHandler(handler)
    logs = stream.getvalue()
    assert 'stage=llm:auditor item_id=REQ-01' in logs
    assert 'stage=llm:auditor item_id=REQ-02' in logs
    assert 'auditor failure sentinel' in logs
    assert 'Traceback' in logs
    assert fake_token not in logs and 'shortsecret' not in logs
    state = json.loads(store.get_artifact('r', 'state.json')['content'])
    if debug:
        assert any('auditor failure sentinel' in item for item in state['errors_debug'])
        assert fake_token not in json.dumps(state['errors_debug'])
        assert 'shortsecret' not in json.dumps(state['errors_debug'])
    else:
        assert 'errors_debug' not in state
    public = json.dumps({'events': store.list_events('r'), 'summary': build_summary(store, 'r')})
    assert fake_token not in public and 'shortsecret' not in public
    assert 'auditor failure sentinel' not in public
