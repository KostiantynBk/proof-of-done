"""In-memory audit trail for the demo."""
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class AuditEvent:
    actor: str
    action: str
    amount: Decimal
    reason: str
    order_id: str


class AuditLog:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, actor: str, action: str, amount: Decimal, reason: str, order_id: str) -> None:
        self.events.append(AuditEvent(actor, action, amount, reason, order_id))
