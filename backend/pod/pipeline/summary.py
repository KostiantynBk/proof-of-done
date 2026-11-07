"""Read-only projections of persisted run state and journal."""
import hashlib
import json
import re
from collections import Counter

from pod.pipeline.context import RunState
from pod.store import GENESIS

STAGES = ['Ingest', 'Extract requirements', 'Mine rules', 'Verify quotes', 'Baseline',
          'Audit', 'Verify evidence', 'Sabotage', 'Validate mutations', 'Prove', 'Verdict',
          'Close gaps', 'Export']
EXPORTS = ['report.md', 'AGENTS.md', 'receipts.json', 'patch.diff']


def run_sandboxes(store, run_id):
    """Read run-local ledger metadata, excluding collected untrusted artifacts."""
    with store._lock:
        rows = store._db.execute(
            "SELECT json_remove(result_json, '$.collected') FROM sandbox_runs WHERE run_id=? "
            'ORDER BY started_at DESC, rowid DESC', (run_id,)).fetchall()
    return [json.loads(row[0]) for row in rows]


def recent_runs(store, limit=20):
    with store._lock:
        rows = store._db.execute('SELECT * FROM runs ORDER BY created_at DESC, id LIMIT ?', (limit,)).fetchall()
    return [store._run(row) for row in rows]


def event_page(store, run_id, after, limit=200):
    with store._lock:
        rows = store._db.execute('SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?',
                                 (run_id, after, limit)).fetchall()
    return [{**dict(row), 'payload': json.loads(row['payload_json'])} for row in rows]


def eligible_recording(store, run_id):
    run = store.get_run(run_id)
    if not run or run['status'] not in ('done', 'awaiting_approval') or not store.verify_chain(run_id):
        return False
    if not any(e['type'] == 'run_finished' for e in store.list_events(run_id)):
        return False
    artifacts = {name: store.get_artifact(run_id, name) for name in EXPORTS}
    if not all(artifacts.values()):
        return False
    try:
        manifest = json.loads(artifacts['receipts.json']['content'])
        expected = manifest.pop('manifest_sha256')
        raw = json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        return (expected == hashlib.sha256(raw.encode()).hexdigest()
                and manifest.get('run', {}).get('id') == run_id and manifest.get('chain_ok') is True)
    except (ValueError, KeyError, TypeError):
        return False


def load_state(store, run_id):
    artifact = store.get_artifact(run_id, 'state.json')
    return RunState.model_validate_json(artifact['content']) if artifact else RunState()


def effective_results(store, run_id, state):
    results = dict(state.results)
    bindings = {e['payload']['item_id']: e['payload'] for e in store.list_events(run_id)
                if e['type'] == 'gap_binding' and 'item_id' in e['payload']}
    for item_id, gap in state.gap_results.items():
        if not gap.approved:
            continue
        checks = bindings.get(item_id, {}).get('guard_checks', {})
        for mid in gap.kills_survivors:
            original, check = results.get(mid), checks.get(mid)
            if original and original.item_id == item_id and original.outcome == 'survived' and check and check.get('nodes'):
                results[mid] = original.model_copy(update={'outcome': 'killed', 'killed_by': check['nodes'],
                                                           'sandbox_id': check['sandbox_id']})
    return results


def item_detail(store, run_id, item_id):
    state = load_state(store, run_id)
    item = next((i for i in state.items if i.id == item_id), None)
    if item is None:
        return None
    proposal = state.gap_proposals.get(item_id)
    gap = {'proposed': proposal, 'result': state.gap_results.get(item_id)} if proposal else None
    ids = {r.sandbox_id for r in state.results.values() if r.item_id == item_id}
    if item_id in state.gap_results:
        ids.update(state.gap_results[item_id].sandbox_ids)
    sandboxes = [s for s in run_sandboxes(store, run_id) if s['sandbox_id'] in ids]
    return {'item': item, 'evidence': state.evidence.get(item_id),
            'mutations': state.mutations.get(item_id, []),
            'results': [r for r in effective_results(store, run_id, state).values() if r.item_id == item_id],
            'verdict': state.verdicts.get(item_id), 'gap': gap, 'sandboxes': sandboxes}


