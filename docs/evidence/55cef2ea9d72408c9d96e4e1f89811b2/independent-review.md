# T14 live-run semantic and proof review

**Reviewer:** 261107-windy-ravine  
**Run:** `55cef2ea9d72408c9d96e4e1f89811b2`  
**Reviewed deployment:** `627b3145d52847fc07de5a9778a740cb13918f77`  
**Review date:** 2026-11-07  
**Status at stored snapshot:** awaiting approval; no signoffs, approvals or patch content.

## Approval recommendations

**Recommend approving only REQ-02 and REQ-03 through the existing live approval gate, using the exact stored proposals.** Recommend rejecting the other four for this gap-closing workflow, for the distinct reasons below. These are recommendations, not approval actions. Revalidate live bindings/current snapshot when acting.

| Item | Recommendation | Reason |
|---|---|---|
| REQ-02 | **APPROVE exact stored guard** | Four original tests pass; three relevant survivors become named failures at the 90-day boundary, approved-old-order path and 91-day threshold. |
| REQ-03 | **APPROVE exact stored guard** | Two original tests pass; all four surviving actor/reason/order-ID defects become direct named assertion failures. |
| REQ-07 | **REJECT as a gap-closing claim** | Guard is reasonable regression coverage, but kills 0/1; remaining loop-bound mutation is equivalent under the shown return/break flow. It cannot establish closure of a real gap. |
| REQ-08 | **REJECT as a gap-closing claim** | Guard is reasonable rounding coverage, but kills 0/1; changing quantize operand 0.01 to 0.02 preserves exponent and therefore the relevant behavior. |
| RULE-01 | **REJECT as-is** | Original failure is generated test's invalid class-annotation lookup, not a proven Decimal defect. Approval would incorrectly endorse VIOLATED. |
| RULE-02 | **REJECT for this rule's proof** | Recorded kill checks timezone awareness inside the clock implementation, not the rule's injectable-clock call-site convention. Also adds an unfrozen wall-clock assertion; only one valid mutation exists. |

REQ-07/REQ-08 are **not unsafe or necessarily bad tests**. They could be separately retained as ordinary regression additions if desired, but that would not remove the survivors or justify PROVEN; the current verdict engine would retain WEAK. Rejection here is a conservative patch/demo-selection recommendation, not a demand to change stored mutation outcomes. Do not manually remove equivalent mutants, rewrite results, or force seeded labels in this run.

## Ranked findings and required interpretation

### HIGH — RULE-01's generated-test error must not be approved as a real violation

**Locations:** `T14-artifacts/guards/RULE-01.py.txt`, `test_gateway_transfer_amount_is_decimal`; `samples/refundly/refundly/gateway.py:16`; `backend/pod/pipeline/gaps.py` `_behavioral` and violation branch of `checked_approval`.

The test asks for `FakeGateway.__annotations__["transfers"]`. The source declares `self.transfers: list[tuple[str, Decimal, str]]` **inside `__init__`**, not as a class annotation. Its runtime values are Decimal, and the earlier runtime type/value assertions pass. Original sandbox `34934aae21c940379b30c1962c99ed33` reports exactly one failed guard node, `test_gateway_transfer_amount_is_decimal`, with `KeyError: 'transfers'`.

The broad behavioral-failure heuristic excludes import/setup errors but not this invalid test assumption, so it sets `reveals_violation=true`. The approval branch could convert the item to VIOLATED after a reviewer approves. **Reject this proposal.** Display the observation as “generated guard failed; reviewer must distinguish test defect from product defect,” not an established product violation. A separately scoped classification/UI fix is appropriate; do not modify this run's bound guard to repair it in place.

Its annotation-only mutants also do not establish runtime float arithmetic. The float-zero comparison mutant need not change behavior for valid Decimal inputs. These subtleties further limit any “three missed defects” narrative.

### MEDIUM — Equivalent survivors are not evidence of unguarded behavior

**Locations:** `T14-artifacts/guard-survivors.txt`, REQ-07-B3 and REQ-08-T6; `refundly/refunds.py:50–60`; `refundly/money.py:6,15`.

