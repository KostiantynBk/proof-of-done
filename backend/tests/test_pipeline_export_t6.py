import asyncio
import json
import subprocess

import pytest
from pod.config import Settings
from pod.pipeline.context import RunContext, RunState
from pod.pipeline.export import digest, export_all
from pod.schemas import GapTestResult, Item, ItemVerdict, ProposedTest, QuoteCheck
from pod.store import Store


@pytest.mark.asyncio
async def test_export_only_proven_and_approved(tmp_path, monkeypatch):
    monkeypatch.setattr('pod.pipeline.gaps.checked_approval', lambda ctx, item_id: None)
    store = Store(tmp_path / 'db')
    store.create_run('r', 'demo:refundly')
    items = [Item(id=key, kind='rule', title=key, statement=f'rule {key}',
                  source_checks=[QuoteCheck(quote='verified source', record_id='review:1',
                                            match='exact', verified=True)], source_verified=True)
             for key in ('RULE-01', 'RULE-02')]
    proposals = {item.id: ProposedTest(item_id=item.id, file=f'tests/test_pod_{item.id}.py',
                                       content=f'def test_{i}(): pass\n', explanation='x')
                 for i, item in enumerate(items)}
    state = RunState(rules=items, verdicts={item.id: ItemVerdict(item_id=item.id, kind='rule',
                             verdict='PROVEN' if i == 0 else 'WEAK') for i, item in enumerate(items)},
                     gap_proposals=proposals,
                     gap_results={item.id: GapTestResult(item_id=item.id, file=proposals[item.id].file,
                                  passes_on_original=True, approved=i == 0) for i, item in enumerate(items)})
    ctx = RunContext('r', tmp_path / 'project', '', [], Settings(data_dir=tmp_path), None, None, store, state)
    await export_all(ctx)
    agents = store.get_artifact('r', 'AGENTS.md')['content']
    patch = store.get_artifact('r', 'patch.diff')['content']
    assert 'rule RULE-01' in agents and 'rule RULE-02' not in agents
    assert 'test_pod_RULE-01' in patch and 'test_pod_RULE-02' not in patch
    receipt = json.loads(store.get_artifact('r', 'receipts.json')['content'])
    assert receipt.pop('manifest_sha256') == digest(receipt)


@pytest.mark.asyncio
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
async def test_patch_applies_without_terminal_newlines(tmp_path, monkeypatch, newline):
    # Patch serialization is isolated here; approval binding is exercised by API integration tests.
    monkeypatch.setattr('pod.pipeline.gaps.checked_approval', lambda ctx, item_id: None)
    store = Store(tmp_path / 'db')
    store.create_run('r', 'demo:refundly')
    items = [Item(id=f'REQ-0{i}', kind='requirement', title='x', statement='x',
                  source_checks=[], source_verified=True) for i in (1, 2)]
    proposals = {item.id: ProposedTest(item_id=item.id, file=f'tests/test_pod_{i}.py',
                                      content=f'def test_{i}():{newline}    assert "á"', explanation='x')
                 for i, item in enumerate(items)}
    state = RunState(requirements=items, gap_proposals=proposals,
                     gap_results={item.id: GapTestResult(item_id=item.id, file=proposals[item.id].file,
                                  passes_on_original=True, approved=True) for item in items})
    ctx = RunContext('r', tmp_path / 'project', '', [], Settings(data_dir=tmp_path), None, None, store, state)
    await export_all(ctx)
    patch = store.get_artifact('r', 'patch.diff')['content']
    target = tmp_path / 'inert'
    target.mkdir()
    await asyncio.to_thread(subprocess.run, ['git', '-c', 'core.autocrlf=false', 'apply', '--check', '-'],
                            cwd=target, input=patch.encode(), check=True)
    await asyncio.to_thread(subprocess.run, ['git', '-c', 'core.autocrlf=false', 'apply', '-'],
                            cwd=target, input=patch.encode(), check=True)
    for proposal in proposals.values():
        assert (target / proposal.file).read_bytes() == proposal.content.encode()
