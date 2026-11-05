"""S0: copy only allowlisted files and prepare sandbox-extracted inputs."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
from pathlib import Path

from pod.pipeline.context import RunContext
from pod.projects import DemoProject
from pod.sandbox.models import SandboxSpec
from pod.schemas import HistoryRecord
from pod.verify.history import parse_ci_log, parse_git_log, parse_reviews_json
from pod.verify.snapshot import Snapshot


def _excluded(name: str) -> bool:
    return name == "__pycache__" or name.startswith(".venv") or name.endswith(".pyc")


def _copy_project(project: DemoProject, dest: Path) -> None:
    # Fresh destinations avoid silently preserving files from a prior run.
    dest.mkdir(parents=True, exist_ok=True)
    if any(dest.iterdir()):
        raise ValueError("Project destination must be empty")
    root = project.root.resolve()
    for entry in project.include:
        parts = Path(entry).parts
        if not parts or Path(entry).is_absolute() or ".." in parts:
            raise ValueError("Invalid project include path")
        src = root.joinpath(*parts)
        if any(root.joinpath(*parts[:i]).is_symlink() for i in range(1, len(parts) + 1)):
            continue
        if _excluded(src.name):
            continue
        if src.is_file() and stat.S_ISREG(src.lstat().st_mode):
            target = dest.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
        elif src.is_dir():
            for directory, dirs, files in os.walk(src, followlinks=False):
                base = Path(directory)
                dirs[:] = [d for d in dirs if not _excluded(d) and not (base / d).is_symlink()]
                for name in files:
                    path = base / name
                    if _excluded(name) or path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
                        continue
                    target = dest / path.relative_to(root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)


def _chatter(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 25:
        return True
    if re.fullmatch(r"```suggestion\s*\n[\s\S]*?\n```\s*", stripped, re.IGNORECASE):
        return True
    return bool(re.fullmatch(
        r"(?:lgtm|thanks|thank you|done|fixed|\+1|nit)[\s!.,:;-]*", stripped, re.IGNORECASE
    ) or re.match(r"^(?:thanks|thank you)\b", stripped, re.IGNORECASE))


def _history(project: DemoProject) -> tuple[list[HistoryRecord], int]:
    records = parse_reviews_json(project.root / project.history["reviews"])
    records += parse_git_log(project.root / project.history["git_log"])
    for path in sorted(project.root.glob(project.history["ci"])):
        if path.is_file() and not path.is_symlink():
            records += parse_ci_log(path)
    kept = [record for record in records if not _chatter(record.text)]
    # Preserve existing pseudonyms and assign disjoint ones to all other author names.
    used = {r.author for r in kept if r.author and re.fullmatch(r"reviewer-\d+", r.author)}
    aliases: dict[str, str] = {}
    next_id = 1
    for record in kept:
        author = record.author
        if author and author not in used:
            if author not in aliases:
                while f"reviewer-{next_id}" in used:
                    next_id += 1
                aliases[author] = f"reviewer-{next_id}"
                used.add(aliases[author])
            record.author = aliases[author]
    return kept, len(records) - len(kept)


async def prepare_inputs(ctx: RunContext, project: DemoProject) -> None:
    """Populate inputs; ctx.project_dir must be the run's fresh project directory."""
    async with ctx.stage("S0", "Ingest inputs"):
        _copy_project(project, ctx.project_dir)
        snap = Snapshot(ctx.project_dir)
        ctx.state.tree_sha256 = snap.tree_sha256()
        spec_source, fallback_reason = "pdf", None
        try:
            result = await ctx.run_sandbox(SandboxSpec(
                purpose="pdf_extract", files={"input.pdf": (project.root / project.spec_pdf).read_bytes()},
                command=["python", "/opt/pod/extract_pdf.py"], collect=["output.txt"],
                limits=ctx.limits(30),
            ))
            extracted = result.collected.get("output.txt", "")
            if result.outcome != "passed" or result.exit_code != 0:
                fallback_reason = f"sandbox_{result.outcome}"
            elif len(extracted.strip()) < 200:
                fallback_reason = "pdf_text_too_short"
        except Exception as exc:  # noqa: BLE001 - any sandbox failure triggers the safe fallback
            fallback_reason = f"pdf_extraction_{type(exc).__name__}"
        if fallback_reason:
            spec_source = "md"
            extracted = (project.root / project.spec_md).read_text(encoding="utf-8")
        ctx.spec_text = extracted[:200_000]
        ctx.state.spec_sha256 = hashlib.sha256(ctx.spec_text.encode("utf-8")).hexdigest()
        ctx.history, dropped = _history(project)
        ctx.state.history_records = len(ctx.history)
        ctx.emit("inputs_ready", {
            "spec_sha256": ctx.state.spec_sha256, "tree_sha256": ctx.state.tree_sha256,
            "spec_source": spec_source, "fallback_reason": fallback_reason,
            "history_records": len(ctx.history), "chatter_dropped": dropped,
            "project_files": sorted(snap.files),
        })
