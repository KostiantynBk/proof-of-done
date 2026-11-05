"""Live-feedback regression cases; Refundly is only read, never executed."""
from pathlib import Path

import pytest
from fakes_pipeline import ScriptedSandboxRunner
from pod.config import Settings
from pod.llm import FakeLLM
from pod.pipeline.audit import audit_items
from pod.pipeline.baseline import run_baseline
from pod.pipeline.context import RunContext
from pod.pipeline.prompts_prove import auditor_messages, saboteur_messages
from pod.pipeline.prove import prove_mutations
from pod.pipeline.sabotage import sabotage_items, validate_mutation
from pod.schemas import Item, Mutation
from pod.store import Store
from pod.verify.snapshot import Snapshot
from test_pipeline_prove_flow import XML_PASS, claim, proposal

ROOT = Path(__file__).resolve().parents[2] / 'samples' / 'refundly'
FILE = 'refundly/refunds.py'


def candidate(find, replace):
    return Mutation(id='REQ-01-T1', item_id='REQ-01', origin='targeted', file=FILE,
                    find=find, replace=replace, description='test')


@pytest.mark.parametrize(('find', 'replace'), [
    ('order.refunded += amount', 'order.refunded = amount'),
    ('order.refunded += amount', 'order.refunded += Decimal("0.00")'),
    (' and not manager_approved', ''),
    ('self.processed[request_id] = result', 'self.processed[request_id + " "] = result'),
])
def test_live_proposals_accepted(find, replace):
    result = validate_mutation(Snapshot(ROOT), candidate(find, replace), set())
    assert result.valid, result.reject_reason


@pytest.mark.asyncio
async def test_empty_line_normalization_is_applied_in_sandbox(tmp_path):
    specs = []

    def script(spec):
        specs.append(spec)
        return 'passed', XML_PASS, []

    ctx = RunContext(run_id='r', project_dir=ROOT, spec_text='', history=[], settings=Settings(),
                     llm=FakeLLM(), sandbox=ScriptedSandboxRunner(script), store=Store(tmp_path / 'db'))
    await run_baseline(ctx)
    proposal_ = candidate('            return self.processed[request_id]\n', '')
    mutation = validate_mutation(Snapshot(ROOT), proposal_, set())
    assert mutation.valid and mutation.replace == '            pass\n'
    assert 'normalized_to_pass' in mutation.description
    ctx.state.mutations['REQ-01'] = [mutation]
    await prove_mutations(ctx)
    sent = specs[-1].files[FILE]
    assert '        if request_id in self.processed:\n            pass\n' in sent


def test_do_not_normalize_partial_lines():
    mutation = validate_mutation(Snapshot(ROOT), candidate('return self.processed[request_id]', ''), set())
    assert not mutation.valid and mutation.reject_reason == 'syntax_error'


class RecordingLLM(FakeLLM):
    def __init__(self, responses):
        super().__init__(responses)
        self.calls = []

    async def chat(self, role, messages, response_model=None, **kwargs):
        self.calls.append((role, messages))
        return await super().chat(role, messages, response_model=response_model, **kwargs)


@pytest.mark.asyncio
async def test_retry_once_with_reasons_and_cap(tmp_path):
    bad = proposal('absent text', 'x')
    first = [bad, bad, proposal('attempts < 3', 'attempts < 2')]
    blind = [proposal('attempts == 3', 'attempts == 2'),
             proposal('timedelta(days=90)', 'timedelta(days=91)'),
             proposal('amount > order.captured', 'amount >= order.captured')]
    retry = [proposal('attempts < 3', 'attempts < 2'),
             proposal('attempts < 3', 'attempts < 1'),
             proposal('attempts == 3', 'attempts == 1')]
    llm = RecordingLLM({'auditor': [claim()], 'saboteur': [
        {'item_id': 'REQ-01', 'mutations': first},
        {'item_id': 'REQ-01', 'mutations': blind},
        {'item_id': 'REQ-01', 'mutations': retry},
    ]})
    ctx = RunContext(run_id='r', project_dir=ROOT, spec_text='', history=[], settings=Settings(),
                     llm=llm, sandbox=ScriptedSandboxRunner(lambda spec: ('passed', XML_PASS, [])),
                     store=Store(tmp_path / 'db'))
    ctx.state.requirements = [Item(id='REQ-01', kind='requirement', title='x', statement='x',
                                   source_checks=[], source_verified=True)]
    await run_baseline(ctx)
    await audit_items(ctx)
    await sabotage_items(ctx)
    mutations = ctx.state.mutations['REQ-01']
    assert sum(m.valid and m.origin == 'targeted' for m in mutations) == 3
    assert sum(m.valid and m.origin == 'blind' for m in mutations) == 3
    assert len([c for c in llm.calls if c[0] == 'saboteur']) == 3
    retry_prompt = llm.calls[-1][1][0]['content']
    assert 'find_not_found' in retry_prompt and 'different edits' in retry_prompt
    assert any(m.reject_reason == 'duplicate' for m in mutations)
    assert len([e for e in ctx.store.list_events('r') if e['type'] == 'mutation_retry']) == 1


@pytest.mark.asyncio
async def test_retry_missing_pass_and_limit_to_three_valid(tmp_path):
    blind = [proposal('attempts == 3', 'attempts == 2'),
             proposal('timedelta(days=90)', 'timedelta(days=91)'),
             proposal('amount > order.captured', 'amount >= order.captured')]
    retry = [proposal('attempts < 3', 'attempts < 2'),
             proposal('attempts < 3', 'attempts < 1'),
             proposal('attempts == 3', 'attempts == 1'),
             proposal('attempts == 3', 'attempts == 0')]
    llm = RecordingLLM({'auditor': [claim()], 'saboteur': [
        {'item_id': 'REQ-01', 'mutations': []},
        {'item_id': 'REQ-01', 'mutations': blind},
        {'item_id': 'REQ-01', 'mutations': retry},
    ]})
    ctx = RunContext(run_id='r', project_dir=ROOT, spec_text='', history=[], settings=Settings(),
                     llm=llm, sandbox=ScriptedSandboxRunner(lambda spec: ('passed', XML_PASS, [])),
                     store=Store(tmp_path / 'db'))
    ctx.state.requirements = [Item(id='REQ-01', kind='requirement', title='x', statement='x',
                                   source_checks=[], source_verified=True)]
    await run_baseline(ctx)
    await audit_items(ctx)
    await sabotage_items(ctx)
    targeted = [m for m in ctx.state.mutations['REQ-01'] if m.origin == 'targeted']
    assert sum(m.valid for m in targeted) == 3
    assert targeted[-1].reject_reason == 'excess_proposal'
    assert 'too_few_proposals' in llm.calls[-1][1][0]['content']
    assert len([c for c in llm.calls if c[0] == 'saboteur']) == 3


def test_prompt_contracts():
    item = Item(id='REQ-01', kind='requirement', title='x', statement='x',
                source_checks=[], source_verified=True)
    snap = Snapshot(ROOT)
    audit = auditor_messages(item, snap)[0]['content']
    sabotage = saboteur_messages(item, snap, blind=True)[0]['content']
    assert 'at most 4 code citations' in audit and 'never put prose in test_name' in audit
    assert 'byte-exact' in sabotage and 'line-number prefix' in sabotage
    assert 'pass' in sabotage and 'at most 12 AST nodes' in sabotage
