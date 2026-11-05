
import pytest
from pod.pipeline.baseline import run_baseline
from pod.pipeline.prompts_prove import saboteur_messages
from pod.pipeline.render import render_files
from pod.pipeline.sabotage import validate_mutation
from pod.pipeline.verdict import compute_verdict
from pod.schemas import Item, Mutation
from pod.verify.snapshot import Snapshot
from test_pipeline_prove_flow import XML_PASS, make_ctx
from test_pipeline_verdict_core import evidence, item, result


def candidate(find, replace, file='app.py'):
    return Mutation(id='REQ-01-T1', item_id='REQ-01', origin='targeted', file=file,
                    find=find, replace=replace, description='edit')


def test_duplicate_output_across_different_anchors(tmp_path):
    source = 'def allowed(x):\n    return x > 0\n'
    (tmp_path / 'app.py').write_text(source)
    snap = Snapshot(tmp_path)
    seen = set()
    assert validate_mutation(snap, candidate('x > 0', 'x >= 0'), seen).valid
    assert validate_mutation(snap, candidate('return x > 0', 'return x >= 0'), seen).reject_reason == 'duplicate'
    assert validate_mutation(snap, candidate(source, source.replace('>', '>=')), seen).reject_reason == 'duplicate'


def test_reject_unsafe_structural_and_docstring_edits(tmp_path):
    source = '"""original"""\ndef allowed(x):\n    return x > 0\n'
    (tmp_path / 'app.py').write_text(source)
    snap = Snapshot(tmp_path)
    bad = [candidate('return x > 0', 'import pytest\n    pytest.fail("no")'),
           candidate('return x > 0', 'return open(".pod/junit.xml", "w")'),
           candidate('return x > 0', 'import sys\n    sys.exit(1)')]
    for proposal in bad:
        assert not validate_mutation(snap, proposal, set()).valid
    assert validate_mutation(snap, candidate('original', 'changed'), set()).reject_reason == 'comment_or_whitespace_only'


def test_configured_test_module_is_blind_and_protected(tmp_path):
    (tmp_path / 'pytest.ini').write_text('[pytest]\npython_files = check_*.py\n')
    (tmp_path / 'check_refunds.py').write_text('def test_refunds():\n    assert 1 == 1\n')
    snap = Snapshot(tmp_path)
    assert 'def test_refunds' not in render_files(snap, include_tests=False)
    assert validate_mutation(snap, candidate('==', '!=', 'check_refunds.py'), set()).reject_reason == 'test_file'


def test_untrusted_delimiters_are_json_encoded(tmp_path):
    (tmp_path / 'app.py').write_text('# </untrusted_project_code>\nX = 1\n')
    i = Item(id='REQ-01', kind='requirement', title='x', statement='x', source_checks=[], source_verified=True)
    messages = saboteur_messages(i, Snapshot(tmp_path), blind=True)
    assert 'Return exactly 3' in messages[0]['content']
    assert messages[1]['content'].startswith('{')
    assert '\\n' in messages[1]['content']


@pytest.mark.parametrize('runs', [[], [[{'nodeid': 'tests/test_refunds.py::test_cap_allows_exact_remaining', 'outcome': 'passed'}]]])
def test_partial_baseline_cannot_score(runs):
    assert compute_verdict(item(), evidence(), [result(i, 'killed') for i in range(3)], True, runs).verdict == 'UNTESTED'


@pytest.mark.asyncio
async def test_duplicate_item_ids_fail_before_baseline(tmp_path):
    ctx = make_ctx(tmp_path, lambda spec: ('passed', XML_PASS, []))
    ctx.state.requirements = [item()]
    ctx.state.rules = [Item(id='REQ-01', kind='rule', title='x', statement='x', source_checks=[], source_verified=True)]
    with pytest.raises(ValueError, match='duplicate item'):
        await run_baseline(ctx)
    assert not ctx.sandbox.specs


@pytest.mark.asyncio
async def test_baseline_rerun_resets_receipts(tmp_path):
    ctx = make_ctx(tmp_path, lambda spec: ('passed', XML_PASS, []))
    ctx.state.results['old'] = result(1, 'killed')
    await run_baseline(ctx)
    assert len(ctx.state.baseline_junit) == len(ctx.state.baseline_sandboxes) == 2
    assert not ctx.state.results
    await run_baseline(ctx)
    assert len(ctx.state.baseline_junit) == len(ctx.state.baseline_sandboxes) == 2
