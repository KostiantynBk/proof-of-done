"""Small deterministic fake-model script, derived from the Refundly sample.

This is simulated inference, not a recorded run or evidence of model performance.
"""
from pod.llm import FakeLLM


def demo_llm():
    quote = 'Every issued refund must create an audit record containing actor, amount, reason and order id.'
    requirement = {'id': 'REQ-03', 'title': 'Refund audit record', 'text': quote,
                   'source_quote': quote, 'source_locator': 'Requirement R-C',
                   'acceptance_criteria': [quote], 'risk': 'high'}
    claim = {'item_id': 'REQ-03', 'ai_status': 'covered', 'reason': 'Audit event is created',
             'code_evidence': [{'file': 'refundly/refunds.py', 'line': 56,
                                'snippet': 'self.audit.record(actor, "refund_issued", amount, reason, order.order_id)'}],
             'test_evidence': [{'file': 'tests/test_refunds.py', 'test_name': 'test_audit_record_exists'}]}
    edits = [{'file': 'refundly/refunds.py',
              'find': 'self.audit.record(actor, "refund_issued", amount, reason, order.order_id)',
              'replace': f'self.audit.record(actor, "refund_issued", amount, {value}, order.order_id)',
              'description': 'Audit reason is incorrect'} for value in ('""', '"other"', 'request_id')]
    guard = {'item_id': 'REQ-03', 'file': 'tests/test_pod_req_03.py',
             'content': ('from decimal import Decimal\n\ndef test_audit_fields(context):\n'
                         '    _, order, _, audit, service = context\n'
                         '    service.issue(order, "req-guard", Decimal("10.00"), "alice", "return")\n'
                         '    event = audit.events[0]\n'
                         '    assert (event.actor, event.amount, event.reason, event.order_id) == '
                         '("alice", Decimal("10.00"), "return", order.order_id)\n'),
             'explanation': 'Checks audit fields; requires human review.'}
    return FakeLLM({'extractor': [{'requirements': [requirement]}], 'miner': [{'rules': []}],
                    'auditor': [claim],
                    'saboteur': [{'item_id': 'REQ-03', 'mutations': edits},
                                 {'item_id': 'REQ-03', 'mutations': edits}],
                    'testwriter': [guard]})
