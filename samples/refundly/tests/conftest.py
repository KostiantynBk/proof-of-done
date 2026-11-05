import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from refundly.audit import AuditLog
from refundly.gateway import FakeGateway
from refundly.orders import Order
from refundly.refunds import RefundService


@pytest.fixture
def context(monkeypatch):
    current = datetime.now(UTC)
    monkeypatch.setattr("refundly.clock.now", lambda: current)
    order = Order("order-1", Decimal("100.00"), "card", current)
    gateway = FakeGateway()
    audit = AuditLog()
    return current, order, gateway, audit, RefundService(gateway, audit)


@pytest.hookimpl(tryfirst=True, optionalhook=True)
def pytest_timeout_set_timer(item, settings):
    """On Windows interrupt this pure-Python demo loop instead of killing pytest.

    pytest-timeout's normal Windows thread method exits the entire process, so
    it cannot emit a named failure. This local compatibility hook is only for
    trusted demo tests; it is not a general-purpose sandbox or timeout guard.
    """
    if sys.platform != "win32" or settings.method != "thread":
        return None
    import ctypes
    import threading

    target = threading.get_ident()

    def interrupt():
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(target), ctypes.py_object(TimeoutError))

    timer = threading.Timer(settings.timeout, interrupt)
    timer.daemon = True
    item.cancel_timeout = lambda: (timer.cancel(), timer.join())
    timer.start()
    return True
