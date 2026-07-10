from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .crawler_runner import build_login_args, run_process_task
from .ui_state_cache import UiStateCache
from .task_manager import TaskManager
from .xidian_service import (
    AuthStateError,
    XIDIAN_LOGIN_URL,
    check_auth_status,
    clear_profile_login_state,
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
DEFAULT_PROFILE = ".xidian-profile"
PROFILE_COOKIE_NAME = "xidian_profile"
BACKEND_STDOUT_LOG = ROOT_DIR / "web_app" / "backend.stdout.log"
BACKEND_STDERR_LOG = ROOT_DIR / "web_app" / "backend.stderr.log"
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8000
SERVER_APP = "web_app.app:app"
SERVER_STARTED_AT = datetime.now().isoformat(timespec="seconds")

app = FastAPI(title="Find PDF Local Web")
manager = TaskManager(root_dir=ROOT_DIR)
ui_state_cache = UiStateCache(cache_path=ROOT_DIR / ".ui-state-cache.json")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def detached_creation_flags() -> int:
    if os.name != "nt":
        return 0
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    flags |= getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    return flags


def powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def active_tasks() -> list[dict[str, Any]]:
    return [
        {
            "id": task.id,
            "kind": task.kind,
            "status": task.status,
        }
        for task in manager.tasks.values()
        if task.status in {"queued", "running"}
    ]


def system_status() -> dict[str, Any]:
    running = active_tasks()
    return {
        "pid": os.getpid(),
        "started_at": SERVER_STARTED_AT,
        "host": SERVER_HOST,
        "port": SERVER_PORT,
        "active_tasks": running,
        "active_task_count": len(running),
        "cache": ui_state_cache.summary(),
    }


def spawn_restart_helper() -> int:
    command = [
        sys.executable,
        "-m",
        "web_app.restart_runner",
        "--root-dir",
        str(ROOT_DIR),
        "--parent-pid",
        str(os.getpid()),
        "--host",
        SERVER_HOST,
        "--port",
        str(SERVER_PORT),
        "--app",
        SERVER_APP,
        "--stdout-log",
        str(BACKEND_STDOUT_LOG),
        "--stderr-log",
        str(BACKEND_STDERR_LOG),
    ]
    if os.name == "nt":
        argument_list = subprocess.list2cmdline(command[1:])
        script = (
            f"$process = Start-Process -FilePath {powershell_quote(command[0])} "
            f"-ArgumentList {powershell_quote(argument_list)} "
            f"-WorkingDirectory {powershell_quote(str(ROOT_DIR))} "
            "-WindowStyle Hidden -PassThru; "
            "Write-Output $process.Id"
        )
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            check=True,
        )
        return int((result.stdout or "0").strip() or "0")

    helper = subprocess.Popen(
        command,
        cwd=str(ROOT_DIR),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=detached_creation_flags(),
        close_fds=True,
    )
    return helper.pid


async def shutdown_after_restart(delay: float = 0.8) -> None:
    await asyncio.sleep(delay)
    os._exit(0)


def raise_api_error(exc: Exception) -> None:
    if isinstance(exc, AuthStateError):
        raise HTTPException(status_code=401, detail=exc.to_response()) from exc
    raise HTTPException(status_code=400, detail={"code": "request_failed", "message": str(exc)}) from exc


def cache_error_state(profile: str, exc: Exception) -> None:
    if isinstance(exc, AuthStateError):
        ui_state_cache.set_auth(profile, exc.to_response())


def render_bootstrap_page(filename: str, bootstrap_payload: dict[str, Any]) -> HTMLResponse:
    html = (STATIC_DIR / filename).read_text(encoding="utf-8")
    script = f"<script>window.__XIDIAN_BOOTSTRAP__={json.dumps(bootstrap_payload, ensure_ascii=False)};</script>"
    if "</head>" in html:
        html = html.replace("</head>", f"{script}\n  </head>", 1)
    else:
        html = f"{script}\n{html}"
    return HTMLResponse(html)


def profile_from_request(request: Request) -> str:
    profile = request.cookies.get(PROFILE_COOKIE_NAME, "").strip()
    return profile or DEFAULT_PROFILE



class LoginRequest(BaseModel):
    login_url: str = XIDIAN_LOGIN_URL
    profile: str = ".xidian-profile"
    channel: str = "auto"
    username: str = ""
    password: str = ""
    headless: bool = False


class AuthStatusRequest(BaseModel):
    profile: str = ".xidian-profile"
    channel: str = "auto"


class LogoutRequest(BaseModel):
    profile: str = ".xidian-profile"


class RestartRequest(BaseModel):
    force: bool = False


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
async def index(request: Request) -> HTMLResponse:
    return render_bootstrap_page("index.html", ui_state_cache.home_bootstrap(profile_from_request(request)))


@app.get("/resources")
async def resources_page(request: Request) -> HTMLResponse:
    return render_bootstrap_page("resources.html", ui_state_cache.resource_bootstrap(profile_from_request(request)))


@app.get("/replay")
async def replay_page(request: Request) -> HTMLResponse:
    return render_bootstrap_page("replay.html", ui_state_cache.replay_bootstrap(profile_from_request(request)))


