from __future__ import annotations

import asyncio
import contextlib
import json
import os
import platform
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from .task_manager import TaskManager, TaskRecord

COURSE_LIST_URL = "https://fycourse.fanya.chaoxing.com/courselist/study"
REPLAY_COURSE_URL = "https://newesxidian.chaoxing.com/frontLive/studentSelectCourse1"
REPLAY_HOST = "https://newesxidian.chaoxing.com"
REPLAY_VIEW_HOST = "http://newes.chaoxing.com"
MOOC1 = "https://mooc1.chaoxing.com"
MOOC2 = "https://mooc2-ans.chaoxing.com"
XIDIAN_LOGIN_URL = "https://ids.xidian.edu.cn/authserver/login?service=https://xdspoc.fanya.chaoxing.com/sso/xdspoc"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def browser_channels(channel: str) -> list[str]:
    if channel != "auto":
        return [channel]
    if platform.system().lower() == "windows":
        return ["msedge", "chrome", "chromium"]
    return ["chrome", "chromium", "msedge"]


async def get_cookie_string(root_dir: Path, profile: str, channel: str) -> str:
    playwright = await async_playwright().start()
    profile_dir = (root_dir / profile).expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    
    errors = []
    cookies = []
    for browser_channel in browser_channels(channel):
        kwargs = {
            "user_data_dir": str(profile_dir),
            "headless": True,
        }
        if browser_channel != "chromium":
            kwargs["channel"] = browser_channel
        try:
            context = await playwright.chromium.launch_persistent_context(**kwargs)
            cookies = await context.cookies()
            await context.close()
            break
        except PlaywrightError as exc:
            errors.append(f"{browser_channel}: {exc}")
    
    await playwright.stop()
    if not cookies:
        if errors:
            raise RuntimeError("启动浏览器失败读取Cookie：" + " | ".join(errors))
        raise RuntimeError("没有读取到Cookie，请先登录。")
    
    return "; ".join(f"{cookie['name']}={cookie['value']}" for cookie in cookies)


async def check_auth_status(root_dir: Path, *, profile: str, channel: str = "auto") -> dict[str, Any]:
    try:
        cookie_string = await get_cookie_string(root_dir, profile, channel)
        course_html, course_url = await fetch_text(COURSE_LIST_URL, cookie_string)
    except Exception as exc:
        return {"authenticated": False, "status": "missing", "message": str(exc)}

    soup = BeautifulSoup(course_html, "html.parser")
    has_course_shell = bool(soup.select_one("#yearList, .myde_course_item"))
    looks_like_login = "authserver/login" in course_url or bool(
        soup.select_one('input[name="username"], input[type="password"]')
    )
    if has_course_shell and not looks_like_login:
        return {"authenticated": True, "status": "authenticated", "url": course_url}

    return {
        "authenticated": False,
        "status": "expired",
        "message": "本地 Cookie 已读取，但未能进入课程列表，请重新登录。",
        "url": course_url,
    }


def build_request(url: str, cookie_string: str, *, referer: str = COURSE_LIST_URL) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Cookie": cookie_string,
            "Referer": referer,
        },
    )


def fetch_text_sync(url: str, cookie_string: str, *, referer: str = COURSE_LIST_URL) -> tuple[str, str]:
    request = build_request(url, cookie_string, referer=referer)
    with urllib.request.urlopen(request, timeout=25) as response:
        body = response.read().decode("utf-8", errors="replace")
        return body, response.geturl()


