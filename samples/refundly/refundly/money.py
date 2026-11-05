"""Money operations avoid binary floating point."""
from decimal import ROUND_DOWN, Decimal

from refundly.errors import InvalidAmount

CENT = Decimal("0.01")


def cents(value: Decimal) -> Decimal:
    """Round a non-negative Decimal toward zero, equivalent to down here."""
    if not isinstance(value, Decimal):
        raise InvalidAmount("amount must be Decimal")
    if not value.is_finite() or value < 0:
        raise InvalidAmount("amount must be finite and non-negative")
    return value.quantize(CENT, rounding=ROUND_DOWN)
