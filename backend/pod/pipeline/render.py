"""Render allowlisted snapshots and conservatively identify test-support files."""
import ast
import configparser
import fnmatch
import tomllib

from pod.schemas import Item
from pod.verify.snapshot import Snapshot, is_test_file


def protected_test_files(snapshot: Snapshot) -> set[str]:
    """Protect configured and statically apparent pytest files, even with unusual names.

    This is conservative static discovery, not a substitute for a trusted collection manifest.
    """
    patterns = ['test_*.py', '*_test.py']
    for config in ('pytest.ini', 'setup.cfg', 'pyproject.toml'):
        path = snapshot.safe_path(config)
        if path is None:
            continue
        try:
            if config.endswith('.toml'):
                opts = tomllib.loads(path.read_text(encoding='utf-8')).get('tool', {}).get('pytest', {}).get('ini_options', {})
                raw = opts.get('python_files', [])
                patterns.extend(raw.split() if isinstance(raw, str) else raw)
            else:
                parser = configparser.ConfigParser(interpolation=None)
                parser.read_string(path.read_text(encoding='utf-8'))
                section = 'pytest' if config == 'pytest.ini' else 'tool:pytest'
                patterns.extend(parser.get(section, 'python_files', fallback='').split())
        except (ValueError, configparser.Error, OSError, TypeError):
            # Unknown config: protect all Python files rather than leak/edit possible tests.
            return {p for p in snapshot.files if p.endswith('.py')}
    protected = set()
    for path in snapshot.files:
        if not path.endswith('.py'):
            continue
        name = path.rsplit('/', 1)[-1]
        if is_test_file(path) or any(fnmatch.fnmatch(name, pattern) for pattern in patterns):
            protected.add(path)
            continue
        try:
            tree = ast.parse('\n'.join(snapshot.read_lines(path)))
            if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith('test_')
                   or isinstance(n, ast.Import) and any(a.name.split('.')[0] in ('pytest', '_pytest') for a in n.names)
                   or isinstance(n, ast.ImportFrom) and (n.module or '').split('.')[0] in ('pytest', '_pytest')
                   for n in ast.walk(tree)):
                protected.add(path)
        except (SyntaxError, ValueError, OSError):
            protected.add(path)
    return protected


def render_files(snapshot: Snapshot, *, include_tests: bool, max_chars: int = 150_000) -> str:
    protected = protected_test_files(snapshot) if not include_tests else set()
    paths = sorted(p for p in snapshot.files if (p.endswith('.py') or p.rsplit('/', 1)[-1] == 'pytest.ini')
                   and p not in protected)
    blocks = {}
    for path in paths:
        lines = snapshot.read_lines(path)
        blocks[path] = f'=== {path} ===\n' + '\n'.join(
            f'{i:4d} | {line}' for i, line in enumerate(lines, 1)) + '\n'
    marker = '\n[... file truncated ...]\n'
    while sum(map(len, blocks.values())) + max(0, len(paths) - 1) > max_chars:
        candidates = [p for p in paths if len(blocks[p]) > len(f'=== {p} ===\n') + len(marker)]
        if not candidates:
            break
        path = max(candidates, key=lambda p: len(blocks[p]))
        block = blocks[path]
        header = f'=== {path} ===\n'
        raw = block.removesuffix(marker)
        excess = sum(map(len, blocks.values())) + max(0, len(paths) - 1) - max_chars
        keep = max(len(header), len(raw) - excess - len(marker))
        blocks[path] = raw[:keep] + marker
    return '\n'.join(blocks[p] for p in paths)


def render_item(item: Item) -> str:
    lines = [f'{item.id}: {item.statement}', 'Acceptance criteria:']
    lines.extend(f'- {criterion}' for criterion in item.acceptance_criteria)
    if item.kind == 'rule':
        lines.append('Verified source quotes:')
        lines.extend(f'- {check.record_id}: {check.quote}' for check in item.source_checks
                     if check.verified)
    return '\n'.join(lines)
