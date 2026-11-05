"""Run context and state shared by all pipeline stages (navigator-owned).

Stages receive a ``RunContext`` and read/write ``ctx.state``. They must use
``ctx.llm_call`` for every model call (so it is recorded in the journal) and
``ctx.run_sandbox`` / ``ctx.run_tests`` for every execution (so it lands in the
containment ledger). Nothing from the target project is ever executed on the host.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from pod.sandbox.junit import parse_junit
from pod.sandbox.models import SandboxLimits, SandboxResult, SandboxSpec
from pod.schemas import (
    EvidenceReport,
    GapTestResult,
    HistoryRecord,
    Item,
    ItemVerdict,
    Mutation,
    MutationResult,
    ProposedTest,
)

PYTEST_ENTRY = "/opt/pod/run_pytest.sh"
JUNIT_PATH = ".pod/junit.xml"


class RunState(BaseModel):
    spec_sha256: str = ""
    tree_sha256: str = ""
    history_records: int = 0
    requirements: list[Item] = Field(default_factory=list)
    rules: list[Item] = Field(default_factory=list)
    dropped: list[Item] = Field(default_factory=list)  # rejected or unverified, kept for the report
    baseline_junit: list[list[dict[str, str]]] = Field(default_factory=list)  # one list per baseline run
    baseline_ok: bool | None = None
    baseline_sandboxes: list[str] = Field(default_factory=list)
    evidence: dict[str, EvidenceReport] = Field(default_factory=dict)
    mutations: dict[str, list[Mutation]] = Field(default_factory=dict)
    results: dict[str, MutationResult] = Field(default_factory=dict)
    verdicts: dict[str, ItemVerdict] = Field(default_factory=dict)
    gap_proposals: dict[str, ProposedTest] = Field(default_factory=dict)
    gap_results: dict[str, GapTestResult] = Field(default_factory=dict)
    llm_calls: list[dict[str, Any]] = Field(default_factory=list)
    sandbox_ids: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def items(self) -> list[Item]:
        return [*self.requirements, *self.rules]


EmitHook = Callable[[dict[str, Any]], None]


@dataclass
class RunContext:
    run_id: str
    project_dir: Path  # host copy of the target project; read for snapshots, NEVER executed on host
    spec_text: str
    history: list[HistoryRecord]
    settings: Any
    llm: Any  # pod.llm.VultrLLM | FakeLLM
    sandbox: Any  # pod.sandbox.runner.DockerSandboxRunner | FakeSandboxRunner
    store: Any  # pod.store.Store
    state: RunState = field(default_factory=RunState)
    emit_hook: EmitHook | None = None  # e.g. the SSE broadcaster; receives the stored event

    # -- journal ---------------------------------------------------------------
    def emit(self, type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        event = self.store.append_event(self.run_id, type, dict(payload))
        if self.emit_hook is not None:
            self.emit_hook(event)
        return event

    @contextlib.asynccontextmanager
    async def stage(self, stage_id: str, name: str) -> AsyncIterator[None]:
        self.emit("stage_started", {"stage": stage_id, "name": name})
        try:
            yield
        except Exception as exc:  # recorded, then re-raised to the orchestrator
            self.state.errors.append(f"{stage_id}: {type(exc).__name__}: {exc}"[:500])
            self.emit("stage_finished", {"stage": stage_id, "status": "failed",
                                         "error": f"{type(exc).__name__}: {exc}"[:500]})
            raise
        else:
            self.emit("stage_finished", {"stage": stage_id, "status": "done"})

    def skip_stage(self, stage_id: str, name: str, reason: str) -> None:
        """Record a stage that intentionally did not run (e.g. no history to mine)."""
        self.emit("stage_started", {"stage": stage_id, "name": name})
        self.emit("stage_finished", {"stage": stage_id, "status": "skipped", "reason": reason[:500]})

    # -- models ----------------------------------------------------------------
    async def llm_call(self, role: str, messages: list[dict[str, Any]],
                       response_model: type[BaseModel] | None = None, **kwargs: Any) -> Any:
        """Call a Vultr model through pod.llm and record the receipt (hashes and tokens only)."""
        result = await self.llm.chat(role, messages, response_model=response_model, **kwargs)
        record = result.record
        record_dict = record.model_dump(mode="json") if hasattr(record, "model_dump") else dict(record)
        self.state.llm_calls.append(record_dict)
        self.emit("llm_call", record_dict)
        return result

    # -- sandboxes -------------------------------------------------------------
    def limits(self, timeout_s: float | None = None) -> SandboxLimits:
        s = self.settings
        return SandboxLimits(memory_mb=s.memory_mb, cpus=s.cpus, pids=s.pids,
                             timeout_s=timeout_s if timeout_s is not None else s.timeout_s)

    async def run_sandbox(self, spec: SandboxSpec) -> SandboxResult:
        spec = spec.model_copy(update={"run_id": self.run_id})
        self.emit("sandbox_started", {"purpose": spec.purpose})
        result: SandboxResult = await self.sandbox.run(spec)
        self.store.save_sandbox_result(result)
        self.state.sandbox_ids.append(result.sandbox_id)
        self.emit("sandbox_finished", result.model_dump(mode="json"))
        return result

    async def run_tests(self, purpose: str, *, files: Mapping[str, str | bytes] | None = None,
                        timeout_s: float | None = None,
                        pytest_args: list[str] | None = None) -> tuple[SandboxResult, list[dict[str, str]]]:
        """Run the project's pytest suite in a fresh sandbox.

        ``files`` overrides or adds workspace files (a mutated source file, a proposed test).
        Returns the sandbox result and the parsed JUnit cases ([] if no report was produced).
        """
        spec = SandboxSpec(
            purpose=purpose,
            source_dir=self.project_dir,
            files=dict(files) if files else None,
            command=[PYTEST_ENTRY, *(pytest_args or [])],
            limits=self.limits(timeout_s),
            collect=[JUNIT_PATH],
            extra_env={"POD_TEST_TIMEOUT": str(self.settings.test_timeout_s)},
        )
        result = await self.run_sandbox(spec)
        xml = result.collected.get(JUNIT_PATH)
        cases: list[dict[str, str]] = []
        if xml:
            try:
                cases = parse_junit(xml)
            except (ValueError, SyntaxError):  # hostile or truncated report = "no report"
                cases = []
        return result, cases
