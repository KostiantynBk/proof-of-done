# Proof of Done — Product & Technical Spec (v1, 2026-11-05)

> **AI says "done". Prove it — inside a sandbox.**
> Lessons from the author's earlier RuleProof and from SoloTrace (third party, studied only) are in `../research/lessons-from-prior-work.md` (outside the repo).
> Hackathon: Vultr Agent Rush, Challenge 1 "Blast Radius Zero". Submit target: Sun 8 Nov 2026 ~20:00 Madrid.

## 1. Problem & user

- Teams merge AI-written code because "tests pass". Tests that pass are not tests that *guard*: a requirement can be unimplemented while every test is green.
- Regulated teams (finance: DORA Art. 9, PCI DSS 6.5; health: FDA software validation) must show every written requirement is implemented **and** tested. Today: spreadsheets.
- Team conventions live in code-review comments; AI coding agents never learn them, and hand-written `AGENTS.md` rules are unverified guesswork.
- **User:** engineering lead / compliance reviewer of a Python service. **Job:** "Before release, show me, with evidence, which requirements and team rules are actually guarded by tests — and fix the gaps."

## 2. What the product does (pipeline)

Every step that executes or parses untrusted material runs in a **throwaway sandbox**. All LLM calls go to **Vultr Serverless Inference**.

| # | Stage | Who | Output |
|---|---|---|---|
| S0 | **Ingest** project (built-in demo or public GitHub repo), spec (MD/TXT/PDF), history (review comments, git log, CI logs) | orchestrator; PDF→text **inside sandbox** | `spec.txt`, `history/`, project snapshot + tree hash |
| S1 | **Extract requirements** from the spec, each with a verbatim spec quote | LLM `extractor` | `requirements.json` |
| S2 | **Mine rules** from history: conventions reviewers enforced in **≥2 independent places** (different PRs/commits), each with verbatim quotes | LLM `miner` | `rules.json` |
| S3 | **Verify quotes** of S1/S2 deterministically; drop items whose quotes are not found | code (no AI) | `verification.json` |
| S4 | **Baseline test run**, done **twice** (flaky tests detected and excluded from evidence) | sandbox | junit results, containment events |
| S5 | **Audit**: per item (requirement or rule), cite code evidence + guarding tests as `file:line` + verbatim snippet | LLM `auditor` (parallel, one call per item) | `evidence/<id>.json` |
| S6 | **Verify evidence** deterministically (file exists in snapshot, snippet found near cited line, not trivial, test exists, test passed in S4) | code | evidence verdicts |
| S7 | **Sabotage, two passes per item**:<br>(a) *targeted*: the model sees item, code and tests, and writes 3 edits aimed at test gaps.<br>(b) *blind adversary*: the model sees item and code but **not the tests**, and writes 3 edits.<br>Each edit is the smallest break of exactly this item: a non-test file, an exact find/replace, with `origin` recorded. | LLM `saboteur` (different model family from auditor) | `mutations/<id>.json` |
| S8 | **Validate mutations**: `find` matches exactly once, `replace != find`, target is not a test file (`test_*`, `*_test.py`, `conftest.py`, `tests/`), path is contained; optional relevance score from the decision model | code + LLM `judge` | valid mutation set |
| S9 | **Prove**: every mutation runs the suite in its **own fresh sandbox**. Outcomes:<br>- `killed`: exit 1 **and** ≥1 named failed test; a pytest-timeout failure counts.<br>- `survived`: exit 0.<br>- `invalid`: collection/syntax error, or exit 1 with no named failure.<br>- `timeout`: sandbox wall-clock kill, which is **not** a kill.<br>Records `killed_by` and `killed_by_cited`. | sandbox (parallel ≤4) | `prove.json` |
| S10 | **Verdict** per item (see §4) | code | `matrix.json` |
| S11 | **Close gaps** for WEAK/UNTESTED items: LLM writes a guarding test; sandbox checks it passes on original code and fails on the survived mutations; **human approves** before it enters the patch | LLM `testwriter` + sandbox + human | `patch.diff`, updated verdicts |
| S12 | **Export**: proof report, `AGENTS.md` containing **only PROVEN rules** (each citing its guarding test), receipts | code | `report.md`, `AGENTS.md`, `receipts.json` |

