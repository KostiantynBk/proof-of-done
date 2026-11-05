from pod.sandbox.junit import parse_junit


def test_junit_outcomes_and_timeout():
    xml = '''<testsuites><testsuite>
      <testcase file="tests/test_refund.py" classname="tests.test_refund.TestRefund" name="test_ok"/>
      <testcase file="tests/test_refund.py" classname="tests.test_refund" name="test_fail"><failure message="assert 1 == 2"/></testcase>
      <testcase file="tests/test_refund.py" classname="tests.test_refund" name="test_err"><error message="boom"/></testcase>
      <testcase file="tests/test_refund.py" classname="tests.test_refund" name="test_skip"><skipped message="later"/></testcase>
      <testcase file="tests/test_refund.py" classname="tests.test_refund" name="test_timeout"><failure message="Timeout (&gt;10.0s)"/></testcase>
    </testsuite></testsuites>'''
    cases = parse_junit(xml)
    assert [c["outcome"] for c in cases] == ["passed", "failed", "error", "skipped", "failed"]
    assert cases[0]["nodeid"] == "tests/test_refund.py::TestRefund::test_ok"
    assert "Timeout" in cases[-1]["message"]


def test_class_chain_uses_file_not_naming_convention():
    xml = '''<testsuite>
      <testcase file="tests/test_refund.py" classname="tests.test_refund.RefundCase" name="test_ok[foo-bar]"/>
      <testcase file="tests/test_refund.py" classname="tests.test_refund.Outer.InnerCase" name="test_nested"/>
      <testcase file="tests/Test_module.py" classname="tests.Test_module" name="test_function"/>
      <testcase file="test_a.py" classname="test_a.Case[one]" name="test_a[1]"/>
    </testsuite>'''
    assert [c["nodeid"] for c in parse_junit(xml)] == [
        "tests/test_refund.py::RefundCase::test_ok[foo-bar]",
        "tests/test_refund.py::Outer::InnerCase::test_nested",
        "tests/Test_module.py::test_function",
        "test_a.py::Case[one]::test_a[1]",
    ]


def test_missing_file_does_not_invent_module_boundary():
    case = parse_junit('<testsuite><testcase classname="tests.test_x.RefundCase" name="test_x"/></testsuite>')[0]
    assert case["file"] == ""
    assert case["nodeid"] == "tests.test_x.RefundCase::test_x"
