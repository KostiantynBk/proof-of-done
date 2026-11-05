"""S9: run each valid edit in its own sandbox and classify strictly."""
import asyncio
import logging

from pod.pipeline.baseline import require_tree, validate_items
from pod.pipeline.context import RunContext
from pod.schemas import Mutation, MutationResult
from pod.verify.snapshot import Snapshot
from pod.verify.test_citations import index_tests

logger = logging.getLogger("pod")


def stable_test_nodes(snapshot: Snapshot, runs: list[list[dict[str, str]]]) -> set[str]:
    if len(runs) != 2:
        return set()
    index = index_tests(snapshot)
    nodes = {c.get('nodeid', '') for run in runs for c in run}
    stable = {node for node in nodes if node and all(
        len(matches := [c for c in run if c.get('nodeid') == node]) == 1
        and matches[0].get('outcome') == 'passed' for run in runs)}
    valid = set()
    for node in stable:
        base = node.split('[', 1)[0]
        parts = base.replace('\\', '/').split('::')
        if len(parts) < 2:
            continue
        file, names = parts[0], parts[1:]
        if len(names) == 1 and (None, names[0]) in index.get(file, set()) or len(names) == 2 and (names[0], names[1]) in index.get(file, set()):
            valid.add(node)
    return valid


async def prove_mutations(ctx: RunContext) -> None:
    async with ctx.stage('S9', 'prove mutations'):
        snapshot = Snapshot(ctx.project_dir)
        validate_items(ctx)
        require_tree(ctx, snapshot)
        valid = [m for mutations in ctx.state.mutations.values() for m in mutations if m.valid]
        if len({m.id for m in valid}) != len(valid) or any(
                m.item_id != item_id for item_id, mutations in ctx.state.mutations.items() for m in mutations):
            raise ValueError('duplicate or misbound mutation identity')
        ctx.state.results.clear()
        ctx.state.verdicts.clear()
        stable_nodes = stable_test_nodes(snapshot, ctx.state.baseline_junit)

        async def prove(mutation: Mutation) -> MutationResult:
            sandbox_id = None
            duration_ms = None
            killed_by: list[str] = []
            detail = None
            try:
                path = snapshot.safe_path(mutation.file)
                if path is None:
                    raise ValueError('mutation target no longer in snapshot')
                source = path.read_text(encoding='utf-8')
                if not mutation.find or source.count(mutation.find) != 1:
                    raise ValueError('mutation target changed after validation')
                changed = source.replace(mutation.find, mutation.replace, 1)
                result, cases = await ctx.run_tests(f'mutation:{mutation.id}',
                    files={mutation.file: changed}, timeout_s=ctx.settings.mutation_timeout_s)
                sandbox_id, duration_ms = result.sandbox_id, result.duration_ms
                if result.outcome == 'passed':
                    outcome = 'survived'
                elif result.outcome == 'timeout_killed':
                    outcome = 'timeout'
                elif result.outcome == 'tests_failed' and result.exit_code == 1:
                    killed_by = sorted({case.get('nodeid', '') for case in cases
                                        if case.get('outcome') == 'failed'
                                        and not any(term in case.get('message', '').lower() for term in
                                                    ('importerror', 'modulenotfounderror', 'syntaxerror',
                                                     'collection error', 'error at setup'))
                                        and case.get('nodeid', '') in stable_nodes})
                    outcome = 'killed' if killed_by else 'invalid'
                else:
                    outcome = 'invalid'
            except Exception as exc:
                logger.exception('S9 mutation %s (item %s) failed', mutation.id, mutation.item_id)
                outcome = 'invalid'
                detail = f'{type(exc).__name__}: {exc}'[:500]
            evidence = ctx.state.evidence.get(mutation.item_id)
            cited = {node for check in evidence.guarding_tests for node in check.nodeids} if evidence else set()
            return MutationResult(mutation_id=mutation.id, item_id=mutation.item_id,
                                  origin=mutation.origin, outcome=outcome, killed_by=killed_by,
                                  killed_by_cited=bool(cited.intersection(killed_by)),
                                  sandbox_id=sandbox_id, duration_ms=duration_ms, detail=detail)

        results = await asyncio.gather(*(prove(mutation) for mutation in valid))
        require_tree(ctx, Snapshot(ctx.project_dir))
        ctx._proof_manifest = {m.id: m.model_dump_json() for m in valid}
        assert len(results) == len(valid)
        for result in results:
            ctx.state.results[result.mutation_id] = result
            ctx.emit('mutation_result', result.model_dump(mode='json', exclude={'detail'}))
