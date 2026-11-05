"""Parse history records and verify independent rule evidence."""

import json
import re
from pathlib import Path

from pod.schemas import ExtractedRequirement, HistoryRecord, Item, MinedRule, QuoteCheck
from pod.verify.normalize import normalize_prose
from pod.verify.spec_quotes import _match, verify_spec_quote


def parse_reviews_json(path: Path) -> list[HistoryRecord]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("reviews", [])
    return [
        HistoryRecord(
            record_id=f"review:{r['id']}",
            kind="review",
            pr=r.get("pr"),
            author=r.get("author") or r.get("reviewer"),
            path=r.get("path") or r.get("file"),
            line=r.get("line"),
            created_at=r.get("created_at") or r.get("date"),
            text=r.get("body", r.get("text", "")),
        )
        for r in data
    ]


def _unique_prefixes(hashes: list[str], minimum: int = 7) -> dict[str, str]:
    """Shortest prefix (>= minimum chars) that is unique among this log's hashes."""
    result = {}
    for h in hashes:
        n = minimum
        while n < len(h) and sum(1 for other in hashes if other[:n] == h[:n]) > 1:
            n += 1
        result[h] = h[:n]
    return result


def parse_git_log(path: Path) -> list[HistoryRecord]:
    text = Path(path).read_text(encoding="utf-8")
    blocks = re.split(r"(?m)^commit\s+([0-9a-fA-F]+)[^\n]*\n", text)
    hashes = [blocks[i].lower() for i in range(1, len(blocks) - 1, 2)]
    if len(set(hashes)) != len(hashes):
        raise ValueError("git log contains duplicate commit hashes")
    short = _unique_prefixes(hashes)
    records = []
    for i in range(1, len(blocks) - 1, 2):
        body = blocks[i + 1].strip()
        pr = re.search(r"\(#(\d+)\)|Merge pull request #(\d+)", body)
        records.append(
            HistoryRecord(
                record_id=f"commit:{short[blocks[i].lower()]}",
                kind="commit",
                pr=int(pr.group(1) or pr.group(2)) if pr else None,
                text=body,
            )
        )
    return records


def parse_ci_log(path: Path, window: int = 20) -> list[HistoryRecord]:
    if window <= 0:
        raise ValueError("window must be positive")
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    return [
        HistoryRecord(
            record_id=f"ci:{path.name}#L{i + 1}-L{min(i + window, len(lines))}",
            kind="ci",
            path=path.name,
            line=i + 1,
            text="\n".join(lines[i : i + window]),
        )
        for i in range(0, len(lines), window)
    ]


def resolve_record_id(cited: str, records_by_id: dict[str, HistoryRecord]) -> str | None:
    """Map a cited id to exactly one known record id, or None.

    Exact ids win. Models often drop the kind prefix (``review-001`` for ``review:review-001``)
    or change case/brackets; such forms are accepted only when they resolve unambiguously.
    """
    if cited in records_by_id:
        return cited
    cleaned = cited.strip().strip("[]()<>").strip()
    if cleaned in records_by_id:
        return cleaned
    folded = cleaned.casefold()
    candidates = {rid for rid in records_by_id if rid.casefold() == folded}
    if not candidates:
        candidates = {rid for rid in records_by_id if rid.split(":", 1)[-1].casefold() == folded}
    return next(iter(candidates)) if len(candidates) == 1 else None


def verify_record_quote(
    quote: str, record_id: str, records_by_id: dict[str, HistoryRecord]
) -> QuoteCheck:
    resolved = resolve_record_id(record_id, records_by_id)
    record = records_by_id.get(resolved) if resolved else None
    if record is None:
        return QuoteCheck(quote=quote, record_id=record_id, match="wrong_record", verified=False)
    record_id = resolved
    match, _, _ = _match(quote, record.text, min_words=True)
    return QuoteCheck(
        quote=quote,
        record_id=record_id,
        match=match,
        verified=match in {"exact", "normalized"},
        locator=record_id,
    )


def is_generic_advice(rule_text: str) -> bool:
    normalized = normalize_prose(rule_text).casefold().strip(" .!;")
    patterns = (
        r"(?:use|choose) (?:clear |meaningful |more )?descriptive names",
        r"(?:avoid|don't make|do not make) unrelated changes",
        r"(?:keep|make) functions small",
        r"add comments",
    )
    return any(re.fullmatch(pattern, normalized) for pattern in patterns)


def accept_rule(rule: MinedRule, records_by_id: dict[str, HistoryRecord]) -> Item:
    checks = [verify_record_quote(q.quote, q.record_id, records_by_id) for q in rule.quotes]
    records = [records_by_id[c.record_id] for c in checks if c.verified and c.record_id is not None]
    units = {
        f"pr:{r.pr}" if r.pr is not None else r.record_id
        for r in records
        if r.pr is not None or r.kind == "commit"
    }
    authors = {r.author for r in records if r.author}
    generic = is_generic_advice(rule.rule)
    accepted = len(units) >= 2 and not generic
    reason = (
        "generic_advice"
        if generic
        else ("insufficient_independent_evidence" if not accepted else None)
    )
    return Item(
        id=rule.id,
        kind="rule",
        title=rule.rule,
        statement=rule.rule,
        source_checks=checks,
        source_verified=accepted,
        recurrence=len(records),
        distinct_prs=len(units),
        distinct_reviewers=len(authors),
        accepted=accepted,
        drop_reason=reason,
    )


def item_from_requirement(req: ExtractedRequirement, spec_text: str) -> Item:
    check = verify_spec_quote(req.source_quote, spec_text, statement=req.text)
    return Item(
        id=req.id,
        kind="requirement",
        title=req.title,
        statement=req.text,
        acceptance_criteria=req.acceptance_criteria,
        risk=req.risk,
        source_checks=[check],
        source_verified=check.verified,
        accepted=check.verified,
        drop_reason=None if check.verified else check.match,
    )
