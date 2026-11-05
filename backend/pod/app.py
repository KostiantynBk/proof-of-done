"""Health and contained playground endpoints."""

import asyncio
import json
import os
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from docker.errors import DockerException
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator

from pod.config import Settings
from pod.llm import VultrLLM, _budget
from pod.pipeline.broadcast import Broadcaster, log_caught, pod_logger
from pod.pipeline.context import RunContext
from pod.pipeline.export import digest, export_all
from pod.pipeline.gaps import apply_gap, checked_approval
from pod.pipeline.run import execute_run, persist, record_failure
from pod.pipeline.summary import (
    EXPORTS,
    build_summary,
    eligible_recording,
    event_page,
    item_detail,
    load_state,
    recent_runs,
)
from pod.playground_presets import PRESETS
from pod.projects import get_project
from pod.sandbox.models import SandboxLimits, SandboxSpec
from pod.sandbox.runner import DockerSandboxRunner
from pod.store import Store


class BodyLimitMiddleware:
    """Bound the body before FastAPI/Starlette parses or buffers it."""

    def __init__(self, app, limit=64 * 1024):
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        lengths = [v for k, v in scope.get('headers', []) if k.lower() == b'content-length']
        if lengths:
            try:
                values = [int(v) for v in lengths]
                if any(v < 0 for v in values) or len(set(values)) != 1:
                    raise ValueError('invalid content length')
            except ValueError as exc:
                log_caught(None, 'http:content_length', None, exc)
                await JSONResponse({'detail': 'invalid content length'}, status_code=400)(scope, receive, send)
                return
            if values[0] > self.limit:
                await JSONResponse({'detail': 'request body too large'}, status_code=413)(scope, receive, send)
                return
        buffered = bytearray()
        size = 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            if message['type'] != 'http.request':
                continue
            chunk = message.get('body', b'')
            size += len(chunk)
            if size > self.limit:
                await JSONResponse({'detail': 'request body too large'}, status_code=413)(scope, receive, send)
                return
            buffered.extend(chunk)
            if not message.get('more_body', False):
                break
        body = bytes(buffered)
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            return await receive()

        await self.app(scope, bounded_receive, send)


class RunRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project: str = Field(max_length=200)


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    approve: bool
    reviewer: str = Field(min_length=1, max_length=60, pattern=r'.*\S.*')


class PlaygroundRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    preset: str | None = Field(default=None, max_length=64)
    code: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_choice(self):
        if (self.preset is None) == (self.code is None):
            raise ValueError("provide exactly one of preset or code")
        return self


