# Containment verification — Vultr VM

These observations are from the Proof of Done Vultr Ubuntu 24.04 VM on 2026-11-07, using Docker 29.9.3, gVisor `runsc` release-20261102.0, and service/sandbox UID:GID `1001:1001`. They are a limited demonstration, not a security certification or proof that defects are absent. Python audit-hook telemetry is **best-effort** and hostile code may bypass it; enforcement is provided by the configured runtime and host limits. The orchestrator belongs to the Docker group, which grants root-equivalent control of the host daemon, so this is not a multi-tenant security boundary for the orchestrator itself.

## Method

The service was stopped before the opt-in Docker integration suite and restarted only after it exited and no labelled sandbox containers or workspaces remained. The suite was explicitly collected from `backend/tests`, not from the target demo project. With the environment prepared on this VM, the command was:

```sh
cd /srv/pod/app
sudo -u pod env POD_DOCKER_TESTS=1 POD_SANDBOX_RUNTIME=runsc \
  POD_WORK_ROOT=/srv/pod/work POD_SANDBOX_UID=1001 POD_SANDBOX_GID=1001 \
  .venv/bin/python -m pytest -q -s backend/tests -m docker
```

Do not run the service or another runner concurrently: orphan cleanup and global container checks would race. This diagnostic currently expects a systemd cgroup-v2 Docker host and reads the cgroup counters while they exist. The recorded suite passed **2 tests**, with 221 deselected; this was before the later tightening of PID acceptance, so the corrected suite still requires an independent rerun. The broader isolation and nested mode-000 cleanup regression used a freshly built UID-matched **test image** `pod-sandbox:test`, observed as `sha256:3d2a7eb6dffa870c13a666ef31f514551a2ffec06577bb5159e59a82383185c1`. It checked a real egress-positive runc control; runsc network isolation and canary telemetry; wall timeout; memory-limit behavior; deletion of sandbox-created nested mode-000 files/directories; and a fresh sandbox after cleanup. The memory assertion permits either a cgroup OOM kill or a guest `MemoryError`; the suite log does not identify which branch passed.

The separate PID/config diagnostic and public playground presets used the **production image** `pod-sandbox:latest`, observed as `sha256:5ea1c0d18c2aaff1faab06e529d228f42dbe1ec6771a9a731896fc8ba17baee8`. These image IDs differ; the test-image mode-000 result must not be described as a production-image mode-000 test.

For a production-image run, selected Docker configuration showed `runsc`, user `1001:1001`, network `none`, read-only root, all capabilities dropped, `no-new-privileges`, 512 MiB memory and equal memory-plus-swap limits, one CPU, 128 PIDs, 50 MiB file and 256 open-file ulimits, a 64 MiB `/tmp` tmpfs, and writable bind mounts only at `/work` and `/telemetry`. The inspected environment **names**, including Python base-image defaults, contained no Vultr credential name; values were not logged. This confirms configuration for the inspected run, not all possible future executions.

## PID limit caveat

The first integration suite failed because it expected a guest `PID_LIMIT` message but got an empty-output `error`. An instrumented, bounded production-image probe then observed cgroup-v2 `pids.max=128`, `pids.current` reaching 128 and `pids.events` `max` increasing from 0 to 1. The sandbox exited 2 with `outcome=error`, not a graceful guest refusal; cleanup and a fresh follow-up run succeeded. The repeated suite independently observed the same pattern. A host kernel message recorded PID-controller rejection. A similar runtime behavior is discussed in [gVisor issue 2490](https://github.com/google/gvisor/issues/2490); that report alone does not establish the cause of this VM's outcome. Do not relabel a generic sandbox error as a PID-limit outcome without independent evidence.

## Public playground observations

After the suite, seven presets were called sequentially over verified HTTPS on the same VM. All returned a `destroyed_at` timestamp and no cleanup error; labelled-container and workspace checks were empty after each, and final health reported `sandbox_leaked=0`.

| Preset | Observed outcome | What the output supports |
|---|---|---|
| Fake AWS-key exfiltration | `passed`, exit 0 | Fake-canary read and DNS lookup telemetry; program printed `blocked: URLError`. |
| Infinite loop | `timeout_killed` | Wall-clock termination after approximately 20.3 seconds. |
| Memory bomb | `oom_killed`, exit 137 | This particular run was reported OOM-killed. |
| Fork bomb | `error`, exit 2 | Runtime terminated; a PID-controller kernel rejection was logged at the same time. Separate bounded probe provides direct counter evidence. |
| Fill disk | `error`, exit 1 | `OSError: [Errno 27] File too large`; the file-size ulimit applied to this write. |
| Read host secrets | `passed`, exit 0 | Container `/etc/shadow` returned `PermissionError`; Docker socket path was absent; `/proc/1/environ` returned 330 bytes inside the container, not printed. This is not an exhaustive secret scan. |
| Remove root | `passed`, exit 0 | The preset ignores deletion errors; its exit status alone does **not** prove root files were protected. Inspection showed a read-only root filesystem and telemetry reported attempted deletion outside `/work`. |

Raw sanitized responses, per-preset cleanup snapshots, integration output and cgroup samples are retained in the project working directory under `../tickets/T9-artifacts/`, outside this repository. The public table above is a summary of those specific artifacts; the external evidence folder is not included in a GitHub clone.
