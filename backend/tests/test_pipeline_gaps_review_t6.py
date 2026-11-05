import pytest
from pod.pipeline.gaps import _behavioral, _test_names, _valid_node


@pytest.mark.parametrize(('outcome', 'message', 'expected'), [
    ('failed', 'assert 1 == 2', True),
    ('failed', 'Timeout >10.0s', True),
    ('error', 'fixture missing', False),
    ('failed', 'ERROR at setup of test_x', False),
    ('failed', 'ImportError: missing dependency', False),
    ('skipped', '', False),
])
def test_only_behavioral_failure_counts(outcome, message, expected):
    assert _behavioral({'outcome': outcome, 'message': message}) is expected


def test_node_identity_matches_generated_module():
    names = _test_names('def test_real():\n    pass\n')
    assert _valid_node('tests/test_pod_req_03.py::test_real', 'tests/test_pod_req_03.py', names)
    assert not _valid_node('tests/test_pod_req_03.py::test_fake', 'tests/test_pod_req_03.py', names)
    assert not _valid_node('tests/test_other.py::test_real', 'tests/test_pod_req_03.py', names)
