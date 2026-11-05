"""Refund orchestration for a single in-memory service instance."""
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from refundly import clock
from refundly.audit import AuditLog
from refundly.errors import ApprovalRequired, InvalidAmount, PaymentMethodMismatch
from refundly.gateway import FakeGateway, GatewayFailure
from refundly.money import cents
from refundly.orders import Order

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Refund:
    request_id: str
    order_id: str
    amount: Decimal
    method: str
    status: Literal["ISSUED", "FAILED"]


class RefundService:
    def __init__(self, gateway: FakeGateway, audit: AuditLog) -> None:
        self.gateway = gateway
        self.audit = audit
        self.processed: dict[str, Refund] = {}
        logger.info("Refund service initialized")

    def issue(self, order: Order, request_id: str, amount: Decimal, actor: str,
              reason: str, method: str | None = None, manager_approved: bool = False) -> Refund:
        if request_id in self.processed:
            return self.processed[request_id]
        amount = cents(amount)
        if amount <= Decimal("0.00"):
            raise InvalidAmount("refund must be positive")
        if amount > order.captured - order.refunded:
            raise InvalidAmount("refund exceeds remaining capture")
        requested_at: datetime = clock.now()
        if requested_at - order.created_at.astimezone(UTC) > timedelta(days=90) and not manager_approved:
            raise ApprovalRequired("manager approval required")
        destination = method or order.payment_method
        if destination != order.payment_method:
            raise PaymentMethodMismatch("refund must use original method")
        attempts = 0
        while attempts < 3:
            attempts += 1
            try:
                self.gateway.refund(order.order_id, amount, destination)
                break
            except GatewayFailure:
                if attempts == 3:
                    logger.warning("Gateway refund attempt %s failed", attempts)
                    failed = Refund(request_id, order.order_id, amount, destination, "FAILED")
                    self.processed[request_id] = failed
                    return failed
        order.refunded += amount
        self.audit.record(actor, "refund_issued", amount, reason, order.order_id)
        result = Refund(request_id, order.order_id, amount, destination, "ISSUED")
        self.processed[request_id] = result
        return result
