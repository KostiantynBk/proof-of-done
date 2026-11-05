"""Shared data contracts for the Proof of Done pipeline (navigator-owned).

Two kinds of models live here:
- *LLM output* models define what a model may claim. They never carry trust fields
  such as ``verified`` or ``confidence``.
- *Computed* models are produced by deterministic code (verifiers, sandbox runs, the
  verdict ladder). Only these may say that something is verified, exists, passed or is proven.

Change this file only through the navigator; tickets import from it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ItemKind = Literal["requirement", "rule"]
Verdict = Literal["PROVEN", "WEAK", "UNTESTED", "VIOLATED", "UNVERIFIED", "INCONCLUSIVE"]
AiStatus = Literal["covered", "untested", "contradicts", "missing"]

# ---------------------------------------------------------------------------
# S0 ingest: history records (input data, normalised by the ingest stage)
# ---------------------------------------------------------------------------

RecordKind = Literal["review", "commit", "ci"]


class HistoryRecord(BaseModel):
    """One citable unit of history. ``record_id`` is what the miner must cite.

    Formats: ``review:<id>``, ``commit:<short-hash>``, ``ci:<file>#L<start>-L<end>``.
    """

    record_id: str
    kind: RecordKind
    pr: int | None = None
    author: str | None = None  # pseudonymous only, never real logins
    path: str | None = None
    line: int | None = None
    created_at: str | None = None
    text: str


# ---------------------------------------------------------------------------
# S1 extraction (LLM output)
# ---------------------------------------------------------------------------


class ExtractedRequirement(BaseModel):
    id: str = Field(description="REQ-01, REQ-02, ... in spec order")
    title: str
    text: str = Field(description="The requirement restated as one testable sentence")
    source_quote: str = Field(description="Verbatim sentence(s) copied from the spec")
    source_locator: str | None = Field(default=None, description="Heading or section label")
    acceptance_criteria: list[str] = Field(min_length=1)
    risk: Literal["high", "medium", "low"]


class RequirementExtraction(BaseModel):
    requirements: list[ExtractedRequirement]


# ---------------------------------------------------------------------------
# S2 rule mining (LLM output)
# ---------------------------------------------------------------------------


class RuleQuote(BaseModel):
    record_id: str = Field(description="Exact record_id of the history record quoted")
    quote: str = Field(description="Verbatim substring of that record's text, >= 6 words")


class MinedRule(BaseModel):
    id: str = Field(description="RULE-01, RULE-02, ...")
    rule: str = Field(description="Imperative, testable, <= 40 words")
    rationale: str
    quotes: list[RuleQuote] = Field(min_length=2)


class RuleMining(BaseModel):
    rules: list[MinedRule]


# ---------------------------------------------------------------------------
# S3 source verification (computed) and the unified audit item
# ---------------------------------------------------------------------------

QuoteMatch = Literal[
    "exact", "normalized", "not_found", "too_short", "abridged", "wrong_record", "number_mismatch"
]


class QuoteCheck(BaseModel):
    quote: str
    record_id: str | None = None
    match: QuoteMatch
    verified: bool
    locator: str | None = None


class Item(BaseModel):
    """A requirement or rule after S3; the unit that is audited, sabotaged and judged."""

    id: str
    kind: ItemKind
    title: str
    statement: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    risk: str | None = None
    source_checks: list[QuoteCheck]
    source_verified: bool
    # rules only; computed from verified quotes, never taken from the model
    recurrence: int | None = None
    distinct_prs: int | None = None
    distinct_reviewers: int | None = None
    accepted: bool = True
    drop_reason: str | None = None


# ---------------------------------------------------------------------------
# S5 audit (LLM output)
# ---------------------------------------------------------------------------


class CodeCitation(BaseModel):
    file: str = Field(description="Path relative to project root")
    line: int = Field(ge=1)
    snippet: str = Field(description="Verbatim code copied from that location")


class TestCitation(BaseModel):
    __test__ = False  # not a pytest class

    file: str
    test_name: str = Field(description="test_x, Class::test_x or test_x[param]")


class AuditClaim(BaseModel):
    item_id: str
    ai_status: AiStatus
    reason: str
    code_evidence: list[CodeCitation] = Field(default_factory=list)
    test_evidence: list[TestCitation] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# S6 evidence verification (computed)
# ---------------------------------------------------------------------------

CodeMatch = Literal[
    "exact", "relocated", "ambiguous", "not_found", "too_short", "invalid_path", "missing_file"
]


class CodeCitationCheck(BaseModel):
    citation: CodeCitation
    match: CodeMatch
    verified: bool
    actual_line: int | None = None


class TestCitationCheck(BaseModel):
    __test__ = False  # not a pytest class

    citation: TestCitation
    exists: bool
    nodeids: list[str] = Field(default_factory=list)
    passed: bool | None = None  # every case passed in both baseline runs
    flaky: bool = False


class EvidenceReport(BaseModel):
    item_id: str
    claim: AuditClaim
    code_checks: list[CodeCitationCheck]
    test_checks: list[TestCitationCheck]

    @property
    def has_verified_code(self) -> bool:
        return any(c.verified for c in self.code_checks)

    @property
    def guarding_tests(self) -> list[TestCitationCheck]:
        return [t for t in self.test_checks if t.exists and t.passed and not t.flaky]


# ---------------------------------------------------------------------------
# S7 sabotage (LLM output) and S8/S9 mutation records (computed)
# ---------------------------------------------------------------------------

MutationOrigin = Literal["targeted", "blind"]


class MutationProposal(BaseModel):
    file: str = Field(description="Non-test source file, path relative to project root")
    find: str = Field(description="Exact text that occurs exactly once in the file")
    replace: str
    description: str = Field(description="How this edit violates the item")


class MutationSet(BaseModel):
    item_id: str
    mutations: list[MutationProposal]


MutationOutcome = Literal["killed", "survived", "invalid", "timeout", "rejected"]


class Mutation(BaseModel):
    id: str  # e.g. REQ-01-T1 (targeted) / REQ-01-B2 (blind)
    item_id: str
    origin: MutationOrigin
    file: str
    find: str
    replace: str
    description: str
    line: int | None = None
    valid: bool = False
    reject_reason: str | None = None
    judge_relevance: float | None = None


class MutationResult(BaseModel):
    mutation_id: str
    item_id: str
    origin: MutationOrigin
    outcome: MutationOutcome
    killed_by: list[str] = Field(default_factory=list)  # failing pytest node ids
    killed_by_cited: bool = False
    sandbox_id: str | None = None
    duration_ms: int | None = None
    detail: str | None = None


# ---------------------------------------------------------------------------
# S10 verdicts (computed)
# ---------------------------------------------------------------------------


class ItemVerdict(BaseModel):
    item_id: str
    kind: ItemKind
    verdict: Verdict
    ai_status: AiStatus | None = None
    reasons: list[str] = Field(default_factory=list)
    mutations_tried: int = 0
    mutations_killed: int = 0
    mutations_survived: int = 0
    mutations_invalid: int = 0
    mutations_timeout: int = 0
    survived_by_origin: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# S11 gap closing
# ---------------------------------------------------------------------------


class ProposedTest(BaseModel):
    """LLM output: a new pytest module that guards one item."""

    item_id: str
    file: str = Field(description="New file under tests/, e.g. tests/test_pod_req_03.py")
    content: str
    explanation: str


class GapTestResult(BaseModel):
    """Computed: how a proposed test behaved in the sandbox."""

    item_id: str
    file: str
    passes_on_original: bool
    kills_survivors: list[str] = Field(default_factory=list)  # mutation ids it now kills
    still_survive: list[str] = Field(default_factory=list)
    reveals_violation: bool = False  # fails on original code -> item is VIOLATED
    sandbox_ids: list[str] = Field(default_factory=list)
    approved: bool | None = None
    approved_by: str | None = None
