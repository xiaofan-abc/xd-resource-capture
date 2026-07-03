from __future__ import annotations

import asyncio
import contextlib
import ctypes
import json
import os
import re
import shutil
import signal
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from .task_manager import TaskManager, TaskRecord

COURSE_LIST_URL = "https://fycourse.fanya.chaoxing.com/courselist/study"
REPLAY_COURSE_URL = "https://newesxidian.chaoxing.com/frontLive/studentSelectCourse1"
REPLAY_HOST = "https://newesxidian.chaoxing.com"
REPLAY_VIEW_HOST = "http://newes.chaoxing.com"
MOOC1 = "https://mooc1.chaoxing.com"
MOOC2 = "https://mooc2-ans.chaoxing.com"
XIDIAN_LOGIN_URL = "https://ids.xidian.edu.cn/authserver/login?service=https://xdspoc.fanya.chaoxing.com/sso/xdspoc"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
STORAGE_STATE_FILENAME = "storage_state.json"
LOGIN_SESSION_FILENAME = "login_session.json"


class AuthStateError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail or {}

    def to_response(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "status": self.status,
            "message": self.message,
            **self.detail,
        }


def profile_dir(root_dir: Path, profile: str) -> Path:
    return (root_dir / profile).expanduser().resolve()


def ensure_profile_dir_within_root(root_dir: Path, profile: str) -> Path:
    root = root_dir.resolve()
    path = profile_dir(root_dir, profile)
    if path != root and not str(path).startswith(str(root) + os.sep):
        raise ValueError("Invalid profile path")
    return path


def storage_state_path(root_dir: Path, profile: str) -> Path:
    return profile_dir(root_dir, profile) / STORAGE_STATE_FILENAME


def login_session_path(root_dir: Path, profile: str) -> Path:
    return profile_dir(root_dir, profile) / LOGIN_SESSION_FILENAME


def iso_from_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")


def pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        except Exception:
            return False
        if process:
            ctypes.windll.kernel32.CloseHandle(process)
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, SystemError):
        return False
    except Exception:
        return False
    return True


def load_login_session(root_dir: Path, profile: str) -> dict[str, Any] | None:
    path = login_session_path(root_dir, profile)
    try:
        exists = path.exists()
    except OSError:
        return None
    if not exists:
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        with contextlib.suppress(OSError):
            path.unlink()
        return None

    pid = int(data.get("pid") or 0)
    if not pid_exists(pid):
        with contextlib.suppress(OSError):
            path.unlink()
        return None
    try:
        session_path = os.fspath(path)
    except OSError:
        with contextlib.suppress(OSError):
            path.unlink()
        return None
    return {"path": session_path, **data}


def terminate_login_session(root_dir: Path, profile: str) -> dict[str, Any] | None:
    session = load_login_session(root_dir, profile)
    if not session:
        return None

    pid = int(session.get("pid") or 0)
    if pid_exists(pid):
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)

    with contextlib.suppress(OSError):
        login_session_path(root_dir, profile).unlink()
    session["terminated"] = True
    return session


def clear_profile_login_state(root_dir: Path, profile: str) -> dict[str, Any]:
    target = ensure_profile_dir_within_root(root_dir, profile)
    existed = target.exists()
    session = terminate_login_session(root_dir, profile)

    removed = False
    if target.exists():
        for _ in range(10):
            try:
                shutil.rmtree(target)
                removed = True
                break
            except OSError:
                time.sleep(0.2)
        if not removed and not target.exists():
            removed = True

    return {
        "profile": profile,
        "profile_path": str(target),
        "profile_existed": existed,
        "profile_removed": removed or not target.exists(),
        "session": session,
        "storage_state": storage_state_metadata(root_dir, profile),
    }


def storage_state_metadata(root_dir: Path, profile: str) -> dict[str, Any]:
    path = storage_state_path(root_dir, profile)
    metadata: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
    }
    if path.exists():
        stat = path.stat()
        metadata["updated_at"] = iso_from_timestamp(stat.st_mtime)
        metadata["size"] = stat.st_size
    return metadata


def missing_auth_error(root_dir: Path, profile: str, message: str = "未找到登录态，请先登录。") -> AuthStateError:
    return AuthStateError(
        "missing_auth_state",
        message,
        status="missing",
        detail={"storage_state": storage_state_metadata(root_dir, profile)},
    )


def expired_auth_error(root_dir: Path, profile: str, message: str = "登录态已过期，请重新登录。") -> AuthStateError:
    return AuthStateError(
        "expired_auth_state",
        message,
        status="expired",
        detail={"storage_state": storage_state_metadata(root_dir, profile)},
    )


def page_soup(html_text: str) -> BeautifulSoup:
    return BeautifulSoup(html_text, "html.parser")


