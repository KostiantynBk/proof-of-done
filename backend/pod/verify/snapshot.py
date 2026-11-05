"""Allowlisted snapshot access; no target code is executed."""

import hashlib
import os
import re
from pathlib import Path, PurePosixPath

_MAX_BYTES = 1_000_000
_EXCLUDED_DIRS = {".git", "venv", "node_modules", "__pycache__", ".pytest_cache"}


def is_test_file(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    name = parts[-1]
    return (
        "tests" in parts
        or name == "conftest.py"
        or (name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py")))
    )


class Snapshot:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.files: set[str] = set()
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = [
                d
                for d in dirs
                if d not in _EXCLUDED_DIRS
                and not d.startswith((".venv", ".env"))
                and not (Path(directory) / d).is_symlink()
            ]
            for name in files:
                path = Path(directory) / name
                if name.startswith(".env") or name.endswith((".db", ".sqlite")):
                    continue
                try:
                    if (
                        path.is_symlink()
                        or not path.resolve().is_relative_to(self.root)
                        or not path.is_file()
                        or path.stat().st_size > _MAX_BYTES
                    ):
                        continue
                    data = path.read_bytes()
                    if b"\x00" in data:
                        continue
                    data.decode("utf-8")
                except (OSError, UnicodeError, ValueError):
                    continue
                self.files.add(path.relative_to(self.root).as_posix())

    def safe_path(self, rel: str) -> Path | None:
        if (
            not isinstance(rel, str)
            or not rel
            or "\x00" in rel
            or rel.startswith(("/", "\\", "~"))
            or re.match(r"^[A-Za-z]:", rel)
        ):
            return None
        normalized = rel.replace("\\", "/")
        parts = PurePosixPath(normalized).parts
        if ".." in parts or normalized not in self.files:
            return None
        path = self.root.joinpath(*parts)
        try:
            if (
                any(self.root.joinpath(*parts[:i]).is_symlink() for i in range(1, len(parts) + 1))
                or not path.resolve().is_relative_to(self.root)
                or not path.is_file()
            ):
                return None
        except (OSError, ValueError):
            return None
        return path

    def read_lines(self, rel: str) -> list[str]:
        path = self.safe_path(rel)
        if path is None:
            raise ValueError("path not in snapshot")
        return path.read_text(encoding="utf-8").splitlines()

    def tree_sha256(self) -> str:
        digest = hashlib.sha256()
        for rel in sorted(self.files):
            path = self.safe_path(rel)
            if path is None:
                raise ValueError("snapshot changed")
            content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            digest.update(f"{rel}\0{content_hash}\n".encode())
        return digest.hexdigest()

    is_test_file = staticmethod(is_test_file)
