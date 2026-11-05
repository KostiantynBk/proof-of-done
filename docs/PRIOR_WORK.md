# Prior work and what is new

**All code in this repository was written during the Vultr Agent Rush event (3–8 Nov 2026).** Two earlier
projects shaped the ideas. Nothing was copied from either one.

## RuleProof (author's own, IBM Bob 2.0 hackathon, Sep 2026)
- Mined coding rules from a repository's review history and verified every quote verbatim.
- A/B-tested each rule by running a coding agent on throwaway copies, with and without the rule.
- Kept only the rules that measurably helped.
- Coupled to IBM Bob, with execution on the host and no sandbox.

## SoloTrace (Shehar Bano, IBM Bob 2.0 hackathon, Sep 2026; MIT)
- Audited requirements from a spec with verbatim `file:line` evidence.
- Proved each requirement by deliberately sabotaging the code.
- Studied for lessons only. We did not use its code, data or demo target.

## Lessons we applied
- **Quote verification:** verbatim checks for spec quotes, review quotes and code citations, with
  path containment and too-short or comment-only rejection.
- **Strict mutation outcomes:** a mutation is killed only when pytest names a failing test. Timeouts
  and crashes don't count. The baseline must pass first.
- **Independent sabotage:** an adversarial pass that never sees the tests.
- **Computed fields:** recurrence and independence are computed by code, never by the model.

## What is new here
- **Throwaway gVisor sandboxes on Vultr** for every untrusted execution, including PDF parsing.
  Fake-canary telemetry, a containment ledger, and an interactive "try to break it" playground.
- **One machinery for requirements and rules:** both are proven by sabotage, and only PROVEN rules
  are exported to `AGENTS.md`.
- **A static single-edit grammar** that blocks harness tampering by sabotage edits.
- **Gap closing:** generated guard tests are proven against the surviving mutations and need human
  approval. Approval is bound to the proof receipts.
- **A hash-chained journal and receipts manifest**, all model calls via Vultr Serverless Inference,
  and a public web product.
