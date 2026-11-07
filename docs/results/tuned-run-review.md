# SUPPLEMENTARY INTERPRETATION — tuned demo run

**Generated:** 7 November 2026. **Run:** `55cef2ea9d72408c9d96e4e1f89811b2`. **Audited product revision:** `627b3145d52847fc07de5a9778a740cb13918f77`. **Disclosure-code revision:** `5f992ad`. This is a separately authored review of preserved evidence, **not** a replacement export, a recomputed audit or another approval. The run's original exports were created before the disclosure-code update and remain unchanged. The old receipt digest **does not cover this supplement**.

## What the run recorded

After six decisions the saved postdecision summary and manifest report **4 PROVEN, 6 WEAK, 2 UNTESTED**, two approved guard tests and **zero PROVEN rules**. The recorded reviewer was **Craft Agent navigator (delegated review)**, not the user personally. REQ-02 and REQ-03 were approved; REQ-07, REQ-08, RULE-01 and RULE-02 were rejected. The model had claimed 10 items covered; model claims are not verification. The 43 valid candidates yielded **29 effective kills, 13 survivors and 1 invalid**, so 42 were scored after applying the two approved guards. These are results for this seeded example, not a benchmark, proof of defect absence or a claim of causal improvement.

All 65 sandbox records show destruction. Stages finishing did **not** mean every operation succeeded: four gap attempts were skipped at the six-item limit (RULE-03, RULE-04, REQ-04 and REQ-05). REQ-05 is the gift-card item; it was not closed or demonstrated violated by a generated guard. There was **one recorded component-failure entry**, associated in the prior review with a saboteur schema-validation failure after repair. An error-entry count is not a deduplicated failed-call or billing count.

## What the decisions mean

- **REQ-02 — late-refund approval:** The approved guard exercised the exact-90-day boundary, beyond-90-day rejection and an approved late refund. Original sandbox `37f56450e4cb4136aa92fa836312a513` passed; survivors T1, T2 and T3 failed named guard tests in sandboxes `d658a9032b4e42c9835443b930ff0b44`, `ea71dd81671241a2be3b176757347131` and `ff409d4e8e184203be06e6687c97c8fe`, respectively. The T2 edit *rejects approved late refunds* by dropping the approval exception; a model description suggesting the opposite is unreliable.
- **REQ-03 — refund audit record:** The approved guard checked actor, amount, reason and order ID, including two refunds. Original sandbox `71cc5350570847a9a07c366996297377` passed; the four survivor checks T2, T3, B1, B2 failed named tests in `cb2464f392c74b64af19e6c43307d102`, `8b8e4a8c56564147b64dee1d859114dc`, `f18984563ee746b59de992a6c0a31952` and `eb5d96964a194ee2ab780f8fc1302e2f` respectively. The independently reviewed [guard bindings and test names](../evidence/55cef2ea9d72408c9d96e4e1f89811b2/independent-review.md) provide details.
- **RULE-01:** The rejected generated guard failed on the original because it assumed `FakeGateway.__annotations__['transfers']` existed and raised `KeyError`. That is a generated-test assumption, not an established Decimal defect. The preserved raw `reveals_violation` flag is not independent confirmation.
- **REQ-07 and REQ-08:** Their recorded survivors appear semantically equivalent in the reviewed flows: an unreachable fourth retry and `Decimal('0.01')` vs `Decimal('0.02')` as a quantize operand with the same exponent. A surviving edit need not demonstrate a real missed behavior.
- **RULE-02:** The killed edit targeted timezone awareness inside the clock implementation rather than the stated injectable-clock call-site rule. The proposed guard was rejected for that rule's proof.

No mutation was manually invalidated, no score promoted to force a desired label, and the original bindings and decisions remain unchanged. More generally, equivalent or off-target mutations can survive or be killed; descriptions and model relevance claims are not proof that an edit represents a real defect. The recorded ladder is evidence against these edits, not a compliance certification or proof of no defects.

## Provenance and verification

[Open the preserved evidence pack](../evidence/55cef2ea9d72408c9d96e4e1f89811b2/README.md) for exact exports, postdecision summary, predecision journal and JUnit-bearing sandbox records, the independent review and reproducible byte hashes. The review independently checked the predecision event chain, sandbox records, guard bindings and semantics; the postdecision verification checked the saved exports, decisions and manifest. These are different snapshots, not a single fresh independent live attestation. A hash chain is consistency evidence, not an authenticated signature.

The predecision journal spans sequence 172–511 and ends at `b1fea63356b3c4a7c9926cf5d0312462fdb96c60862023aaa927507a8f2432c6`. REQ-02 binding sequence 456 has event hash `2d2b39835a8fe4460884da9c43ebc590c544024f4ef9ad6b0e64e31a67e53e80` and proposal content SHA-256 `afd155486d89497726c4b585e00da0f462e20f125b6058d8be640ee64af5fc59`. REQ-03 binding sequence 470 has event hash `07ef843c22acc9764757c2653230925cf3d5a031859b4c8dff47df8c95f8e05a` and proposal content SHA-256 `910bcfc433c658a9c0d5bfbbe673ddc9a537be72e1287dd208122143df093e1d`. The postdecision receipt's internal canonical manifest digest is `960e20f2f931922f3c194cccdc2d0f21fa1c02596245c74c9825c5ec8e088b2b`, with recorded journal head `7e729cf5c9dea10efe4af9eedb6720a6d5eadbc7feb8c906c19549afe379aba5`.

Exact postdecision export **file-byte SHA-256** values (different from the internal canonical manifest digest):

| Original export | SHA-256 |
|---|---|
| [report.md](../evidence/55cef2ea9d72408c9d96e4e1f89811b2/report.md) | `d92f60a07fb0a95d1dd007f220845dc2efc08f877abeb9dd3b1d132961530fb7` |
| [AGENTS.md, renamed as inert text](../evidence/55cef2ea9d72408c9d96e4e1f89811b2/exported-AGENTS.txt) | `daca55fecbe153c889ac07548e08b9ed1a94a0bd3fcb10374f608cc2363fc63b` |
| [receipts.json](../evidence/55cef2ea9d72408c9d96e4e1f89811b2/receipts.json) | `6fecbdaab75991c2e679374587aae30ee7039b99a3c55560fac67e0727699dc4` |
| [patch.diff, inert text](../evidence/55cef2ea9d72408c9d96e4e1f89811b2/patch.diff) | `6b4547c154e6afd0a15b2fb3cb2b1e36004818528a400e83ed6bba40793c06f7` |

The [full inventory](../evidence/55cef2ea9d72408c9d96e4e1f89811b2/SHA256SUMS) includes the journal and independent-review artifacts. Never execute or apply the evidence pack's untrusted source or patch.
