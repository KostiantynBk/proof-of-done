"""Guard proposals and evidence-bound, fail-closed approval verification."""
import ast
import re

from pod.pipeline.baseline import require_tree, validate_items
from pod.pipeline.broadcast import log_caught
from pod.pipeline.export import digest
from pod.pipeline.prompts_gap import gap_messages
from pod.pipeline.prove import stable_test_nodes
from pod.pipeline.summary import run_sandboxes
from pod.pipeline.verdict import compute_verdict
from pod.sandbox.junit import parse_junit
from pod.schemas import GapTestResult, ItemVerdict, ProposedTest
from pod.verify.snapshot import Snapshot


def _identity(ctx, item_id, proposal):
    state = ctx.state
    return {'run_id': ctx.run_id, 'item_id': item_id, 'tree_sha256': state.tree_sha256,
            'item': next(i.model_dump(mode='json') for i in state.items if i.id == item_id),
            'evidence': state.evidence[item_id].model_dump(mode='json'),
            'baseline_ok': state.baseline_ok, 'baseline_sandboxes': state.baseline_sandboxes,
            'baseline_junit': state.baseline_junit,
            'mutations': [m.model_dump(mode='json') for m in state.mutations.get(item_id, [])],
            'results': {m.id: state.results[m.id].model_dump(mode='json')
                        for m in state.mutations.get(item_id, []) if m.id in state.results},
            'proposal': proposal.model_dump(mode='json')}


def _test_names(content):
    tree = ast.parse(content)
    names = {(None, n.name) for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name.startswith('test')}
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        names.update((cls.name, n.name) for n in cls.body
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith('test'))
    return names


def _valid_node(node, path, names):
    parts = node.split('::')
    if len(parts) not in (2, 3) or parts[0] != path:
        return False
    name = parts[-1].split('[', 1)[0]
    return (parts[1] if len(parts) == 3 else None, name) in names


def _behavioral(case):
    message = case.get('message', '').lower()
    return case.get('outcome') == 'failed' and not any(
        term in message for term in ('importerror', 'modulenotfounderror', 'syntaxerror',
                                     'collection error', 'error at setup'))


def apply_gap(verdict: ItemVerdict, result: GapTestResult) -> ItemVerdict:
    """Legacy helper: never promote without the complete context-backed proof gate."""
    updated = verdict.model_copy(deep=True)
    if result.approved is False:
        updated.reasons.append('Guard test rejected by reviewer')
    elif result.approved:
        updated.reasons.append('Guard requires proof verification')
    return updated


def _receipt_cases(store, run_id, sandbox_id):
    with store._lock:
        row = store._db.execute(
            "SELECT json_extract(result_json, '$.collected.\".pod/junit.xml\"') "
            'FROM sandbox_runs WHERE run_id=? AND sandbox_id=?', (run_id, sandbox_id)).fetchone()
    if not row or not row[0]:
        raise ValueError('junit receipt missing')
    return parse_junit(row[0])


