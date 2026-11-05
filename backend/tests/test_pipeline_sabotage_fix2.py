"""Static validation only: never import or execute Refundly."""
from pathlib import Path

import pytest
from pod.pipeline.sabotage import validate_mutation
from pod.schemas import Mutation
from pod.verify.snapshot import Snapshot

ROOT = Path(__file__).resolve().parents[2] / 'samples' / 'refundly'


def check(snapshot, file, find, replace):
    mutation = Mutation(id='REQ-01-T1', item_id='REQ-01', origin='targeted', file=file,
                        find=find, replace=replace, description='static test')
    return validate_mutation(snapshot, mutation, set())


@pytest.mark.parametrize(('file', 'find', 'replace'), [
    ('refundly/money.py', 'CENT = Decimal("0.01")', 'CENT = float("0.01")'),
    ('refundly/refunds.py', 'raise InvalidAmount("refund must be positive")',
     'raise ValueError("refund must be positive")'),
    ('refundly/refunds.py', 'self.audit.record(actor, "refund_issued", amount, reason, order.order_id)', 'pass'),
    ('refundly/refunds.py', '        if destination != order.payment_method:\n            raise PaymentMethodMismatch("refund must use original method")\n', ''),
    ('refundly/orders.py', '"store_credit"', '"gift_card"'),
    ('refundly/refunds.py', 'attempts += 1', 'pass'),
    ('refundly/refunds.py', 'attempts += 1', 'attempts += 0'),
    ('refundly/refunds.py', 'attempts < 3', 'True'),
    ('refundly/refunds.py', 'not manager_approved', 'manager_approved'),
    ('refundly/refunds.py', 'self.audit.record(actor, "refund_issued", amount, reason, order.order_id)',
     'self.audit.record(actor, "refund_issued", amount, reason)'),
])
def test_real_refundly_edits_are_accepted(file, find, replace):
    result = check(Snapshot(ROOT), file, find, replace)
    assert result.valid, result.reject_reason


@pytest.mark.parametrize('payload', [
    'import os', 'pytest.skip()', 'sys.exit(0)', '__import__("os")',
    'def evil():\n                pass', 'setattr(object(), "x", 1)',
])
def test_unsafe_statements_are_rejected(payload):
    original = 'attempts += 1'
    assert not check(Snapshot(ROOT), 'refundly/refunds.py', original, payload).valid


def test_new_module_assignment_rejected():
    result = check(Snapshot(ROOT), 'refundly/money.py', 'CENT = Decimal("0.01")',
                   'CENT = Decimal("0.01")\nOTHER = 1')
    assert not result.valid


def test_unbound_references_rejected_and_existing_references_allowed(tmp_path):
    source = ('from datetime import datetime\nfrom refundly import clock\n'
              'def f():\n    clock.now()\n    datetime.now()\n')
    (tmp_path / 'app.py').write_text(source)
    snap = Snapshot(tmp_path)
    assert check(snap, 'app.py', 'clock.now()', 'datetime.now()').valid
    assert not check(snap, 'app.py', 'clock.now()', 'logger.info()').valid
    # refunds.py now imports datetime (demo follow-up), so that swap is legal there;
    # `time` is still not imported, so referencing it must be rejected.
    assert check(Snapshot(ROOT), 'refundly/refunds.py', 'clock.now()', 'datetime.now(UTC)').valid
    assert not check(Snapshot(ROOT), 'refundly/refunds.py', 'clock.now()', 'time.time()').valid


@pytest.mark.parametrize(('find', 'replace', 'valid'), [
    ('if x and y:', 'if True and y:', True),
    ('if x and y:', 'if not (x and y):', True),
    ('call(x, label="safe")', 'call(x)', True),
    ('label="safe"', 'label="pytest"', False),
    ('return x', 'return __import__("os")', False),
])
def test_single_edit_grammar(tmp_path, find, replace, valid):
    source = 'def f(x, y):\n    if x and y:\n        call(x, label="safe")\n    return x\n'
    (tmp_path / 'app.py').write_text(source)
    assert check(Snapshot(tmp_path), 'app.py', find, replace).valid is valid


def test_existing_logger_and_refunderror_references(tmp_path):
    source = ('def f():\n    logger.info("x")\n    audit.record("x")\n'
              '    raise RefundError("x")\n')
    (tmp_path / 'app.py').write_text(source)
    snap = Snapshot(tmp_path)
    assert check(snap, 'app.py', 'audit.record("x")', 'logger.info("x")').valid
    assert check(snap, 'app.py', 'raise RefundError("x")', 'raise ValueError("x")').valid


def test_unimported_stdlib_reference_rejected(tmp_path):
    (tmp_path / 'app.py').write_text('def f():\n    clock.now()\n    datetime.now()\n')
    assert not check(Snapshot(tmp_path), 'app.py', 'clock.now()', 'datetime.now()').valid


def test_reject_changed_decorators_and_multiple_edits(tmp_path):
    (tmp_path / 'app.py').write_text('def f(x):\n    return x > 1\n')
    snap = Snapshot(tmp_path)
    assert not check(snap, 'app.py', 'def f(x):', '@print\ndef f(x):').valid
    assert not check(snap, 'app.py', 'return x > 1', 'return x >= 2').valid
