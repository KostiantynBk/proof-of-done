from pod.config import Settings


def test_defaults_and_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("POD_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("POD_WORK_ROOT", raising=False)
    monkeypatch.setenv("POD_MODEL_AUDITOR", "override")
    monkeypatch.setenv("VULTR_INFERENCE_API_KEY", "placeholder")
    settings = Settings()
    assert settings.work_root == tmp_path / "work"
    assert settings.models["auditor"] == "override"
    assert settings.models["saboteur"] == "minimax-m3.1-flash"
    assert "placeholder" not in repr(settings)
    assert settings.runtime == "runsc"


def test_workroot_policy_depends_on_runtime(monkeypatch):
    monkeypatch.delenv("POD_REQUIRE_CAPPED_WORKROOT", raising=False)
    assert Settings(runtime="runsc").require_capped_workroot is True
    assert Settings(runtime="runc").require_capped_workroot is False
    monkeypatch.setenv("POD_REQUIRE_CAPPED_WORKROOT", "0")
    assert Settings(runtime="runsc").require_capped_workroot is False