async def fetch_text(url: str, cookie_string: str, *, referer: str = COURSE_LIST_URL) -> tuple[str, str]:
    return await asyncio.to_thread(fetch_text_sync, url, cookie_string, referer=referer)


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
    soup = BeautifulSoup(html_text, "html.parser")
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
        week_text = (soup.select_one(".w_pull_time") or {}).get_text(" ", strip=True) if soup.select_one(".w_pull_time") else ""
        week_match = re.search(r"\d+", week_text)
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
    cookie_string = await get_cookie_string(root_dir, profile, channel)
    page_html, page_url = await fetch_text(replay_course_url(semester_id), cookie_string, referer=REPLAY_COURSE_URL)
    bootstrap = parse_replay_bootstrap(page_html)
    selected_week = week or bootstrap.get("selected_week") or "1"
    params = dict(bootstrap.get("params") or {})
    params["week"] = selected_week
    list_url = f"{REPLAY_HOST}/frontLive/listStudentCourseLivePage?{urllib.parse.urlencode(params)}"
    list_text, list_final_url = await fetch_text(list_url, cookie_string, referer=page_url)
    items = json.loads(list_text)
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
    cookie_string = await get_cookie_string(root_dir, profile, channel)
    page_html, page_url = await fetch_text(replay_course_url(semester_id), cookie_string, referer=REPLAY_COURSE_URL)
    bootstrap = parse_replay_bootstrap(page_html)
    base_params = dict(bootstrap.get("params") or {})
    courses: dict[tuple[Any, Any, str], dict[str, Any]] = {}
    weeks = bootstrap.get("weeks") or []

    for week_item in weeks:
        week = week_item.get("value")
        if not week:
            continue
        params = {**base_params, "week": week}
        list_url = f"{REPLAY_HOST}/frontLive/listStudentCourseLivePage?{urllib.parse.urlencode(params)}"
        try:
            list_text, _ = await fetch_text(list_url, cookie_string, referer=page_url)
            items = json.loads(list_text)
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


async def get_replay_course_sessions(
    root_dir: Path,
    *,
    live_id: str,
    profile: str,
    channel: str = "auto",
) -> dict[str, Any]:
    cookie_string = await get_cookie_string(root_dir, profile, channel)
    detail_url = f"{REPLAY_HOST}/live/viewNewCourseLive1?isStudent=1&liveId={urllib.parse.quote(str(live_id))}"
    detail_html, final_url = await fetch_text(detail_url, cookie_string, referer=REPLAY_COURSE_URL)
    params = extract_replay_detail_params(detail_html)
    params["liveId"] = str(live_id)
    list_url = f"{REPLAY_VIEW_HOST}/xidianpj/live/listSignleCourse?{urllib.parse.urlencode(params)}"
    list_text, list_final_url = await fetch_text(list_url, cookie_string, referer=final_url)
    sessions = [normalize_replay_item(item) for item in json.loads(list_text)]
    return {"sessions": sessions, "url": final_url, "list_url": list_final_url}


def extract_replay_infostr(html_text: str) -> dict[str, Any]:
    match = re.search(r"var\s+infostr\s*=\s*[\"']([^\"']+)[\"']", html_text)
    if not match:
        return {}
    decoded = urllib.parse.unquote(match.group(1))
    return json.loads(decoded)


async def get_replay_video_resources(
    root_dir: Path,
    *,
    live_ids: list[str],
    profile: str,
    channel: str = "auto",
) -> dict[str, Any]:
    cookie_string = await get_cookie_string(root_dir, profile, channel)
    resources = []
    details = []
    field_labels = {
        "teacherTrack": "教师画面",
        "pptVideo": "课件画面",
        "studentFull": "学生全景",
    }
    for live_id in live_ids:
        view_url_api = f"{REPLAY_VIEW_HOST}/xidianpj/live/getXidianViewUrl?{urllib.parse.urlencode({'liveId': str(live_id), 'status': ''})}"
        view_path, _ = await fetch_text(view_url_api, cookie_string, referer=REPLAY_COURSE_URL)
        view_url = view_path.strip().strip('"')
        if view_url.startswith("/"):
            view_url = REPLAY_VIEW_HOST + view_url
        player_html, final_url = await fetch_text(view_url, cookie_string, referer=REPLAY_COURSE_URL)
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
    soup = BeautifulSoup(html_text, "html.parser")
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
    cookie_string = await get_cookie_string(root_dir, profile, channel)
    if term and not term_label:
        initial_html, _ = await fetch_text(COURSE_LIST_URL, cookie_string)
        term_label = selected_term_label(initial_html, term)
    url = course_list_url(term, term_label)
    course_html, course_url = await fetch_text(url, cookie_string)
    
    soup = BeautifulSoup(course_html, "html.parser")
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
            dt = item.select_one("dt")
            if dt:
                name = dt.find(string=True, recursive=False) or dt.get_text(" ", strip=True)
                name = name.strip()
        code = (item.select_one(".myde_course_span") or {}).get_text(" ", strip=True).strip("()") if item.select_one(".myde_course_span") else ""
        dds = [dd.get_text(" ", strip=True) for dd in item.select("dd")]
        teacher = dds[0] if len(dds) > 0 else ""
        clazz = dds[1] if len(dds) > 1 else ""
        
        # 兼容 term 筛选
        # 实际上网页的 term 筛选是在超星服务端做的，我们这里如果不带参数，它返回当前学期。
        # 这里只是简单提取。
        key = (course_id, clazz_id)
        if key in seen:
            continue
        seen.add(key)
        courses.append({
            "index": index,
            "name": name,
            "href": href,
            "course_id": course_id,
            "clazz_id": clazz_id,
            "cpi": cpi,
            "code": code,
            "teacher": teacher,
            "clazz": clazz,
        })
        
    return {"terms": terms, "courses": courses, "url": course_url}


