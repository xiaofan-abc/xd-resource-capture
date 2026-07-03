from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import platform
import time
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from .xidian_service import COURSE_LIST_URL, REPLAY_COURSE_URL

STORAGE_STATE_FILENAME = "storage_state.json"
LOGIN_SESSION_FILENAME = "login_session.json"
BOOTSTRAP_URLS = [COURSE_LIST_URL, REPLAY_COURSE_URL]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open a browser login session for the local crawler UI.")
    parser.add_argument("--login-url", required=True)
    parser.add_argument("--profile", default=".xidian-profile")
    parser.add_argument("--channel", choices=("auto", "chromium", "chrome", "msedge"), default="auto")
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--headless", action="store_true")
    return parser.parse_args()


def browser_channels(channel: str) -> list[str]:
    if channel != "auto":
        return [channel]
    if platform.system().lower() == "windows":
        return ["msedge", "chrome", "chromium"]
    return ["chrome", "chromium", "msedge"]


def storage_state_path(profile_dir: Path) -> Path:
    return profile_dir / STORAGE_STATE_FILENAME


def login_session_path(profile_dir: Path) -> Path:
    return profile_dir / LOGIN_SESSION_FILENAME


def write_login_session(profile_dir: Path, storage_path: Path, channel: str) -> None:
    payload = {
        "pid": os.getpid(),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "storage_state_path": str(storage_path),
        "channel": channel,
    }
    login_session_path(profile_dir).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_login_session(profile_dir: Path) -> None:
    with contextlib.suppress(OSError):
        login_session_path(profile_dir).unlink()


async def fill_if_possible(page, username: str, password: str) -> None:
    if username:
        for selector in (
            "input[name='username']",
            "input[name='phone']",
            "input[name='uname']",
            "input[type='text']",
        ):
            with contextlib.suppress(PlaywrightError):
                await page.locator(selector).first.fill(username, timeout=1500)
                print("[登录] 已尝试填写账号。")
                break
    if password:
        for selector in ("input[name='password']", "input[name='pwd']", "input[type='password']"):
            with contextlib.suppress(PlaywrightError):
                await page.locator(selector).first.fill(password, timeout=1500)
                print("[登录] 已尝试填写密码。")
                break
    if username and password:
        for selector in (
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('登录')",
            "a:has-text('登录')",
        ):
            with contextlib.suppress(PlaywrightError):
                await page.locator(selector).first.click(timeout=1500)
                print("[登录] 已尝试点击登录按钮，如遇验证码或风控，请在浏览器中手动完成。")
                break


async def page_is_logged_in(page) -> bool:
    try:
        url = page.url.lower()
    except PlaywrightError:
        return False

    if "authserver/login" in url:
        return False
    if "passport2.chaoxing.com/login" in url:
        return False
    if "chaoxing.com" in url:
        return True

    for selector in ("#yearList", ".myde_course_item", "#semesterList", ".weekNum"):
        with contextlib.suppress(PlaywrightError):
            if await page.locator(selector).first.count():
                return True
    return False


async def export_storage_state(context, target: Path, *, announce: bool, reason: str) -> bool:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=str(target))
    except PlaywrightError as exc:
        if announce:
            print(f"[登录态] 导出失败（{reason}）：{exc}")
        return False

    if announce:
        print(f"[登录态] 已导出（{reason}）：{target}")
    return True


async def bootstrap_service_pages(context) -> None:
    for url in BOOTSTRAP_URLS:
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(1000)
            print(f"[登录态] 已同步服务页：{url}")
        except PlaywrightError as exc:
            print(f"[登录态] 同步服务页失败：{url} | {exc}")
        finally:
            with contextlib.suppress(PlaywrightError):
                await page.close()


def active_pages(context) -> list:
    return [page for page in context.pages if not page.is_closed()]


async def pick_login_page(context, fallback_page):
    pages = active_pages(context)
    if not pages:
        return None

    if fallback_page in pages:
        return fallback_page
    return pages[-1]


async def keep_storage_state_fresh(page, context, target: Path) -> None:
    exported_once = False
    last_export_at = 0.0
    bootstrap_done = False
    empty_since: float | None = None
    while True:
        current_page = await pick_login_page(context, page)
        if current_page is None:
            now = time.monotonic()
            empty_since = empty_since or now
            if now - empty_since >= 5:
                print("[登录窗口] 5 秒内未检测到活动页面，结束等待。")
                return
            await asyncio.sleep(0.5)
            continue
        empty_since = None
        page = current_page
        await asyncio.sleep(1.5)
        if not await page_is_logged_in(page):
            continue

        if not bootstrap_done:
            await bootstrap_service_pages(context)
            bootstrap_done = True

        now = time.monotonic()
        if exported_once and now - last_export_at < 5:
            continue

        exported = await export_storage_state(
            context,
            target,
            announce=not exported_once,
            reason="检测到登录成功" if not exported_once else "刷新缓存",
        )
        if exported:
            exported_once = True
            last_export_at = now


async def wait_for_login_window(context, timeout_ms: int) -> None:
    deadline = time.monotonic() + timeout_ms / 1000
    empty_since: float | None = None
    while time.monotonic() < deadline:
        if not active_pages(context):
            now = time.monotonic()
            empty_since = empty_since or now
            if now - empty_since >= 5:
                print("[登录窗口] 5 秒内未检测到活动页面，结束等待。")
                return
            await asyncio.sleep(0.5)
            continue
        empty_since = None
        await asyncio.sleep(1)


async def main() -> int:
    args = parse_args()
    profile_dir = Path(args.profile).expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    state_path = storage_state_path(profile_dir)
    write_login_session(profile_dir, state_path, args.channel)

    async with async_playwright() as playwright:
        context = None
        exporter_task: asyncio.Task | None = None
        errors: list[str] = []
        try:
            for channel in browser_channels(args.channel):
                launch_kwargs = {
                    "user_data_dir": str(profile_dir),
                    "headless": args.headless,
                    "accept_downloads": True,
                    "viewport": {"width": 1280, "height": 900},
                }
                if channel != "chromium":
                    launch_kwargs["channel"] = channel
                try:
                    context = await playwright.chromium.launch_persistent_context(**launch_kwargs)
                    print(f"[浏览器] 使用 {channel}")
                    break
                except PlaywrightError as exc:
                    errors.append(f"{channel}: {exc}")

            if context is None:
                print("[失败] 浏览器启动失败。")
                for error in errors:
                    print(f"  {error}")
                return 2

            page = context.pages[0] if context.pages else await context.new_page()
            print(f"[打开] {args.login_url}")
            await page.goto(args.login_url, wait_until="domcontentloaded", timeout=60_000)
            await fill_if_possible(page, args.username, args.password)
            exporter_task = asyncio.create_task(keep_storage_state_fresh(page, context, state_path))

            if args.headless:
                await page.wait_for_timeout(10_000)
            else:
                print("[等待] 请在浏览器中完成登录。登录成功后会自动导出 storage_state.json。")
                print("[提示] 这个登录窗口会保留 10 分钟，关闭窗口或超时前都会继续尝试刷新登录态缓存。")
                try:
                    await wait_for_login_window(context, 10 * 60 * 1000)
                except PlaywrightError:
                    pass

            await bootstrap_service_pages(context)
            await export_storage_state(context, state_path, announce=True, reason="登录任务结束")
            return 0
        finally:
            if exporter_task is not None:
                exporter_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await exporter_task
            if context is not None:
                with contextlib.suppress(PlaywrightError):
                    await context.close()
            clear_login_session(profile_dir)
            print("[完成] 登录准备流程结束。")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
