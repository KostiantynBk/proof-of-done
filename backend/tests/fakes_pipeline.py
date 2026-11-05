"""Scripted sandbox fake: never executes project files."""
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256

from pod.sandbox.models import SandboxResult, SandboxSpec, TelemetryEvent


class ScriptedSandboxRunner:
    def __init__(self, script: Callable[[SandboxSpec], tuple[str, str, list[TelemetryEvent]]]):
        self.script = script
        self.specs: list[SandboxSpec] = []

    async def run(self, spec: SandboxSpec) -> SandboxResult:
        self.specs.append(spec)
        outcome, xml, telemetry = self.script(spec)
        now = datetime.now(UTC)
        return SandboxResult(sandbox_id=f'fake-{len(self.specs)}', run_id=spec.run_id,
            purpose=spec.purpose, image='fake', runtime='fake', limits=spec.limits,
            started_at=now, ended_at=now, duration_ms=1, outcome=outcome,
            exit_code={'passed': 0, 'tests_failed': 1, 'error': 2}.get(outcome),
            stdout_sha256=sha256(b'').hexdigest(), destroyed_at=now, telemetry=telemetry,
            collected={'.pod/junit.xml': xml} if xml else {})