def looks_like_login_page(html_text: str, final_url: str, soup: BeautifulSoup | None = None) -> bool:
    soup = soup or page_soup(html_text)
    final_url_lower = final_url.lower()
    if "authserver/login" in final_url_lower:
        return True
    if "passport2.chaoxing.com/login" in final_url_lower:
        return True
    return bool(soup.select_one('input[name="username"], input[name="password"], input[type="password"]'))


def ensure_course_list_page(root_dir: Path, profile: str, html_text: str, final_url: str) -> BeautifulSoup:
    soup = page_soup(html_text)
    if soup.select_one("#yearList, .myde_course_item"):
        return soup
    if looks_like_login_page(html_text, final_url, soup):
        raise expired_auth_error(root_dir, profile)
    raise RuntimeError(f"未能识别课程列表页面，当前地址：{final_url}")


def ensure_replay_bootstrap(
    root_dir: Path,
    profile: str,
    html_text: str,
    final_url: str,
    bootstrap: dict[str, Any],
) -> dict[str, Any]:
    params = bootstrap.get("params") or {}
    has_bootstrap = bool(bootstrap.get("semesters")) or bool(bootstrap.get("weeks")) or any(
        str(value).strip() for value in params.values()
    )
    if has_bootstrap:
        return bootstrap
    if looks_like_login_page(html_text, final_url):
        raise expired_auth_error(root_dir, profile)
    raise RuntimeError(f"未能识别课程回放页面，当前地址：{final_url}")


