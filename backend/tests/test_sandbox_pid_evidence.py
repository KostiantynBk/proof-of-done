"""Unit tests run without Docker or a live cgroup."""
import pytest
from sandbox_pid_evidence import pid_cap_proven


def sample(counter, *, maximum="128", current="128"):
    return {"pids.max": maximum, "pids.current": current, "pids.events": counter}


GOOD = [sample("max 0\nother 9"), sample("other 9\nmax 1")]


@pytest.mark.parametrize(("outcome", "code", "stdout"), [
    ("error", 42, "PID_LIMIT EAGAIN\n"),
    ("error", 2, ""),
])
def test_accepts_only_evidence_backed_terminations(outcome, code, stdout):
    assert pid_cap_proven(GOOD, outcome, code, stdout)


@pytest.mark.parametrize(("samples", "outcome", "code", "stdout"), [
    (GOOD, "error", 42, "PID_LIMIT EPERM\n"),
    (GOOD, "error", 42, "PID_LIMIT ENOMEM\n"),
    (GOOD, "error", 42, "PID_LIMIT EAGAIN\nPID_LIMIT EPERM\n"),
    (GOOD, "timeout_killed", None, "PID_LIMIT EAGAIN\n"),
    (GOOD, "passed", 0, "PID_LIMIT EAGAIN\n"),
    (GOOD, "error", 2, "PID_LIMIT EPERM\n"),
    ([sample("max 0"), sample("max 0")], "error", 2, ""),
    ([sample("max 0"), sample("max 0")], "error", 42, "PID_LIMIT EAGAIN"),
    ([], "error", 2, ""),
    ([sample("max 1")], "error", 2, ""),
    ([sample("other 0"), sample("other 1")], "error", 2, ""),
    ([sample("max bad"), sample("max 1")], "error", 2, ""),
    ([sample("max 0\nmax 1"), sample("max 2")], "error", 2, ""),
    ([sample("max 0"), sample("max 1", maximum="max")], "error", 2, ""),
    ([sample("max 0"), sample("max 1", current="129")], "error", 2, ""),
    ([sample("max 1"), sample("max 0"), sample("max 2")], "error", 2, ""),
    ([sample("max 0\nother 2"), sample("max 0\nother 9")], "error", 2, ""),
])
def test_rejects_unsubstantiated_or_malformed_evidence(samples, outcome, code, stdout):
    assert not pid_cap_proven(samples, outcome, code, stdout)
