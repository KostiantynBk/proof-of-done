# Refundly demo target

A small, deliberately imperfect in-memory refund service for Proof of Done. This is a synthetic seeded example, not a production payment system or a benchmark. Its spec, generated PDF, synthetic PR reviews, git-like log, and CI log are inputs for an audit. `ground_truth.json` and the oracle tooling are for evaluation only; never supply them to the LLM or audit pipeline.

## Planted ground truth

| Item | Expected | Planted evidence |
| --- | --- | --- |
| REQ-refund-cap | PROVEN | Exact remaining amount and one cent over are tested. |
| REQ-manager-approval | WEAK | Only a 120-day request is tested. |
| REQ-audit-fields | WEAK | A test counts events but does not check their fields. |
| REQ-original-method | UNTESTED | No test requests a different method. |
| REQ-gift-credit | VIOLATED | Gift-card refunds go back to the gift card, not store credit. |
| REQ-idempotency | PROVEN | A repeated request cannot transfer twice. |
| REQ-retries | PROVEN | Retry count, failure state and no money movement are checked. |
| REQ-round-down | PROVEN | Fractional cents are checked in the result and gateway. |
| RULE-money | PROVEN | Tests reject floats and catch rounding through float conversion. |
| RULE-clock | WEAK | Functional age coverage does not enforce the clock seam. |
| RULE-audit | PROVEN | The event-existence test kills both seeded replacements of the record call with logging or printing. |
| RULE-errors | PROVEN | Tests require domain errors for invalid amounts. |

The table describes this finite, seeded set of oracle edits, not proof of absence of bugs. In particular, the audit test does not detect extra logging alongside a valid event. The two clock edits both substitute the real clock; the second adds a zero-duration timedelta and is not an independent behavioral challenge. The policy's gift-card exception qualifies the original-method rule. The answer key contains exact matches and expected outcomes; `oracle_report.txt` contains the latest check results.

## Safety and execution

**Never execute `tests/test_usage_metrics.py` on the host.** It deliberately reads the sandbox's fake AWS credentials and attempts to POST them to an `.invalid` domain, swallowing exceptions. It is for a disposable, network-isolated sandbox only. Host-side pytest commands must always include the explicit ignore flag. Do not use real credentials as sandbox canaries.

From the repository root (Windows paths shown):

```sh
uv venv --python 3.12 samples/refundly/.venv
uv pip install --python samples/refundly/.venv pytest pytest-timeout
samples/refundly/.venv/Scripts/python.exe -m pytest -q samples/refundly/tests --ignore=samples/refundly/tests/test_usage_metrics.py
samples/refundly/.venv/Scripts/python.exe samples/refundly/oracle_check.py
```

The oracle runs each mutation in a fresh temporary copy, strips caches and the venv, ignores the hostile file on every subprocess, and classifies only pytest exit 1 with a named failing test as killed. An outer 30-second wall-clock timeout is **not** a kill. pytest-timeout is required for the seeded unbounded retry mutation; that failure is a named test timeout, distinct from a sandbox wall-clock kill. On Windows, this trusted sample installs a small pytest-timeout compatibility hook to interrupt the pure-Python test thread: pytest-timeout's default Windows thread mode exits the process and cannot report a named failure. It is not a security boundary; production containment still requires the sandbox wall-clock limit. On POSIX the plugin's normal signal timer is used.

Regenerate the deterministic PDF with `python samples/refundly/tools/make_pdf.py`; regenerate the synthetic data with `tools/make_history.py` and `tools/make_ground_truth.py`. The in-memory idempotency registry is single-process and intentionally does not represent durable or concurrent payment processing.
