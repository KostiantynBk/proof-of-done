"""Unit tests must never touch the real Docker daemon.

App startup reaps every container labelled ``pod.sandbox=1``. If a unit test reached a real
daemon it would destroy sandboxes of a live run on the same machine. Docker integration tests
opt in with POD_DOCKER_TESTS=1.
"""

import os

import pytest
from docker.errors import DockerException


@pytest.fixture(autouse=True)
def _no_real_docker(monkeypatch):
    if os.getenv("POD_DOCKER_TESTS") == "1":
        return

    def refuse(*_args, **_kwargs):
        raise DockerException("real Docker is disabled in unit tests (set POD_DOCKER_TESTS=1)")

    monkeypatch.setattr("pod.sandbox.runner.docker.from_env", refuse)
