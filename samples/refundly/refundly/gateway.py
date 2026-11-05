"""A deterministic gateway, with per-attempt failures for tests."""
from collections.abc import Iterable
from decimal import Decimal


class GatewayFailure(Exception):
    """The payment network did not accept this attempt."""


class FakeGateway:
    def __init__(self, failures: Iterable[bool] | None = None) -> None:
        self.failures = iter(failures or ())
        self.attempts = 0
        self.transfers: list[tuple[str, Decimal, str]] = []

    def refund(self, order_id: str, amount: Decimal, method: str) -> None:
        self.attempts += 1
        if next(self.failures, False):
            raise GatewayFailure("temporary payment failure")
        self.transfers.append((order_id, amount, method))