- **REQ-07-B3:** `<3` becomes `<4`, but a successful gateway call breaks immediately and the third `GatewayFailure` returns FAILED. The fourth iteration is unreachable along either documented path. Unexpected exceptions propagate in both versions. The recorded guard correctly still sees three attempts, no transfers/audit/balance movement and FAILED.
- **REQ-08-T6:** `Decimal("0.01")` becomes `Decimal("0.02")` solely as `quantize`'s operand. Quantize takes its exponent, not a multiple-of-two-cents grid. Both operands have exponent -2 and use unchanged ROUND_DOWN. The generated odd-cent tests cannot kill this equivalent edit.

Keep the recorded survived outcomes and current WEAK computation, but disclose semantic-equivalence limitations. Future mutation relevance handling requires a separate reviewed policy/ticket; do not call either survivor an actual missed requirement defect or doctor results to obtain PROVEN.

### MEDIUM — RULE-02 killed mutation measures a different property

**Locations:** `T14-artifacts/guards/RULE-02.py.txt`; RULE-02-T4; source review comments `review-004`/`review-005`.

The cited comments target `refundly/refunds.py` and require callers to use `clock.now()` so time is injectable. The mutant changes the implementation of `clock.now()` from `datetime.now(UTC)` to naive `datetime.now()`; the service still uses the injectable seam. The two failing generated nodes are the timezone-awareness assertion and the naive/aware time comparison. The third test, which actually monkeypatches the clock and checks service behavior, does **not** fail that mutant.

Timezone awareness is a useful API property but not the mined rule's stated property. The unfrozen `before <= stamp <= after` check also depends on real wall-clock continuity. Reject this proposal for closing RULE-02 as scoped. Even a mechanically accepted kill would leave only one scored mutation, so the ≥3 threshold would yield INCONCLUSIVE, not PROVEN.

## Why REQ-02 and REQ-03 are acceptable

### REQ-02 — late-refund approval

The spec says **more than** 90 days requires manager approval. The fixture freezes the service clock and provides a valid funded card order. Tests cover exactly 90 days without approval, 90 days +1 second without approval, 120 days with approval, and 91 days without approval, with relevant money-movement assertions.

Original receipt `37f56450e4cb4136aa92fa836312a513` passes all four generated nodes. Bound mutant receipts show:

- T1 (`>`→`>=`): `test_exactly_90_days_requires_no_approval` raises ApprovalRequired.
- T2 (remove `and not manager_approved`): `test_more_than_90_days_with_manager_approval_proceeds` raises ApprovalRequired. **The edit rejects approved late refunds; any model description claiming it permits unapproved refunds is backwards.**
- T3 (90→91): both the +1-second and 91-day rejection tests fail to raise ApprovalRequired.

These failures are semantically tied to the requirement, not collection errors. All three valid mutants originally survived and now have named failures; no generated tests alter production source or use external services.

Binding sequence **456**. Exact bound content SHA-256:
`afd155486d89497726c4b585e00da0f462e20f125b6058d8be640ee64af5fc59`.

### REQ-03 — complete audit records

The spec requires an audit record for every issued refund containing actor, amount, reason and order ID. The two tests inspect all fields for one refund, then two distinct refunds/actors/reasons. `order-1` is the actual fixture order ID. The additional `refund_issued` action assertion is consistent with the existing audit API; it is not the reason the reviewed survivor kills occur.

Original receipt `71cc5350570847a9a07c366996297377` passes both generated nodes. The four survivor checks fail on empty/wrong actor, empty reason or actor substituted for order ID. Named JUnit messages show direct expected/actual field mismatches. The already-killed T1 removes the audit call entirely and is also relevant. Thus the resulting candidate has five scored kills (one original plus four guard kills), not merely four total.

Binding sequence **470**. Exact bound content SHA-256:
`910bcfc433c658a9c0d5bfbbe673ddc9a537be72e1287dd208122143df093e1d`.

Both items' extracted quotes occur exactly in the stored PDF-extraction output; its SHA-256 matches state. Their verified code snippets match source at the cited lines, and cited test nodes passed in both baseline receipts. If current live checks accept these exact proposals, expected item outcomes are PROVEN under this implementation's ladder. **Do not publish projected counts as actual results before approval/export verification.**

