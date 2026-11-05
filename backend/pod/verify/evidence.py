"""Combine deterministic citation checks into an evidence report."""

from pod.schemas import AuditClaim, EvidenceReport
from pod.verify.code_citations import verify_code_citation
from pod.verify.snapshot import Snapshot
from pod.verify.test_citations import verify_test_citation


def verify_claim(
    claim: AuditClaim,
    snap: Snapshot,
    test_index: dict[str, set[tuple[str | None, str]]],
    junit_runs: list[list[dict]],
) -> EvidenceReport:
    return EvidenceReport(
        item_id=claim.item_id,
        claim=claim,
        code_checks=[verify_code_citation(c, snap) for c in claim.code_evidence],
        test_checks=[
            verify_test_citation(c, snap, test_index, junit_runs) for c in claim.test_evidence
        ],
    )
