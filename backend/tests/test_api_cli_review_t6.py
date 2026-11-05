import sys

import pytest
from pod import cli
from pod.llm import FakeLLM


def test_cli_fake_forbidden_with_key(monkeypatch, tmp_path):
    monkeypatch.setenv('POD_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('VULTR_INFERENCE_API_KEY', 'dummy-test-value')
    monkeypatch.setattr(sys, 'argv', ['pod.cli', 'run', 'demo:refundly', '--fake-llm'])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_cli_fake_reaps_before_execution(monkeypatch, tmp_path):
    monkeypatch.setenv('POD_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('VULTR_INFERENCE_API_KEY', raising=False)
    monkeypatch.setattr(sys, 'argv', ['pod.cli', 'run', 'demo:refundly', '--fake-llm'])
    order = []

    class Runner:
        def __init__(self, settings):
            pass

        async def reap_orphans(self):
            order.append('reap')

        def leaked_count(self):
            return 0

    async def execute(*args, **kwargs):
        order.append('run')
        assert isinstance(kwargs['llm'], FakeLLM)

    monkeypatch.setattr(cli, 'DockerSandboxRunner', Runner)
    monkeypatch.setattr(cli, 'execute_run', execute)
    cli.main()
    assert order == ['reap', 'run']
