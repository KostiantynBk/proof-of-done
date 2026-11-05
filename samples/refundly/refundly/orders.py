"""Captured orders with their original payment instrument."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from refundly.money import cents

PaymentMethod = Literal["card", "gift_card", "store_credit"]


@dataclass
class Order:
    order_id: str
    captured: Decimal
    payment_method: PaymentMethod
    created_at: datetime
    refunded: Decimal = Decimal("0.00")

    def __post_init__(self) -> None:
        self.captured = cents(self.captured)
