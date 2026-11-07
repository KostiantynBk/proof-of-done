import asyncio
import io
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import docker
import pytest
from docker.errors import NotFound
from pod.sandbox import DockerSandboxRunner, SandboxSpec, build_run_kwargs
from pod.sandbox import runner as module
from requests.exceptions import ReadTimeout


def settings(tmp_path):
    return SimpleNamespace(work_root=tmp_path, sandbox_image="pod-sandbox:latest", runtime="runsc",
                           sandbox_concurrency=4, require_capped_workroot=False, sandbox_uid=1000, sandbox_gid=1000)


def fake_client(container=None):
    client = Mock()
    container = container or Mock()
    container.wait.return_value = {"StatusCode": 0}
    container.attrs = {"State": {"OOMKilled": False}, "Image": "sha256:image"}
    container.logs.return_value = b""
    client.containers.create.return_value = container
    client.containers.get.side_effect = NotFound("absent")
    client.containers.list.return_value = []
    return client, container


def run(runner, spec=None):
    return asyncio.run(runner.run(spec or SandboxSpec(purpose="x", command=[])))


def test_run_kwargs_and_final_sdk_proxy_config(tmp_path):
    spec = SandboxSpec(purpose="baseline", command=["echo", "ok"])
    kw = build_run_kwargs(spec, settings(tmp_path), {"work": tmp_path / "work", "telemetry": tmp_path / "telemetry"})
    assert kw["runtime"] == "runsc" and kw["network_mode"] == "none" and kw["read_only"]
    assert kw["mem_limit"] == kw["memswap_limit"] == "512m"
    assert kw["nano_cpus"] == 1_000_000_000 and kw["pids_limit"] == 128
    assert kw["user"] == "1000:1000" and kw["cap_drop"] == ["ALL"]
    assert kw["use_config_proxy"] is False
    api = docker.APIClient(base_url="http://localhost:2375", version="1.43")
    api._proxy_configs = Mock()
    api._proxy_configs.inject_proxy_environment.side_effect = lambda env: env + ["HTTP_PROXY=http://fake:password@proxy"]
    api.create_container_from_config = Mock(return_value={"Id": "x"})
    from docker.models.containers import _create_container_args
    args = dict(kw)
    args.pop("detach")
    args["version"] = api._version
    api.create_container(**_create_container_args(args))
    final = api.create_container_from_config.call_args.args[0]
    assert not any("PROXY=" in entry or "password" in entry for entry in final["Env"])
    api._proxy_configs.inject_proxy_environment.assert_not_called()


def test_custom_sandbox_identity(tmp_path):
    configured = settings(tmp_path)
    configured.sandbox_uid, configured.sandbox_gid = 1001, 1002
    kwargs = build_run_kwargs(SandboxSpec(purpose="x", command=[]), configured,
                              {"work": tmp_path, "telemetry": tmp_path})
    assert kwargs["user"] == "1001:1002"
    assert kwargs["network_mode"] == "none" and kwargs["cap_drop"] == ["ALL"]


def test_mount_longest_match_and_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(module.sys, "platform", "linux")
    path = "/tmp/test mount"
    escaped = path.replace(" ", r"\040")
    root = Mock()
    root.resolve.return_value = path + "/new"
    monkeypatch.setattr(module, "open", lambda *_a, **_k: io.StringIO(
        f"1 0 0:1 / / rw - ext4 /dev/x rw\n2 1 0:2 / {escaped} rw - tmpfs tmpfs rw,size=64m,nr_inodes=4k\n"), raising=False)
    module._verify_work_root(root)
    monkeypatch.setattr(module, "open", lambda *_a, **_k: io.StringIO(
        f"1 0 0:1 / / rw - tmpfs tmpfs rw,size=64m,nr_inodes=4k\n2 1 0:2 / {escaped} rw - ext4 /dev/x rw\n"), raising=False)
    with pytest.raises(RuntimeError):
        module._verify_work_root(root)


def test_timeout_and_artifact_error_preserves_outcome(tmp_path):
    client, c = fake_client()
    c.wait.side_effect = ReadTimeout()
    c.logs.side_effect = RuntimeError("secret")
    result = run(DockerSandboxRunner(settings(tmp_path), client))
    assert result.outcome == "timeout_killed" and result.destroyed_at
    assert result.artifact_error == "artifact collection failed"
    assert c.kill.call_count >= 1 and c.remove.call_count == 1
    assert not list(tmp_path.iterdir())


