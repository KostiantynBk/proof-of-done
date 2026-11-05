"""Static AST validation only; no untrusted code is executed."""
from pod.pipeline.sabotage import validate_mutation
from pod.schemas import Mutation
from pod.verify.snapshot import Snapshot


def validate(tmp_path, source, find, replace):
    (tmp_path / 'app.py').write_text(source, encoding='utf-8')
    mutation = Mutation(id='REQ-01-T1', item_id='REQ-01', origin='targeted', file='app.py',
                        find=find, replace=replace, description='bounded expression')
    return validate_mutation(Snapshot(tmp_path), mutation, set())


def test_imported_clock_expression(tmp_path):
    source = ('from datetime import UTC, datetime\nfrom refundly import clock\n'
              'def f():\n    return clock.now()\n')
    assert validate(tmp_path, source, 'clock.now()', 'datetime.now(UTC)').valid


def test_local_attribute_chain(tmp_path):
    source = ('def f(self, a, b, x):\n    self.audit.record(a, b)\n')
    assert validate(tmp_path, source, 'self.audit.record(a, b)',
                    'self.audit.events.append(x)').valid


def test_forbidden_unknown_reference(tmp_path):
    source = 'def f(clock):\n    return clock.now()\n'
    assert not validate(tmp_path, source, 'clock.now()', 'requests.get("x")').valid
    assert not validate(tmp_path, source, 'clock.now()', 'unknown.now()').valid


def test_expression_over_twelve_nodes_rejected(tmp_path):
    source = 'def f(clock, fn, a):\n    return clock.now()\n'
    assert not validate(tmp_path, source, 'clock.now()', 'fn(a, a, a, a, a, a, a)').valid


def test_no_structural_edits_or_unbound_scope_alias(tmp_path):
    source = 'def first(clock):\n    return clock.now()\ndef other(hidden):\n    return hidden\n'
    assert not validate(tmp_path, source, 'clock.now()', 'hidden.action()').valid
    assert not validate(tmp_path, source, 'return clock.now()', 'import os\n    return clock.now()').valid


def test_no_forbidden_strings_lambda_or_comprehension(tmp_path):
    source = 'def f(clock):\n    return clock.now()\n'
    for replacement in ('"pytest"', '(lambda: clock.now())', '[x for x in clock]'):
        assert not validate(tmp_path, source, 'clock.now()', replacement).valid
