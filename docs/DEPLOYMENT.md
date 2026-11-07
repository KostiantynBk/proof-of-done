# VM deployment

This describes the single Ubuntu 24.04 Vultr VM deployment, not a production security certification. Never execute target-project code on the host; use the sandbox runner.

1. From a trusted machine, copy `deploy/bootstrap.sh` to the VM and run it as root. The script installs Docker, gVisor `runsc`, Caddy, UFW, Python, and a capped tmpfs at `/srv/pod/work`. It creates a dedicated `pod` account using the first free matching UID/GID at or above 1000; it does not modify existing users. An existing `pod` account must have a non-root UID and a primary `pod` group. The service user belongs to the Docker group, which grants root-equivalent control over the host Docker daemon; this is not a multi-tenant deployment.
2. Transfer **only** the inference credential over SSH stdin into `/etc/pod/pod.env`, with `POD_REGION=sea`; set owner `root:pod` and mode `0640`. Do not pass the key as a command-line argument, print it, or deploy the local secret file. Keep the Vultr account API key off the VM.
3. From a fresh checkout, first install frontend dependencies with `(cd frontend && npm ci)`. Then run `bash deploy/deploy.sh <VM-IP>` from the repo on a machine with Node, npm, SSH, and the configured private key. The script builds the UI locally, uploads the working tree subset, builds the sandbox image and Python environment, installs systemd/Caddy config, and starts the service. Review the working tree before deploying: this deploys local changes, not necessarily the committed revision.
4. Verify `https://<dashed-IP>.sslip.io/api/health` with normal TLS verification. Check `runtime` is `runsc`, `sandbox_available` is true and `fake_llm` is false. Check service status, firewall and the workspace mount independently. A health check is not a containment test.

## Experimental role-specific reasoning controls

The default request is unchanged: no reasoning field is sent unless configured. For a separately reviewed experiment, set either or both nonsecret environment variables for each role, for example:

```text
POD_REASONING_EFFORT_AUDITOR=low
POD_REASONING_MAX_TOKENS_AUDITOR=1024
POD_REASONING_EFFORT_SABOTEUR=low
POD_REASONING_MAX_TOKENS_SABOTEUR=1024
```

Other roles follow the same naming pattern (for example, `POD_REASONING_EFFORT_TESTWRITER`). Supported effort names are `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, and `ultra`; the configured model must support the selected option. The reasoning token value must be a positive integer strictly below the call's total `max_tokens`; invalid settings fail rather than silently removing the control. These are provider-requested controls, **not host resource limits**. A small six-call experiment produced schema-valid responses, but is not a reliability benchmark; two testwriter outputs had mismatched item IDs, and MiniMax reported 1,037 reasoning tokens in two calls despite the requested 1,024 cap. No generated test was executed or approved. Do not enable testwriter controls as a claim of resolved guard generation; review the ID mismatch and evaluate the whole pipeline separately.

Set nonsecret options in the protected service environment only through the authorized deployment workflow, then restart the service only when no audit/playground is active. Existing retry/repair and per-process daily token budget still apply; a model/provider may reject or not honor a supported-looking option. No options or model changes were deployed by the experiment.

## Sandbox identity and cleanup

The service owns workspace directories. Bootstrap writes its non-root UID/GID to root-owned `/etc/pod/identity.env`; systemd reads this alongside the protected inference env file. Deploy verifies these IDs match the `pod` account and uses them as sandbox image build arguments. The runtime sets the same numeric container user. Local development defaults to `1000:1000`; `POD_SANDBOX_UID` and `POD_SANDBOX_GID` override them, and root IDs are rejected. Rebuild the image and restart the service together if the service identity changes; do not manually change one component in isolation. The Docker integration test builds its test image with the invoking non-root user's IDs and must be run as `pod` with the capped work root configured.

The service uses `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, and `NoNewPrivileges`; workspace isolation also depends on the runtime and resource limits. Telemetry is best-effort, not a security boundary. Run the separately authorized gVisor integration and containment checks before claiming containment is verified.