def test_start_failure_is_removed(tmp_path):
    client, c = fake_client()
    c.start.side_effect = RuntimeError("start response lost")
    result = run(DockerSandboxRunner(settings(tmp_path), client))
    assert result.outcome == "error" and result.destroyed_at
    c.remove.assert_called_once_with(force=True)
    assert client.containers.create.call_args.kwargs["name"] == "pod-" + result.sandbox_id


def test_create_response_lost_recovers_by_name(tmp_path):
    client, c = fake_client()
    client.containers.create.side_effect = RuntimeError("lost")
    client.containers.get.side_effect = [c, NotFound("absent")]
    result = run(DockerSandboxRunner(settings(tmp_path), client))
    assert result.destroyed_at and not result.cleanup_error
    c.remove.assert_called_once_with(force=True)


def test_leak_blocks_runs_and_reap_recovers(tmp_path):
    client, c = fake_client()
    c.remove.side_effect = RuntimeError("unavailable")
    client.containers.get.side_effect = None
    client.containers.get.return_value = c
    runner = DockerSandboxRunner(settings(tmp_path), client)
    first = run(runner)
    assert first.destroyed_at is None and first.cleanup_error
    second = run(runner)
    assert second.outcome == "error" and client.containers.create.call_count == 1
    c.remove.side_effect = None
    client.containers.get.side_effect = NotFound("absent")
    asyncio.run(runner.reap_orphans())
    assert not runner._leaked and not list(tmp_path.iterdir())


def test_source_limits_files_and_bytes(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    for i in range(2001):
        (source / str(i)).touch()
    with pytest.raises(ValueError, match="source too large"):
        module._copy_source(source, dest)
    for item in source.iterdir():
        item.unlink()
    with (source / "huge").open("wb") as stream:
        stream.truncate(25 * 1024 * 1024 + 1)
    client, _ = fake_client()
    result = run(DockerSandboxRunner(settings(tmp_path / "runs"), client),
                 SandboxSpec(purpose="x", command=[], source_dir=source))
    assert result.artifact_error == "source too large"
    client.containers.create.assert_not_called()


def test_telemetry_bounds(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"x" * 50000)
    assert [e.type for e in module._read_events(path)] == ["telemetry_truncated"]
    path.write_bytes(b"bad\n" * 501)
    assert [e.type for e in module._read_events(path)] == ["telemetry_truncated"]
    path.write_bytes((b"{" + b"x" * 700 + b"}\n") * 500)
    assert [e.type for e in module._read_events(path)] == ["telemetry_truncated"]


def test_non_test_exit_one_and_named_junit_failure(tmp_path):
    client, c = fake_client()
    c.wait.return_value = {"StatusCode": 1}
    runner = DockerSandboxRunner(settings(tmp_path), client)
    assert run(runner).outcome == "error"
    xml = '<testsuite><testcase name="test_bad" classname="test_x"><failure/></testcase></testsuite>'
    spec = SandboxSpec(purpose="baseline", command=[], files={".pod/junit.xml": xml}, collect=[".pod/junit.xml"])
    assert run(runner, spec).outcome == "tests_failed"


def test_multiple_cancellations_hold_slot(tmp_path):
    client, _ = fake_client()
    opts = settings(tmp_path)
    opts.sandbox_concurrency = 1
    runner = DockerSandboxRunner(opts, client)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def worker(spec, cancel):
        calls.append(spec.purpose)
        entered.set()
        release.wait(3)
        return Mock()
    runner._run = worker
    async def scenario():
        first = asyncio.create_task(runner.run(SandboxSpec(purpose="first", command=[])))
        await asyncio.to_thread(entered.wait, 2)
        first.cancel()
        await asyncio.sleep(.02)
        first.cancel()
        second = asyncio.create_task(runner.run(SandboxSpec(purpose="second", command=[])))
        await asyncio.sleep(.05)
        assert calls == ["first"]
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await second
    asyncio.run(scenario())
    assert calls == ["first", "second"]


def test_paths_cannot_escape_workspace(tmp_path):
    client, _ = fake_client()
    result = run(DockerSandboxRunner(settings(tmp_path), client),
                 SandboxSpec(purpose="x", command=[], files={"../escape": "x"}))
    assert result.outcome == "error" and result.destroyed_at
    client.containers.create.assert_not_called()
