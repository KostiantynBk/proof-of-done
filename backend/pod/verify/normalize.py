"""Normalization helpers for quoted prose and source code."""

import re
import unicodedata
from collections import Counter

_TRANSLATION = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "′": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "″": '"',
        "–": "-",
        "—": "-",
    }
)


def normalize_prose(s: str) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", s.translate(_TRANSLATION)).translate(_TRANSLATION).split()
    )


def normalize_code(s: str) -> str:
    return " ".join(s.split())


def strip_code_comment(line: str) -> str:
    quote = ""
    i = 0
    while i < len(line):
        if quote:
            if line[i] == "\\":
                i += 2
                continue
            if line.startswith(quote, i):
                i += len(quote)
                quote = ""
                continue
        elif line[i] in "'\"":
            quote = line[i] * (3 if line.startswith(line[i] * 3, i) else 1)
            i += len(quote)
            continue
        elif line[i] == "#":
            return line[:i].rstrip()
        i += 1
    return line.rstrip()


def numbers_in(s: str) -> Counter[str]:
    text = normalize_prose(s)
    text = "".join(str(unicodedata.decimal(ch)) if ch.isdecimal() else ch for ch in text)
    pattern = (
        r"(?<![\w.])(?P<sign>[+-]?)(?P<number>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?![\w.,]\d)"
    )
    return Counter(
        ("-" if m.group("sign") == "-" else "") + m.group("number").replace(",", "")
        for m in re.finditer(pattern, text)
    )
