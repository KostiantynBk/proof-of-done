"""Verify Python implementation citations using whole-file tokens and AST context."""

import ast
import io
import re
import tokenize

from pod.schemas import CodeCitation, CodeCitationCheck
from pod.verify.normalize import strip_code_comment
from pod.verify.snapshot import Snapshot, is_test_file

_IGNORED = {
    tokenize.ENCODING,
    tokenize.ENDMARKER,
    tokenize.COMMENT,
    tokenize.NL,
    tokenize.INDENT,
    tokenize.DEDENT,
}


def _tokens(text: str) -> list[tokenize.TokenInfo] | None:
    try:
        return list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None


def _docstrings(tree: ast.AST) -> list[tuple[int, int]]:
    spans = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            spans.append((node.lineno, node.end_lineno or node.lineno))
    return spans


def _canonical(tokens: list[tokenize.TokenInfo]) -> list[tokenize.TokenInfo]:
    result = [t for t in tokens if t.type not in _IGNORED]
    # A trailing comma before a closing delimiter is formatting, not literal text.
    return [
        t
        for i, t in enumerate(result)
        if not (t.string == "," and i + 1 < len(result) and result[i + 1].string in (")", "]", "}"))
    ]


def _signature(t: tokenize.TokenInfo) -> tuple[int, str]:
    return (t.type, "\n" if t.type == tokenize.NEWLINE else t.string)


def verify_code_citation(c: CodeCitation, snap: Snapshot) -> CodeCitationCheck:
    path = snap.safe_path(c.file)
    if path is None:
        invalid = (
            not c.file
            or "\x00" in c.file
            or c.file.startswith(("/", "\\", "~"))
            or re.match(r"^[A-Za-z]:", c.file)
            or ".." in c.file.replace("\\", "/").split("/")
        )
        candidate = snap.root / c.file.replace("\\", "/") if not invalid else None
        try:
            missing = (
                candidate is not None
                and candidate.resolve().is_relative_to(snap.root)
                and not candidate.exists()
            )
        except (OSError, ValueError):
            missing = False
        return CodeCitationCheck(
            citation=c, match="missing_file" if missing else "invalid_path", verified=False
        )
    if not c.file.lower().endswith(".py") or is_test_file(c.file):
        return CodeCitationCheck(citation=c, match="invalid_path", verified=False)
    joined = " ".join(strip_code_comment(line).strip() for line in c.snippet.splitlines()).strip()
    if len(joined) < 8 or len(re.findall(r"\w", joined)) < 4:
        return CodeCitationCheck(citation=c, match="too_short", verified=False)
    text = "\n".join(snap.read_lines(c.file)) + "\n"
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return CodeCitationCheck(citation=c, match="not_found", verified=False)
    raw = _tokens(text)
    snippet = _tokens(c.snippet.strip() + "\n")
    if raw is None or snippet is None:
        return CodeCitationCheck(citation=c, match="not_found", verified=False)
    docs = _docstrings(tree)
    source = _canonical([t for t in raw if not any(a <= t.start[0] <= b for a, b in docs)])
    needle = _canonical(snippet)
    while needle and needle[-1].type == tokenize.NEWLINE:
        needle.pop()
    # A standalone string is data, not executable implementation evidence.
    if not needle or all(t.type == tokenize.STRING for t in needle):
        return CodeCitationCheck(citation=c, match="not_found", verified=False)
    # A short quote must match a whole source line, not a fragment.
    if len(c.snippet.splitlines()) == 1 and len(joined) < 16:
        short_exact = True
    else:
        short_exact = False
    target = [_signature(t) for t in needle]
    hits = []
    for i in range(len(source) - len(needle) + 1):
        chunk = source[i : i + len(needle)]
        if [_signature(t) for t in chunk] != target:
            continue
        start, end = chunk[0].start[0], chunk[-1].end[0]
        if end - start >= 8:
            continue
        if short_exact:
            same_line = [t for t in source if t.start[0] == start and t.type != tokenize.NEWLINE]
            if [_signature(t) for t in same_line] != target:
                continue
        hits.append(start)
    hits = sorted(set(hits))
    if c.line in hits:
        match, actual = "exact", c.line
    elif hits and (len(hits) == 1 or min(abs(n - c.line) for n in hits) <= 10):
        match, actual = "relocated", min(hits, key=lambda n: (abs(n - c.line), n))
    else:
        match, actual = ("ambiguous" if hits else "not_found"), None
    return CodeCitationCheck(
        citation=c, match=match, verified=match in {"exact", "relocated"}, actual_line=actual
    )
