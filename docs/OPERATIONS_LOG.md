# Deployment and verification log

Operational observations, not product-evaluation results. No containment or quality claim is established by provisioning alone.

## 2026-11-07 — Step A: Vultr access and one VM

- Authenticated `GET /v2/account`: HTTP 200.
- Real `POST /v1/chat/completions` through Vultr Serverless Inference, model `deepseek-v4.1-flash-1017`: HTTP 200 with a completion. The public model-list endpoint was not used as an authentication test.
- Authenticated instance listing was empty before provisioning. Created one Ubuntu 24.04 VM in Seattle, plan `vc2-4c-8gb`, label `proof-of-done`.
- The created VM reported `active`, `running`, and `ok` at 08:23:41 UTC. Public IP: `108.61.195.77`.
- Initial SSH connection timed out during boot; server setup and public HTTPS verification remain Step B work. No additional VM will be created as a retry.
- Sanitized source artifacts are held in navigator session `261107-alert-heron`: `data/step-a-api.json` and `data/step-a-vm.json`. They contain selected API fields only, no credentials or server passwords.

## 2026-11-07 — Step B: public deployment verified

- Public app: https://108-61-195-77.sslip.io/ — loaded successfully in the navigator's browser with valid TLS. Public health reports `runsc`, `sandbox_available=true`, `fake_llm=false`, `sandbox_leaked=0`.
- Navigator independently verified active app/Caddy/Docker services, UFW, capped tmpfs, and credential file metadata `root:pod 0640` without displaying credentials.
- Existing `linuxuser` stays UID/GID 1000. Dedicated `pod` uses 1001; image and runtime identity are aligned by deployment configuration. No additional instance was created.
- Independent backend test run: **219 passed, 3 skipped**, with dependency deprecation warnings. Ruff, shell syntax checks, diff whitespace check and frontend build passed. Test output: navigator session `261107-alert-heron/data/step-b-pytest.txt`.
- Builder evidence is in `hackathon-entry/tickets/T8-artifacts/`; read-only review is `hackathon-entry/reviews/T8-deploy-review.md`. Reviewer found no blocker/high issue; a low-severity missing `npm ci` prerequisite in the standalone guide is assigned to the next ticket.
- These are deployment/readiness results. No actual gVisor workload, tuned live audit or SSE check has yet been claimed.

## 2026-11-07 — Step C: measured gVisor checks verified

- Initial fork check failed because it expected a guest refusal marker. Bounded diagnosis observed the actual per-container host PID cap being reached; gVisor terminated with `error`/exit 2, not a graceful guest refusal. No limits were removed or raised.
- Reviewer identified an overly permissive marker branch in the diagnostic assertion. Builder tightened it to require valid, increasing named cgroup counters for both accepted outcomes; reviewer confirmed closure. Initial failures and raw observations are preserved.
- Navigator independently reran corrected real Docker checks with the app service stopped: **2 passed, 240 deselected**. The production-image probe measured `pids.max=128`, current reaching 128, `pids.events max` increasing 0→1, then error/exit 2. A fresh follow-up succeeded. Test-image checks also covered network isolation with a reachable-network control, time/memory limits and nested mode-000 cleanup. The two image roles are distinguished in `CONTAINMENT.md`.
- After the suite: zero labelled containers, empty work root; app service restored. Independent local suite: **238 passed, 4 skipped**; Ruff passed.
- Seven stored public playground receipts independently checked: all used runsc and had destruction timestamps with no cleanup errors. Exfil attempt recorded fake-canary read/DNS lookup and failure; loop timed out; memory OOM-killed; fork terminated with error/2; file-size limit produced EFBIG; shadow access denied and Docker socket absent. Root-deletion preset ignores errors, so its exit status alone is not proof of root protection. Final public health: `sandbox_leaked=0`.
- Navigator artifacts: session `261107-alert-heron/data/step-c-independent-docker.log`, `.exit`, `step-c-independent-unit.log`, `step-c-preset-verification.json`. Builder raw evidence: `hackathon-entry/tickets/T9-artifacts/`; reviews: `T9-containment-review.md`, `T9-fix2-review.md`.

## 2026-11-07 — Step D baseline and structured-output diagnosis (not ready to pin)

- Real service audit `57444101c07d4e90a54d20a7db2ea062`, product revision `2becfef`, reached terminal `done`, but generated no guard proposals: four guard attempts were skipped on inference errors. Terminal lifecycle state must not be read as complete successful coverage.
- Actual verdicts: 1 PROVEN, 2 WEAK, 4 UNTESTED, 5 INCONCLUSIVE; 8 source-verified requirements and 4 rules. Recorded 19 runsc sandboxes had teardown. Navigator independently inspected the public UI, replayed SSE through HTTPS, and recomputed all 171 retained journal events to head `badcf62dc7f10c244ad83a0f7ee7f57f3be964a2f37576ee00133d7d75ce9348`.
- Recorded successful calls report 172,850 tokens across 20 records; this excludes unrecorded failed/repair attempts and is not a total billing claim. Semantic seeded-label comparison was 3/12; supplied expected labels were not independently oracle-verified. No benchmark claim, approvals or pin.
- Six bounded role probes on the exact baseline context produced 11 provider responses: selected auditor and saboteur responses exhausted both 4,096 and 8,192 output budgets with zero content and provider-reported reasoning consuming essentially the whole cap. Testwriter validated on repair at the lower budget and initially at the higher budget, single examples only. This establishes a symptom for these probes, not the cause of every historical failure or a reliability fix. Probe-reported usage totals 106,118 tokens including repairs.
- Added opt-in, metadata-only diagnostic sink, not wired to public service/API. No model settings, token defaults, validators or runtime flags changed. Review found no blocker/high/medium; independent local tests **241 passed, 4 skipped**, Ruff passed. Further provider-supported reasoning-control experiments are separately gated.
- Evidence: `hackathon-entry/tickets/T10-artifacts/`, `T11-artifacts/`, reports T10/T11 and T11-diagnostics review; navigator session `261107-alert-heron/data/step-d-*`.

## 2026-11-07 — Step D reasoning-control experiment

- Official Vultr documentation and live model catalog support per-request reasoning effort and reasoning-token budget controls. Six bounded role probes requested low effort / 1,024 reasoning tokens within the unchanged 4,096 total cap; all six returned schema-valid output initially. Reported usage: 27,239 tokens. This is a small diagnostic sample, not a reliability benchmark.
- Limited static checks found exact code snippets in the two auditor samples and unique find text in the two saboteur samples. Neither proves full semantic correctness. Both testwriter samples returned wrong item IDs and remain rejected by the unchanged guard gate; no test was executed or approved from these probes.
- MiniMax reported 1,037 reasoning tokens despite requesting 1,024. The setting is not claimed as an observed hard cap.
- Added opt-in per-role configuration; defaults and model families unchanged. Reviewer found no actionable issue; navigator independently reran **257 passed, 4 skipped**, Ruff passed. Controls have not yet been deployed or validated in a complete tuned audit. Evidence: T12 report/artifacts and T12-reasoning review.

## Current gates

- Step B: server bootstrap, deployment, valid TLS and health verification — verified; low documentation follow-up fixed.
- Step C: scoped actual gVisor containment tests and cleanup evidence — verified; no general security-certification claim.
- Step D: tuned live model run, evaluation against the seeded target, approvals and pinned receipts — pending.
- Steps E–F: evidence-backed submission assets, real captures, video and form — pending.
