"""Generate honest, reproducible run artifacts from recorded evidence."""
import difflib
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime

from pod.pipeline.summary import completeness_summary, effective_results, run_sandboxes


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


async def export_all(ctx):
    async with ctx.stage('S12', 'Export'):
        state = ctx.state
        events = ctx.store.list_events(ctx.run_id)
        sandboxes = run_sandboxes(ctx.store, ctx.run_id)
        run = ctx.store.get_run(ctx.run_id)
        effective = effective_results(ctx.store, ctx.run_id, state)
        manifest = {'run': {'id': ctx.run_id, 'project': run['project'], 'created_at': run['created_at']},
                    'inputs': {'spec_sha256': state.spec_sha256, 'tree_sha256': state.tree_sha256,
                               'history_records': state.history_records},
                    'llm_calls': state.llm_calls,
                    'sandboxes': [{k: v for k, v in s.items() if k not in ('stdout_tail', 'collected')}
                                  for s in sandboxes],
                    'verdicts': {k: v.model_dump(mode='json') for k, v in state.verdicts.items()},
                    'original_results': {k: v.model_dump(mode='json') for k, v in state.results.items()},
                    'effective_results': {k: v.model_dump(mode='json') for k, v in effective.items()},
                    'signoffs': [e['payload'] for e in events if e['type'] == 'signoff'],
                    'journal_head_hash': events[-1]['hash'] if events else None,
                    'chain_ok': ctx.store.verify_chain(ctx.run_id)}
        manifest['manifest_sha256'] = digest(manifest)
        ctx.store.put_artifact(ctx.run_id, 'receipts.json', canonical(manifest))
        counts = Counter(v.verdict for v in state.verdicts.values())
        covered = sum(e.claim.ai_status == 'covered' for e in state.evidence.values())
        report = ['# Proof of Done report', '', f'Run: {ctx.run_id}', f"Project: {run['project']}",
                  f"Created: {run['created_at']}", f'Exported: {datetime.now(UTC).isoformat()}',
                  f'Spec SHA-256: {state.spec_sha256}',
                  f'Tree SHA-256: {state.tree_sha256}', '', '## Verdict summary', '',
                  '| Verdict | Count |', '| --- | ---: |']
        report += [f'| {k} | {counts[k]} |' for k in
                   ('PROVEN', 'WEAK', 'UNTESTED', 'VIOLATED', 'UNVERIFIED', 'INCONCLUSIVE')]
        survival = {origin: {'survived': sum(r.origin == origin and r.outcome == 'survived'
                                              for r in effective.values()),
                             'scored': sum(r.origin == origin and r.outcome in ('killed', 'survived')
                                           for r in effective.values())}
                    for origin in ('targeted', 'blind')}
        completeness = completeness_summary(state, events)
        report += ['', f'AI said covered {covered} → proven {counts["PROVEN"]}',
                   f'Survival by origin: {canonical(survival)}',
                   f'Recorded successful model calls (failed-call usage may be unrecorded): {canonical(state.llm_calls)}',
                   '', '## Completeness and review',
                   'A finished lifecycle or stage does not mean every operation succeeded.',
                   f'Component failure entries: {completeness["component_failures"]} (not a failed-call count).',
                   f'Skipped gap attempts: {len(completeness["skipped_gaps"])}.']
        report += [f'- {s["item_id"] or "Unknown item"}: {s["reason"]}' for s in completeness['skipped_gaps']]
        decisions = completeness['guard_decisions']
        report.append('Guard decisions: ' + ', '.join(f'{status} {sum(g["decision"] == status for g in decisions)}'
                                                     for status in ('approved', 'rejected', 'pending')) + '.')
        report += [f'- {g["item_id"]}: {g["decision"]}; original {g["original"]}' for g in decisions]
        if any(g['original'] == 'failed' for g in decisions):
            report.append('Generated guard failed on original; may be a test defect, reviewer assessment required.')
        report += [('A WEAK verdict means a recorded candidate survived; equivalent or off-target edits can survive. '
                    'Model descriptions and relevance are not proof of a real defect. Only executed and verified '
                    'evidence yields a ladder result; this does not prove absence of defects.'), '']
        agents = ['# Proven rules', '']
        patch = []
        patch_paths = set()
        for item in state.items:
            verdict = state.verdicts.get(item.id)
            report += [f'## {item.id}: {item.title}', item.statement,
                       f'Verdict: {verdict.verdict if verdict else "pending"}']
            for quote in item.source_checks:
                report.append(f'Source ({quote.record_id or quote.locator}, {quote.match}): {quote.quote}')
            ev = state.evidence.get(item.id)
            if ev:
                report.append('Evidence: ' + ev.model_dump_json())
            report += ['', '| Mutation | Origin | Description | Outcome | Killed by | Sandbox |',
                       '| --- | --- | --- | --- | --- | --- |']
            for m in state.mutations.get(item.id, []):
                result = effective.get(m.id)
                report.append(f'| {m.id} | {m.origin} | {m.description} | '
                              f'{result.outcome if result else m.reject_reason} | '
                              f'{", ".join(result.killed_by) if result else ""} | '
                              f'{result.sandbox_id if result else ""} |')
            gap = state.gap_results.get(item.id)
            if gap:
                if gap.reveals_violation:
                    report.append('Generated guard failed on original; may be a test defect, reviewer assessment required.')
                report.append('Guard test (raw recorded fields, not independent confirmation): ' + gap.model_dump_json())
                if gap.approved:
                    from pod.pipeline.gaps import checked_approval
                    # A restored or later-mutated proposal must not enter a patch.
                    checked_approval(ctx, item.id)
                    proposal = state.gap_proposals[item.id]
                    if proposal.file in patch_paths:
                        raise ValueError('duplicate guard patch path')
                    patch_paths.add(proposal.file)
                    lines = proposal.content.splitlines(keepends=True)
                    diff = list(difflib.unified_diff([], lines, fromfile='/dev/null', tofile='b/' + proposal.file))
                    if lines and not lines[-1].endswith(('\n', '\r')):
                        diff[-1] += '\n\\ No newline at end of file\n'
                    patch.extend(diff)
            if item.kind == 'rule' and verdict and verdict.verdict == 'PROVEN':
                agents += [f'## {item.id}', item.statement]
                agents += [f'- {q.record_id}: {q.quote}' for q in item.source_checks if q.verified]
                if ev:
                    agents += [f'- Guard: {node}' for t in ev.guarding_tests for node in t.nodeids]
                if gap and gap.approved:
                    nodes = next((e['payload'].get('nodeids', []) for e in reversed(events)
                                  if e['type'] == 'gap_original_result'
                                  and e['payload'].get('item_id') == item.id), [])
                    agents.extend(f'- Approved guard: {node}' for node in nodes)
                    if not nodes:
                        agents.append(f'- Approved guard: {gap.file} (node IDs unavailable)')
                agents.append('')
        if len(agents) == 2:
            agents.append('No proven rules.')
        agents.append(f'Run {ctx.run_id}; receipts SHA-256: {manifest["manifest_sha256"]}')
        report += ['', '## Containment', 'Telemetry is best-effort, not a security boundary.']
        report += [canonical(e['payload']) for e in events if e['type'] == 'containment_event']
        report.append('Sandbox outcomes: ' + canonical(dict(Counter(s['outcome'] for s in sandboxes))))
        report += [f"Limits: {canonical(s['limits'])}" for s in sandboxes]
        report += ['', '## Limitations', ('Mutation testing shows tests guard these items against '
                   'these edits; it does not prove absence of defects. Telemetry is best-effort. '
                   'The demo is a small seeded example.')]
        ctx.store.put_artifact(ctx.run_id, 'report.md', '\n'.join(report) + '\n')
        ctx.store.put_artifact(ctx.run_id, 'AGENTS.md', '\n'.join(agents) + '\n')
        ctx.store.put_artifact(ctx.run_id, 'patch.diff', ''.join(patch))
