"""Generate the seeded, evaluation-only answer key."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
entries = []


def add(key, kind, anchor, verdict, why, file, changes, oracle_test=None):
    mutations = [{"file": file, "search": s, "replace": r, "expect": e} for s, r, e in changes]
    row = {"key": key, "kind": kind, "anchor": anchor, "expected_verdict": verdict, "why": why, "oracle_mutations": mutations}
    if oracle_test:
        row["oracle_test"] = oracle_test
    entries.append(row)


R = "refundly/refunds.py"
M = "refundly/money.py"
add("REQ-refund-cap", "requirement", "A refund may never exceed the captured amount minus refunds already issued.", "PROVEN", "The boundary tests protect the remaining capture and equality.", R, [
    ("amount > order.captured - order.refunded", "amount >= order.captured - order.refunded", "killed"),
    ("amount > order.captured - order.refunded", "amount > order.captured", "killed")])
add("REQ-manager-approval", "requirement", "Refunds requested more than 90 days after the order date require manager approval.", "WEAK", "The only age assertion is for 120 days.", R, [
    (" > timedelta(days=90)", " >= timedelta(days=90)", "survived"),
    ("timedelta(days=90)", "timedelta(days=100)", "survived")])
add("REQ-audit-fields", "requirement", "Every issued refund must create an audit record containing actor, amount, reason and order id.", "WEAK", "The existing test counts records but does not check their fields.", R, [
    ('self.audit.record(actor, "refund_issued", amount, reason, order.order_id)', 'self.audit.record("unknown", "refund_issued", amount, reason, order.order_id)', "survived"),
    ('self.audit.record(actor, "refund_issued", amount, reason, order.order_id)', 'self.audit.record(actor, "refund_issued", amount, "", order.order_id)', "survived")])
add("REQ-original-method", "requirement", "requests to a different method are rejected", "UNTESTED", "No test sends a mismatched payment method.", R, [
    ("if destination != order.payment_method:", "if False:", "survived"),
    ("destination != order.payment_method", "destination != destination", "survived")])
oracle_test = '''from decimal import Decimal
from refundly.orders import Order
from refundly.audit import AuditLog
from refundly.gateway import FakeGateway
from refundly.refunds import RefundService
from refundly import clock

def test_gift_card_refunds_to_store_credit():
    order = Order("gift", Decimal("10"), "gift_card", clock.now())
    gateway = FakeGateway()
    result = RefundService(gateway, AuditLog()).issue(order, "gift-1", Decimal("1"), "alice", "return")
    assert result.method == "store_credit"
    assert gateway.transfers[0][2] == "store_credit"
'''
add("REQ-gift-credit", "requirement", "Orders paid by gift card are refunded only as store credit.", "VIOLATED", "The service currently directs gift card refunds back to gift cards.", R, [
    ("destination != order.payment_method", "False", "survived"),
    ("destination != order.payment_method", "destination != destination", "survived")], oracle_test)
add("REQ-idempotency", "requirement", "the same request_id processed twice results in exactly one refund.", "PROVEN", "A repeated request is checked for both identity and transfer count.", R, [
    ("request_id in self.processed", "False", "killed"),
    ("self.processed[request_id] = result", "pass", "killed")])
add("REQ-retries", "requirement", "after the third failure the refund is marked FAILED and no money moves.", "PROVEN", "Retry tests check the attempt count, failure state, and lack of transfers.", R, [
    ("attempts += 1", "attempts += 0", "killed"),
    ("if attempts == 3:", "if attempts == 2:", "killed"),
    ("while attempts < 3:", "while attempts < 2:", "killed")])
add("REQ-round-down", "requirement", "Partial refund amounts are rounded down to the cent.", "PROVEN", "The fractional-cent test checks the result, accounting, and gateway amount.", M, [
    ("value.quantize(CENT, rounding=ROUND_DOWN)", "value.quantize(CENT)", "killed"),
    ("value.quantize(CENT, rounding=ROUND_DOWN)", "value.quantize(Decimal('0.1'), rounding=ROUND_DOWN)", "killed")])
add("RULE-money", "rule", "Use Decimal and money helpers for amounts, never float.", "PROVEN", "Float inputs are rejected and fractional-cent arithmetic stays exact.", R, [
    ("cents(amount)", "cents(float(amount))", "killed"),
    ("cents(amount)", "cents(Decimal(float(amount)))", "killed")])
add("RULE-clock", "rule", "Use clock.now() instead of datetime.now() for all current timestamps.", "WEAK", "Tests patch the clock but do not assert that calls go through it.", R, [
    ("clock.now()", "datetime.now(UTC)", "survived"),
    ("clock.now()", "datetime.now(UTC) + timedelta()", "survived")])
add("RULE-audit", "rule", "Write audit events with audit.record(), not logger.info or print.", "PROVEN", "The record-existence test kills both seeded replacements of the audit call, but does not exclude extra logs.", R, [
    ('self.audit.record(actor, "refund_issued", amount, reason, order.order_id)', 'logger.info("refund issued")', "killed"),
    ('self.audit.record(actor, "refund_issued", amount, reason, order.order_id)', 'print(actor)', "killed")])
add("RULE-errors", "rule", "Raise errors from refundly.errors instead of bare ValueError or Exception.", "PROVEN", "Tests require domain errors for invalid amounts and excess refunds.", M, [
    ('raise InvalidAmount("amount must be Decimal")', 'raise ValueError("amount must be Decimal")', "killed"),
    ('raise InvalidAmount("amount must be finite and non-negative")', 'raise ValueError("amount must be finite and non-negative")', "killed")])
(ROOT / "ground_truth.json").write_text(json.dumps(entries, indent=2) + "\n", encoding="ascii")
