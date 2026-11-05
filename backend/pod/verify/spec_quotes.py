"""Verbatim source quote verification."""

import re
import unicodedata

from pod.schemas import QuoteCheck
from pod.verify.normalize import normalize_prose, numbers_in


def _match(quote: str, text: str, *, min_words: bool = False) -> tuple[str, int | None, int | None]:
    if (len(quote.split()) < 6 and (min_words or len(quote.strip()) < 25)) or not quote.strip():
        return "too_short", None, None
    pos = text.find(quote)
    if pos >= 0:
        return "exact", pos, pos + len(quote)
    # Map normalized characters back to source offsets, including Unicode expansions.
    chars: list[str] = []
    offsets: list[tuple[int, int]] = []
    i = 0
    while i < len(text):
        end = i + 1
        while end < len(text) and unicodedata.combining(text[end]):
            end += 1
        part = " " if text[i].isspace() else normalize_prose(text[i:end])
        for c in part:
            if c == " " and (not chars or chars[-1] == " "):
                continue
            chars.append(c)
            offsets.append((i, end))
        i = end
    needle = normalize_prose(quote)
    pos = "".join(chars).find(needle)
    if pos >= 0 and needle:
        return "normalized", offsets[pos][0], offsets[pos + len(needle) - 1][1]
    return ("abridged" if "..." in quote or "…" in quote else "not_found"), None, None


def verify_spec_quote(quote: str, spec_text: str, *, statement: str | None = None) -> QuoteCheck:
    match, start, end = _match(quote, spec_text)
    locator = None
    if start is not None and end is not None:
        prefix = spec_text[:start]
        headings = [
            line.strip()
            for line in prefix.splitlines()
            if re.match(r"^\s*(?:#{1,6}\s|Requirement\b)", line)
        ]
        locator = headings[-1] if headings else f"L{prefix.count(chr(10)) + 1}"
        if statement is not None and numbers_in(statement) - numbers_in(
            spec_text[max(0, start - 200) : end + 200]
        ):
            match = "number_mismatch"
    return QuoteCheck(
        quote=quote, match=match, verified=match in {"exact", "normalized"}, locator=locator
    )