def create_app(settings: Settings | None = None, runner=None, store=None) -> FastAPI:
    settings = settings or Settings()
    pod_logger()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        app.state.store = store or Store(settings.data_dir / "pod.sqlite3")
        app.state.runner = runner
        app.state.sandbox_available = False
        try:
            if app.state.runner is None:
                app.state.runner = await asyncio.to_thread(DockerSandboxRunner, settings)
            await app.state.runner.reap_orphans()
            app.state.sandbox_available = True
        except (DockerException, OSError) as exc:
            log_caught(None, 'startup:orphan_reaping', None, exc)
            # An unavailable daemon must not prevent health reporting.
            app.state.sandbox_available = False
        yield
        for task in tuple(active):
            task.cancel()
        if active:
            await asyncio.gather(*tuple(active), return_exceptions=True)
        if store is None:
            app.state.store.close()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(BodyLimitMiddleware)
    requests: dict[str, deque[float]] = {}
    lock = asyncio.Lock()
    broadcaster = Broadcaster()
    run_requests: dict[str, deque[float]] = {}
    app.state.rate_limit_buckets = (requests, run_requests)
    # Reserve at least one shared runner slot for the pipeline, even if reconfigured.
    playground_capacity = min(2, max(0, settings.sandbox_concurrency - 1))
    playground_slots = asyncio.Semaphore(playground_capacity)
    active: set[asyncio.Task] = set()
    approval_lock = asyncio.Lock()
    stream_counts: dict[str, int] = {}
    stream_total = 0

    def leaked_count():
        runner_instance = app.state.runner
        counter = getattr(runner_instance, 'leaked_count', None)
        return counter() if counter else 0

    def rate_queue(mapping, ip, now):
        # Expire empty keys globally, not just for the current client; never evict
        # active keys to make room for a new client.
        for table, expiry in ((requests, 600), (run_requests, 3600)):
            for key, queue in list(table.items()):
                while queue and queue[0] <= now - expiry:
                    queue.popleft()
                if not queue:
                    del table[key]
        if ip not in mapping:
            all_ips = requests.keys() | run_requests.keys()
            if ip not in all_ips and len(all_ips) >= 10000:
                raise HTTPException(429, 'rate limit capacity reached')
            mapping[ip] = deque()
        return mapping[ip]

    def recorded_id():
        pin = settings.data_dir / 'recorded-run-id'
        value = os.getenv('POD_RECORDED_RUN_ID') or (pin.read_text().strip() if pin.is_file() else '')
        return value if value and eligible_recording(app.state.store, value) else None

    @app.get("/api/health")
    async def health():
        return {"ok": True, "region": os.getenv('POD_REGION', 'unknown'),
                "inference_base_url": settings.inference_base_url,
                "runtime": settings.runtime, "sandbox_image": settings.sandbox_image,
                "sandbox_available": app.state.sandbox_available, "sandbox_leaked": leaked_count(),
                "models": settings.models,
                "fake_llm": settings.fake_llm and not bool(settings.inference_api_key)}

    @app.get("/api/playground/presets")
    async def presets():
        return PRESETS

    @app.post("/api/playground")
    async def playground(body: PlaygroundRequest, request: Request):
        now = time.monotonic()
        ip = request.client.host if request.client else "unknown"
        if not app.state.sandbox_available:
            raise HTTPException(503, "sandbox unavailable")
        if leaked_count() > 0:
            raise HTTPException(503, 'sandbox cleanup pending')
        code = body.code
        if body.preset is not None:
            selected = next((p for p in PRESETS if p["id"] == body.preset), None)
            if selected is None:
                raise HTTPException(404, "unknown preset")
            code = selected["code"]
        spec = SandboxSpec(purpose="playground", run_id=None, files={"snippet.py": code},
                           command=["python", "/work/snippet.py"],
                           limits=SandboxLimits(memory_mb=settings.memory_mb, cpus=settings.cpus,
                                                pids=settings.pids, timeout_s=20))
        async with lock:
            if leaked_count() > 0:
                raise HTTPException(503, 'sandbox cleanup pending')
            queue = rate_queue(requests, ip, now)
            if len(queue) >= 10:
                raise HTTPException(429, 'playground rate limit exceeded')
            if playground_capacity == 0:
                raise HTTPException(503, 'no playground capacity reserved')
            if playground_slots.locked():
                raise HTTPException(429, 'playground capacity reached')
            await playground_slots.acquire()
            queue.append(now)
        try:
            result = await app.state.runner.run(spec)
            app.state.store.save_sandbox_result(result)
            return {"sandbox_run": result}
        finally:
            playground_slots.release()

    @app.get("/api/sandboxes")
    async def sandboxes(limit: int = 50):
        if not 1 <= limit <= 500:
            raise HTTPException(422, "limit must be between 1 and 500")
        return app.state.store.recent_sandboxes(limit)

    @app.get('/api/runs/recorded')
    async def recorded():
        pin = recorded_id()
        if not pin:
            raise HTTPException(404, 'no recorded run')
        simulated = any(e['type'] == 'llm_call' and e['payload'].get('model') == 'fake'
                        for e in app.state.store.list_events(pin))
        return {'run_id': pin, 'simulated': simulated}

    @app.get('/api/runs')
    async def runs():
        result = []
        for run in recent_runs(app.state.store, 20):
            summary = build_summary(app.state.store, run['id'])
            result.append({k: summary[k] for k in ('id', 'created_at', 'status', 'project', 'counts')})
        return result

    @app.post('/api/runs')
    async def start_run(body: RunRequest, request: Request):
        try:
            get_project(body.project)
        except (ValueError, NotImplementedError) as exc:
            log_caught(None, 'api:run_validation', None, exc)
            raise HTTPException(422, 'unsupported project') from None
        if leaked_count() > 0:
            raise HTTPException(503, 'sandbox cleanup pending')
        ip = request.client.host if request.client else 'unknown'
        async with lock:
            if leaked_count() > 0:
                raise HTTPException(503, 'sandbox cleanup pending')
            now = time.monotonic()
            queue = rate_queue(run_requests, ip, now)
            if len(queue) >= 3:
                raise HTTPException(429, 'run rate limit exceeded')
            if len(active) >= max(1, int(os.getenv('POD_MAX_ACTIVE_RUNS', '1'))):
                raise HTTPException(429, 'run concurrency limit reached')
            with _budget.lock:
                from datetime import UTC, datetime
                used = _budget.used + _budget.reserved if _budget.day == datetime.now(UTC).date().isoformat() else 0
            if settings.daily_token_budget - used < 400000:
                return JSONResponse(status_code=429, content={'detail': 'daily token budget low',
                                                              'recorded_run_id': recorded_id()})
            queue.append(now)
            run_id = uuid.uuid4().hex
            app.state.store.create_run(run_id, body.project, status='queued')
            if settings.fake_llm and not settings.inference_api_key:
                from pod.demo_script import demo_llm
                llm = demo_llm()
            else:
                llm = VultrLLM(settings)
            task = asyncio.create_task(execute_run(run_id, body.project, settings=settings, llm=llm,
                                       sandbox=app.state.runner if app.state.sandbox_available else None,
                                       store=app.state.store, broadcaster=broadcaster))
            active.add(task)

            def observe(finished):
                active.discard(finished)
                if finished.cancelled():
                    return
                if finished.exception() is not None:
                    record_failure(app.state.store, run_id, broadcaster, 'Pipeline task failed')

            task.add_done_callback(observe)
        return {'run_id': run_id}

    @app.get('/api/runs/{run_id}')
    async def run_summary(run_id: str):
        result = build_summary(app.state.store, run_id)
        if result is None:
            raise HTTPException(404, 'run not found')
        return result

    @app.get('/api/runs/{run_id}/items/{item_id}')
    async def item(run_id: str, item_id: str):
        result = item_detail(app.state.store, run_id, item_id)
        if result is None:
            raise HTTPException(404, 'item not found')
        return result

    @app.get('/api/runs/{run_id}/events')
    async def events(run_id: str, request: Request, after: int = 0):
        nonlocal stream_total
        if not app.state.store.get_run(run_id):
            raise HTTPException(404, 'run not found')
        try:
            header_cursor = int(request.headers.get('last-event-id', '0'))
            if not 0 <= after <= 9223372036854775807 or not 0 <= header_cursor <= 9223372036854775807:
                raise ValueError('cursor out of range')
            cursor = max(after, header_cursor)
        except ValueError as exc:
            log_caught(None, 'api:sse_cursor', None, exc)
            raise HTTPException(400, 'invalid event cursor') from None
        ip = request.client.host if request.client else 'unknown'
        async with lock:
            if stream_total >= 100 or stream_counts.get(ip, 0) >= 5:
                raise HTTPException(429, 'stream capacity reached')
            stream_total += 1
            stream_counts[ip] = stream_counts.get(ip, 0) + 1
            queue = broadcaster.subscribe(run_id)

        async def stream():
            nonlocal cursor, stream_total
            started = time.monotonic()
            try:
                while time.monotonic() - started < 300:
                    batch = event_page(app.state.store, run_id, cursor)
                    for event in batch:
                        cursor = event['seq']
                        data = {k: event[k] for k in ('seq', 'ts', 'type', 'payload')}
                        yield f'id: {cursor}\ndata: {json.dumps(data)}\n\n'
                    if batch:
                        continue
                    if not batch:
                        run = app.state.store.get_run(run_id)
                        with app.state.store._lock:
                            finished = app.state.store._db.execute(
                                "SELECT 1 FROM events WHERE run_id=? AND seq<=? AND type='run_finished' LIMIT 1",
                                (run_id, cursor)).fetchone()
                        if run and run['status'] in ('done', 'failed', 'awaiting_approval') and finished:
                            return
                    try:
                        await asyncio.wait_for(queue.get(), 15)
                    except TimeoutError:
                        yield ': heartbeat\n\n'
            finally:
                broadcaster.unsubscribe(run_id, queue)
                async with lock:
                    stream_total -= 1
                    stream_counts[ip] -= 1
                    if not stream_counts[ip]:
                        del stream_counts[ip]
        return StreamingResponse(stream(), media_type='text/event-stream')

    @app.post('/api/runs/{run_id}/approvals/{item_id}')
    async def approve(run_id: str, item_id: str, body: ApprovalRequest):
        async with approval_lock:
            state_artifact = app.state.store.get_artifact(run_id, 'state.json')
            if not state_artifact:
                raise HTTPException(404, 'no pending guard test')
            original_state_sha = state_artifact['sha256']
            state = load_state(app.state.store, run_id)
            result = state.gap_results.get(item_id)
            if not result or result.approved is not None or item_id not in state.verdicts:
                raise HTTPException(404, 'no pending guard test')
            ctx = RunContext(run_id, settings.data_dir / 'runs' / run_id / 'project', '', [],
                             settings, None, None, app.state.store, state, broadcaster.publish)
            binding = None
            if body.approve:
                try:
                    updated, binding = checked_approval(ctx, item_id)
                except (ValueError, OSError, KeyError, StopIteration, SyntaxError) as exc:
                    log_caught(ctx, 'api:approval_validation', item_id, exc)
                    raise HTTPException(409, 'guard proof is incomplete or stale') from None
                state.verdicts[item_id] = updated
            else:
                state.verdicts[item_id] = apply_gap(state.verdicts[item_id], result.model_copy(update={'approved': False}))
            result.approved, result.approved_by = body.approve, body.reviewer.strip()
            signoff = {'item_id': item_id, 'approve': body.approve,
                       'reviewer': result.approved_by, 'statement': 'Review guard test',
                       'binding_sha256': digest(binding) if binding else None,
                       'result_sha256': digest({'verdict': state.verdicts[item_id].model_dump(mode='json'),
                                                'gap_result': result.model_dump(mode='json'), 'binding': binding})}
            # Render against a consistent in-memory snapshot; publish the new journal,
            # state and all four exports together in a single SQLite transaction.
            original = app.state.store
            staged = Store(':memory:')
            try:
                with original._lock, staged._db:
                    source = original._db
                    target = staged._db
                    baseline_seq = source.execute('SELECT COALESCE(MAX(seq), 0) FROM events').fetchone()[0]
                    previous_state = original.get_artifact(run_id, 'state.json')['sha256']
                    if previous_state != original_state_sha:
                        raise ValueError('concurrent state change')
                    run_row = source.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
                    target.execute('INSERT INTO runs VALUES (?, ?, ?, ?, ?)', tuple(run_row))
                    for table in ('events', 'sandbox_runs', 'artifacts'):
                        rows = source.execute(f'SELECT * FROM {table} WHERE run_id=?', (run_id,)).fetchall()
                        if rows:
                            placeholders = ','.join('?' for _ in rows[0])
                            target.executemany(f'INSERT INTO {table} VALUES ({placeholders})',
                                               [tuple(row) for row in rows])
                    # Preserve the global SQLite sequence without copying unrelated rows.
                    if baseline_seq and not target.execute('SELECT 1 FROM events WHERE seq=?', (baseline_seq,)).fetchone():
                        target.execute('INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)',
                                       (baseline_seq, '__staging__', '', 'placeholder', '{}', '', ''))
                ctx.store, ctx.emit_hook = staged, None
                ctx.emit('signoff', signoff)
                persist(ctx)
                await export_all(ctx)
                status = ('awaiting_approval' if any(g.approved is None for g in state.gap_results.values())
                          else 'done')
                persist(ctx, status)
                new_events = staged._db.execute('SELECT * FROM events WHERE seq > ? ORDER BY seq',
                                                (baseline_seq,)).fetchall()
                new_artifacts = staged._db.execute('SELECT * FROM artifacts WHERE run_id=? AND name IN '
                    "('state.json','report.md','AGENTS.md','receipts.json','patch.diff')", (run_id,)).fetchall()
                if len(new_artifacts) != 5:
                    raise ValueError('incomplete export generation')
                staged_run = staged.get_run(run_id)
                with original._lock:
                    db = original._db
                    try:
                        db.execute('BEGIN IMMEDIATE')
                        current_seq = db.execute('SELECT COALESCE(MAX(seq), 0) FROM events').fetchone()[0]
                        current_state = db.execute('SELECT sha256 FROM artifacts WHERE run_id=? AND name=?',
                                                   (run_id, 'state.json')).fetchone()
                        if current_seq != baseline_seq or not current_state or current_state[0] != previous_state:
                            raise ValueError('concurrent publication')
                        db.executemany('INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)',
                                       [tuple(row) for row in new_events])
                        db.executemany('INSERT OR REPLACE INTO artifacts VALUES (?, ?, ?, ?)',
                                       [tuple(row) for row in new_artifacts])
                        db.execute('UPDATE runs SET status=?, summary_json=? WHERE id=?',
                                   (staged_run['status'], staged_run['summary_json'], run_id))
                        db.commit()
                    except BaseException as exc:
                        log_caught(ctx, 'api:approval_transaction', item_id, exc)
                        db.rollback()
                        raise
                for event in original.list_events(run_id, baseline_seq):
                    broadcaster.publish(event)
            except Exception as exc:  # noqa: BLE001 -- no partially published approval; retry remains possible
                log_caught(ctx, 'api:approval_export', item_id, exc)
                raise HTTPException(503, 'approval export failed; retry') from None
            finally:
                staged.close()
            return item_detail(original, run_id, item_id)

    @app.get('/api/runs/{run_id}/export/{name}')
    async def download(run_id: str, name: str):
        if name not in EXPORTS:
            raise HTTPException(404, 'unknown export')
        artifact = app.state.store.get_artifact(run_id, name)
        if not artifact:
            raise HTTPException(404, 'export not available')
        media = 'application/json' if name.endswith('.json') else 'text/plain'
        return Response(content=artifact['content'], media_type=media,
                        headers={'Content-Disposition': f'attachment; filename="{name}"'})

    frontend = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'
    if frontend.is_dir():
        if (frontend / 'assets').is_dir():
            app.mount('/assets', StaticFiles(directory=frontend / 'assets'), name='assets')

        @app.get('/{path:path}')
        async def spa(path: str):
            if path == 'api' or path.startswith('api/'):
                raise HTTPException(404, 'not found')
            target = (frontend / path).resolve()
            if target.is_relative_to(frontend.resolve()) and target.is_file():
                return FileResponse(target)
            return FileResponse(frontend / 'index.html')
    return app


app = create_app()
