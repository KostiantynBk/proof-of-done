"""Disposable Docker executions. Audit telemetry is not a security boundary."""

import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import docker
from docker.errors import NotFound
from docker.types import Ulimit
from requests.exceptions import ReadTimeout

from .models import SandboxResult, SandboxSpec, TelemetryEvent


def _now() -> datetime:
    return datetime.now(UTC)


def _safe_path(root: Path, name: str) -> Path:
    p = PurePosixPath(name)
    if (not name or "\x00" in name or "\\" in name or p.is_absolute()
            or PureWindowsPath(name).drive or any(x in ("..", ".") for x in p.parts)):
        raise ValueError("unsafe workspace path")
    target = root.joinpath(*p.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("workspace path escapes root")
    return target


def build_run_kwargs(spec: SandboxSpec, settings: Any, paths: dict[str, Path]) -> dict[str, Any]:
    if set(spec.extra_env) - {"POD_TEST_TIMEOUT"}:
        raise ValueError("extra_env contains disallowed keys")
    memory = f"{spec.limits.memory_mb}m"
    env = {"HOME": "/home/runner", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}
    env.update(spec.extra_env)
    return {
        "image": settings.sandbox_image, "command": spec.command, "detach": True,
        "runtime": settings.runtime, "network_mode": "none", "read_only": True,
        "tmpfs": {"/tmp": "rw,size=64m,mode=1777"},
        "volumes": {str(paths["work"]): {"bind": "/work", "mode": "rw"},
                    str(paths["telemetry"]): {"bind": "/telemetry", "mode": "rw"}},
        "mem_limit": memory, "memswap_limit": memory,
        "nano_cpus": int(spec.limits.cpus * 1_000_000_000), "pids_limit": spec.limits.pids,
        "ulimits": [Ulimit(name="fsize", soft=50 * 1024 * 1024, hard=50 * 1024 * 1024),
                    Ulimit(name="nofile", soft=256, hard=256)],
        "user": "1000:1000", "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"], "environment": env, "use_config_proxy": False,
        "labels": {"pod.sandbox": "1", "pod.purpose": spec.purpose,
                   "pod.run_id": spec.run_id or ""},
        "working_dir": "/work", "init": True,
        "log_config": docker.types.LogConfig(type="json-file", config={"max-size": "1m", "max-file": "1"}),
    }


def _verify_work_root(root: Path) -> None:
    """Verify the effective mount, not merely a parent mount, on Linux."""
    if sys.platform != "linux":
        raise RuntimeError("capped work root requires Linux")
    target = str(root.resolve())
    matches = []
    with open("/proc/self/mountinfo", encoding="utf-8") as mounts:
        for line in mounts:
            left, sep, right = line.partition(" - ")
            if not sep:
                continue
            fields, after = left.split(), right.split()
            if len(fields) < 6 or len(after) < 3:
                continue
            mount = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), fields[4])
            if target == mount or target.startswith(mount.rstrip("/") + "/"):
                matches.append((len(mount), after[0], fields[5] + "," + after[2]))
    if not matches:
        raise RuntimeError("work root mount unavailable")
    _, fstype, options = max(matches, key=lambda item: item[0])
    opts = dict(option.split("=", 1) for option in options.split(",") if "=" in option)
    if (fstype != "tmpfs" or not re.fullmatch(r"[1-9][0-9]*[kKmMgG]?", opts.get("size", ""))
            or not re.fullmatch(r"[1-9][0-9]*[kKmM]?", opts.get("nr_inodes", ""))):
        raise RuntimeError("work root requires tmpfs size and nr_inodes caps")