def parse_json_or_raise(root_dir: Path, profile: str, text: str, final_url: str, label: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        if looks_like_login_page(text, final_url):
            raise expired_auth_error(root_dir, profile) from exc
        raise RuntimeError(f"未能解析{label}返回内容。") from exc


def load_storage_state(root_dir: Path, profile: str) -> tuple[dict[str, Any], Path]:
    path = storage_state_path(root_dir, profile)
    if not path.exists():
        raise missing_auth_error(root_dir, profile)

    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise missing_auth_error(root_dir, profile, "登录态文件损坏，请重新登录。") from exc

    if not isinstance(state, dict):
        raise missing_auth_error(root_dir, profile, "登录态文件损坏，请重新登录。")
    return state, path


def cookie_pairs_from_storage_state(state: dict[str, Any]) -> list[tuple[str, str]]:
    now = time.time()
    pairs: list[tuple[str, str]] = []
    for cookie in state.get("cookies") or []:
        if not isinstance(cookie, dict):
            continue
        name = str(cookie.get("name") or "").strip()
        value = cookie.get("value")
        if not name or value is None:
            continue
        expires = cookie.get("expires")
        if isinstance(expires, (int, float)) and expires > 0 and expires < now:
            continue
        pairs.append((name, str(value)))
    return pairs


def cookie_is_expired(cookie: dict[str, Any], *, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    expires = cookie.get("expires")
    return isinstance(expires, (int, float)) and expires > 0 and expires < now


def cookie_domain_matches(cookie_domain: str, hostname: str) -> bool:
    domain = cookie_domain.lstrip(".").lower()
    hostname = hostname.lower()
    return bool(domain) and (hostname == domain or hostname.endswith(f".{domain}"))


def cookie_path_matches(cookie_path: str, request_path: str) -> bool:
    cookie_path = cookie_path or "/"
    request_path = request_path or "/"
    if cookie_path == "/":
        return True
    if request_path == cookie_path:
        return True
    return request_path.startswith(cookie_path.rstrip("/") + "/")


def cookie_header_for_url(state: dict[str, Any], url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    hostname = (parsed.hostname or "").lower()
    request_path = parsed.path or "/"
    is_https = parsed.scheme.lower() == "https"
    now = time.time()
    matched: list[tuple[int, int, str, str]] = []

    for index, cookie in enumerate(state.get("cookies") or []):
        if not isinstance(cookie, dict):
            continue
        name = str(cookie.get("name") or "").strip()
        value = cookie.get("value")
        domain = str(cookie.get("domain") or "").strip()
        path = str(cookie.get("path") or "/")
        if not name or value is None or not domain:
            continue
        if cookie_is_expired(cookie, now=now):
            continue
        if cookie.get("secure") and not is_https:
            continue
        if not cookie_domain_matches(domain, hostname):
            continue
        if not cookie_path_matches(path, request_path):
            continue
        matched.append((len(path), index, name, str(value)))

    matched.sort(key=lambda item: (-item[0], item[1]))
    return "; ".join(f"{name}={value}" for _, _, name, value in matched)


async def get_auth_state(root_dir: Path, profile: str, channel: str) -> dict[str, Any]:
    del channel
    state, _ = load_storage_state(root_dir, profile)
    if not cookie_pairs_from_storage_state(state):
        raise expired_auth_error(root_dir, profile)
    return state


async def get_cookie_string(root_dir: Path, profile: str, channel: str, *, url: str = COURSE_LIST_URL) -> str:
    auth_state = await get_auth_state(root_dir, profile, channel)
    cookie_header = cookie_header_for_url(auth_state, url)
    if not cookie_header:
        raise expired_auth_error(root_dir, profile)
    return cookie_header


def build_auth_status_response(
    *,
    authenticated: bool,
    status: str,
    message: str,
    root_dir: Path,
    profile: str,
    session: dict[str, Any] | None,
    url: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "authenticated": authenticated,
        "status": status,
        "message": message,
        "storage_state": storage_state_metadata(root_dir, profile),
        "profile": {
            "status": "occupied" if session else "available",
            "message": "登录窗口仍在运行。" if session else "",
            "session": session,
        },
    }
    if url:
        payload["url"] = url
    return payload


async def check_auth_status(root_dir: Path, *, profile: str, channel: str = "auto") -> dict[str, Any]:
    session = load_login_session(root_dir, profile)
    try:
        auth_state = await get_auth_state(root_dir, profile, channel)
        course_html, course_url = await fetch_text(COURSE_LIST_URL, auth_state)
        ensure_course_list_page(root_dir, profile, course_html, course_url)
    except AuthStateError as exc:
        message = exc.message
        if exc.status == "missing" and session:
            message = "登录窗口仍在运行，但尚未导出可用登录态，请先完成登录。"
        return build_auth_status_response(
            authenticated=False,
            status=exc.status,
            message=message,
            root_dir=root_dir,
            profile=profile,
            session=session,
            url=locals().get("course_url"),
        )

    message = "已找到可用登录态。"
    if session:
        message = "profile 被占用，但已有可用 storage_state，可继续使用。"
    return build_auth_status_response(
        authenticated=True,
        status="authenticated",
        message=message,
        root_dir=root_dir,
        profile=profile,
        session=session,
        url=course_url,
    )


def build_request(url: str, auth_state: dict[str, Any], *, referer: str = COURSE_LIST_URL) -> urllib.request.Request:
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": referer,
    }
    cookie_header = cookie_header_for_url(auth_state, url)
    if cookie_header:
        headers["Cookie"] = cookie_header
    return urllib.request.Request(url, headers=headers)


def fetch_text_sync(url: str, auth_state: dict[str, Any], *, referer: str = COURSE_LIST_URL) -> tuple[str, str]:
    request = build_request(url, auth_state, referer=referer)
    with urllib.request.urlopen(request, timeout=25) as response:
        body = response.read().decode("utf-8", errors="replace")
        return body, response.geturl()


async def fetch_text(url: str, auth_state: dict[str, Any], *, referer: str = COURSE_LIST_URL) -> tuple[str, str]:
    return await asyncio.to_thread(fetch_text_sync, url, auth_state, referer=referer)


def replay_course_url(semester_id: str | None = None) -> str:
    if not semester_id:
        return REPLAY_COURSE_URL
    return f"{REPLAY_COURSE_URL}?{urllib.parse.urlencode({'semesterId': semester_id})}"


def script_value(html_text: str, key: str) -> str:
    patterns = [
        rf'"{re.escape(key)}"\s*:\s*[\'"]([^\'"]*)[\'"]',
        rf"{re.escape(key)}\s*:\s*[\'\"]([^\'\"]*)[\'\"]",
    ]
    for pattern in patterns:
        match = re.search(pattern, html_text)
        if match:
            return match.group(1)
    return ""


def ms_to_iso(value: Any) -> str:
    try:
        timestamp = int(value.get("time") if isinstance(value, dict) else value) / 1000
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
    except Exception:
        return ""


def parse_replay_bootstrap(html_text: str) -> dict[str, Any]:
    soup = page_soup(html_text)
    semesters = []
    for option in soup.select("#semesterList option"):
        value = (option.get("value") or "").strip()
        label = option.get_text(" ", strip=True)
        if value:
            semesters.append({"value": value, "label": label, "selected": option.has_attr("selected")})

    weeks = []
    for item in soup.select(".weekNum"):
        value = (item.get("value") or "").strip()
        label = item.get_text(" ", strip=True)
        if value:
            weeks.append({"value": value, "label": label})

    week = ""
    match = re.search(r"getWeekDetail\(['\"](\d+)['\"]\)", html_text)
    if match:
        week = match.group(1)
    if not week:
        week_text = soup.select_one(".w_pull_time")
        week_match = re.search(r"\d+", week_text.get_text(" ", strip=True) if week_text else "")
        week = week_match.group(0) if week_match else ""

    params = {
        "fid": script_value(html_text, "fid"),
        "userId": script_value(html_text, "userId"),
        "termYear": script_value(html_text, "termYear"),
        "termId": script_value(html_text, "termId"),
        "type": script_value(html_text, "type") or "1",
    }
    return {
        "semesters": semesters,
        "weeks": weeks,
        "selected_semester": next((item for item in semesters if item.get("selected")), None),
        "selected_week": week,
        "params": params,
    }


def normalize_replay_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "course_id": item.get("courseId"),
        "course_code": item.get("courseCode", ""),
        "course_name": item.get("courseName", ""),
        "clazz_id": item.get("teachClazzId") or item.get("clazzId"),
        "clazz_name": item.get("teachClazzName") or item.get("clazzName", ""),
        "teacher": item.get("teacherRealName") or item.get("teacherName") or "",
        "place": item.get("place") or item.get("schoolRoomName") or item.get("schoolRoomCode") or "",
        "week_day": item.get("weekDay"),
        "jie": str(item.get("jie", "")),
        "section": item.get("section", ""),
        "status": item.get("status"),
        "status_label": {0: "未开始", 1: "直播中", 2: "可回放"}.get(item.get("status"), "未知"),
        "schedule_id": item.get("scheduleId"),
        "start_time": ms_to_iso(item.get("startTime")),
        "end_time": ms_to_iso(item.get("endTime")),
        "raw": item,
    }


async def get_replay_schedule(
    root_dir: Path,
    *,
    profile: str,
    channel: str = "auto",
    semester_id: str | None = None,
    week: str | None = None,
) -> dict[str, Any]:
    auth_state = await get_auth_state(root_dir, profile, channel)
    page_html, page_url = await fetch_text(replay_course_url(semester_id), auth_state, referer=REPLAY_COURSE_URL)
    bootstrap = ensure_replay_bootstrap(root_dir, profile, page_html, page_url, parse_replay_bootstrap(page_html))
    selected_week = week or bootstrap.get("selected_week") or "1"
    params = {**(bootstrap.get("params") or {}), "week": selected_week}
    list_url = f"{REPLAY_HOST}/frontLive/listStudentCourseLivePage?{urllib.parse.urlencode(params)}"
    list_text, list_final_url = await fetch_text(list_url, auth_state, referer=page_url)
    items = parse_json_or_raise(root_dir, profile, list_text, list_final_url, "回放课表")
    return {
        **bootstrap,
        "selected_week": selected_week,
        "items": [normalize_replay_item(item) for item in items],
        "url": page_url,
        "list_url": list_final_url,
    }


async def get_replay_courses(
    root_dir: Path,
    *,
    profile: str,
    channel: str = "auto",
    semester_id: str | None = None,
) -> dict[str, Any]:
    auth_state = await get_auth_state(root_dir, profile, channel)
    page_html, page_url = await fetch_text(replay_course_url(semester_id), auth_state, referer=REPLAY_COURSE_URL)
    bootstrap = ensure_replay_bootstrap(root_dir, profile, page_html, page_url, parse_replay_bootstrap(page_html))
    base_params = dict(bootstrap.get("params") or {})
    courses: dict[tuple[Any, Any, str], dict[str, Any]] = {}

    for week_item in bootstrap.get("weeks") or []:
        week = week_item.get("value")
        if not week:
            continue
        params = {**base_params, "week": week}
        list_url = f"{REPLAY_HOST}/frontLive/listStudentCourseLivePage?{urllib.parse.urlencode(params)}"
        try:
            list_text, list_final_url = await fetch_text(list_url, auth_state, referer=page_url)
            items = parse_json_or_raise(root_dir, profile, list_text, list_final_url, "回放课程列表")
        except AuthStateError:
            raise
        except Exception:
            continue

        for raw_item in items:
            item = normalize_replay_item(raw_item)
            key = (item.get("course_id"), item.get("clazz_id"), item.get("course_code") or item.get("course_name"))
            course = courses.setdefault(
                key,
                {
                    "course_id": item.get("course_id"),
                    "course_code": item.get("course_code", ""),
                    "course_name": item.get("course_name", ""),
                    "clazz_id": item.get("clazz_id"),
                    "clazz_name": item.get("clazz_name", ""),
                    "teacher": item.get("teacher", ""),
                    "place": item.get("place", ""),
                    "replay_live_id": "",
                    "live_count": 0,
                    "replay_count": 0,
                    "weeks": set(),
                },
            )
            course["live_count"] += 1
            course["weeks"].add(str(week))
            if item.get("status") == 2:
                course["replay_count"] += 1
                if not course["replay_live_id"]:
                    course["replay_live_id"] = str(item.get("id"))

    result = []
    for course in courses.values():
        course["weeks"] = sorted(course["weeks"], key=lambda value: int(value) if value.isdigit() else value)
        result.append(course)
    result.sort(key=lambda item: (item.get("course_name") or "", item.get("clazz_name") or ""))
    return {
        **bootstrap,
        "courses": result,
        "url": page_url,
    }


def extract_replay_detail_params(html_text: str) -> dict[str, str]:
    return {
        "liveId": script_value(html_text, "liveId"),
        "fid": script_value(html_text, "fid"),
        "uId": script_value(html_text, "uId"),
    }


def ensure_replay_detail_page(root_dir: Path, profile: str, html_text: str, final_url: str) -> dict[str, str]:
    params = extract_replay_detail_params(html_text)
    if any(str(value).strip() for value in params.values()):
        return params
    if looks_like_login_page(html_text, final_url):
        raise expired_auth_error(root_dir, profile)
    raise RuntimeError(f"未能识别课程回放详情页，当前地址：{final_url}")


async def get_replay_course_sessions(
    root_dir: Path,
    *,
    live_id: str,
    profile: str,
    channel: str = "auto",
) -> dict[str, Any]:
    auth_state = await get_auth_state(root_dir, profile, channel)
    detail_url = f"{REPLAY_HOST}/live/viewNewCourseLive1?isStudent=1&liveId={urllib.parse.quote(str(live_id))}"
    detail_html, final_url = await fetch_text(detail_url, auth_state, referer=REPLAY_COURSE_URL)
    params = ensure_replay_detail_page(root_dir, profile, detail_html, final_url)
    params["liveId"] = str(live_id)
    list_url = f"{REPLAY_VIEW_HOST}/xidianpj/live/listSignleCourse?{urllib.parse.urlencode(params)}"
    list_text, list_final_url = await fetch_text(list_url, auth_state, referer=final_url)
    sessions = [normalize_replay_item(item) for item in parse_json_or_raise(root_dir, profile, list_text, list_final_url, "课时列表")]
    return {"sessions": sessions, "url": final_url, "list_url": list_final_url}


def extract_replay_infostr(html_text: str) -> dict[str, Any]:
    match = re.search(r"var\s+infostr\s*=\s*[\"']([^\"']+)[\"']", html_text)
    if not match:
        return {}
    try:
        decoded = urllib.parse.unquote(match.group(1))
        return json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        return {}


async def get_replay_video_resources(
    root_dir: Path,
    *,
    live_ids: list[str],
    profile: str,
    channel: str = "auto",
) -> dict[str, Any]:
    auth_state = await get_auth_state(root_dir, profile, channel)
    resources = []
    details = []
    field_labels = {
        "teacherTrack": "教师画面",
        "pptVideo": "课件画面",
        "studentFull": "学生全景",
    }

    for live_id in live_ids:
        view_url_api = f"{REPLAY_VIEW_HOST}/xidianpj/live/getXidianViewUrl?{urllib.parse.urlencode({'liveId': str(live_id), 'status': ''})}"
        view_path, view_api_url = await fetch_text(view_url_api, auth_state, referer=REPLAY_COURSE_URL)
        if looks_like_login_page(view_path, view_api_url):
            raise expired_auth_error(root_dir, profile)

        view_url = view_path.strip().strip('"')
        if view_url.startswith("/"):
            view_url = REPLAY_VIEW_HOST + view_url
        player_html, final_url = await fetch_text(view_url, auth_state, referer=REPLAY_COURSE_URL)
        if looks_like_login_page(player_html, final_url):
            raise expired_auth_error(root_dir, profile)

        info = extract_replay_infostr(player_html)
        details.append({"live_id": str(live_id), "view_url": final_url, "info": info})
        video_path = info.get("videoPath") or {}
        for field, url in video_path.items():
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            resources.append(
                {
                    "chapter": f"live_{live_id}",
                    "chapter_id": str(live_id),
                    "attachment_name": f"{live_id}_{field_labels.get(field, field)}",
                    "attachment_type": "video",
                    "object_id": str(live_id),
                    "url": url,
                    "kind": "mp4" if ".mp4" in urllib.parse.urlparse(url).path.lower() else "video",
                    "source_field": field,
                    "live_id": str(live_id),
                    "label": field_labels.get(field, field),
                    "start_time": ms_to_iso(info.get("startTime")),
                    "end_time": ms_to_iso(info.get("endTime")),
                }
            )
    return {"resources": resources, "details": details}


def course_list_url(term: str | None = None, term_label: str | None = None) -> str:
    if not term:
        return COURSE_LIST_URL
    query = urllib.parse.urlencode(
        {
            "v": "1",
            "ctype": "0",
            "semesternum": term,
            "showContent": term_label or "",
        }
    )
    return f"{COURSE_LIST_URL}?{query}"


def selected_term_label(html_text: str, term: str) -> str:
    soup = page_soup(html_text)
    option = soup.select_one(f'#yearList option[value="{term}"]')
    return option.get_text(" ", strip=True) if option else ""


async def get_terms_and_courses(
    root_dir: Path,
    *,
    profile: str,
    channel: str = "auto",
    term: str | None = None,
    term_label: str | None = None,
) -> dict[str, Any]:
    auth_state = await get_auth_state(root_dir, profile, channel)
    if term and not term_label:
        initial_html, initial_url = await fetch_text(COURSE_LIST_URL, auth_state)
        ensure_course_list_page(root_dir, profile, initial_html, initial_url)
        term_label = selected_term_label(initial_html, term)

    url = course_list_url(term, term_label)
    course_html, course_url = await fetch_text(url, auth_state)
    soup = ensure_course_list_page(root_dir, profile, course_html, course_url)

    terms = []
    for option in soup.select("#yearList option"):
        value = (option.get("value") or "").strip()
        label = option.get_text(" ", strip=True)
        if value:
            terms.append({"value": value, "label": label, "selected": option.has_attr("selected")})

    courses = []
    seen = set()
    for index, item in enumerate(soup.select(".myde_course_item")):
        link = item.select_one("a[href*='opencoursenewfy'], a[href*='courseId'], a[href]")
        href = urllib.parse.urljoin(course_url, link.get("href", "")) if link else ""
        query = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        course_id = (item.get("cid") or query.get("courseId", [""])[0] or query.get("courseid", [""])[0]).strip()
        clazz_id = (query.get("clazzId", [""])[0] or query.get("clazzid", [""])[0]).strip()
        cpi = (query.get("cpi", [""])[0]).strip()
        if not href or not course_id or not clazz_id:
            continue

        name = item.get("cname", "").strip()
        if not name:
            title = item.select_one("dt")
            if title:
                name = (title.find(string=True, recursive=False) or title.get_text(" ", strip=True)).strip()
        code = (
            (item.select_one(".myde_course_span") or {}).get_text(" ", strip=True).strip("()")
            if item.select_one(".myde_course_span")
            else ""
        )
        dds = [dd.get_text(" ", strip=True) for dd in item.select("dd")]
        teacher = dds[0] if len(dds) > 0 else ""
        clazz = dds[1] if len(dds) > 1 else ""

        key = (course_id, clazz_id)
        if key in seen:
            continue
        seen.add(key)
        courses.append(
            {
                "index": index,
                "name": name,
                "href": href,
                "course_id": course_id,
                "clazz_id": clazz_id,
                "cpi": cpi,
                "code": code,
                "teacher": teacher,
                "clazz": clazz,
            }
        )

    return {"terms": terms, "courses": courses, "url": course_url}


def input_value(html_text: str, element_id: str) -> str:
    match = re.search(
        rf'id=["\']{re.escape(element_id)}["\'][^>]*value=["\']([^"\']*)',
        html_text,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else ""


async def get_course_chapters(root_dir: Path, *, course_url: str, profile: str, channel: str = "auto") -> dict[str, Any]:
    auth_state = await get_auth_state(root_dir, profile, channel)
    stu_html, stu_url = await fetch_text(course_url, auth_state, referer=COURSE_LIST_URL)
    if looks_like_login_page(stu_html, stu_url):
        raise expired_auth_error(root_dir, profile)

    query = urllib.parse.parse_qs(urllib.parse.urlparse(course_url).query)
    course_id = input_value(stu_html, "courseid") or query.get("courseId", [""])[0] or query.get("courseid", [""])[0]
    clazz_id = input_value(stu_html, "clazzid") or query.get("clazzId", [""])[0] or query.get("clazzid", [""])[0]
    cpi = input_value(stu_html, "cpi") or query.get("cpi", [""])[0]
    enc = input_value(stu_html, "enc")
    t_value = input_value(stu_html, "t") or str(int(time.time() * 1000))

    if not all((course_id, clazz_id, cpi, enc)):
        raise RuntimeError("课程入口页缺少 courseid/clazzid/cpi/enc，无法拼装章节页。")

    student_course_url = (
        f"{MOOC2}/mooc2-ans/mycourse/studentcourse?"
        f"courseid={urllib.parse.quote(course_id)}"
        f"&clazzid={urllib.parse.quote(clazz_id)}"
        f"&cpi={urllib.parse.quote(cpi)}"
        f"&ut=s&t={urllib.parse.quote(t_value)}"
        f"&stuenc={urllib.parse.quote(enc)}"
    )
    html_text, final_url = await fetch_text(student_course_url, auth_state, referer=stu_url)
    if looks_like_login_page(html_text, final_url):
        raise expired_auth_error(root_dir, profile)

    soup = page_soup(html_text)
    chapters = []
    seen = set()
    for index, item in enumerate(soup.select(".chapter_item[id^='cur'], [id^='cur'][onclick*='toOld']")):
        title_el = item.select_one(".clicktitle") or item.select_one(".catalog_name") or item
        title = re.sub(r"\s+", " ", title_el.get_text(" ", strip=True))
        onclick = item.get("onclick", "")
        match = re.search(
            r"toOld\(['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]",
            onclick,
        )
        c_id = match.group(1) if match else course_id
        chapter_id = match.group(2) if match else (item.get("id") or "").removeprefix("cur")
        cl_id = match.group(3) if match else clazz_id
        if not title or not chapter_id or chapter_id in seen:
            continue
        seen.add(chapter_id)
        chapters.append(
            {
                "index": index,
                "title": title,
                "chapter_id": chapter_id,
                "course_id": c_id,
                "clazz_id": cl_id,
                "cpi": cpi,
                "url": chapter_id,
            }
        )

    return {"course_url": course_url, "page_url": final_url, "chapters": chapters}


def extract_marg(html_text: str) -> dict[str, Any] | None:
    match = re.search(r"mArg\s*=\s*(\{.*?\});\s*(?:\n|</script>)", html_text, flags=re.DOTALL)
    if not match:
        return None
    return json.loads(match.group(1))


def classify_link(url: str, attachment_type: str, field: str) -> str | None:
    path = urllib.parse.unquote(urllib.parse.urlparse(url).path).lower()
    if path.endswith(".mp4") or attachment_type == "video":
        return "mp4"
    if path.endswith(".pdf") or field == "pdf":
        return "pdf"
    return None


async def discover_chapter_resources(
    chapter: dict[str, Any],
    auth_state: dict[str, Any],
    semaphore: asyncio.Semaphore,
    mode: str,
) -> list[dict[str, Any]]:
    async with semaphore:
        try:
            query = urllib.parse.urlencode(
                {
                    "clazzid": chapter["clazz_id"],
                    "courseid": chapter["course_id"],
                    "knowledgeid": chapter["chapter_id"],
                    "num": "0",
                    "ut": "s",
                    "cpi": chapter["cpi"],
                    "v": "2025-0424-1038-4",
                    "mooc2": "1",
                    "isMicroCourse": "false",
                    "editorPreview": "0",
                }
            )
            cards_url = f"{MOOC1}/knowledge/cards?{query}"
            cards_html, _ = await fetch_text(cards_url, auth_state, referer=MOOC1)
            marg = extract_marg(cards_html)
            if not marg:
                return []

            attachments = marg.get("attachments") or []
            resources = []
            for attachment in attachments:
                prop = attachment.get("property") or {}
                object_id = prop.get("objectid") or prop.get("objectId") or attachment.get("objectid") or attachment.get("objectId")
                if not object_id:
                    continue
                name = prop.get("name") or attachment.get("name") or object_id
                attachment_type = attachment.get("type") or "unknown"

                status_url = f"{MOOC1}/ananas/status/{urllib.parse.quote(object_id)}?flag=normal"
                status_text, _ = await fetch_text(status_url, auth_state, referer=cards_url)
                try:
                    status = json.loads(status_text)
                except json.JSONDecodeError:
                    continue

                for field in ("http", "pdf", "download"):
                    value = status.get(field)
                    if not isinstance(value, str) or not value.startswith(("http://", "https://")):
                        continue
                    kind = classify_link(value, attachment_type, field)
                    if not kind:
                        continue
                    if mode != "all" and ((mode == "pdf" and kind != "pdf") or (mode == "video" and kind != "mp4")):
                        continue

                    resources.append(
                        {
                            "chapter": chapter["title"],
                            "chapter_id": chapter["chapter_id"],
                            "attachment_name": name,
                            "attachment_type": attachment_type,
                            "object_id": object_id,
                            "url": value,
                            "kind": kind,
                            "source_field": field,
                        }
                    )
            return resources
        except Exception:
            return []


async def run_extract_links_task(manager: TaskManager, record: TaskRecord, payload: dict[str, Any]) -> None:
    await manager.set_status(record, "running")
    chapters = payload.get("chapters") or []
    if not chapters:
        await manager.emit(record, "log", {"level": "error", "message": "没有选择章节。"})
        await manager.set_status(record, "failed", exit_code=2)
        await manager.close_stream(record)
        return

    profile = payload.get("profile") or ".xidian-profile"
    channel = payload.get("channel") or "auto"
    mode = payload.get("mode") or "all"
    concurrency = int(payload.get("metadata_concurrency") or 24)

    try:
        await manager.emit(record, "log", {"level": "info", "message": "正在读取登录态缓存..."})
        auth_state = await get_auth_state(manager.root_dir, profile, channel)
    except Exception as exc:
        await manager.emit(record, "log", {"level": "error", "message": f"读取登录态失败：{exc}"})
        await manager.set_status(record, "failed", exit_code=2)
        await manager.close_stream(record)
        return

    await manager.emit(
        record,
        "log",
        {"level": "info", "message": f"开始并发解析 {len(chapters)} 个章节，并发数：{concurrency}"},
    )
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def worker(chapter: dict[str, Any]) -> list[dict[str, Any]]:
        await manager.emit(record, "log", {"level": "info", "message": f"解析章节：{chapter['title']}"})
        resources = await discover_chapter_resources(chapter, auth_state, semaphore, mode)
        if resources:
            await manager.emit(
                record,
                "log",
                {"level": "info", "message": f"章节 {chapter['title']} 找到 {len(resources)} 个资源"},
            )
        return resources

    all_resources: list[dict[str, Any]] = []
    try:
        nested = await asyncio.gather(*(worker(chapter) for chapter in chapters))
        all_resources = [resource for group in nested for resource in group]
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        await manager.emit(record, "log", {"level": "error", "message": f"解析出错：{exc}"})

    record.result = all_resources
    await manager.emit(record, "log", {"level": "info", "message": f"解析完成，共提取到 {len(all_resources)} 个资源。"})
    await manager.set_status(record, "done" if record.status != "cancelled" else "cancelled", exit_code=0)
    await manager.close_stream(record)


def safe_filename(value: str, suffix: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    if not value:
        value = "resource"
    if suffix and not value.lower().endswith(suffix.lower()):
        value += suffix
    return value[:160]


def download_one_sync(resource: dict[str, Any], auth_state: dict[str, Any], out_dir: Path) -> Path:
    suffix = ".mp4" if resource["kind"] == "mp4" else ".pdf"
    chapter_dir = out_dir / safe_filename(resource["chapter"], "")
    chapter_dir.mkdir(parents=True, exist_ok=True)
    path = chapter_dir / safe_filename(resource["attachment_name"], suffix)

    counter = 1
    while path.exists():
        path = chapter_dir / safe_filename(f"{resource['attachment_name']}_{counter}", suffix)
        counter += 1

    request = build_request(resource["url"], auth_state, referer=MOOC1)
    with urllib.request.urlopen(request, timeout=120) as response:
        with path.open("wb") as file:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                file.write(chunk)
    return path


async def run_chapter_download_task(manager: TaskManager, record: TaskRecord, payload: dict[str, Any]) -> None:
    await manager.set_status(record, "running")
    resources = payload.get("resources") or []
    if not resources:
        await manager.emit(record, "log", {"level": "error", "message": "没有要下载的资源。"})
        await manager.set_status(record, "failed", exit_code=2)
        await manager.close_stream(record)
        return

    profile = payload.get("profile") or ".xidian-profile"
    channel = payload.get("channel") or "auto"
    out_dir_name = payload.get("out") or "downloaded_xidian"
    out_dir = (manager.root_dir / out_dir_name).resolve()
    concurrency = int(payload.get("concurrency") or 4)

    try:
        await manager.emit(record, "log", {"level": "info", "message": "正在读取登录态缓存..."})
        auth_state = await get_auth_state(manager.root_dir, profile, channel)
    except Exception as exc:
        await manager.emit(record, "log", {"level": "error", "message": f"读取登录态失败：{exc}"})
        await manager.set_status(record, "failed", exit_code=2)
        await manager.close_stream(record)
        return

    await manager.emit(
        record,
        "log",
        {"level": "info", "message": f"开始并发下载 {len(resources)} 个资源，并发数：{concurrency}"},
    )
    semaphore = asyncio.Semaphore(max(1, concurrency))
    stop_event = asyncio.Event()

    async def worker(index: int, resource: dict[str, Any]) -> int:
        async with semaphore:
            if stop_event.is_set():
                return 0

            title = resource.get("attachment_name", "未知资源")
            prefix = f"[{index}/{len(resources)}] "
            await manager.emit(record, "log", {"level": "info", "message": f"{prefix}开始下载：{title}"})

            try:
                path = await asyncio.to_thread(download_one_sync, resource, auth_state, out_dir)
                rel_path = path.relative_to(manager.root_dir)
                record.saved_files.append(str(rel_path))
                await manager.emit(record, "saved", {"path": str(rel_path)})
                await manager.emit(record, "log", {"level": "info", "message": f"{prefix}已保存：{rel_path}"})
                return 0
            except Exception as exc:
                await manager.emit(record, "log", {"level": "error", "message": f"{prefix}下载失败（{title}）：{exc}"})
                if payload.get("stop_on_error", False):
                    stop_event.set()
                return 1

    try:
        codes = await asyncio.gather(*(worker(index, resource) for index, resource in enumerate(resources, start=1)))
    except asyncio.CancelledError:
        codes = []

    exit_code = next((code for code in codes if code != 0), 0) if codes else 0
    if record.status != "cancelled":
        await manager.set_status(record, "done" if exit_code == 0 else "failed", exit_code=exit_code)
        await manager.close_stream(record)
