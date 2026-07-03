from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .crawler_runner import build_login_args, run_process_task
from .task_manager import TaskManager
from .xidian_service import (
    XIDIAN_LOGIN_URL,
    check_auth_status,
    get_course_chapters,
    get_replay_courses,
    get_replay_course_sessions,
    get_replay_schedule,
    get_replay_video_resources,
    get_terms_and_courses,
    run_chapter_download_task,
    run_extract_links_task,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Find PDF Local Web")
manager = TaskManager(root_dir=ROOT_DIR)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")



class LoginRequest(BaseModel):
    login_url: str = XIDIAN_LOGIN_URL
    profile: str = ".browser-profile"
    channel: str = "auto"
    username: str = ""
    password: str = ""
    headless: bool = False


class AuthStatusRequest(BaseModel):
    profile: str = ".xidian-profile"
    channel: str = "auto"


class XidianBrowseRequest(BaseModel):
    profile: str = ".xidian-profile"
    channel: str = "auto"
    term: str | None = None
    term_label: str | None = None


class XidianChaptersRequest(BaseModel):
    course_url: str
    profile: str = ".xidian-profile"
    channel: str = "auto"


class XidianExtractLinksRequest(BaseModel):
    chapters: list[dict[str, Any]]
    mode: str = Field(default="all", pattern="^(pdf|video|all)$")
    profile: str = ".xidian-profile"
    channel: str = "auto"
    metadata_concurrency: int = 24


class XidianDownloadRequest(BaseModel):
    resources: list[dict[str, Any]]
    out: str = "downloaded_xidian"
    profile: str = ".xidian-profile"
    channel: str = "auto"
    concurrency: int = Field(default=4, ge=1, le=16)
    stop_on_error: bool = False


class ReplayScheduleRequest(BaseModel):
    profile: str = ".xidian-profile"
    channel: str = "auto"
    semester_id: str | None = None
    week: str | None = None


class ReplayCoursesRequest(BaseModel):
    profile: str = ".xidian-profile"
    channel: str = "auto"
    semester_id: str | None = None


class ReplaySessionsRequest(BaseModel):
    live_id: str
    profile: str = ".xidian-profile"
    channel: str = "auto"


class ReplayVideosRequest(BaseModel):
    live_ids: list[str]
    profile: str = ".xidian-profile"
    channel: str = "auto"


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/replay")
async def replay_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "replay.html")


@app.get("/sync-guide")
async def sync_guide_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "sync-guide.html")


@app.get("/api/tasks")
async def list_tasks() -> list[dict[str, Any]]:
    return manager.list_public()


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    record = manager.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return record.public_dict()



@app.post("/api/xidian/terms")
async def xidian_terms(request: XidianBrowseRequest) -> dict[str, Any]:
    try:
        data = await get_terms_and_courses(
            ROOT_DIR,
            profile=request.profile,
            channel=request.channel,
            term=None,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"terms": data.get("terms", []), "selected": next((term for term in data.get("terms", []) if term.get("selected")), None)}


@app.post("/api/xidian/courses")
async def xidian_courses(request: XidianBrowseRequest) -> dict[str, Any]:
    try:
        data = await get_terms_and_courses(
            ROOT_DIR,
            profile=request.profile,
            channel=request.channel,
            term=request.term,
            term_label=request.term_label,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return data


@app.post("/api/xidian/chapters")
async def xidian_chapters(request: XidianChaptersRequest) -> dict[str, Any]:
    try:
        return await get_course_chapters(
            ROOT_DIR,
            course_url=request.course_url,
            profile=request.profile,
            channel=request.channel,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/xidian/extract_links")
async def xidian_extract_links(request: XidianExtractLinksRequest) -> dict[str, Any]:
    payload = request.model_dump()
    record = await manager.create_task("xidian-extract", payload)
    record.worker = asyncio.create_task(run_extract_links_task(manager, record, payload))
    return record.public_dict()


@app.post("/api/xidian/download")
async def xidian_download(request: XidianDownloadRequest) -> dict[str, Any]:
    payload = request.model_dump()
    record = await manager.create_task("xidian-download", payload)
    record.worker = asyncio.create_task(run_chapter_download_task(manager, record, payload))
    return record.public_dict()


@app.post("/api/replay/schedule")
async def replay_schedule(request: ReplayScheduleRequest) -> dict[str, Any]:
    try:
        return await get_replay_schedule(
            ROOT_DIR,
            profile=request.profile,
            channel=request.channel,
            semester_id=request.semester_id,
            week=request.week,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/replay/courses")
async def replay_courses(request: ReplayCoursesRequest) -> dict[str, Any]:
    try:
        return await get_replay_courses(
            ROOT_DIR,
            profile=request.profile,
            channel=request.channel,
            semester_id=request.semester_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/replay/sessions")
async def replay_sessions(request: ReplaySessionsRequest) -> dict[str, Any]:
    try:
        return await get_replay_course_sessions(
            ROOT_DIR,
            live_id=request.live_id,
            profile=request.profile,
            channel=request.channel,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/replay/videos")
async def replay_videos(request: ReplayVideosRequest) -> dict[str, Any]:
    try:
        return await get_replay_video_resources(
            ROOT_DIR,
            live_ids=request.live_ids,
            profile=request.profile,
            channel=request.channel,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/auth/open-login")
async def open_login(request: LoginRequest) -> dict[str, Any]:
    payload = request.model_dump()
    payload["username"] = ""
    payload["password"] = ""
    record = await manager.create_task("login", {k: v for k, v in payload.items() if k != "password"})
    args = build_login_args(ROOT_DIR, payload)
    record.worker = asyncio.create_task(run_process_task(manager, record, args))
    return record.public_dict()


@app.post("/api/auth/status")
async def auth_status(request: AuthStatusRequest) -> dict[str, Any]:
    return await check_auth_status(ROOT_DIR, profile=request.profile, channel=request.channel)


@app.post("/api/auth/password-login")
async def password_login(request: LoginRequest) -> dict[str, Any]:
    payload = request.model_dump()
    public_payload = {k: v for k, v in payload.items() if k != "password"}
    public_payload["password"] = "***" if payload.get("password") else ""
    record = await manager.create_task("login", public_payload)
    args = build_login_args(ROOT_DIR, payload)
    record.worker = asyncio.create_task(
        run_process_task(manager, record, args, redact=[payload.get("username", ""), payload.get("password", "")])
    )
    return record.public_dict()


@app.get("/api/tasks/{task_id}/events")
async def task_events(task_id: str) -> StreamingResponse:
    record = manager.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return StreamingResponse(manager.sse_stream(record), media_type="text/event-stream")


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str) -> dict[str, Any]:
    record = manager.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found")
    processes = list(record.processes)
    if record.process and record.process not in processes:
        processes.append(record.process)
    for process in processes:
        if process.returncode is None:
            process.terminate()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=5)
            if process.returncode is None:
                process.kill()
    if record.worker and not record.worker.done() and record.worker is not asyncio.current_task():
        record.worker.cancel()
    await manager.set_status(record, "cancelled", exit_code=record.exit_code)
    await manager.close_stream(record)
    return record.public_dict()


@app.get("/api/files")
async def list_files(out: str = "downloaded_resources") -> list[dict[str, Any]]:
    base = (ROOT_DIR / out).resolve()
    if not str(base).startswith(str(ROOT_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid directory")
    if not base.exists():
        return []
    files = []
    for path in sorted(base.rglob("*"), key=lambda item: item.stat().st_mtime if item.is_file() else 0, reverse=True):
        if path.is_file():
            stat = path.stat()
            files.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                }
            )
    return files[:500]
