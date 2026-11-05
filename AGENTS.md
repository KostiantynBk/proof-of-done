# AGENTS.md — rules for every coding agent working in this repo

Read `docs/SPEC.md` first. Your ticket file (path given in your prompt) defines scope, owned paths and acceptance criteria.

## Hard rules
1. **Stay inside your ticket's owned paths.** Do not edit files outside them. If you need a change elsewhere, write it under "Requests" in your report.
2. **Never put secrets in the repo, tests, fixtures, logs or prompts.** The only secret is `VULTR_INFERENCE_API_KEY`, read from the environment at runtime. Honeytokens in `sandbox/` are fake canaries and must be labelled as such.
3. **Never run untrusted or target-project code on the host.** Target-project tests run only through the sandbox runner. Unit tests use fakes for Docker and the LLM.
4. **Do not run `git commit`, `git push`, or change git config.** The navigator commits.
5. All LLM calls go through `pod.llm` (Vultr Serverless Inference, OpenAI-compatible). No other model providers.
6. Python 3.12+, type hints, `pydantic` v2 models for all JSON contracts, `ruff`-clean. Keep modules small and focused.
7. Every new behaviour gets a test. `pytest -q backend/tests` must pass (Docker-dependent tests are marked `@pytest.mark.docker` and skipped when Docker is unavailable).
8. Use only facts from run artifacts in the UI/README; never hard-code result numbers.

## Finish protocol
When done, write `../tickets/<TICKET>.report.md` with:
- what you built (files);
- how to run it;
- test output (the command you ran and a pass/fail summary);
- known gaps;
- requests for other tickets.

Then create the empty file `../tickets/<TICKET>.done`.
