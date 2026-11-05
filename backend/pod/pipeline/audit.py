"""S5–S6: bounded audit and deterministic evidence verification."""
import asyncio

from pod.pipeline.baseline import require_tree, validate_items
from pod.pipeline.context import RunContext
from pod.pipeline.prompts_prove import auditor_messages
from pod.schemas import AuditClaim
from pod.verify.evidence import verify_claim
from pod.verify.snapshot import Snapshot
from pod.verify.test_citations import index_tests


async def audit_items(ctx: RunContext) -> None:
    snapshot = Snapshot(ctx.project_dir)
    claims: dict[str, AuditClaim] = {}
    async with ctx.stage('S5', 'audit'):
        validate_items(ctx)
        require_tree(ctx, snapshot)
        ctx.state.evidence.clear()
        ctx.state.mutations.clear()
        ctx.state.results.clear()
        ctx.state.verdicts.clear()
        semaphore = asyncio.Semaphore(4)

        async def audit(item):
            if not item.source_verified or (item.kind == 'rule' and not item.accepted):
                return
            try:
                async with semaphore:
                    response = await ctx.llm_call('auditor', auditor_messages(item, snapshot),
                                                  AuditClaim, temperature=0.1)
                claim = response.value
                if claim.item_id != item.id:
                    ctx.emit('audit_id_corrected', {'item_id': item.id, 'claimed_id': claim.item_id})
                    claim = claim.model_copy(update={'item_id': item.id})
                claims[item.id] = claim
            except Exception as exc:  # noqa: BLE001 -- isolate untrusted calls
                ctx.state.errors.append(f'audit {item.id}: {type(exc).__name__}: {exc}'[:500])

        await asyncio.gather(*(audit(item) for item in ctx.state.items))
    async with ctx.stage('S6', 'verify evidence'):
        require_tree(ctx, Snapshot(ctx.project_dir))
        tests = index_tests(snapshot)
        for item in ctx.state.items:
            claim = claims.get(item.id)
            if claim is None:
                continue
            report = verify_claim(claim, snapshot, tests, ctx.state.baseline_junit)
            ctx.state.evidence[item.id] = report
            ctx.emit('evidence_verified', {'item_id': item.id, 'ai_status': claim.ai_status,
                     'code_verified': f'{sum(c.verified for c in report.code_checks)}/{len(report.code_checks)}',
                     'tests_guarding': f'{len(report.guarding_tests)}/{len(report.test_checks)}'})