def input_value(html_text: str, element_id: str) -> str:
    match = re.search(
        rf'id=["\']{re.escape(element_id)}["\'][^>]*value=["\']([^"\']*)',
        html_text,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else ""


async def get_course_chapters(root_dir: Path, *, course_url: str, profile: str, channel: str = "auto") -> dict[str, Any]:
    cookie_string = await get_cookie_string(root_dir, profile, channel)
    stu_html, stu_url = await fetch_text(course_url, cookie_string, referer=COURSE_LIST_URL)
    
    # 提取隐藏参数拼装真正的学生课表
    query = urllib.parse.parse_qs(urllib.parse.urlparse(course_url).query)
    course_id = input_value(stu_html, "courseid") or query.get("courseId", [""])[0] or query.get("courseid", [""])[0]
    clazz_id = input_value(stu_html, "clazzid") or query.get("clazzId", [""])[0] or query.get("clazzid", [""])[0]
    cpi = input_value(stu_html, "cpi") or query.get("cpi", [""])[0]
    enc = input_value(stu_html, "enc")
    t_value = input_value(stu_html, "t") or str(int(time.time() * 1000))
    
    if not all((course_id, clazz_id, cpi, enc)):
        raise RuntimeError("课程入口页缺少 courseid/clazzid/cpi/enc，无法拼章节页。")
        
    student_course_url = (
        f"{MOOC2}/mooc2-ans/mycourse/studentcourse?"
        f"courseid={urllib.parse.quote(course_id)}"
        f"&clazzid={urllib.parse.quote(clazz_id)}"
        f"&cpi={urllib.parse.quote(cpi)}"
        f"&ut=s&t={urllib.parse.quote(t_value)}"
        f"&stuenc={urllib.parse.quote(enc)}"
    )
    html_text, final_url = await fetch_text(student_course_url, cookie_string, referer=stu_url)
    
    soup = BeautifulSoup(html_text, "html.parser")
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
        
        # 为了兼容前端，我们保留一个类似旧版的 url
        # 前端勾选用的 url 只需是一个唯一标志即可，我们使用 chapter_id
        chapters.append({
            "index": index,
            "title": title,
            "chapter_id": chapter_id,
            "course_id": c_id,
            "clazz_id": cl_id,
            "cpi": cpi,
            "url": chapter_id, # 唯一标识符，供前端选择
        })
        
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


async def discover_chapter_resources(chapter: dict[str, Any], cookie_string: str, semaphore: asyncio.Semaphore, mode: str) -> list[dict[str, Any]]:
    async with semaphore:
        try:
            query = urllib.parse.urlencode({
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
            })
            cards_url = f"{MOOC1}/knowledge/cards?{query}"
            cards_html, _ = await fetch_text(cards_url, cookie_string, referer=MOOC1)
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
                status_text, _ = await fetch_text(status_url, cookie_string, referer=cards_url)
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
                        
                    resources.append({
                        "chapter": chapter["title"],
                        "chapter_id": chapter["chapter_id"],
                        "attachment_name": name,
                        "attachment_type": attachment_type,
                        "object_id": object_id,
                        "url": value,
                        "kind": kind,
                        "source_field": field,
                    })
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
        await manager.emit(record, "log", {"level": "info", "message": "正在获取本地 Cookie..."})
        cookie_string = await get_cookie_string(manager.root_dir, profile, channel)
    except Exception as exc:
        await manager.emit(record, "log", {"level": "error", "message": f"获取 Cookie 失败：{exc}"})
        await manager.set_status(record, "failed", exit_code=2)
        await manager.close_stream(record)
        return

    await manager.emit(record, "log", {"level": "info", "message": f"开始并发解析 {len(chapters)} 个章节，并发数：{concurrency}"})
    semaphore = asyncio.Semaphore(max(1, concurrency))
    
    all_resources = []
    
    async def worker(chapter):
        await manager.emit(record, "log", {"level": "info", "message": f"解析章节：{chapter['title']}"})
        resources = await discover_chapter_resources(chapter, cookie_string, semaphore, mode)
        if resources:
            await manager.emit(record, "log", {"level": "info", "message": f"章节 {chapter['title']} 找到 {len(resources)} 个资源"})
        return resources

    try:
        nested = await asyncio.gather(*(worker(c) for c in chapters))
        all_resources = [resource for group in nested for resource in group]
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        await manager.emit(record, "log", {"level": "error", "message": f"解析报错：{exc}"})
    
    record.result = all_resources
    await manager.emit(record, "log", {"level": "info", "message": f"解析完毕，共提取到 {len(all_resources)} 个资源。"})
    await manager.set_status(record, "done" if record.status != "cancelled" else "cancelled", exit_code=0)
    await manager.close_stream(record)


def safe_filename(value: str, suffix: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    if not value:
        value = "resource"
    if not value.lower().endswith(suffix.lower()):
        value += suffix
    return value[:160]


def download_one_sync(resource: dict[str, Any], cookie_string: str, out_dir: Path) -> Path:
    suffix = ".mp4" if resource["kind"] == "mp4" else ".pdf"
    chapter_dir = out_dir / safe_filename(resource["chapter"], "")
    chapter_dir.mkdir(parents=True, exist_ok=True)
    path = chapter_dir / safe_filename(resource["attachment_name"], suffix)
    
    counter = 1
    original_path = path
    while path.exists():
        path = chapter_dir / safe_filename(f"{resource['attachment_name']}_{counter}", suffix)
        counter += 1
        
    request = build_request(resource["url"], cookie_string, referer=MOOC1)
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
        await manager.emit(record, "log", {"level": "info", "message": "正在获取本地 Cookie..."})
        cookie_string = await get_cookie_string(manager.root_dir, profile, channel)
    except Exception as exc:
        await manager.emit(record, "log", {"level": "error", "message": f"获取 Cookie 失败：{exc}"})
        await manager.set_status(record, "failed", exit_code=2)
        await manager.close_stream(record)
        return

    await manager.emit(record, "log", {"level": "info", "message": f"开始并发下载 {len(resources)} 个资源，并发数：{concurrency}"})
    semaphore = asyncio.Semaphore(max(1, concurrency))
    stop_event = asyncio.Event()

    async def worker(index: int, resource: dict[str, Any]) -> int:
        async with semaphore:
            if stop_event.is_set():
                return 0
                
            title = resource.get("attachment_name", "未知")
            prefix = f"[{index}/{len(resources)}] "
            await manager.emit(record, "log", {"level": "info", "message": f"{prefix}开始下载：{title}"})
            
            try:
                path = await asyncio.to_thread(download_one_sync, resource, cookie_string, out_dir)
                rel_path = path.relative_to(manager.root_dir)
                record.saved_files.append(str(rel_path))
                await manager.emit(record, "saved", {"path": str(rel_path)})
                await manager.emit(record, "log", {"level": "info", "message": f"{prefix}已保存：{rel_path}"})
                return 0
            except Exception as exc:
                await manager.emit(record, "log", {"level": "error", "message": f"{prefix}下载失败 ({title})：{exc}"})
                if payload.get("stop_on_error", False):
                    stop_event.set()
                return 1

    try:
        codes = await asyncio.gather(*(worker(i, res) for i, res in enumerate(resources, start=1)))
    except asyncio.CancelledError:
        pass
        
    exit_code = next((code for code in codes if code != 0), 0) if 'codes' in locals() else 0
    if record.status != "cancelled":
        await manager.set_status(record, "done" if exit_code == 0 else "failed", exit_code=exit_code)
        await manager.close_stream(record)
