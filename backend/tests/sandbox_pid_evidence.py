"""Pure release-check predicates for per-container cgroup PID evidence."""

from itertools import pairwise


def _events_max(raw: object) -> int | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    counters: dict[str, int] = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[1].isascii() or not parts[1].isdigit() or parts[0] in counters:
            return None
        counters[parts[0]] = int(parts[1])
    return counters.get("max")


def pid_cap_proven(samples: object, outcome: str, exit_code: int | None, stdout: str) -> bool:
    """Require a positive named counter delta plus one specifically identified termination."""
    if not isinstance(samples, list) or len(samples) < 2 or not isinstance(stdout, str):
        return False
    counts: list[int] = []
    for sample in samples:
        if not isinstance(sample, dict) or sample.get("pids.max") != "128":
            return False
        current = sample.get("pids.current")
        if not isinstance(current, str) or not current.isascii() or not current.isdigit() or int(current) > 128:
            return False
        counter = _events_max(sample.get("pids.events"))
        if counter is None:
            return False
        counts.append(counter)
    if any(after < before for before, after in pairwise(counts)) or counts[-1] <= counts[0]:
        return False
    marker = "PID_LIMIT EAGAIN"
    if outcome == "error" and exit_code == 42:
        return stdout.strip() == marker
    return outcome == "error" and exit_code == 2 and "PID_LIMIT" not in stdout
