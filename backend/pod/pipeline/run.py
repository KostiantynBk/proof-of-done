"""Orchestrate pipeline stages and persist the last completed state."""
import asyncio
import json
import os
import re
import sys

from pod.pipeline.audit import audit_items
from pod.pipeline.baseline import run_baseline
from pod.pipeline.broadcast import log_caught, pod_logger, redact_secrets
from pod.pipeline.context import RunContext, RunState
from pod.pipeline.export import export_all
from pod.pipeline.extract import extract_requirements
from pod.pipeline.gaps import close_gaps
from pod.pipeline.ingest import prepare_inputs
from pod.pipeline.mine import mine_rules
from pod.pipeline.prove import prove_mutations
from pod.pipeline.sabotage import sabotage_items
from pod.pipeline.summary import build_summary
from pod.pipeline.verdict import judge
from pod.projects import get_project


class DiagnosticErrors(list):
    """Record exceptions when isolated pipeline stages append their error messages."""

    def __init__(self, context, values=()):
        super().__init__(values)
        self.context = context

    def append(self, value):
        exc = sys.exception()
        if exc is not None:
            text = str(value)
            stage = text.split(':', 1)[0][:100] or 'pipeline'
            match = re.search(r'\b(?:REQ|RULE)-[A-Za-z0-9_-]+', text)
            log_caught(self.context, stage, match.group() if match else None, exc)
        super().append(value)


class PublicJournalStore:
    """Sanitize stage failures before the shared context stores or broadcasts them."""

    def __init__(self, store):
        self._store = store
        self.context = None

    def __getattr__(self, name):
        return getattr(self._store, name)

    def append_event(self, run_id, event_type, payload, *args, **kwargs):
        if event_type == 'stage_finished' and payload.get('status') == 'failed':
            exc = sys.exception()
            if exc is not None:
                log_caught(self.context, payload.get('stage', 'unknown'), None, exc)
            payload = {**payload, 'error': 'Stage failed'}
        return self._store.append_event(run_id, event_type, payload, *args, **kwargs)


def persist(ctx, status=None):
    # Stage/model exceptions can contain credentials or host paths; public persistence
    # stores only a fixed diagnostic rather than arbitrary exception text.
    debug_enabled = os.getenv('POD_DEBUG_ERRORS') == '1'
    if debug_enabled:
        debug = getattr(ctx, '_errors_debug', None)
        if debug is None:
            existing = ctx.store.get_artifact(ctx.run_id, 'state.json')
            debug = json.loads(existing['content']).get('errors_debug', []) if existing else []
            ctx._errors_debug = debug
        debug.extend(redact_secrets(error) for error in ctx.state.errors if error != 'Pipeline component failed')
    ctx.state.errors[:] = ['Pipeline component failed' for _ in ctx.state.errors]
    for result in ctx.state.results.values():
        if result.detail and result.detail != 'Mutation execution failed':
            # T5 has already caught this exception; its traceback is no longer available here.
            pod_logger().error('stage=S9:prove item_id=%s caught failure: %s', result.item_id, result.detail)
            if debug_enabled:
                ctx._errors_debug.append(redact_secrets(f'mutation {result.item_id}: {result.detail}'))
            result.detail = 'Mutation execution failed'
    document = ctx.state.model_dump(mode='json')
    if debug_enabled:
        document['errors_debug'] = ctx._errors_debug
    ctx.store.put_artifact(ctx.run_id, 'state.json', json.dumps(document, ensure_ascii=False))
    if status:
        ctx.store.update_run(ctx.run_id, status=status)
    ctx.store.update_run(ctx.run_id, summary=build_summary(ctx.store, ctx.run_id))


def record_failure(store, run_id, broadcaster, message):
    """Best effort: never expose underlying exception messages."""
    try:
        store.update_run(run_id, status='failed')
        event = store.append_event(run_id, 'error', {'message': message})
        broadcaster.publish(event)
        event = store.append_event(run_id, 'run_finished', {'status': 'failed'})
        broadcaster.publish(event)
    except Exception as exc:  # noqa: BLE001 -- storage may itself be unavailable
        log_caught(None, 'terminal_journal', None, exc)


