"""Integration of xunit1 test identities with verifier matching."""

import pytest
from pod.sandbox.junit import parse_junit
from pod.schemas import TestCitation
from pod.verify.snapshot import Snapshot
from pod.verify.test_citations import index_tests, verify_test_citation


@pytest.mark.parametrize("parameter", ["a::b", "a.b", "a/[x]", "a b"])
def test_junit_nested_class_and_opaque_parameter(tmp_path, parameter):
    root = tmp_path / "TestDirectory" / "test_app.py"
    root.parent.mkdir()
    root.write_text("class RefundTests:\n    class Inner:\n        def test_pay(self): pass\n")
    case = f'<testsuite><testcase file="TestDirectory/test_app.py" classname="TestDirectory.test_app.RefundTests.Inner" name="test_pay[{parameter}]" /></testsuite>'
    results = parse_junit(case)
    snap = Snapshot(tmp_path)
    citation = TestCitation(
        file="TestDirectory\\test_app.py", test_name=f"RefundTests::Inner::test_pay[{parameter}]"
    )
    check = verify_test_citation(citation, snap, index_tests(snap), [results, results])
    assert check.exists and check.passed
    assert check.nodeids == [
        f"TestDirectory/test_app.py::RefundTests::Inner::test_pay[{parameter}]"
    ]


def test_junit_without_file_fails_closed(tmp_path):
    (tmp_path / "test_app.py").write_text("def test_pay(): pass\n")
    results = parse_junit(
        '<testsuite><testcase classname="test_app" name="test_pay" /></testsuite>'
    )
    snap = Snapshot(tmp_path)
    check = verify_test_citation(
        TestCitation(file="test_app.py", test_name="test_pay"),
        snap,
        index_tests(snap),
        [results, results],
    )
    assert check.exists and check.passed is None
