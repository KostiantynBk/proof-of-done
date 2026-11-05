"""Domain errors exposed to callers."""


class RefundError(Exception):
    """Base class for rejected requests."""


class InvalidAmount(RefundError):
    """The requested amount is invalid."""


class ApprovalRequired(RefundError):
    """A manager must approve the request."""


class PaymentMethodMismatch(RefundError):
    """The destination does not match the order."""
