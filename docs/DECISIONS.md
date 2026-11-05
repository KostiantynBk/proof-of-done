# Decision Log

Newest first. Format: date — decision — why — alternatives rejected.

- **2026-11-05 — Product: "Proof of Done"** (Challenge 1, Blast Radius Zero). It extracts requirements from a spec and mines rules from review history, then proves each one by sandboxed execution plus sabotage (mutation) testing. The user chose this.
  - **Why:** it carries forward the user's RuleProof ideas (IBM Bob hackathon) without Bob. The containment moment is natural because the audited code and the AI's sabotage edits are untrusted and run in throwaway sandboxes.
  - **Rejected:** forking `KostiantynBk/ruleproof` or `bano1397/solotrace`. Both were previous lablab entries and both are coupled to Bob; SoloTrace is a third party's work. We learn lessons only; all code is new (see `PRIOR_WORK.md`).
- **2026-11-05 — Rules are proven by sabotage, not by agent A/B.**
  - **Why:** same machinery as requirements, a deterministic verdict, cheaper, and less noise.
  - **Status:** RuleProof-style A/B is a stretch goal (Sat, only if ahead).
- **2026-11-05 — Scope: Python + pytest targets only; built-in demo target `refundly` with planted ground truth.**
  - **Why:** a 3.3-day build and a reliable demo.
  - **Status:** GitHub-repo mode is a stretch.
- **2026-11-05 — Sandbox: Docker + gVisor `runsc`, no network, caps, no secrets, destroyed per run.**
  - The Python audit-hook telemetry and honeytokens are detection only; enforcement comes from gVisor and the caps.
  - **Rejected for v1:** a disposable Vultr VM per run (boot latency; kept as a stretch tier), E2B cloud (not on Vultr), OpenSandbox/Microsandbox (setup risk).
- **2026-11-05 — Stack: Python 3.12 FastAPI backend, React/Vite/TS/Tailwind frontend, SQLite, hash-chained event journal.**
  - **Why:** target projects are Python; one engine language; the journal gives verifiable receipts.
- **2026-11-05 — All LLM calls via Vultr Serverless Inference, with role-based models** (extractor/miner, auditor, saboteur from a different family, testwriter, optional decision-model judge). Final model choice after the spike.
- **2026-11-05 — Team process.**
  - Claude navigates: specs, integration, testing, commits.
  - Builder: GPT-6.1 Terra (high) as a Craft session on the ChatGPT Plus connection.
  - Reviewer: GPT-6.1 Sol (max) as a Craft session on ChatGPT Plus 2.
  - Builders do not commit; Claude reviews and commits.
