"""Evaluate planted mutations in private copies; never run the hostile fixture."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IGNORE = "--ignore=tests/test_usage_metrics.py"


def copy_sample(dst: Path) -> None:
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", "*.pyc"))


def run_pytest(folder: Path, extra: str | None = None) -> tuple[str, str]:
    args = [sys.executable, "-m", "pytest", "-q", "-x", "tests", IGNORE, "-p", "no:cacheprovider", "-o", "addopts="]
    if extra:
        args.append(extra)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(folder), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    try:
        import pytest_timeout  # noqa: F401
        args.extend(["-p", "pytest_timeout"])
    except ImportError:
        pass
    try:
        result = subprocess.run(args, check=False, cwd=folder, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return "wallclock_timeout", "subprocess exceeded 30 seconds"
    output = result.stdout
    failed = re.search(r"^FAILED\s+(\S+)", output, re.MULTILINE)
    if result.returncode == 0:
        return "survived", "passed"
    if result.returncode == 1 and failed:
        detail = failed.group(1)
        if "TimeoutError" in output or "Timeout (" in output:
            detail += " (named timeout failure)"
        return "killed", detail
    return "error", f"exit={result.returncode}; {output[-1200:]}"


def main() -> int:
    entries = json.loads((ROOT / "ground_truth.json").read_text(encoding="ascii"))
    errors = 0
    with tempfile.TemporaryDirectory(prefix="refundly-oracle-") as temp:
        base = Path(temp)
        copy_sample(base / "baseline")
        outcome, detail = run_pytest(base / "baseline")
        print(f"BASELINE {outcome}: {detail}")
        if outcome != "survived":
            return 1
        for entry in entries:
            for number, mutation in enumerate(entry["oracle_mutations"], 1):
                work = base / f"{entry['key']}-{number}"
                copy_sample(work)
                file = work / mutation["file"]
                text = file.read_text(encoding="utf-8")
                matches = text.count(mutation["search"])
                if matches != 1 or mutation["search"] == mutation["replace"]:
                    print(f"FAIL {entry['key']} #{number}: invalid edit (matches={matches})")
                    errors += 1
                    continue
                file.write_text(text.replace(mutation["search"], mutation["replace"], 1), encoding="utf-8")
                outcome, detail = run_pytest(work)
                ok = outcome == mutation["expect"]
                print(f"{'PASS' if ok else 'FAIL'} {entry['key']} #{number}: expected={mutation['expect']} observed={outcome} {detail}")
                errors += not ok
            if "oracle_test" in entry:
                work = base / f"{entry['key']}-test"
                copy_sample(work)
                (work / "tests" / "test_oracle_gap.py").write_text(entry["oracle_test"], encoding="utf-8")
                outcome, detail = run_pytest(work)
                ok = outcome == "killed" and "test_oracle_gap.py::test_gift_card_refunds_to_store_credit" in detail
                print(f"{'PASS' if ok else 'FAIL'} {entry['key']} oracle test: {outcome} {detail}")
                errors += not ok
                service_file = work / "refundly" / "refunds.py"
                code = service_file.read_text(encoding="utf-8")
                code = code.replace("destination = method or order.payment_method", "destination = method or (\"store_credit\" if order.payment_method == \"gift_card\" else order.payment_method)")
                code = code.replace("if destination != order.payment_method:", "if destination != (\"store_credit\" if order.payment_method == \"gift_card\" else order.payment_method):")
                service_file.write_text(code, encoding="utf-8")
                outcome, detail = run_pytest(work)
                ok = outcome == "survived"
                print(f"{'PASS' if ok else 'FAIL'} {entry['key']} reference fix: {outcome} {detail}")
                errors += not ok
    print(f"RESULT {'PASS' if not errors else 'FAIL'} mismatches={errors}")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
