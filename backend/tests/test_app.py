from datetime import UTC, datetime

from fastapi.testclient import TestClient
from pod.app import create_app
from pod.config import Settings
from pod.sandbox.models import SandboxLimits, SandboxResult
from pod.sandbox.runner import FakeSandboxRunner


def test_playground_and_rate_limit(tmp_path):
    now = datetime.now(UTC)
    result = SandboxResult(sandbox_id="one", purpose="playground", image="test", runtime="runsc",
                           limits=SandboxLimits(), started_at=now, ended_at=now, duration_ms=0,
                           exit_code=0, outcome="passed", stdout_sha256="0" * 64,
                           destroyed_at=now)
    runner = FakeSandboxRunner(result)
    app = create_app(Settings(data_dir=tmp_path), runner=runner)
    with TestClient(app) as client:
        assert client.get("/api/health").json()["sandbox_available"] is True
        assert len(client.get("/api/playground/presets").json()) == 7
        response = client.post("/api/playground", json={"preset": "infinite_loop"})
        assert response.status_code == 200
        assert response.json()["sandbox_run"]["outcome"] == "passed"
        assert runner.specs[0].limits.timeout_s == 20
        assert runner.specs[0].files == {"snippet.py": "while True: pass\n"}
        assert len(client.get("/api/sandboxes").json()) == 1
        for _ in range(9):
            assert client.post("/api/playground", json={"code": "print(1)"}).status_code == 200
        assert client.post("/api/playground", json={"code": "print(1)"}).status_code == 429
        assert client.post("/api/playground", json={"code": "x" * 4001}).status_code == 422
