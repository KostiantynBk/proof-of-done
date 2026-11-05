from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class SandboxLimits(BaseModel):
    memory_mb: int = Field(default=512, gt=0)
    cpus: float = Field(default=1.0, gt=0)
    pids: int = Field(default=128, gt=0)
    timeout_s: float = Field(default=90, gt=0)
    network: Literal["none"] = "none"


class SandboxSpec(BaseModel):
    purpose: str
    run_id: str | None = None
    source_dir: Path | None = None
    files: dict[str, str | bytes] | None = None
    command: list[str]
    limits: SandboxLimits = Field(default_factory=SandboxLimits)
    collect: list[str] = Field(default_factory=list)
    extra_env: dict[str, str] = Field(default_factory=dict)

    @field_validator("extra_env")
    @classmethod
    def allowed_env(cls, value: dict[str, str]) -> dict[str, str]:
        if set(value) - {"POD_TEST_TIMEOUT"}:
            raise ValueError("extra_env contains disallowed keys")
        return value


class TelemetryEvent(BaseModel):
    type: str
    detail: str
    ts: str


class SandboxResult(BaseModel):
    sandbox_id: str
    run_id: str | None = None
    purpose: str
    image: str
    image_digest: str | None = None
    runtime: str
    limits: SandboxLimits
    started_at: datetime
    ended_at: datetime
    duration_ms: int
    exit_code: int | None = None
    outcome: Literal["passed", "tests_failed", "timeout_killed", "oom_killed", "error"]
    telemetry: list[TelemetryEvent] = Field(default_factory=list)
    stdout_tail: str = ""
    stdout_sha256: str
    destroyed_at: datetime | None = None
    cleanup_error: str | None = None
    artifact_error: str | None = None
    collected: dict[str, str] = Field(default_factory=dict)
