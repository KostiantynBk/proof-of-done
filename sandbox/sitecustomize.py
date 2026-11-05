"""Best-effort, evadable attempt telemetry; gVisor provides isolation.

Audit events occur before operations complete: none proves a successful read,
write, deletion, or network connection. Paths are normalized lexically; symlinks
and racing filesystem changes can still make attribution inaccurate.
"""
import json
import os
import sys
import threading
from datetime import UTC, datetime

_state = threading.local()
_count = 0


def _path(value, dir_fd=None):
    if not isinstance(value, (str, bytes, os.PathLike)):
        return None
    path = os.fsdecode(value)
    if not os.path.isabs(path) and dir_fd not in (None, -1):
        try:
            base = os.readlink(f"/proc/self/fd/{dir_fd}")
            if not os.path.isabs(base):
                return None
            path = os.path.join(base, path)
        except (OSError, ValueError):
            return None
    return os.path.abspath(path)


def _access(mode, flags):
    if isinstance(flags, int):
        access = flags & os.O_ACCMODE
        read = access in (os.O_RDONLY, os.O_RDWR)
        write = access in (os.O_WRONLY, os.O_RDWR)
        write = write or bool(flags & (os.O_CREAT | os.O_TRUNC | os.O_APPEND))
        return read, write
    if isinstance(mode, str):
        return "r" in mode or "+" in mode, any(c in mode for c in "wax+")
    return False, False


def audit(event, args):
    global _count
    if getattr(_state, "busy", False) or _count >= 500:
        return
    try:
        _state.busy = True
        records = []
        if event == "socket.connect":
            records.append(("net_connect", str(args[1:])))
        elif event == "socket.getaddrinfo":
            records.append(("dns_lookup", str(args[:2])))
        elif event == "open" and args:
            path = _path(args[0])
            if path:
                read, write = _access(args[1] if len(args) > 1 else None,
                                      args[2] if len(args) > 2 else None)
                honey = path in ("/home/runner/.aws/credentials", "/home/runner/.ssh/id_rsa", "/home/runner/.netrc")
                sensitive = (path in ("/etc/passwd", "/etc/shadow", "/var/run/docker.sock")
                             or path.startswith("/root/") or (path.startswith("/proc/") and path.endswith("/environ")))
                if honey or sensitive:
                    prefix = "honeytoken" if honey else "sensitive"
                    if read:
                        records.append((prefix + "_read", path))
                    if write:
                        records.append((prefix + "_write", path))
        elif event in ("subprocess.Popen", "os.system", "os.posix_spawn") or event.startswith("os.exec"):
            records.append(("process_spawn", event))
        elif event == "os.fork":
            records.append(("fork", event))
        elif event in ("shutil.rmtree", "os.remove") and args:
            path = _path(args[0], args[1] if len(args) > 1 else None)
            if path is None:
                records.append(("delete_path_unresolved", str(args[0])))
            elif path not in ("/work", "/tmp") and not path.startswith(("/work/", "/tmp/")):
                records.append(("delete_outside_work", path))
        for kind, detail in records:
            if _count >= 500:
                break
            record = {"ts": datetime.now(UTC).isoformat(), "type": kind, "detail": detail[:1024]}
            with open("/telemetry/events.jsonl", "a", encoding="utf-8") as stream:
                stream.write(json.dumps(record) + "\n")
            _count += 1
    except BaseException:  # noqa: BLE001, S110 -- telemetry must never block target operations
        pass
    finally:
        _state.busy = False


sys.addaudithook(audit)