@app.get("/sync-guide")
async def sync_guide_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "sync-guide.html")


@app.get("/api/tasks")
async def list_tasks() -> list[dict[str, Any]]:
    return manager.list_public()


@app.get("/api/system/status")
async def get_system_status() -> dict[str, Any]:
    return system_status()


@app.post("/api/system/restart")
async def restart_backend(request: RestartRequest) -> dict[str, Any]:
    running = active_tasks()
    if running and not request.force:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "active_tasks_running",
                "message": "褰撳墠杩樻湁杩愯涓殑浠诲姟锛岃鍏堝彇娑堟垨绛夊緟瀹屾垚鍚庡啀閲嶅惎鍚庣銆?",
                "tasks": running,
            },
        )

    ui_state_cache.flush()
    helper_pid = spawn_restart_helper()
    asyncio.create_task(shutdown_after_restart())
    return {
        "status": "restarting",
        "message": "鍚庣姝ｅ湪閲嶅惎锛岀紦瀛樺凡淇濆瓨銆?",
        "helper_pid": helper_pid,
        **system_status(),
    }


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
        cache_error_state(request.profile, exc)
        raise_api_error(exc)
    payload = {
        "terms": data.get("terms", []),
        "selected": next((term for term in data.get("terms", []) if term.get("selected")), None),
    }
    ui_state_cache.set_authenticated(request.profile)
    ui_state_cache.set_xidian_terms(request.profile, payload)
    return payload


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
        cache_error_state(request.profile, exc)
        raise_api_error(exc)
    ui_state_cache.set_authenticated(request.profile)
    ui_state_cache.set_xidian_terms(
        request.profile,
        {
            "terms": data.get("terms", []),
            "selected": next((term for term in data.get("terms", []) if term.get("selected")), None),
        },
    )
    ui_state_cache.set_xidian_courses(request.profile, data)
    return data


@app.post("/api/xidian/chapters")
async def xidian_chapters(request: XidianChaptersRequest) -> dict[str, Any]:
    try:
        payload = await get_course_chapters(
            ROOT_DIR,
            course_url=request.course_url,
            profile=request.profile,
            channel=request.channel,
        )
    except Exception as exc:
        cache_error_state(request.profile, exc)
        raise_api_error(exc)
    ui_state_cache.set_authenticated(request.profile)
    return payload


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
        payload = await get_replay_schedule(
            ROOT_DIR,
            profile=request.profile,
            channel=request.channel,
            semester_id=request.semester_id,
            week=request.week,
        )
    except Exception as exc:
        cache_error_state(request.profile, exc)
        raise_api_error(exc)
    ui_state_cache.set_authenticated(request.profile)
    return payload


@app.post("/api/replay/courses")
async def replay_courses(request: ReplayCoursesRequest) -> dict[str, Any]:
    try:
        payload = await get_replay_courses(
            ROOT_DIR,
            profile=request.profile,
            channel=request.channel,
            semester_id=request.semester_id,
        )
    except Exception as exc:
        cache_error_state(request.profile, exc)
        raise_api_error(exc)
    ui_state_cache.set_authenticated(request.profile)
    ui_state_cache.set_replay_courses(request.profile, payload)
    return payload


@app.post("/api/replay/sessions")
async def replay_sessions(request: ReplaySessionsRequest) -> dict[str, Any]:
    try:
        payload = await get_replay_course_sessions(
            ROOT_DIR,
            live_id=request.live_id,
            profile=request.profile,
            channel=request.channel,
        )
    except Exception as exc:
        cache_error_state(request.profile, exc)
        raise_api_error(exc)
    ui_state_cache.set_authenticated(request.profile)
    return payload


@app.post("/api/replay/videos")
async def replay_videos(request: ReplayVideosRequest) -> dict[str, Any]:
    try:
        payload = await get_replay_video_resources(
            ROOT_DIR,
            live_ids=request.live_ids,
            profile=request.profile,
            channel=request.channel,
        )
    except Exception as exc:
        cache_error_state(request.profile, exc)
        raise_api_error(exc)
    ui_state_cache.set_authenticated(request.profile)
    return payload


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
    payload = await check_auth_status(ROOT_DIR, profile=request.profile, channel=request.channel)
    ui_state_cache.set_auth(request.profile, payload)
    return payload


@app.post("/api/auth/logout")
async def auth_logout(request: LogoutRequest) -> dict[str, Any]:
    blocking_tasks = [
        {
            "id": task.id,
            "kind": task.kind,
            "status": task.status,
        }
        for task in manager.tasks.values()
        if task.status in {"queued", "running"}
        and task.kind != "login"
        and str(task.request.get("profile") or DEFAULT_PROFILE) == request.profile
    ]
    if blocking_tasks:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "active_tasks_running",
                "message": "当前 profile 还有运行中的任务，请先等待完成或取消后再退出登录。",
                "tasks": blocking_tasks,
            },
        )

    try:
        payload = await asyncio.to_thread(clear_profile_login_state, ROOT_DIR, request.profile)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_profile", "message": str(exc)}) from exc

    ui_state_cache.clear_profile(request.profile)
    return {
        "status": "logged_out",
        "message": "当前 profile 的登录态已清除。",
        **payload,
    }


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
