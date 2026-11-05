from pod.verify.history import parse_git_log


def test_colliding_short_hashes_get_unique_record_ids(tmp_path):
    log = tmp_path / "git-log.txt"
    log.write_text(
        "commit 0000000000000000000000000000000000000001\nAuthor: a\n\n    first (#1)\n\n"
        "commit 0000000000000000000000000000000000000002\nAuthor: b\n\n    second\n\n"
        "commit abcdef1234567890abcdef1234567890abcdef12\nAuthor: c\n\n    third (#3)\n",
        encoding="utf-8",
    )
    ids = [r.record_id for r in parse_git_log(log)]
    assert len(set(ids)) == 3
    assert ids[2] == "commit:abcdef1"  # non-colliding hashes keep the 7-char form
    assert all(i.startswith("commit:") for i in ids)


def test_record_id_resolution_tolerates_dropped_prefix_but_not_ambiguity():
    from pod.schemas import HistoryRecord
    from pod.verify.history import resolve_record_id, verify_record_quote

    recs = {r.record_id: r for r in [
        HistoryRecord(record_id="review:review-001", kind="review", pr=1, text="Use Decimal and money helpers for amounts, never float."),
        HistoryRecord(record_id="commit:abc1234", kind="commit", text="x"),
        HistoryRecord(record_id="review:dup", kind="review", text="a"),
        HistoryRecord(record_id="ci:dup", kind="ci", text="b"),
    ]}
    assert resolve_record_id("review-001", recs) == "review:review-001"
    assert resolve_record_id("[Review:Review-001]", recs) == "review:review-001"
    assert resolve_record_id("dup", recs) is None  # ambiguous across kinds
    assert resolve_record_id("review-999", recs) is None
    check = verify_record_quote("Use Decimal and money helpers for amounts, never float.", "review-001", recs)
    assert check.verified and check.record_id == "review:review-001"
