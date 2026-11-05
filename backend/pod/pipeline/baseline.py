"""S4: two independent sandboxed baselines and containment telemetry."""
from pod.pipeline.context import RunContext
from pod.verify.snapshot import Snapshot


def validate_items(ctx: RunContext) -> None:
    ids = [item.id for item in ctx.state.items]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate item IDs')
    if any(not item.id.startswith('REQ-' if item.kind == 'requirement' else 'RULE-')
           for item in ctx.state.items):
        raise ValueError('item ID outside its kind namespace')


def require_tree(ctx: RunContext, snapshot: Snapshot) -> None:
    if not ctx.state.tree_sha256 or snapshot.tree_sha256() != ctx.state.tree_sha256:
        raise ValueError('source snapshot changed or baseline snapshot missing')

_INTERESTING = {'honeytoken_read', 'net_connect', 'dns_lookup', 'exfil_attempt',
                'sensitive_read', 'process_spawn', 'delete_outside_work'}


async def run_baseline(ctx: RunContext) -> None:
    async with ctx.stage('S4', 'baseline'):
        validate_items(ctx)
        snapshot = Snapshot(ctx.project_dir)
        tree_hash = snapshot.tree_sha256()
        if ctx.state.tree_sha256 and ctx.state.tree_sha256 != tree_hash:
            raise ValueError('source snapshot differs from ingested snapshot')
        ctx.state.tree_sha256 = tree_hash
        ctx.state.baseline_junit.clear()
        ctx.state.baseline_sandboxes.clear()
        ctx.state.baseline_ok = None
        ctx.state.evidence.clear()
        ctx.state.mutations.clear()
        ctx.state.results.clear()
        ctx.state.verdicts.clear()
        outcomes = []
        for number in (1, 2):
            purpose = f'baseline:{number}'
            result, cases = await ctx.run_tests(purpose)
            ctx.state.baseline_junit.append(cases)
            ctx.state.baseline_sandboxes.append(result.sandbox_id)
            outcomes.append(result.outcome)
            for event in result.telemetry:
                if event.type in _INTERESTING:
                    ctx.emit('containment_event', {'sandbox_id': result.sandbox_id,
                             'purpose': purpose, 'type': event.type, 'detail': event.detail})
        require_tree(ctx, Snapshot(ctx.project_dir))
        ctx.state.baseline_ok = (all(outcome == 'passed' for outcome in outcomes)
                                 and len(set(ctx.state.baseline_sandboxes)) == 2)
