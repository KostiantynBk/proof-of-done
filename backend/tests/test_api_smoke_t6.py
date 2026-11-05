import os
import socket
import subprocess
import sys
import time

import httpx


def test_uvicorn_without_docker(tmp_path):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env.update(POD_FAKE_LLM='1', POD_SANDBOX_RUNTIME='runc', POD_DATA_DIR=str(tmp_path),
               DOCKER_HOST='tcp://127.0.0.1:1')
    env.pop('VULTR_INFERENCE_API_KEY', None)
    proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'pod.app:app', '--host', '127.0.0.1',
                             '--port', str(port)], env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    url = f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            try:
                health = httpx.get(url + '/api/health', timeout=.2)
                break
            except httpx.HTTPError:
                time.sleep(.1)
        else:
            raise AssertionError('uvicorn did not start')
        assert health.json()['sandbox_available'] is False
        response = httpx.post(url + '/api/runs', json={'project': 'demo:refundly'}, timeout=5)
        assert response.status_code == 200
        run_id = response.json()['run_id']
        for _ in range(100):
            summary = httpx.get(url + '/api/runs/' + run_id, timeout=2).json()
            if summary['status'] == 'failed':
                break
            time.sleep(.05)
        assert summary['status'] == 'failed'
        events = httpx.get(url + '/api/runs/' + run_id + '/events', timeout=2).text
        assert 'Sandbox unavailable' in events
    finally:
        proc.terminate()
        proc.wait(timeout=10)
