"""Single clock seam for production time and deterministic tests."""
from datetime import UTC, datetime


def now() -> datetime:
    return datetime.now(UTC)
