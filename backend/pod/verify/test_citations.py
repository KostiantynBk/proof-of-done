"""Statically index pytest definitions and correlate baseline JUnit cases."""

import ast
import warnings

from pod.schemas import TestCitation, TestCitationCheck
from pod.verify.snapshot import Snapshot, is_test_file


def _collect(
    body: list[ast.stmt], tests: set[tuple[str | None, str]], classes: tuple[str, ...] = ()
) -> None:
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test"
        ):
            tests.add(("::".join(classes) or None, node.name))
        elif isinstance(node, ast.ClassDef) and node.name[:1].isupper():
            _collect(node.body, tests, (*classes, node.name))


def index_tests(snap: Snapshot) -> dict[str, set[tuple[str | None, str]]]:
    index: dict[str, set[tuple[str | None, str]]] = {}
    for file in sorted(snap.files):
        if not file.endswith(".py") or not is_test_file(file):
            continue
        tests: set[tuple[str | None, str]] = set()
        index[file] = tests
        try:
            tree = ast.parse("\n".join(snap.read_lines(file)), filename=file)
        except (SyntaxError, ValueError, OSError) as exc:
            warnings.warn(f"Cannot parse test file {file}: {type(exc).__name__}", stacklevel=2)
            continue

        _collect(tree.body, tests)
    return index


def _parts(name: str) -> tuple[list[str], str | None]:
    before, bracket, suffix = name.partition("[")
    return before.replace(".", "::").split("::"), ("[" + suffix if bracket else None)


def verify_test_citation(
    c: TestCitation,
    snap: Snapshot,
    index: dict[str, set[tuple[str | None, str]]],
    junit_runs: list[list[dict]],
) -> TestCitationCheck:
    file = c.file.replace("\\", "/")
    name = c.test_name
    prefix = name.split("[", 1)[0]
    if "::" in prefix and prefix.split("::", 1)[0].endswith(".py"):
        embedded, name = name.split("::", 1)
        if embedded.replace("\\", "/") != file:
            return TestCitationCheck(citation=c, exists=False)
    parts, requested_param = _parts(name)
    base = parts[-1]
    cls = "::".join(parts[:-1]) or None
    valid = all(parts) and snap.safe_path(file) is not None
    matches = (
        [
            (klass, test)
            for klass, test in index.get(file, set())
            if test == base and (cls is None or klass == cls)
        ]
        if valid
        else []
    )
    by_run: list[dict[str, str]] = []
    duplicate = False
    for run in junit_runs:
        found: dict[str, str] = {}
        for case in run:
            nodeid = case.get("nodeid", "")
            before, bracket, suffix = nodeid.partition("[")
            fields = before.split("::")
            if len(fields) < 2 or fields[0].replace("\\", "/") != file:
                continue
            method = fields[-1]
            klass = "::".join(fields[1:-1]) or None
            param = "[" + suffix if bracket else None
            if (klass, method) not in matches or (
                requested_param is not None and param != requested_param
            ):
                continue
            canonical = "::".join([file, *fields[1:]]) + (param or "")
            if canonical in found:
                duplicate = True
            found[canonical] = case.get("outcome", "")
        by_run.append(found)
    nodeids = sorted({node for run in by_run for node in run})
    flaky = any(len({run[node] for run in by_run if node in run}) > 1 for node in nodeids)
    passed = (
        None
        if not nodeids
        else len(by_run) >= 2
        and not duplicate
        and all(
            set(run) == set(nodeids) and all(outcome == "passed" for outcome in run.values())
            for run in by_run
        )
    )
    return TestCitationCheck(
        citation=c, exists=bool(matches), nodeids=nodeids, passed=passed, flaky=flaky
    )
