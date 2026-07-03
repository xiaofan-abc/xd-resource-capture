from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Any

from .task_manager import TaskManager, TaskRecord


SAVED_LINE_RE = re.compile(r"^\s*(?:\[保存\]|\[淇濆瓨\])\s*(.+?)\s*$")


def truthy(value: Any) -> bool:
    return bool(value)



def build_login_args(root_dir: Path, payload: dict[str, Any]) -> list[str]:
    args = [
        sys.executable,
        "-m",
        "web_app.login_runner",
        "--login-url",
        payload["login_url"],
        "--profile",
        payload.get("profile") or ".xidian-profile",
        "--channel",
        payload.get("channel") or "auto",
    ]
    if payload.get("username"):
        args.extend(["--username", payload["username"]])
    if payload.get("password"):
        args.extend(["--password", payload["password"]])
    if payload.get("headless"):
        args.append("--headless")
    return args


async def run_process_task(
    manager: TaskManager,
    record: TaskRecord,
    args: list[str],
    *,
    redact: list[str] | None = None,
) -> None:
    redact = [value for value in (redact or []) if value]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    await manager.set_status(record, "running")
    await manager.emit(record, "log", {"level": "info", "message": "任务已启动。"})

    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(manager.root_dir),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    record.process = process

    async def read_stream(stream: asyncio.StreamReader | None, level: str) -> None:
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            message = line.decode("utf-8", errors="replace").rstrip()
            for secret in redact:
                message = message.replace(secret, "***")
            if message:
                await manager.emit(record, "log", {"level": level, "message": message})
                match = SAVED_LINE_RE.match(message)
                if match:
                    saved_path = match.group(1)
                    record.saved_files.append(saved_path)
                    await manager.emit(record, "saved", {"path": saved_path})

    await asyncio.gather(read_stream(process.stdout, "info"), read_stream(process.stderr, "error"))
    exit_code = await process.wait()
    status = "cancelled" if exit_code < 0 else "done" if exit_code == 0 else "failed"
    await manager.set_status(record, status, exit_code=exit_code)
    await manager.emit(record, "log", {"level": "info", "message": f"任务结束，退出码：{exit_code}"})
    await manager.close_stream(record)