def completeness_summary(state, events):
    """Project fixed categories and known canonical IDs; never expose raw errors."""
    known = {item.id for item in state.items if
             (item.kind == 'requirement' and re.fullmatch(r'REQ-[0-9]{2,8}', item.id)) or
             (item.kind == 'rule' and re.fullmatch(r'RULE-[0-9]{2,8}', item.id))}
    reasons = {'six item limit': 'six item limit', 'LLMError': 'model response failed',
               'BudgetExceeded': 'inference budget exhausted', 'ValueError': 'validation failed'}
    skipped = []
    for event in events:
        if event.get('type') != 'gap_skipped':
            continue
        payload = event.get('payload')
        payload = payload if isinstance(payload, dict) else {}
        item_id, reason = payload.get('item_id'), payload.get('reason')
        skipped.append({'item_id': item_id if isinstance(item_id, str) and item_id in known else None,
                        'reason': reasons.get(reason, 'other failure') if isinstance(reason, str) else 'other failure'})
    decisions = []
    for item_id, gap in state.gap_results.items():
        if item_id not in known:
            continue
        decisions.append({'item_id': item_id,
                          'decision': 'pending' if gap.approved is None else 'approved' if gap.approved else 'rejected',
                          'original': 'passed' if gap.passes_on_original else
                                      'failed' if gap.reveals_violation else 'not passed'})
    return {'component_failures': len(state.errors), 'skipped_gaps': skipped,
            'guard_decisions': decisions}


def build_summary(store, run_id):
    run = store.get_run(run_id)
    if not run:
        return None
    state = load_state(store, run_id)
    events = store.list_events(run_id)
    stages = [{'id': f'S{i}', 'name': name, 'status': 'pending', 'started_at': None,
               'ended_at': None, 'detail': None} for i, name in enumerate(STAGES)]
    current = 'not_started'
    for e in events:
        p = e['payload']
        if e['type'] not in ('stage_started', 'stage_finished'):
            continue
        stage = next ( (s for s in stages if s['id'] == p.get('stage')), None )
        if stage is None:
            continue
        if e['type'] == 'stage_started':
            stage.update(status='running', started_at=e['ts'])
            current = stage['id']
        else:
            stage.update(status=p.get('status', 'done'), ended_at=e['ts'],
                         detail=('Stage failed' if p.get('status') == 'failed' else
                                 'Stage skipped' if p.get('status') == 'skipped' else None))
    counts = dict.fromkeys(['PROVEN', 'WEAK', 'UNTESTED', 'VIOLATED', 'UNVERIFIED', 'INCONCLUSIVE'], 0)
    for v in state.verdicts.values():
        counts[v.verdict] += 1
    results = effective_results(store, run_id, state)
    items = []
    for item in state.items:
        v = state.verdicts.get(item.id)
        evidence = state.evidence.get(item.id)
        items.append({'id': item.id, 'kind': item.kind, 'title': item.title,
                      'statement': item.statement, 'verdict': v.verdict if v else None,
                      'ai_status': evidence.claim.ai_status if evidence else None,
                      'mutations': {k: getattr(v, 'mutations_' + k, 0) for k in
                                    ('tried', 'killed', 'survived', 'invalid', 'timeout')},
                      'survived_by_origin': v.survived_by_origin if v else {},
                      'mutations_by_origin': {origin: sum(r.origin == origin and r.outcome in ('killed', 'survived')
                                                          for r in results.values() if r.item_id == item.id)
                                              for origin in ('targeted', 'blind')}})
    sandboxes = run_sandboxes(store, run_id)
    telemetry = Counter(t['type'] for s in sandboxes for t in s['telemetry'])
    llms = state.llm_calls
    return {'id': run_id, 'created_at': run['created_at'], 'status': run['status'],
            'project': run['project'], 'stage': current, 'stages': stages,
            'inputs': {'spec_sha256': state.spec_sha256, 'tree_sha256': state.tree_sha256,
                       'history_records': state.history_records}, 'counts': counts,
            'ai_covered': sum(i['ai_status'] == 'covered' for i in items), 'items': items,
            'completeness': completeness_summary(state, events),
            'journal': {'events': len(events), 'head_hash': events[-1]['hash'] if events else GENESIS,
                        'chain_ok': store.verify_chain(run_id)},
            'containment': {'sandboxes': len(sandboxes),
                            'destroyed': sum(bool(s['destroyed_at']) for s in sandboxes),
                            'timeouts': sum(s['outcome'] == 'timeout_killed' for s in sandboxes),
                            'oom': sum(s['outcome'] == 'oom_killed' for s in sandboxes),
                            'telemetry': dict(telemetry)},
            'models': {c['role']: c['model'] for c in llms},
            'tokens': {'prompt': sum(c['prompt_tokens'] for c in llms),
                       'completion': sum(c['completion_tokens'] for c in llms)},
            'exports': [n for n in EXPORTS if store.get_artifact(run_id, n)]}
