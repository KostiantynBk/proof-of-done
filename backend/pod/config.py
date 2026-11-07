"""Runtime configuration. Secrets are read only from the process environment."""

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MODELS = {
    "extractor": "deepseek-v4.1-flash-1017",
    "miner": "deepseek-v4.1-flash-1017",
    "auditor": "glm-5.3",
    "testwriter": "glm-5.3",
    "saboteur": "minimax-m3.1-flash",
    "judge": "pplx-decider-v1.2",
}


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("POD_DATA_DIR", "./data")))
    work_root: Path | None = None
    sandbox_image: str = field(default_factory=lambda: os.getenv("POD_SANDBOX_IMAGE", "pod-sandbox:latest"))
    runtime: str = field(default_factory=lambda: os.getenv("POD_SANDBOX_RUNTIME", "runsc"))
    sandbox_uid: int = field(default_factory=lambda: int(os.getenv("POD_SANDBOX_UID", "1000")))
    sandbox_gid: int = field(default_factory=lambda: int(os.getenv("POD_SANDBOX_GID", "1000")))
    sandbox_concurrency: int = field(default_factory=lambda: int(os.getenv("POD_SANDBOX_CONCURRENCY", "4")))
    require_capped_workroot: bool | None = None
    memory_mb: int = field(default_factory=lambda: int(os.getenv("POD_SANDBOX_MEMORY_MB", "512")))
    cpus: float = field(default_factory=lambda: float(os.getenv("POD_SANDBOX_CPUS", "1.0")))
    pids: int = field(default_factory=lambda: int(os.getenv("POD_SANDBOX_PIDS", "128")))
    timeout_s: int = field(default_factory=lambda: int(os.getenv("POD_SANDBOX_TIMEOUT_S", "90")))
    mutation_timeout_s: int = field(default_factory=lambda: int(os.getenv("POD_MUTATION_TIMEOUT_S", "60")))
    test_timeout_s: int = field(default_factory=lambda: int(os.getenv("POD_TEST_TIMEOUT", "10")))
    inference_base_url: str = field(default_factory=lambda: os.getenv("VULTR_INFERENCE_BASE_URL", "https://api.vultrinference.com/v1"))
    inference_api_key: str = field(default_factory=lambda: os.getenv("VULTR_INFERENCE_API_KEY", ""), repr=False)
    models: dict[str, str] = field(default_factory=lambda: {role: os.getenv(f"POD_MODEL_{role.upper()}", model) for role, model in DEFAULT_MODELS.items()})
    daily_token_budget: int = field(default_factory=lambda: int(os.getenv("POD_DAILY_TOKEN_BUDGET", "3000000")))
    fake_llm: bool = field(default_factory=lambda: os.getenv("POD_FAKE_LLM", "0") == "1")

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        self.work_root = Path(self.work_root or os.getenv("POD_WORK_ROOT") or self.data_dir / "work")
        if self.require_capped_workroot is None:
            raw = os.getenv("POD_REQUIRE_CAPPED_WORKROOT", "1" if self.runtime == "runsc" else "0")
            if raw not in {"0", "1"}:
                raise ValueError("POD_REQUIRE_CAPPED_WORKROOT must be 0 or 1")
            self.require_capped_workroot = raw == "1"
        if not (1 <= self.sandbox_uid <= 2147483647 and 1 <= self.sandbox_gid <= 2147483647):
            raise ValueError("sandbox UID and GID must be positive non-root IDs")
        if self.sandbox_concurrency < 1:
            raise ValueError("sandbox concurrency must be positive")