## 3. Containment design (the "Blast Radius Zero" part)

**Enforcement (cannot be bypassed by code inside):**
- Docker with **gVisor `runsc`** runtime (config `POD_SANDBOX_RUNTIME`, default `runsc`; dev fallback `runc` shows a warning banner).
- `--network none`, `--read-only` rootfs, `--tmpfs /tmp:size=64m`, workspace bind at `/work` from a size-capped host tmpfs, `--memory 512m --memory-swap 512m`, `--cpus 1`, `--pids-limit 128`, `--ulimit fsize=50MB`, `--ulimit nofile=256`, `--user 1000:1000`, `--cap-drop ALL`, `--security-opt no-new-privileges`.
- Wall-clock timeout per run (default 90 s; mutation runs 60 s) → `docker kill`, outcome `timeout_killed`. OOM → `oom_killed`.
- **No secrets in the sandbox:** environment is an explicit allowlist (empty except `PYTHONDONTWRITEBYTECODE=1`, `HOME=/home/runner`, pytest flags). The Vultr key exists only in the orchestrator process env.
- **Lifecycle:** `docker rm -f` in `finally`; orphan reaper on startup removes containers labelled `pod.sandbox=1`; host workspace dir deleted after each run. Ledger records `destroyed_at`.
- Concurrency semaphore (default 4) so sandboxes cannot starve the orchestrator.

**Detection (best-effort telemetry, shown in the ledger):**
- Sandbox image ships a `sitecustomize.py` that installs a Python **audit hook** (`sys.addaudithook`) logging `socket.connect`, `socket.getaddrinfo`, `open` of sensitive paths, `subprocess.Popen`, `os.system`, `os.exec*`, `os.fork`, `shutil.rmtree` outside `/work` to `/telemetry/events.jsonl` (separate tmpfs mount, read after exit).
- **Honeytokens** (fake, clearly labelled canaries — not real secrets): `/home/runner/.aws/credentials`, `/home/runner/.ssh/id_rsa`, `/home/runner/.netrc` with canary values. A read = `honeytoken_read` event; canary string in any captured output = `exfil_attempt`.
- Honest framing in UI/README: enforcement is gVisor + caps + no network; the audit hook is telemetry that hostile code could evade — but evading it never grants network or host access.

**Ledger entry (`sandbox_runs` table + UI):** `sandbox_id, run_id, purpose (pdf_extract|baseline|mutation:<id>|gap_test:<id>|playground), image_digest, runtime, limits{memory_mb,cpus,pids,timeout_s,network}, started_at, ended_at, duration_ms, exit_code, outcome (passed|tests_failed|timeout_killed|oom_killed|error), telemetry[{type, detail, ts}], stdout_tail, stdout_sha256, destroyed_at`.

**Playground ("Try to break it"):** public page with presets (exfiltrate fake AWS key, fork bomb, infinite loop, fill disk, read host `/etc/shadow`, `rm -rf /`) plus an editable snippet; runs in the same sandbox; shows the ledger entry live. Rate-limited.

## 4. Verdicts

Per item (requirement `REQ-xx` or rule `RULE-xx`):
- **PROVEN** — ≥1 verified code citation, ≥1 cited test that exists and passed at baseline (both runs), and **every** valid mutation killed (**≥3 valid mutations required**, across both saboteur passes).
- **WEAK** — tests exist and pass, but ≥1 valid mutation survived.
- **UNTESTED** — no verified guarding test.
- **VIOLATED** — a guarding test (existing or gap-closing) fails on the original code, i.e. the code does not do what the item says.
- **UNVERIFIED** — the item's own source quote (spec/history) or all evidence failed deterministic verification; excluded from claims.
- **INCONCLUSIVE** — evidence verified but fewer than 3 valid mutations could be produced or run (e.g. all invalid/timeout).