def _copy_source(source: Path, dest: Path, budget: list[int] | None = None) -> None:
    excluded = {".git", "__pycache__", ".venv", "venv", "node_modules", ".env"}
    if source.is_symlink() or not source.is_dir():
        raise ValueError("source_dir must be a real directory")
    budget = budget if budget is not None else [0, 0]
    for current, dirs, files in os.walk(source, followlinks=False):
        base = Path(current)
        dirs[:] = [d for d in dirs if d not in excluded and not (base / d).is_symlink()]
        # Count directories too, to prevent unbounded inode consumption during ingestion.
        budget[0] += len(dirs)
        if budget[0] > 2000:
            raise ValueError("source too large")
        target = dest / base.relative_to(source)
        target.mkdir(parents=True, exist_ok=True)
        for name in files:
            src = base / name
            if (name in excluded or name.startswith(".env.") or name.endswith((".pyc", ".db"))
                    or src.is_symlink() or not src.is_file()):
                continue
            budget[0] += 1
            if budget[0] > 2000:
                raise ValueError("source too large")
            with src.open("rb") as inp, (target / name).open("wb") as out:
                while True:
                    chunk = inp.read(min(65536, 25 * 1024 * 1024 - budget[1] + 1))
                    if not chunk:
                        break
                    budget[1] += len(chunk)
                    if budget[1] > 25 * 1024 * 1024:
                        raise ValueError("source too large")
                    out.write(chunk)


def _remove_tree(root: Path) -> None:
    if not root.exists():
        return
    if os.name == "nt":
        shutil.rmtree(root)
        return

    # Restore traversal before rmtree descends; retrying scandir via onerror alone
    # would not cause rmtree to recurse into the newly accessible directory.
    os.chmod(root, 0o700, follow_symlinks=False)
    def walk_error(error: OSError) -> None:
        raise error
    for current, dirs, _files in os.walk(root, topdown=True, onerror=walk_error, followlinks=False):
        for directory in dirs:
            child = Path(current) / directory
            if not child.is_symlink():
                os.chmod(child, 0o700, follow_symlinks=False)

    def repair(function: Any, path: str, _exc: Any) -> None:
        os.chmod(path, 0o700, follow_symlinks=False)
        function(path)

    shutil.rmtree(root, onerror=repair)
    if root.exists():
        raise OSError("workspace cleanup failed")


def _read_events(path: Path) -> list[TelemetryEvent]:
    events: list[TelemetryEvent] = []
    if path.is_symlink() or not path.is_file():
        return events
    total = records = 0
    truncated = False
    with path.open("rb") as stream:
        while total < 256 * 1024 and records < 500:
            remaining = 256 * 1024 - total
            line = stream.readline(min(8193, remaining))
            if not line:
                break
            total += len(line)
            records += 1
            if len(line) > 8192 or (not line.endswith(b"\n") and len(line) == min(8193, remaining)):
                truncated = True
                break
            try:
                events.append(TelemetryEvent.model_validate(json.loads(line)))
            except (ValueError, TypeError):
                pass
        if total >= 256 * 1024 or records >= 500:
            truncated = True
    if truncated:
        events.append(TelemetryEvent(type="telemetry_truncated", detail="telemetry limits reached", ts=_now().isoformat()))
    return events


