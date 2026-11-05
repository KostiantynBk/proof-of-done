"""Headless run, pin, export and receipt verification commands."""
import argparse
import asyncio
import json
import uuid
from pathlib import Path

from docker.errors import DockerException

from pod.config import Settings
from pod.llm import VultrLLM
from pod.pipeline.broadcast import Broadcaster, log_caught, pod_logger
from pod.pipeline.export import digest
from pod.pipeline.run import execute_run
from pod.pipeline.summary import EXPORTS, build_summary, eligible_recording
from pod.sandbox.runner import DockerSandboxRunner
from pod.store import Store


def main():
    pod_logger()
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run')
    run.add_argument('project')
    run.add_argument('--fake-llm', action='store_true', help='Use the simulated demo model (requires no real key)')
    pin = sub.add_parser('pin')
    pin.add_argument('run_id')
    exp = sub.add_parser('export')
    exp.add_argument('run_id')
    exp.add_argument('directory')
    verify = sub.add_parser('verify')
    verify.add_argument('run_id')
    args = parser.parse_args()
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(settings.data_dir / 'pod.sqlite3')
    try:
        if args.command == 'run':
            if args.fake_llm and settings.inference_api_key:
                parser.error('--fake-llm is forbidden when VULTR_INFERENCE_API_KEY is configured')
            if args.fake_llm:
                from pod.demo_script import demo_llm
                llm = demo_llm()
            else:
                llm = VultrLLM(settings)
            run_id = uuid.uuid4().hex
            store.create_run(run_id, args.project, status='queued')
            try:
                sandbox = DockerSandboxRunner(settings)
                asyncio.run(sandbox.reap_orphans())
                if sandbox.leaked_count():
                    raise RuntimeError('sandbox cleanup pending')
            except (DockerException, OSError, RuntimeError) as exc:
                log_caught(None, 'cli:orphan_reaping', None, exc)
                sandbox = None
            asyncio.run(execute_run(run_id, args.project, settings=settings, llm=llm,
                                    sandbox=sandbox, store=store, broadcaster=Broadcaster()))
            print(json.dumps(build_summary(store, run_id), indent=2))
        elif not store.get_run(args.run_id):
            parser.error('run not found')
        elif args.command == 'pin':
            if not eligible_recording(store, args.run_id):
                parser.error('run is not a complete verified recording')
            (settings.data_dir / 'recorded-run-id').write_text(args.run_id, encoding='utf-8')
        elif args.command == 'export':
            out = Path(args.directory)
            out.mkdir(parents=True, exist_ok=True)
            for name in EXPORTS:
                artifact = store.get_artifact(args.run_id, name)
                if artifact:
                    content = artifact['content']
                    (out / name).write_bytes(content.encode('utf-8') if isinstance(content, str) else content)
        else:
            artifact = store.get_artifact(args.run_id, 'receipts.json')
            if not artifact:
                raise SystemExit('receipts not found')
            manifest = json.loads(artifact['content'])
            expected = manifest.pop('manifest_sha256', None)
            ok = store.verify_chain(args.run_id) and expected == digest(manifest)
            print('verified' if ok else 'verification failed')
            if not ok:
                raise SystemExit(1)
    finally:
        store.close()


if __name__ == '__main__':
    main()