Ladder order (first match wins):
1. **UNVERIFIED**: the source quote failed verification.
2. **VIOLATED**: a cited guarding test failed in both baseline runs, or a gap test fails on the original code.
3. **UNTESTED**: no verified code citation, or no guarding test that exists and passed both baseline runs without flaking.
4. **WEAK**: ≥1 valid mutation survived. A single survivor is enough, however few mutations ran.
5. **INCONCLUSIVE**: fewer than 3 killed mutations, or the baseline suite failed. Runs that end `invalid` (import or collection error, crash) or in a wall-clock `timeout` are neither kills nor survivals. They are reported and **excluded from scoring**.
6. **PROVEN**.

Sabotage runs for every item with a verified source and verified code, **including UNTESTED ones**. Their survivors are what a gap-closing test must kill. The AI's own claim never yields PROVEN; only executed evidence does. Show 'AI said covered vs sabotage result' per item, and the survival rate per saboteur `origin` (targeted vs blind).

**Computed, never model-supplied:** `verified`, `exists`, `passed`, `recurrence`, `distinct_prs`, `confidence`. Rule acceptance = verified quotes from **≥2 distinct PRs/commits**; each quote is checked against its cited record only, and must be ≥6 words. One-off or generic-advice items are dropped and the drop is logged.

Every verdict links to its receipts. Numbers in UI/README come only from run artifacts.

## 5. Vultr usage

- **Cloud Compute VM** (Ubuntu 24.04, region `sea` near the inference models): FastAPI orchestrator + job runner, SQLite, static web UI, Caddy TLS, Docker + gVisor sandboxes. Not static hosting: the VM plans, dispatches, verifies.
- **Serverless Inference** (`https://api.vultrinference.com/v1`, OpenAI-compatible) for **all** LLM calls. Roles → default models (final choice after spike, configurable via env):

| Role | Default | Why |
|---|---|---|
| extractor / miner | `deepseek-v4.1-flash-1017` | 1M context, cheap, reasoning |
| auditor | `glm-5.3` | strongest agentic coding |
| saboteur | `minimax-m3.1-flash` | different family from auditor → independent sabotage |
| testwriter | `glm-5.3` | coding |
| judge (mutation relevance, optional) | `pplx-decider-v1.2` | calibrated probabilities; spike the request format first |

- Optional stretch: **Vultr API** creates a disposable VM for "high-risk" projects and destroys it after the run.

## 6. Architecture

```
Browser ──HTTPS──> Caddy (VM) ──> FastAPI app (systemd, user pod)
                                   ├─ API + SSE events
                                   ├─ Job runner (asyncio, per-run pipeline S0–S12)
                                   ├─ LLM client ──HTTPS──> Vultr Serverless Inference
                                   ├─ SQLite + /srv/pod/data/runs/<id>/ artifacts
                                   └─ Sandbox runner ──docker API──> runsc containers (no network)
```

- **Backend:** Python 3.12, FastAPI, uvicorn, `docker` SDK, `httpx`, `pydantic` v2, SQLite via stdlib `sqlite3` (thin repo layer), `sse-starlette` (or plain StreamingResponse). Package `pod` in `backend/`.
- **Frontend:** React + Vite + TypeScript + Tailwind in `frontend/`, built to static files served by FastAPI at `/`.
- **Sandbox image:** `sandbox/Dockerfile` — `python:3.12-slim`, non-root `runner` (uid 1000), pytest, pytest-timeout, pypdf, `sitecustomize.py` telemetry, honeytokens. Built on the VM as `pod-sandbox:latest`.
- **Journal & receipts:** every pipeline event appended to a hash-chained journal (`events` table: `seq, run_id, ts, type, payload_json, prev_hash, hash`). Receipts export = run manifest with sha256 of inputs, each LLM call (model, prompt sha, response sha, tokens), each sandbox run, and final matrix; top-level hash shown in UI.
- **Public-demo protection:** per-IP limits (runs 3/h, playground 10/10 min), global concurrent runs 2, daily token budget; when exceeded, offer the **recorded demo run** (a completed run stored in the DB, replayable instantly).