def checked_approval(ctx, item_id):
    """Recompute proof using immutable journal binding, current snapshot and receipts."""
    state = ctx.state
    validate_items(ctx)
    require_tree(ctx, Snapshot(ctx.project_dir))
    item = next((i for i in state.items if i.id == item_id), None)
    proposal = state.gap_proposals.get(item_id)
    gap = state.gap_results.get(item_id)
    evidence = state.evidence.get(item_id)
    verdict = state.verdicts.get(item_id)
    if not all((item, proposal, gap, evidence, verdict)):
        raise ValueError('missing proof evidence')
    expected = 'tests/test_pod_' + item_id.lower().replace('-', '_') + '.py'
    if (not re.fullmatch(r'tests/test_pod_[a-z0-9_]+\.py', expected)
            or proposal.item_id != item_id or gap.item_id != item_id or verdict.item_id != item_id
            or proposal.file != expected or gap.file != expected or (ctx.project_dir / expected).exists()
            or len(proposal.content.encode()) > 20480):
        raise ValueError('guard identity mismatch')
    names = _test_names(proposal.content)
    bindings = [e['payload'] for e in ctx.store.list_events(ctx.run_id)
                if e['type'] == 'gap_binding' and e['payload'].get('item_id') == item_id]
    if not bindings:
        raise ValueError('missing guard proof binding')
    binding = bindings[-1]
    if binding.get('identity_sha256') != digest(_identity(ctx, item_id, proposal)):
        raise ValueError('guard proof binding mismatch')
    if binding.get('result_sha256') != digest(gap.model_dump(mode='json', exclude={'approved', 'approved_by'})):
        raise ValueError('guard results changed')
    mutations = [m for m in state.mutations.get(item_id, []) if m.valid]
    ids = [m.id for m in mutations]
    if len(ids) != len(set(ids)) or any(m.item_id != item_id for m in mutations):
        raise ValueError('invalid mutation identities')
    original = [state.results.get(mid) for mid in ids]
    if (any(r is None or r.mutation_id != m.id or r.item_id != item_id or r.origin != m.origin
            for m, r in zip(mutations, original, strict=True))
            or {key for key, result in state.results.items() if result.item_id == item_id} != set(ids)):
        raise ValueError('incomplete mutation results')
    survivors = {r.mutation_id for r in original if r.outcome == 'survived'}
    killed = gap.kills_survivors
    still = gap.still_survive
    if len(killed) != len(set(killed)) or len(still) != len(set(still)) or set(killed) & set(still):
        raise ValueError('duplicate guard outcomes')
    if set(killed) | set(still) != survivors:
        raise ValueError('incomplete survivor checks')
    checks = binding.get('guard_checks', {})
    if set(checks) != survivors or any(not checks[mid].get('nodes') for mid in killed):
        raise ValueError('missing guard kill receipts')
    if not ctx.store.verify_chain(ctx.run_id):
        raise ValueError('journal integrity failure')
    receipts = {s['sandbox_id']: s for s in run_sandboxes(ctx.store, ctx.run_id)}
    if len(gap.sandbox_ids) != len(set(gap.sandbox_ids)) or not gap.sandbox_ids:
        raise ValueError('invalid guard sandbox identities')
    original_receipt = receipts.get(gap.sandbox_ids[0])
    if (not original_receipt or original_receipt['purpose'] != f'gap_test:{item_id}'
            or not original_receipt.get('destroyed_at')):
        raise ValueError('missing original guard receipt')
    if gap.passes_on_original and (original_receipt['outcome'] != 'passed' or original_receipt.get('exit_code') != 0):
        raise ValueError('guard original receipt mismatch')
    if gap.reveals_violation and (original_receipt['outcome'] != 'tests_failed'
                                  or original_receipt.get('exit_code') != 1 or not binding.get('failed_nodeids')):
        raise ValueError('guard failure receipt mismatch')
    original_cases = _receipt_cases(ctx.store, ctx.run_id, gap.sandbox_ids[0])
    original_nodes = [c.get('nodeid', '') for c in original_cases
                      if _valid_node(c.get('nodeid', ''), expected, names)]
    if original_nodes != binding.get('original_nodeids') or len(original_nodes) != len(set(original_nodes)):
        raise ValueError('guard original cases changed')
    if any(node.startswith(expected + '::') and not _valid_node(node, expected, names)
           for node in (c.get('nodeid', '') for c in original_cases)):
        raise ValueError('invalid guard original case')
    own_cases = [c for c in original_cases if c.get('nodeid') in original_nodes]
    if gap.passes_on_original and (not own_cases or any(c.get('outcome') != 'passed' for c in own_cases)):
        raise ValueError('guard original test did not pass')
    if gap.reveals_violation and [c['nodeid'] for c in own_cases if _behavioral(c)] != binding['failed_nodeids']:
        raise ValueError('guard original failure changed')
    if set(gap.sandbox_ids[1:]) != {checks[mid]['sandbox_id'] for mid in survivors}:
        raise ValueError('guard sandbox set mismatch')
    for mid in survivors:
        receipt = receipts.get(checks[mid]['sandbox_id'])
        if (not receipt or receipt['purpose'] != f'gap_test:{item_id}:{mid}'
                or not receipt.get('destroyed_at')):
            raise ValueError('missing mutation guard receipt')
        if mid in killed:
            if receipt['outcome'] != 'tests_failed' or receipt.get('exit_code') != 1:
                raise ValueError('guard kill receipt mismatch')
            cases = _receipt_cases(ctx.store, ctx.run_id, receipt['sandbox_id'])
            own = [c for c in cases if c.get('nodeid', '').startswith(expected + '::')]
            nodes = [c['nodeid'] for c in own if _behavioral(c) and c['nodeid'] in original_nodes]
            if (nodes != checks[mid]['nodes'] or {c.get('nodeid') for c in own} != set(original_nodes)
                    or len({c.get('nodeid') for c in cases}) != len(cases)
                    or any(c.get('outcome') in ('error', 'skipped') for c in own)):
                raise ValueError('guard kill cases changed')
    if not item.source_verified or not evidence.has_verified_code:
        raise ValueError('source or code evidence missing')
    stable_nodes = stable_test_nodes(Snapshot(ctx.project_dir), state.baseline_junit)
    for result in original:
        if result.outcome not in ('killed', 'survived'):
            continue
        receipt = receipts.get(result.sandbox_id)
        expected_outcome, exit_code = ('tests_failed', 1) if result.outcome == 'killed' else ('passed', 0)
        if (not receipt or receipt['purpose'] != f'mutation:{result.mutation_id}'
                or receipt['outcome'] != expected_outcome or receipt.get('exit_code') != exit_code
                or not receipt.get('destroyed_at')
                or (result.outcome == 'killed' and not set(result.killed_by).intersection(stable_nodes))):
            raise ValueError('mutation proof receipt mismatch')
    if gap.reveals_violation:
        updated = verdict.model_copy(deep=True)
        updated.verdict = 'VIOLATED'
        updated.reasons.append('approved guard test fails on current code: ' + ', '.join(binding['failed_nodeids']))
        return updated, binding
    # Baseline and stable-citation gates must remain true, even if S10 reported WEAK first.
    if not state.baseline_ok or len(state.baseline_sandboxes) != 2 or len(set(state.baseline_sandboxes)) != 2:
        raise ValueError('baseline proof gate failed')
    if any((s := receipts.get(sid)) is None or s['purpose'] != f'baseline:{n}'
           or s['outcome'] != 'passed' or s.get('exit_code') != 0 or not s.get('destroyed_at')
           for n, sid in enumerate(state.baseline_sandboxes, 1)):
        raise ValueError('baseline receipts incomplete')
    if len(state.baseline_junit) != 2:
        raise ValueError('baseline junit incomplete')
    for index, sid in enumerate(state.baseline_sandboxes):
        if _receipt_cases(ctx.store, ctx.run_id, sid) != state.baseline_junit[index]:
            raise ValueError('baseline junit receipt mismatch')
    if not item.source_verified or not evidence.has_verified_code or not gap.passes_on_original:
        raise ValueError('guard proof gate failed')
    effective = []
    for result in original:
        if result.mutation_id in killed:
            detail = checks[result.mutation_id]
            result = result.model_copy(update={'outcome': 'killed', 'killed_by': detail['nodes'],
                                               'sandbox_id': detail['sandbox_id']})
        effective.append(result)
    updated = compute_verdict(item, evidence, effective, state.baseline_ok, state.baseline_junit)
    if updated.verdict == 'PROVEN':
        updated.reasons.append(f'proven with approved guard test {proposal.file}')
    else:
        updated.reasons.append('approved guard test did not establish proof')
    return updated, binding


