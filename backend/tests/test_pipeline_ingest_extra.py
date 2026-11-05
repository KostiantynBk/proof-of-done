import asyncio
from dataclasses import replace

import pytest
from pod.llm import FakeLLM
from pod.pipeline.ingest import _copy_project
from pod.pipeline.mine import mine_rules
from pod.schemas import HistoryRecord
from test_pipeline_ingest_extract_mine import context


def test_copy_skips_caches_symlinks_and_other_roots(tmp_path):
    from pod.projects import get_project

    root = tmp_path / "source"
    (root / "refundly" / "__pycache__").mkdir(parents=True)
    (root / "refundly" / ".venv-other").mkdir()
    (root / "refundly" / "ok.py").write_text("safe", encoding="utf-8")
    (root / "refundly" / "cache.pyc").write_text("skip", encoding="utf-8")
    (root / "refundly" / ".venv-other" / "private").write_text("skip", encoding="utf-8")
    (root / "ground_truth.json").write_text("secret", encoding="utf-8")
    try:
        (root / "refundly" / "link").symlink_to(root / "ground_truth.json")
    except OSError:
        pass  # Windows may not permit symlink creation for this user.
    dest = tmp_path / "dest"
    _copy_project(replace(get_project("demo:refundly"), root=root, include=("refundly/",)), dest)
    assert [p.name for p in (dest / "refundly").iterdir()] == ["ok.py"]
    with pytest.raises(ValueError, match="empty"):
        _copy_project(replace(get_project("demo:refundly"), root=root), dest)


def test_standalone_ci_windows_are_not_independent_prs(tmp_path):
    records = [HistoryRecord(record_id=f"ci:log{i}", kind="ci",
                             text=f"The payment test failed because the amount was invalid {i}")
               for i in range(2)]
    rule = {"id": "RULE-01", "rule": "Validate payment amounts.", "rationale": "History",
            "quotes": [{"record_id": r.record_id, "quote": r.text} for r in records]}
    ctx = context(tmp_path, llm=FakeLLM({"miner": [{"rules": [rule]}]}))
    ctx.history = records
    asyncio.run(mine_rules(ctx))
    assert not ctx.state.rules
    assert ctx.state.dropped[0].distinct_prs == 0
    assert ctx.state.dropped[0].drop_reason == "insufficient_independent_evidence"
