from __future__ import annotations

import argparse
import contextlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Wait for the old backend to exit, then start a new uvicorn process.")
    parser.add_argument("--root-dir", required=True)
    parser.add_argument("--parent-pid", required=True, type=int)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default="8000")
    parser.add_argument("--app", default="web_app.app:app")
    parser.add_argument("--stdout-log", default="")
    parser.add_argument("--stderr-log", default="")
    parser.add_argument("--wait-timeout", type=float, default=45.0)
    return parser.parse_args()


def pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        output = (result.stdout or "").strip()
        return bool(output) and not output.startswith("INFO:")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def wait_for_parent_exit(parent_pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_exists(parent_pid):
            return True
        time.sleep(0.25)
    return not pid_exists(parent_pid)


def port_is_listening(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def wait_for_port_release(host: str, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not port_is_listening(host, port):
            return True
        time.sleep(0.25)
    return not port_is_listening(host, port)


def open_log(path: str):
    if not path:
        return contextlib.nullcontext(subprocess.DEVNULL)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target.open("ab")


def creation_flags() -> int:
    if os.name != "nt":
        return 0
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    flags |= getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    return flags


def main() -> int:
    args = parse_args()
    root_dir = Path(args.root_dir).resolve()
    port = int(args.port)

    if not wait_for_parent_exit(args.parent_pid, args.wait_timeout):
        return 1

    wait_for_port_release(args.host, port, min(15.0, args.wait_timeout))
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        args.app,
        "--host",
        args.host,
        "--port",
        str(port),
    ]

    with open_log(args.stdout_log) as stdout_handle, open_log(args.stderr_log) as stderr_handle:
        subprocess.Popen(
            command,
            cwd=str(root_dir),
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            creationflags=creation_flags(),
            close_fds=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