## 7. API contract (v1)

- `GET /api/health` → `{ok, runtime, models, sandbox_image}`
- `POST /api/runs` body `{project: "demo:refundly" | "github:<owner>/<repo>", spec_text?: str}` (+ multipart spec file later) → `{run_id}`
- `GET /api/runs/{id}` → run summary + items with verdicts + counts
- `GET /api/runs/{id}/events` → SSE stream of journal events (`stage_started`, `stage_finished`, `item_extracted`, `evidence_verified`, `mutation_result`, `sandbox_started`, `sandbox_finished`, `verdict`, `approval_needed`, `error`)
- `GET /api/runs/{id}/items/{item_id}` → item detail (quotes, evidence, mutations with diffs, sandbox receipts)
- `POST /api/runs/{id}/approvals/{item_id}` body `{approve: bool, reviewer: str}`
- `GET /api/runs/{id}/export/{report.md|AGENTS.md|receipts.json|patch.diff}`
- `GET /api/sandboxes?limit=50` → recent ledger entries
- `POST /api/playground` body `{preset?: str, code?: str}` → `{sandbox_run}`

## 8. Demo project `refundly` (built-in target, in `samples/refundly/`)

Small pure-Python orders/refunds library (no web framework) with a spec (`SPEC-v2.md` + PDF), a synthetic review history (`history/reviews.json`, `history/git-log.txt`, `history/ci/*.txt`) and a pytest suite with **planted, documented ground truth** (`ground_truth.json`, hidden from the LLM):
- ~8 requirements: some PROVEN (strong tests), some WEAK (loose assertions), some UNTESTED, one VIOLATED (real bug).
- ~4 rules each raised ≥2 times in history (+ noise comments raised once → must NOT become rules).
- **Hostile fixtures for the containment moment:** a test module that reads the honeytoken AWS credentials and tries to POST them to an external host; a code path where a plausible mutation causes an infinite loop (→ `timeout_killed`).
- Known-answer evaluation: compare pipeline verdicts with `ground_truth.json`; report the score honestly (small seeded demo, not a benchmark).

## 9. Non-goals (v1)

Languages other than Python/pytest; private repos; auto-merging PRs; claiming defect absence (mutation score shows tests *guard* items, not that code is bug-free); production auth/multi-tenant accounts.

## 10. Stretch (only if ahead of schedule)

1. GitHub mode: fetch public PR review comments via GitHub API for real-repo rule mining.
2. RuleProof-style A/B: coding agent does a task with/without each rule in sandboxes (3 repeats).
3. Disposable Vultr VM tier via Vultr API.
4. Input screening with `nemotron-3.5-content-safety` for prompt injection in spec/history.
5. gVisor `runsc` strace-based telemetry (outside the sandbox, not evadable).

## 11. Repo layout

```
proof-of-done/
  backend/pod/            # FastAPI app, pipeline, llm, sandbox, verify, store
  backend/tests/
  frontend/               # React/Vite/TS/Tailwind
  sandbox/                # Dockerfile, sitecustomize.py, honeytokens, entry scripts
  samples/refundly/       # demo target + spec + history + ground_truth.json
  deploy/                 # bootstrap.sh (docker, runsc, caddy), Caddyfile, pod.service, deploy.sh
  docs/                   # SPEC.md, DECISIONS.md, ARCHITECTURE.md (diagram), PRIOR_WORK.md
  AGENTS.md  README.md  LICENSE (MIT)  pyproject.toml
```
