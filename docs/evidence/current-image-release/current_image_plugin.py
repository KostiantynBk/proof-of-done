"""T17-fix1: pytest adapter for the EXISTING integration suite; no image build.

This operational harness selects the existing image, adds a tracking label and
fail-closed checks on real containers/results, and records sanitized observations.
The suite's workload and outcome assertions stay intact.
"""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import docker
import pytest
from pod.sandbox import DockerSandboxRunner

IMAGE = 'sha256:e57243f7dac1425ad9fe0c844e03f74a0f5f15b253e177e0ccd47fcb10850697'
ENV_NAMES = {'GPG_KEY', 'HOME', 'LANG', 'PATH', 'PYTHONDONTWRITEBYTECODE',
             'PYTHONUNBUFFERED', 'PYTHON_SHA256', 'PYTHON_VERSION'}


@pytest.fixture(autouse=True)
def current_production_image(monkeypatch, request):
    """Use immutable image; inspect real Docker objects without faking outcomes."""
    assert os.getuid() == 1001 and os.getgid() == 1001
    client = docker.from_env()
    assert client.images.get(IMAGE).id == IMAGE
    assert client.images.get('pod-sandbox:latest').id == IMAGE
    assert client.images.get(IMAGE).attrs['Config']['User'] == '1001:1001'
    observations = []

    def select_existing(_images, *, path, tag, buildargs):
        # Exactly the one trusted test fixture's image selection, never a build.
        assert Path(path).name == 'sandbox' and tag == 'pod-sandbox:test'
        assert buildargs == {'RUNNER_UID': '1001', 'RUNNER_GID': '1001'}
        return SimpleNamespace(tags=[IMAGE]), []

    monkeypatch.setattr(docker.models.images.ImageCollection, 'build', select_existing)
    original_init = DockerSandboxRunner.__init__

    def select_runner_image(runner, settings, client=None):
        assert settings.sandbox_image in (IMAGE, 'pod-sandbox:latest')
        settings.sandbox_image = IMAGE
        return original_init(runner, settings, client)

    monkeypatch.setattr(DockerSandboxRunner, '__init__', select_runner_image)
    original_create = docker.models.containers.ContainerCollection.create

    def checked_create(collection, *args, **kwargs):
        # A suite-specific label lets the shell find even a control container if
        # the process dies between Docker create and durable ID recording.
        labels = dict(kwargs.get('labels') or {})
        labels['pod.t17.fix1'] = '1'
        kwargs['labels'] = labels
        sandbox = labels.get('pod.sandbox') == '1'
        container = original_create(collection, *args, **kwargs)
        try:
            inventory = Path(os.environ['T17_OBSERVATIONS_DIR']) / 'created-ids.jsonl'
            with inventory.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'id': container.id, 'sandbox': sandbox}) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            container.reload()
            attrs = container.attrs
            host, config = attrs['HostConfig'], attrs['Config']
            assert (config.get('Labels') or {}).get('pod.t17.fix1') == '1'
            return inspect_created(container, attrs, host, config, sandbox)
        except BaseException:
            if not sandbox:
                # containers.run has not received the control object yet. Ensure
                # it cannot escape unlabelled/unaccounted on an inspection error.
                cleanup = 'unconfirmed'
                try:
                    container.remove(force=True)
                    try:
                        collection.get(container.id)
                    except docker.errors.NotFound:
                        cleanup = 'absent'
                except docker.errors.NotFound:
                    cleanup = 'absent'
                except Exception as exc:
                    print('T17_CONTROL_CLEANUP_ERROR ' + type(exc).__name__, file=sys.stderr, flush=True)
                print('T17_CONTROL_CLEANUP ' + json.dumps({'id': container.id, 'status': cleanup}),
                      file=sys.stderr, flush=True)
            raise

    def inspect_created(container, attrs, host, config, sandbox):
        row = {'id': container.id, 'image': attrs['Image'], 'sandbox': sandbox,
               'runtime': host['Runtime'], 'user': config['User'], 'network': host['NetworkMode']}
        observations.append(row)
        print('T17_CONTAINER ' + json.dumps(row, sort_keys=True), flush=True)
        assert attrs['Image'] == IMAGE
        if not sandbox:
            # The suite's one positive-control container is intentionally networked/runc.
            assert host['Runtime'] == 'runc' and host['NetworkMode'] == 'bridge'
            return container
        assert host['Runtime'] == 'runsc' and config['User'] == '1001:1001'
        assert host['NetworkMode'] == 'none' and host['ReadonlyRootfs'] is True
        assert host['CapDrop'] == ['ALL'] and 'no-new-privileges' in host['SecurityOpt']
        assert host['Memory'] == host['MemorySwap'] == 512 * 1024 * 1024
        assert host['NanoCpus'] == 1_000_000_000 and host['PidsLimit'] == 128
        assert {u['Name']: (u['Soft'], u['Hard']) for u in host['Ulimits']} == {
            'fsize': (52428800, 52428800), 'nofile': (256, 256)}
        mounts = attrs['Mounts']
        assert {m['Destination'] for m in mounts} == {'/work', '/telemetry'}
        assert all(m['Type'] == 'bind' and m['RW'] for m in mounts)
        assert set(host['Tmpfs']) == {'/tmp'} and 'size=64m' in host['Tmpfs']['/tmp']
        env = dict(x.split('=', 1) for x in config['Env'])
        assert set(env) == ENV_NAMES
        assert env['HOME'] == '/home/runner' and env['PYTHONDONTWRITEBYTECODE'] == '1'
        assert env['PYTHONUNBUFFERED'] == '1'
        return container

    monkeypatch.setattr(docker.models.containers.ContainerCollection, 'create', checked_create)
    original_run = DockerSandboxRunner.run

    async def checked_run(runner, spec):
        result = await original_run(runner, spec)
        row = {'sandbox_id': result.sandbox_id, 'image_digest': result.image_digest,
               'purpose': result.purpose, 'outcome': result.outcome,
               'exit_code': result.exit_code, 'destroyed_at': str(result.destroyed_at),
               'cleanup_error': result.cleanup_error, 'artifact_error': result.artifact_error,
               'telemetry_types': sorted({event.type for event in result.telemetry})}
        print('T17_RESULT ' + json.dumps(row, sort_keys=True), flush=True)
        assert result.image_digest == IMAGE and result.destroyed_at and not result.cleanup_error
        assert not result.artifact_error
        return result

    monkeypatch.setattr(DockerSandboxRunner, 'run', checked_run)
    yield
    output = Path(os.environ['T17_OBSERVATIONS_DIR'])
    output.mkdir(parents=True, exist_ok=True)
    (output / (request.node.name + '.json')).write_text(
        json.dumps({'image': IMAGE, 'containers': observations}, indent=2) + '\n', encoding='utf-8')
    client.close()