class DockerSandboxRunner:
    def __init__(self, settings: Any, client: Any = None):
        self.settings = settings
        self.client = client if client is not None else docker.from_env()
        self._semaphore = asyncio.Semaphore(settings.sandbox_concurrency)
        self._lock = threading.RLock()
        self._leaked: set[str] = set()
        self._active: set[str] = set()

    async def run(self, spec: SandboxSpec) -> SandboxResult:
        async with self._semaphore:
            cancel = threading.Event()
            task = asyncio.create_task(asyncio.to_thread(self._run, spec, cancel))
            cancelled = False
            while True:
                try:
                    result = await asyncio.shield(task)
                    if cancelled:
                        raise asyncio.CancelledError
                    return result
                except asyncio.CancelledError:
                    if task.done():
                        raise
                    cancelled = True
                    cancel.set()

    def _cleanup_container(self, name: str, container: Any = None) -> bool:
        for attempt in range(3):
            if attempt:
                time.sleep(0.05 * attempt)
            try:
                if container is None:
                    container = self.client.containers.get(name)
                try:
                    container.kill()
                except NotFound:
                    return True
                except Exception:  # noqa: BLE001, S110
                    pass
                try:
                    container.remove(force=True)
                except NotFound:
                    return True
                except Exception:  # noqa: BLE001, S110
                    pass
                try:
                    self.client.containers.get(name)
                except NotFound:
                    return True
                container = None
            except NotFound:
                return True
            except Exception:  # noqa: BLE001
                container = None
        return False

    def _run(self, spec: SandboxSpec, cancel: threading.Event | None = None) -> SandboxResult:
        sid = uuid.uuid4().hex
        name = "pod-" + sid
        base = Path(self.settings.work_root).resolve()
        root = base / sid
        work, telemetry_dir = root / "work", root / "telemetry"
        container = None
        started, clock = _now(), time.monotonic()
        exit_code, outcome, output = None, "error", b""
        events: list[TelemetryEvent] = []
        collected: dict[str, str] = {}
        digest = destroyed = cleanup_error = artifact_error = None
        create_attempted = False
        watcher_done = threading.Event()
        try:
            with self._lock:
                if self._leaked:
                    self._reap_orphans()
                if self._leaked:
                    raise RuntimeError("pending sandbox cleanup")
                self._active.add(sid)
            if (sys.platform == "linux"
                    and getattr(self.settings, "require_capped_workroot", self.settings.runtime == "runsc")):
                _verify_work_root(base)
            work.mkdir(parents=True)
            telemetry_dir.mkdir()
            budget = [0, 0]
            if spec.source_dir is not None:
                _copy_source(spec.source_dir, work, budget)
            for filename, content in (spec.files or {}).items():
                data = content.encode() if isinstance(content, str) else content
                budget[0] += 1
                budget[1] += len(data)
                if budget[0] > 2000 or budget[1] > 25 * 1024 * 1024:
                    raise ValueError("source too large")
                path = _safe_path(work, filename)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            if os.name != "nt":
                for current, _dirs, files in os.walk(root):
                    os.chmod(current, 0o777)
                    for filename in files:
                        os.chmod(Path(current) / filename, 0o666)
            kwargs = build_run_kwargs(spec, self.settings, {"work": work, "telemetry": telemetry_dir})
            kwargs["name"] = name
            kwargs.pop("detach", None)  # create() differs from run()
            create_attempted = True
            container = self.client.containers.create(**kwargs)
            container.start()
            if cancel is not None:
                def watch() -> None:
                    while not watcher_done.wait(0.1):
                        if cancel.is_set():
                            try:
                                container.kill()
                            except Exception:  # noqa: BLE001, S110
                                pass
                            return
                threading.Thread(target=watch, daemon=True).start()
            wait_started = time.monotonic()
            try:
                result = container.wait(timeout=spec.limits.timeout_s)
                exit_code = result.get("StatusCode")
                outcome = "passed" if exit_code == 0 else "error"
            except Exception as exc:
                waited = time.monotonic() - wait_started
                if not isinstance(exc, (ReadTimeout, TimeoutError)) and waited < spec.limits.timeout_s * 0.95:
                    raise
                outcome = "timeout_killed"
                try:
                    container.kill()
                except Exception:  # noqa: BLE001, S110
                    pass
            try:
                container.reload()
                # Timeout wins when both observations are present: the wall-clock kill is certain.
                if outcome != "timeout_killed" and container.attrs.get("State", {}).get("OOMKilled"):
                    outcome = "oom_killed"
                digest = container.attrs.get("Image")
            except Exception:  # noqa: BLE001
                artifact_error = "artifact collection failed"
            try:
                output = container.logs(stdout=True, stderr=True) or b""
                if isinstance(output, str):
                    output = output.encode()
            except Exception:  # noqa: BLE001
                artifact_error = "artifact collection failed"
            try:
                events = _read_events(telemetry_dir / "events.jsonl")
                if b"POD-CANARY" in output or b"AKIAPODCANARY" in output:
                    events.append(TelemetryEvent(type="exfil_attempt", detail="canary in output", ts=_now().isoformat()))
                for filename in spec.collect:
                    path = _safe_path(work, filename)
                    if path.is_file() and not path.is_symlink():
                        with path.open("rb") as stream:
                            collected[filename] = stream.read(256 * 1024).decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                artifact_error = "artifact collection failed"
            if outcome == "error" and exit_code == 1 and spec.purpose.startswith(("baseline", "mutation:", "gap_test:")):
                from .junit import parse_junit
                for filename, content in collected.items():
                    if filename.endswith("junit.xml"):
                        try:
                            if any(case["outcome"] == "failed" and case["name"] for case in parse_junit(content)):
                                outcome = "tests_failed"
                        except (ValueError, TypeError):
                            artifact_error = "artifact collection failed"
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, ValueError) and str(exc) == "source too large":
                artifact_error = "source too large"
        finally:
            watcher_done.set()
            ended = _now()
            absent = not create_attempted or self._cleanup_container(name, container)
            if absent:
                try:
                    _remove_tree(root)
                except Exception:  # noqa: BLE001
                    cleanup_error = "workspace cleanup failed"
            else:
                cleanup_error = "container cleanup unconfirmed"
            with self._lock:
                self._active.discard(sid)
                if cleanup_error:
                    self._leaked.add(sid)
                else:
                    self._leaked.discard(sid)
                    destroyed = _now()
        return SandboxResult(sandbox_id=sid, run_id=spec.run_id, purpose=spec.purpose,
            image=self.settings.sandbox_image, image_digest=digest, runtime=self.settings.runtime,
            limits=spec.limits, started_at=started, ended_at=ended,
            duration_ms=int((time.monotonic() - clock) * 1000), exit_code=exit_code,
            outcome=outcome, telemetry=events, stdout_tail=output[-16384:].decode("utf-8", errors="replace"),
            stdout_sha256=hashlib.sha256(output).hexdigest(), destroyed_at=destroyed, collected=collected,
            cleanup_error=cleanup_error, artifact_error=artifact_error)

    def leaked_count(self) -> int:
        with self._lock:
            return len(self._leaked)

    async def reap_orphans(self) -> None:
        await asyncio.to_thread(self._reap_orphans)

    def _reap_orphans(self) -> None:
        with self._lock:
            containers = self.client.containers.list(all=True, filters={"label": "pod.sandbox=1"})
            for container in containers:
                name = container.name.lstrip("/")
                if name.startswith("pod-") and name[4:] in self._active:
                    continue
                if not self._cleanup_container(name, container):
                    self._leaked.add(name.removeprefix("pod-"))
                    raise RuntimeError("container cleanup unconfirmed")
            base = Path(self.settings.work_root).resolve()
            if base.exists():
                for root in base.iterdir():
                    if not re.fullmatch(r"[0-9a-f]{32}", root.name) or root.name in self._active or root.is_symlink():
                        continue
                    if not self._cleanup_container("pod-" + root.name):
                        self._leaked.add(root.name)
                        raise RuntimeError("container cleanup unconfirmed")
                    try:
                        _remove_tree(root)
                    except Exception as exc:
                        self._leaked.add(root.name)
                        raise RuntimeError("workspace cleanup failed") from exc
                    self._leaked.discard(root.name)
            # Also reconcile failed create calls with no workspace.
            for sid in list(self._leaked):
                if not self._cleanup_container("pod-" + sid):
                    raise RuntimeError("container cleanup unconfirmed")
                _remove_tree(base / sid)
                self._leaked.discard(sid)


class FakeSandboxRunner:
    def __init__(self, result: SandboxResult):
        self.result = result
        self.specs: list[SandboxSpec] = []

    async def run(self, spec: SandboxSpec) -> SandboxResult:
        self.specs.append(spec)
        return self.result.model_copy(deep=True)

    async def reap_orphans(self) -> None:
        pass
