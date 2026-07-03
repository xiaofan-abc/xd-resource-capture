from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class TaskRecord:
    id: str
    kind: str
    status: str = "queued"
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    exit_code: int | None = None
    request: dict[str, Any] = field(default_factory=dict)
    saved_files: list[str] = field(default_factory=list)
    logs: list[dict[str, Any]] = field(default_factory=list)
    result: Any = None
    queue: asyncio.Queue[dict[str, Any] | None] = field(default_factory=asyncio.Queue)
    process: asyncio.subprocess.Process | None = None
    processes: list[asyncio.subprocess.Process] = field(default_factory=list)
    worker: asyncio.Task | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "exit_code": self.exit_code,
            "request": self.request,
            "saved_files": self.saved_files,
            "result": self.result,
            "log_count": len(self.logs),
        }


class TaskManager:
    def __init__(self, *, root_dir: Path, max_log_lines: int = 1000) -> None:
        self.root_dir = root_dir
        self.max_log_lines = max_log_lines
        self.tasks: dict[str, TaskRecord] = {}
        self._lock = asyncio.Lock()

    async def create_task(self, kind: str, request: dict[str, Any]) -> TaskRecord:
        task_id = uuid.uuid4().hex[:12]
        record = TaskRecord(id=task_id, kind=kind, request=request)
        async with self._lock:
            self.tasks[task_id] = record
        await self.emit(record, "status", {"status": record.status})
        return record

    def get(self, task_id: str) -> TaskRecord | None:
        return self.tasks.get(task_id)

    def list_public(self) -> list[dict[str, Any]]:
        return [task.public_dict() for task in sorted(self.tasks.values(), key=lambda item: item.created_at, reverse=True)]

    async def emit(self, record: TaskRecord, event: str, data: dict[str, Any]) -> None:
        payload = {
            "event": event,
            "data": data,
            "time": now_iso(),
            "task_id": record.id,
        }
        record.updated_at = payload["time"]
        if event == "log":
            record.logs.append(payload)
            if len(record.logs) > self.max_log_lines:
                del record.logs[: len(record.logs) - self.max_log_lines]
        await record.queue.put(payload)

    async def set_status(self, record: TaskRecord, status: str, *, exit_code: int | None = None) -> None:
        record.status = status
        record.exit_code = exit_code
        await self.emit(record, "status", {"status": status, "exit_code": exit_code})

    async def close_stream(self, record: TaskRecord) -> None:
        await record.queue.put(None)

    async def sse_stream(self, record: TaskRecord):
        for item in record.logs[-200:]:
            yield self.format_sse(item["event"], item)
        yield self.format_sse("snapshot", record.public_dict())
        while True:
            item = await record.queue.get()
            if item is None:
                yield self.format_sse("done", record.public_dict())
                break
            yield self.format_sse(item["event"], item)

    @staticmethod
    def format_sse(event: str, data: Any) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
