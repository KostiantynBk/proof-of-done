# Deployment and verification log

Operational observations, not product-evaluation results. No containment or quality claim is established by provisioning alone.

## 2026-11-07 — Step A: Vultr access and one VM

- Authenticated `GET /v2/account`: HTTP 200.
- Real `POST /v1/chat/completions` through Vultr Serverless Inference, model `deepseek-v4.1-flash-1017`: HTTP 200 with a completion. The public model-list endpoint was not used as an authentication test.
- Authenticated instance listing was empty before provisioning. Created one Ubuntu 24.04 VM in Seattle, plan `vc2-4c-8gb`, label `proof-of-done`.
- The created VM reported `active`, `running`, and `ok` at 08:23:41 UTC. Public IP: `108.61.195.77`.
- Initial SSH connection timed out during boot; server setup and public HTTPS verification remain Step B work. No additional VM will be created as a retry.
- Sanitized source artifacts are held in navigator session `261107-alert-heron`: `data/step-a-api.json` and `data/step-a-vm.json`. They contain selected API fields only, no credentials or server passwords.

## Current gates

- Step B: server bootstrap, deployment, valid TLS and health verification — pending.
- Step C: actual gVisor containment tests and cleanup evidence — pending.
- Step D: tuned live model run, evaluation against the seeded target, approvals and pinned receipts — pending.
- Steps E–F: evidence-backed submission assets, real captures, video and form — pending.
