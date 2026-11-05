"""Exercise the trusted hook with mocked registration and telemetry writes."""
import builtins
import importlib.util
import io
import json
import os
import posixpath
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def hook(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "sandbox" / "sitecustomize.py"
    spec = importlib.util.spec_from_file_location("pod_test_sitecustomize", path)
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "addaudithook") as register:
        spec.loader.exec_module(module)
    register.assert_called_once_with(module.audit)
    return module


def capture(hook, monkeypatch, event, args, cwd="/home/runner", links=None):
    output = io.StringIO()
    with monkeypatch.context() as m:
        m.setattr(os, "path", posixpath)
        m.setattr(os, "getcwd", lambda: cwd)
        m.setattr(os, "O_ACCMODE", 3, raising=False)
        if links is not None:
            def readlink(path):
                if path not in links:
                    raise OSError("unavailable")
                return links[path]
            m.setattr(os, "readlink", readlink)
        # Keep StringIO accessible after the hook's context manager exits.
        class Stream:
            def __enter__(self):
                return output

            def __exit__(self, *args):
                return False

        m.setattr(builtins, "open", lambda *args, **kwargs: Stream())
        hook.audit(event, args)
    return [json.loads(line) for line in output.getvalue().splitlines()]


def test_relative_read_and_write_attempts(hook, monkeypatch):
    read = capture(hook, monkeypatch, "open", (".aws/../.aws/credentials", "r", os.O_RDONLY))
    assert [(e["type"], e["detail"]) for e in read] == [
        ("honeytoken_read", "/home/runner/.aws/credentials")
    ]
    write = capture(hook, monkeypatch, "open", (".aws/credentials", "w", os.O_WRONLY | os.O_CREAT))
    assert [e["type"] for e in write] == ["honeytoken_write"]
    both = capture(hook, monkeypatch, "open", (".netrc", "r+", os.O_RDWR))
    assert [e["type"] for e in both] == ["honeytoken_read", "honeytoken_write"]
    assert capture(hook, monkeypatch, "open", ("normal.txt", "r", os.O_RDONLY)) == []


def test_sensitive_paths_and_mode_fallback(hook, monkeypatch):
    assert capture(hook, monkeypatch, "open", ("../etc/shadow", "r", None), cwd="/")[0]["type"] == "sensitive_read"
    assert capture(hook, monkeypatch, "open", ("/etc/shadow", "a", None))[0]["type"] == "sensitive_write"
    assert capture(hook, monkeypatch, "open", (3, None, None)) == []


def test_directory_relative_deletion_and_unresolved_fd(hook, monkeypatch):
    links = {"/proc/self/fd/7": "/home/runner", "/proc/self/fd/8": "/work"}
    outside = capture(hook, monkeypatch, "os.remove", (".netrc", 7), links=links)
    assert [(e["type"], e["detail"]) for e in outside] == [("delete_outside_work", "/home/runner/.netrc")]
    assert capture(hook, monkeypatch, "shutil.rmtree", ("folder", 8), links=links) == []
    unresolved = capture(hook, monkeypatch, "os.remove", ("file", 9), cwd="/work", links=links)
    assert unresolved[0]["type"] == "delete_path_unresolved"
