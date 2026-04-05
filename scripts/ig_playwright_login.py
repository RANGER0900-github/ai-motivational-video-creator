import asyncio
import os
import re
from pathlib import Path

from playwright.async_api import async_playwright


LOGIN_URL = "https://www.instagram.com/accounts/login/"


async def main() -> None:
    username = os.getenv("IG_USERNAME")
    password = os.getenv("IG_PASSWORD")
    if not username or not password:
        raise SystemExit("Set IG_USERNAME and IG_PASSWORD in the environment.")

    out_dir = Path("state")
    out_dir.mkdir(parents=True, exist_ok=True)
    storage_path = out_dir / "ig_storage.json"
    cookies_path = out_dir / "ig_cookies.json"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        # Try to accept cookies if the banner is present.
        for label in ("Allow all cookies", "Accept all", "Allow all"):
            try:
                await page.get_by_role("button", name=label).click(timeout=2000)
                break
            except Exception:
                pass

        try:
            await page.wait_for_selector("input", timeout=20000)
        except Exception:
            debug_html = out_dir / "ig_login_debug.html"
            debug_png = out_dir / "ig_login_debug.png"
            debug_html.write_text(await page.content(), encoding="utf-8")
            await page.screenshot(path=str(debug_png), full_page=True)
            raise SystemExit(
                f"Login form not found. Saved debug files: {debug_html} and {debug_png}"
            )
        username_input = page.locator('input[type="text"]').first
        password_input = page.locator('input[type="password"]').first
        await username_input.wait_for(timeout=20000)
        await password_input.wait_for(timeout=20000)
        await username_input.fill(username)
        await password_input.fill(password)
        try:
            await page.get_by_role("button", name="Log in", exact=True).click()
        except Exception:
            await page.get_by_role("button", name="Log In", exact=True).click()

        # Wait for either a successful login or a checkpoint/challenge.
        await page.wait_for_timeout(5000)
        current = page.url
        if "challenge" in current or "checkpoint" in current:
            await context.storage_state(path=str(storage_path))
            cookies = await context.cookies()
            cookies_path.write_text(str(cookies), encoding="utf-8")
            raise SystemExit(
                f"Instagram challenge detected at {current}. "
                "Cookies saved, but login not fully completed."
            )

        # If login succeeded, Instagram redirects to feed or home.
        await page.wait_for_timeout(3000)
        await context.storage_state(path=str(storage_path))
        cookies = await context.cookies()
        cookies_path.write_text(str(cookies), encoding="utf-8")
        await browser.close()
        print(f"Saved storage to {storage_path} and cookies to {cookies_path}")


if __name__ == "__main__":
    asyncio.run(main())
