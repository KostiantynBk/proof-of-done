from datetime import timedelta
from decimal import Decimal
from itertools import repeat

import pytest
from refundly.errors import ApprovalRequired, InvalidAmount, RefundError


def issue(service, order, request_id="req-1", amount="10.00", **kwargs):
    return service.issue(order, request_id, Decimal(amount), "alice", "return", **kwargs)


def test_cap_allows_exact_remaining(context):
    _, order, gateway, _, service = context
    issue(service, order, "first", "40.00")
    issue(service, order, "second", "60.00")
    assert order.refunded == Decimal("100.00")
    assert len(gateway.transfers) == 2


def test_cap_rejects_one_cent_over_remaining(context):
    _, order, gateway, _, service = context
    issue(service, order, "first", "40.00")
    with pytest.raises(InvalidAmount):
        issue(service, order, "second", "60.01")
    assert len(gateway.transfers) == 1


def test_old_order_requires_approval(context):
    current, order, _, _, service = context
    order.created_at = current - timedelta(days=120)
    with pytest.raises(ApprovalRequired):
        issue(service, order)


def test_audit_record_exists(context):
    _, order, _, audit, service = context
    issue(service, order)
    assert len(audit.events) == 1


def test_idempotency_returns_original_without_extra_transfer(context):
    _, order, gateway, audit, service = context
    original = issue(service, order)
    repeated = issue(service, order)
    assert repeated is original
    assert order.refunded == Decimal("10.00")
    assert len(gateway.transfers) == len(audit.events) == 1


@pytest.mark.timeout(5)
def test_gateway_failure_stops_after_three_attempts(context):
    _, order, gateway, audit, service = context
    gateway.failures = repeat(True)
    result = issue(service, order)
    assert result.status == "FAILED"
    assert gateway.attempts == 3
    assert not gateway.transfers and not audit.events
    assert order.refunded == Decimal("0.00")


@pytest.mark.timeout(5)
def test_gateway_retry_then_success(context):
    _, order, gateway, _, service = context
    gateway.failures = iter([True, True, False])
    result = issue(service, order)
    assert result.status == "ISSUED"
    assert gateway.attempts == 3
    assert len(gateway.transfers) == 1


def test_partial_amount_rounds_down(context):
    _, order, gateway, _, service = context
    result = issue(service, order, amount="10.999")
    assert result.amount == Decimal("10.99")
    assert order.refunded == Decimal("10.99")
    assert gateway.transfers[0][1] == Decimal("10.99")


def test_float_amount_rejected_with_domain_error(context):
    _, order, _, _, service = context
    with pytest.raises(RefundError):
        service.issue(order, "float", 1.5, "alice", "return")


def test_invalid_amount_rejected_with_domain_error(context):
    _, order, _, _, service = context
    with pytest.raises(RefundError):
        issue(service, order, amount="-1")
