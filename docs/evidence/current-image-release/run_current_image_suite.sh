#!/usr/bin/env bash
# T17-fix1 proposed operator script. DO NOT RUN until independent review passes.
set -Eeuo pipefail
IMAGE='sha256:e57243f7dac1425ad9fe0c844e03f74a0f5f15b253e177e0ccd47fcb10850697'
BASE=/tmp/pod-t17-fix1
APP=/srv/pod/app
pod_stopped=0
suite_started=0
[ "$(id -u)" = 0 ]
[ "$(docker image inspect pod-sandbox:latest --format '{{.Id}}')" = "$IMAGE" ]
[ "$(docker image inspect "$IMAGE" --format '{{.Config.User}}')" = '1001:1001' ]
[ "$(id -u pod):$(id -g pod)" = '1001:1001' ]
printf '%s  %s\n' \
 '5e81025a9b592b9b172df79bc6ffa760add25c32ca8b28932616502e59a5cbbd' "$APP/backend/tests/test_sandbox_docker.py" \
 'e293de418cc238551767db686c437146e36a289e4621ed9aeb283f22ce35d51e' "$APP/backend/tests/sandbox_pid_evidence.py" \
 '71cf5ed096a93a83e161635df08f9666cfc7d7c3080edddb5fb5788e668d3cf4' "$BASE/current_image_plugin.py" | sha256sum -c -
[ "$(systemctl is-active pod)" = active ] && [ "$(systemctl is-active caddy)" = active ]
[ ! -e "$BASE/results" ] && [ ! -e "$BASE/post-suite-inventory.json" ]
# Unexpected residue or failed inspection leaves pod and ingress stopped. Never
# let pod's startup reaper erase a failed-cleanup observation before inspection.
restore() {
  rc=$?
  trap - EXIT
  echo "T17_SUITE_OR_GATE_EXIT $rc"
  if [ "$pod_stopped" = 1 ]; then
    if ! T17_SUITE_STARTED="$suite_started" python3 - "$BASE" "$rc" <<'PY'
import json, os, re, subprocess, sys
from pathlib import Path
base, rc = Path(sys.argv[1]), int(sys.argv[2])
output = base / 'post-suite-inventory.json'
try:
    def docker(*args):
        return subprocess.check_output(['docker', *args], text=True).splitlines()
    all_ids = set(docker('ps', '-aq', '--no-trunc'))
    labelled = docker('ps', '-aq', '--no-trunc', '--filter', 'label=pod.sandbox=1')
    tagged = docker('ps', '-aq', '--no-trunc', '--filter', 'label=pod.t17.fix1=1')
    log = base / 'results' / 'created-ids.jsonl'
    started = os.environ['T17_SUITE_STARTED'] == '1'
    if started and not log.is_file():
        raise RuntimeError('created-ID inventory missing')
    rows = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    ids = [row['id'] for row in rows]
    if any(not re.fullmatch(r'[0-9a-f]{64}', item) for item in ids):
        raise RuntimeError('invalid created-ID inventory')
    remaining = sorted(set(ids) & all_ids)
    work = sorted(p.name for p in Path('/srv/pod/work').iterdir())
    ps = subprocess.run(['ps', '-u', 'pod', '-o', 'args='], capture_output=True, text=True)
    if ps.returncode != 0 and not (ps.returncode == 1 and not ps.stdout.strip() and not ps.stderr.strip()):
        raise RuntimeError('process inventory unavailable')
    workers = [p for p in ps.stdout.splitlines() if '/backend/tests/test_sandbox_docker.py' in p]
    result = {'suite_or_gate_exit': rc, 'suite_started': started, 'recorded_ids': rows,
              'remaining_recorded_ids': remaining, 'labelled_sandboxes': labelled,
              't17_tagged_containers': tagged, 'workroot_entries': work,
              'remaining_suite_processes': len(workers)}
    clean = not (remaining or labelled or tagged or work or workers)
    result['clean_before_restart'] = clean
    output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print('T17_PRE_RESTART_INVENTORY ' + json.dumps(result, sort_keys=True), flush=True)
    if not clean:
        sys.exit(1)
except Exception as exc:
    print('T17_INVENTORY_ERROR ' + type(exc).__name__, file=sys.stderr, flush=True)
    sys.exit(1)
PY
    then
      echo 'T17_FAIL_CLOSED: inventory nonempty/unavailable; pod and ingress remain stopped; navigator disposition required' >&2
      exit 1
    fi
  fi
  if ! systemctl start pod || ! systemctl is-active --quiet pod; then
    echo 'T17_FAIL_CLOSED: pod restoration failed; ingress remains stopped' >&2
    exit 1
  fi
  if ! systemctl start caddy || ! systemctl is-active --quiet caddy; then
    echo 'T17_RESTORE_ERROR: ingress restoration failed' >&2
    exit 1
  fi
  echo "T17_RESTORED: clean inventory confirmed before pod restart; suite/gate exit $rc"
  exit "$rc"
}
trap restore EXIT
systemctl stop caddy
python3 - <<'PY'
import sqlite3, subprocess
c = sqlite3.connect('file:/srv/pod/data/pod.sqlite3?mode=ro', uri=True)
runs = c.execute('select id,status from runs').fetchall()
containers = subprocess.check_output(['docker','ps','-aq','--filter','label=pod.sandbox=1'], text=True).splitlines()
tagged = subprocess.check_output(['docker','ps','-aq','--filter','label=pod.t17.fix1=1'], text=True).splitlines()
print('IDLE_GATE', runs, 'sandbox_containers', len(containers), 't17_tagged', len(tagged), flush=True)
assert all(status in ('done','failed','awaiting_approval') for _, status in runs)
assert not containers and not tagged
PY
[ -z "$(find /srv/pod/work -mindepth 1 -maxdepth 1 -print -quit)" ]
systemctl stop pod
pod_stopped=1
[ -z "$(docker ps -aq --filter label=pod.sandbox=1)" ]
[ "$(docker image inspect pod-sandbox:latest --format '{{.Id}}')" = "$IMAGE" ]
# Do not overwrite evidence from a prior invocation.
mkdir "$BASE/results"
: > "$BASE/results/created-ids.jsonl"
chown -R pod:pod "$BASE/results"
cd "$APP"
suite_started=1
# One bounded invocation. The suite's runc bridge-network positive control is
# intentionally separate from actual runner/runsc target-snippet executions.
timeout --kill-after=10s 240s sudo -u pod env \
 POD_DOCKER_TESTS=1 POD_WORK_ROOT=/srv/pod/work \
 PYTHONPATH="$BASE:$APP/backend/tests" T17_OBSERVATIONS_DIR="$BASE/results" \
 "$APP/.venv/bin/python" -m pytest -q -s -x -p current_image_plugin \
 "$APP/backend/tests/test_sandbox_docker.py"