async def execute_run(run_id, project_ref, *, settings, llm, sandbox, store, broadcaster):
    pod_logger()
    ctx = None
    cancelled = False
    try:
        run_dir = settings.data_dir / 'runs' / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        public_store = PublicJournalStore(store)
        ctx = RunContext(run_id=run_id, project_dir=run_dir / 'project', spec_text='', history=[],
                         settings=settings, llm=llm, sandbox=sandbox, store=public_store,
                         emit_hook=broadcaster.publish)
        public_store.context = ctx
        ctx.state.errors = DiagnosticErrors(ctx, ctx.state.errors)
        original_llm_call, original_run_tests, original_run_sandbox = (
            ctx.llm_call, ctx.run_tests, ctx.run_sandbox)

        async def logged_llm_call(role, messages, *args, **kwargs):
            try:
                return await original_llm_call(role, messages, *args, **kwargs)
            except Exception as exc:
                item_id = None
                for message in messages:
                    content = message.get('content', '')
                    if not isinstance(content, str):
                        continue
                    try:
                        decoded = json.loads(content)
                        text = decoded.get('item', '') if isinstance(decoded, dict) else ''
                    except (ValueError, TypeError):
                        text = content[:500]
                    match = re.search(r'\b(?:REQ|RULE)-[A-Za-z0-9_-]+', text) if isinstance(text, str) else None
                    if match:
                        item_id = match.group()
                        break
                log_caught(ctx, f'llm:{role}', item_id, exc)
                raise

        async def logged_run_tests(purpose, *args, **kwargs):
            try:
                return await original_run_tests(purpose, *args, **kwargs)
            except Exception as exc:
                match = re.search(r'\b(?:REQ|RULE)-[A-Za-z0-9_-]+', purpose)
                log_caught(ctx, f'sandbox:{purpose}', match.group() if match else None, exc)
                raise

        async def logged_run_sandbox(spec):
            try:
                return await original_run_sandbox(spec)
            except Exception as exc:
                purpose = spec.purpose
                match = re.search(r'\b(?:REQ|RULE)-[A-Za-z0-9_-]+', purpose)
                log_caught(ctx, f'sandbox:{purpose}', match.group() if match else None, exc)
                raise

        ctx.llm_call, ctx.run_tests, ctx.run_sandbox = logged_llm_call, logged_run_tests, logged_run_sandbox

        def on_event(event):
            broadcaster.publish(event)
            if event['type'] == 'stage_finished':
                persist(ctx)

        ctx.emit_hook = on_event
        persist(ctx, 'running')
        if sandbox is None:
            raise RuntimeError('sandbox unavailable')
        await prepare_inputs(ctx, get_project(project_ref))
        persist(ctx)
        for stage in (extract_requirements, mine_rules, run_baseline, audit_items,
                      sabotage_items, prove_mutations, judge, close_gaps, export_all):
            await stage(ctx)
            persist(ctx)
        status = 'awaiting_approval' if any(r.approved is None for r in ctx.state.gap_results.values()) else 'done'
        persist(ctx, status)
        ctx.emit('run_finished', {'status': status})
        persist(ctx)
    except asyncio.CancelledError:
        cancelled = True
        record_failure(store, run_id, broadcaster, 'Run cancelled during shutdown')
    except Exception as exc:  # noqa: BLE001 -- terminal failure boundary
        log_caught(ctx, 'orchestrator', None, exc)
        message = ('Sandbox unavailable: Docker daemon could not be reached' if sandbox is None
                   else 'Pipeline execution failed')
        if ctx is not None:
            ctx.state.errors.append(message)
            try:
                persist(ctx)
            except Exception as persist_exc:  # noqa: BLE001 -- storage may itself be unavailable
                log_caught(ctx, 'state_persist', None, persist_exc)
        record_failure(store, run_id, broadcaster, message)
    if cancelled:
        raise asyncio.CancelledError
    return ctx.state if ctx else RunState()