## Independent proof and provenance checks

Created and ran only the stdlib, data-only reviewer script `T14_verify_artifacts.py`; its output is `T14-proof-verification.json`. It imports/executes no product, target or generated module. It reports **no unresolved verification errors** after accounting for inert-text newline conversion and SQLite character-count metadata.

Verified independently rather than trusting `chain_ok`:

- All **340 journal rows**, seq **172–511**, link from genesis and match recomputed canonical event SHA-256. Final head: `b1fea63356b3c4a7c9926cf5d0312462fdb96c60862023aaa927507a8f2432c6`.
- Receipts manifest digest recomputes to `23e0a85423381ac5ed6b7ebe229ecc831183b5d2a6dbb6a432f76b18ce9209b`.
- Manifest journal head is seq **509**. Seq 510 stage_finished and 511 run_finished occur after export construction; this is expected, not a broken chain.
- All six guard identity/result hashes recompute from complete stored item/evidence/baseline/mutation/result/proposal state. Survivor partitions, bound sandbox IDs, named failures and original node sets reconcile with parsed JUnit XML. Unapproved state and empty signoffs are consistent.
- Both baseline JUnit documents exactly reproduce stored baseline case lists; original guards and mutation results match their expected purposes, exit codes and outcomes. Killed guard receipts have the expected complete node sets and no guard setup/skip errors.
- All **65** unique sandbox records are runsc, have destruction timestamps, null cleanup/artifact errors, and match manifest summaries. They all name image `sha256:0c6edc5075e36402d70571708f10d9c4d7558e7fadba82ac2f4144626d9144b9`. Outcomes: 30 passed, 34 tests_failed, 1 error.
- The saved 12-file baseline copy independently hashes to T14's tree `e1bf6305462494b49599655e320a006dba5d4a1c359bd182f868a064ef61932b`. The seven implementation/fixture files read for semantic review match that copy byte-for-byte.
- All **45** saved local/VM product-file hashes agree and match this checked-out deployment. The selected config artifact shows only the six intended role controls, service/image 1001:1001, linuxuser 1000 preserved, secret-file mode root:pod 0640 and capped tmpfs. These operational observations are builder-captured evidence, not a new reviewer SSH attestation.
- Downloaded state/receipts/report/AGENTS/patch hashes match inventory. Report inventory size is SQLite text characters, not UTF-8 bytes.
- The six inert `.py.txt` copies use CRLF, whereas bound proposal contents use LF. They match exactly **after CRLF→LF normalization**, not byte-for-byte. Review/approval must bind to stored proposal bytes, not replace them with the inert copies. Content hashes above refer to the bound bytes.

Hash chains are consistency evidence, not authenticated signatures, semantic correctness or independent proof that a malicious host could not rewrite/recompute all artifacts. Current live source/state must still be checked by the actual approval path.

## Truthful demo/report boundaries

The builder's T14 report accurately warns about the faulty guard, equivalent/relevance mutations, four skipped gaps and one model component failure. Public report/UI wording needs separate work before presenting this as finished evidence:

- All 13 stages finishing is **not** all work succeeding. Four gaps were skipped: RULE-03, RULE-04, REQ-04, REQ-05; a saboteur call failed after repair.
- Do not expose pending `reveals_violation=true` as a confirmed violation; RULE-01 demonstrates why review exists.
- Model mutation descriptions are unverified claims. REQ-02-T2's direction and the “two-cent grid” story show that a syntactically valid mutation description can be wrong.
- Recorded 53 successful-call receipts sum to **255,237 tokens**; failed/billable attempts may be absent. This is not total billing.
- Preapproval exports contain no proven rules and an empty patch, as expected. Do not advertise learned PROVEN rules or approved fixes yet.
- Do not force agreement with seeded labels. No oracle was executed and no ground truth was changed or needed for these recommendations.

## Operations performed

Read inert guard text, actual source/spec/review comments, full relevant proof-gate/hash algorithms, selected report/config evidence and JSON receipts. Executed only reviewer-authored stdlib parsing/hash/XML checks against saved data. No generated/target code execution, model calls, sandbox workloads, live approvals, service operations, SSH, product edits, commits or pushes. All writes are under `../reviews/`.
