"""S10: deterministic verdicts, independent of the model's self-assessment."""
from collections import Counter

from pod.pipeline.baseline import require_tree, validate_items
from pod.pipeline.context import RunContext
from pod.schemas import EvidenceReport, Item, ItemVerdict, MutationResult
from pod.verify.snapshot import Snapshot


def compute_verdict(item: Item, evidence: EvidenceReport | None, results: list[MutationResult],
                    baseline_ok: bool | None, baseline_junit: list[list[dict[str, str]]]) -> ItemVerdict:
    results = [r for r in results if r.item_id == item.id]
    counts = Counter(r.outcome for r in results)
    duplicate_results = len({r.mutation_id for r in results}) != len(results)
    if evidence and (evidence.item_id != item.id or evidence.claim.item_id != item.id):
        evidence = None
    survived = [r for r in results if r.outcome == 'survived']
    failures = []
    if evidence and len(baseline_junit) >= 2:
        for check in evidence.test_checks:
            if not check.exists:
                continue
            for node in check.nodeids:
                if all(any(c.get('nodeid') == node and c.get('outcome') in ('failed', 'error')
                           for c in run) for run in baseline_junit[:2]):
                    failures.append(node)
    if not item.source_verified:
        verdict, reason = 'UNVERIFIED', 'Source quote did not verify.'
    elif failures:
        verdict, reason = 'VIOLATED', 'Cited test failed in both baseline runs: ' + ', '.join(failures)
    elif not evidence or not evidence.has_verified_code or not any(
        check.nodeids and all(all(len(matches := [c for c in run if c.get('nodeid') == node]) == 1
                                  and matches[0].get('outcome') == 'passed'
                                  for node in check.nodeids) for run in baseline_junit)
        for check in evidence.guarding_tests
    ) or len(baseline_junit) != 2:
        verdict, reason = 'UNTESTED', 'No verified code citation or stable passing guarding test in two baselines.'
    elif survived:
        verdict, reason = 'WEAK', (f'{len(survived)}/{len(results)} sabotage edits survived: '
                                   + ', '.join(r.mutation_id for r in survived))
    elif not baseline_ok or duplicate_results or counts['killed'] < 3:
        # Invalid runs (import/collection errors, crashes) and wall-clock timeouts are not kills
        # and not survivals: they are reported and excluded from scoring.
        verdict, reason = 'INCONCLUSIVE', (f"Baseline {'passed' if baseline_ok else 'not OK'}; "
                            f"{counts['killed']} killed (3 needed), {counts['invalid']} invalid, "
                            f"{counts['timeout']} timed out.")
    else:
        excluded = counts['invalid'] + counts['timeout']
        verdict, reason = 'PROVEN', (f"All {counts['killed']} scored sabotage edits killed; baseline passed"
                                     + (f"; {excluded} unscored (invalid/timeout)." if excluded else "."))
    return ItemVerdict(item_id=item.id, kind=item.kind, verdict=verdict,
        ai_status=evidence.claim.ai_status if evidence else None, reasons=[reason],
        mutations_tried=len(results), mutations_killed=counts['killed'],
        mutations_survived=counts['survived'], mutations_invalid=counts['invalid'],
        mutations_timeout=counts['timeout'],
        survived_by_origin={origin: sum(r.origin == origin for r in survived)
                            for origin in ('targeted', 'blind')})


async def judge(ctx: RunContext) -> None:
    async with ctx.stage('S10', 'verdicts'):
        validate_items(ctx)
        require_tree(ctx, Snapshot(ctx.project_dir))
        verdicts = []
        for item in ctx.state.items:
            if item.kind == 'rule' and not item.accepted:
                continue
            manifest = [m for m in ctx.state.mutations.get(item.id, []) if m.valid]
            ids = [m.id for m in manifest]
            results = [ctx.state.results[mid] for mid in ids if mid in ctx.state.results
                       and ctx.state.results[mid].item_id == item.id]
            current_manifest = {m.id: m.model_dump_json() for m in manifest}
            proof_manifest = getattr(ctx, '_proof_manifest', {})
            complete = (len(ids) == len(set(ids)) == len(results)
                        and all(proof_manifest.get(mid) == content for mid, content in current_manifest.items())
                        and all(ctx.state.results[m.id].origin == m.origin for m in manifest
                                if m.id in ctx.state.results)
                        and len(ctx.state.baseline_sandboxes) == 2
                        and len(set(ctx.state.baseline_sandboxes)) == 2)
            if not complete:
                results = []
            verdict = compute_verdict(item, ctx.state.evidence.get(item.id), results,
                                      ctx.state.baseline_ok and complete, ctx.state.baseline_junit)
            ctx.state.verdicts[item.id] = verdict
            verdicts.append(verdict)
            ctx.emit('verdict', verdict.model_dump(mode='json'))
        counts = {name: sum(v.verdict == name for v in verdicts) for name in
                  ('PROVEN', 'WEAK', 'UNTESTED', 'VIOLATED', 'UNVERIFIED', 'INCONCLUSIVE')}
        ctx.emit('verdicts_ready', {'counts': counts,
                 'ai_covered': sum(v.ai_status == 'covered' for v in verdicts),
                 'ai_covered_but_not_proven': sum(v.ai_status == 'covered' and v.verdict != 'PROVEN'
                                                  for v in verdicts)})
