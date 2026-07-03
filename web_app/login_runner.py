from __future__ import annotations

import argparse
import asyncio
import contextlib
import platform
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open a browser login session for the local crawler UI.")
    parser.add_argument("--login-url", required=True)
    parser.add_argument("--profile", default=".browser-profile")
    parser.add_argument("--channel", choices=("auto", "chromium", "chrome", "msedge"), default="auto")
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--headless", action="store_true")
    return parser.parse_args()


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
                print("[登录] 已尝试点击登录按钮。如遇验证码或风控，请在浏览器中手动完成。")
                break


async def main() -> int:
    args = parse_args()
    profile_dir = Path(args.profile).expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)

    if args.channel == "auto":
        channels = ["msedge", "chrome", "chromium"] if platform.system().lower() == "windows" else ["chrome", "chromium", "msedge"]
    else:
        channels = [args.channel]

    async with async_playwright() as playwright:
        context = None
        errors: list[str] = []
        for channel in channels:
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
        if args.headless:
            await page.wait_for_timeout(10_000)
        else:
            print("[等待] 请在浏览器中完成登录。登录成功后可直接回到网页开始爬取。")
            print("[提示] 这个登录窗口会保持 10 分钟，关闭窗口或等待超时都会保存 profile。")
            try:
                await page.wait_for_timeout(10 * 60 * 1000)
            except PlaywrightError:
                pass
        await context.close()
    print("[完成] 登录准备流程结束。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