async def close_gaps(ctx):
    async with ctx.stage('S11', 'Close gaps'):
        validate_items(ctx)
        snapshot = Snapshot(ctx.project_dir)
        require_tree(ctx, snapshot)
        eligible = [i for i in ctx.state.items if i.source_verified
                    and i.id in ctx.state.evidence and ctx.state.evidence[i.id].has_verified_code
                    and i.id in ctx.state.verdicts
                    and ctx.state.verdicts[i.id].verdict in ('WEAK', 'UNTESTED')]
        eligible.sort(key=lambda i: (ctx.state.verdicts[i.id].verdict != 'WEAK', i.id))
        for item in eligible[6:]:
            ctx.emit('gap_skipped', {'item_id': item.id, 'reason': 'six item limit'})
        for item in eligible[:6]:
            try:
                manifest = [m for m in ctx.state.mutations.get(item.id, []) if m.valid]
                proof_manifest = getattr(ctx, '_proof_manifest', {})
                if (len({m.id for m in manifest}) != len(manifest)
                        or any(m.item_id != item.id or proof_manifest.get(m.id) != m.model_dump_json()
                               or m.id not in ctx.state.results for m in manifest)):
                    raise ValueError('mutation proof manifest mismatch')
                survivors = [m for m in manifest if ctx.state.results[m.id].outcome == 'survived']
                response = await ctx.llm_call('testwriter', gap_messages(ctx, item, survivors),
                                              response_model=ProposedTest)
                proposal = response.value
                expected = 'tests/test_pod_' + item.id.lower().replace('-', '_') + '.py'
                if (proposal.item_id != item.id or proposal.file != expected
                        or len(proposal.content.encode('utf-8')) > 20480
                        or (ctx.project_dir / expected).exists()
                        or not re.fullmatch(r'tests/test_pod_[a-z0-9_]+\.py', expected)):
                    raise ValueError('invalid proposed test path, item, or size')
                # Normalize before running so the exported patch always adds exactly checked bytes.
                proposal = proposal.model_copy(update={'content': proposal.content.rstrip('\r\n') + '\n'})
                if len(proposal.content.encode('utf-8')) > 20480:
                    raise ValueError('invalid proposed test size')
                names = _test_names(proposal.content)
                original, cases = await ctx.run_tests(f'gap_test:{item.id}', files={expected: proposal.content})
                own = [c for c in cases if _valid_node(c.get('nodeid', ''), expected, names)]
                counts = [c['nodeid'] for c in own]
                unique = (len(counts) == len(set(counts)) and not any(
                    c.get('nodeid', '').startswith(expected + '::')
                    and not _valid_node(c.get('nodeid', ''), expected, names) for c in cases))
                failed = [c['nodeid'] for c in own if _behavioral(c)
                          and original.outcome == 'tests_failed' and original.exit_code == 1] if unique else []
                passed = (original.outcome == 'passed' and original.exit_code == 0 and bool(own) and unique
                          and all(c['outcome'] == 'passed' for c in own))
                ctx.emit('gap_original_result', {'item_id': item.id, 'failed_nodeids': failed,
                                                 'nodeids': counts})
                result = GapTestResult(item_id=item.id, file=expected, passes_on_original=passed,
                                       reveals_violation=bool(failed), sandbox_ids=[original.sandbox_id])
                checks = {}
                for mutation in survivors:
                    path = snapshot.safe_path(mutation.file)
                    if path is None:
                        raise ValueError('mutation target missing')
                    source = path.read_text(encoding='utf-8')
                    if not mutation.find or source.count(mutation.find) != 1:
                        raise ValueError('mutation changed')
                    changed = source.replace(mutation.find, mutation.replace, 1)
                    sandbox, cases = await ctx.run_tests(
                        f'gap_test:{item.id}:{mutation.id}',
                        files={expected: proposal.content, mutation.file: changed})
                    result.sandbox_ids.append(sandbox.sandbox_id)
                    own_cases = [c for c in cases if c.get('nodeid', '').startswith(expected + '::')]
                    nodes = [c['nodeid'] for c in own_cases if _behavioral(c) and c['nodeid'] in counts]
                    if (not passed or sandbox.outcome != 'tests_failed' or sandbox.exit_code != 1
                            or len({c.get('nodeid') for c in cases}) != len(cases)
                            or {c.get('nodeid') for c in own_cases} != set(counts)
                            or any(c.get('outcome') in ('error', 'skipped') for c in own_cases)):
                        nodes = []
                    (result.kills_survivors if nodes else result.still_survive).append(mutation.id)
                    checks[mutation.id] = {'nodes': nodes, 'sandbox_id': sandbox.sandbox_id}
                require_tree(ctx, Snapshot(ctx.project_dir))
                ctx.state.gap_proposals[item.id] = proposal
                ctx.state.gap_results[item.id] = result
                binding = {'item_id': item.id, 'identity_sha256': digest(_identity(ctx, item.id, proposal)),
                           'result_sha256': digest(result.model_dump(mode='json', exclude={'approved', 'approved_by'})),
                           'guard_checks': checks, 'failed_nodeids': failed, 'original_nodeids': counts}
                ctx.emit('gap_binding', binding)
                ctx.emit('approval_needed', {'item_id': item.id, 'file': expected,
                         'passes_on_original': passed, 'reveals_violation': bool(failed),
                         'kills': len(result.kills_survivors), 'still': len(result.still_survive)})
            except Exception as exc:  # noqa: BLE001 -- isolate a failed proposal
                log_caught(ctx, 'S11:gap', item.id, exc)
                ctx.state.errors.append(f'gap {item.id}: {type(exc).__name__}')
                ctx.emit('gap_skipped', {'item_id': item.id, 'reason': type(exc).__name__})
