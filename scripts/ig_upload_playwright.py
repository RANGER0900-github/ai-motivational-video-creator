import asyncio
import shutil
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright


DEFAULT_COOKIES_FILE = Path("/home/meet/Downloads/cookies (2).txt")
PROFILE_URL = "https://www.instagram.com/_thecoco_club/"
DEFAULT_DB_PATH = Path("state/app.db")
DEFAULT_HASHTAGS = (
    "#motivation #mindset #discipline #selfimprovement #success "
    "#focus #reels #explorepage #viralreels #motivationdaily"
)


def pick_video() -> Path:
    outputs = Path("outputs")
    if not outputs.exists():
        raise SystemExit("outputs/ folder not found.")
    candidates = sorted(outputs.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise SystemExit("No .mp4 files found in outputs/.")
    return candidates[0]


def parse_cookie_file(path: Path) -> list[dict]:
    cookies = []
    for line in path.read_text(errors="ignore").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        domain, _include_subdomains, cookie_path, secure, expires, name, value = parts
        if "instagram.com" not in domain:
            continue
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": cookie_path,
                "expires": int(expires) if expires.isdigit() else -1,
                "httpOnly": False,
                "secure": secure.upper() == "TRUE",
                "sameSite": "Lax",
            }
        )
    return cookies


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def copy_user_data_tree(source_root: Path, profile_name: str) -> Path:
    if not source_root.exists():
        raise SystemExit(f"Missing Instagram profile root: {source_root}")
    profile_source = source_root / profile_name
    if not profile_source.exists():
        raise SystemExit(f"Missing Instagram profile {profile_name!r} under {source_root}")
    temp_root = Path(tempfile.mkdtemp(prefix="ig-profile-"))
    local_state = source_root / "Local State"
    if local_state.exists():
        shutil.copy2(local_state, temp_root / "Local State")
    profile_copy = temp_root / profile_name
    shutil.copytree(profile_source, profile_copy)
    for pattern in ("Singleton*", "lockfile", "LOCK", "lock"):
        for path in temp_root.glob(pattern):
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)
        for path in profile_copy.glob(pattern):
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)
    return temp_root


def lookup_job_metadata(video_path: Path, db_path: Path) -> dict[str, str | int | None]:
    if not db_path.exists():
        return {"job_id": None, "quote": "", "author": ""}
    basename = video_path.name
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, quote, author, output_path
            FROM jobs
            WHERE status = 'completed'
              AND output_path IS NOT NULL
              AND (output_path = ? OR output_path LIKE ?)
            ORDER BY id DESC
            LIMIT 1
            """,
            (str(video_path).replace("\\", "/"), f"%{basename}"),
        ).fetchone()
    if not row:
        return {"job_id": None, "quote": "", "author": ""}
    return {"job_id": row["id"], "quote": row["quote"] or "", "author": row["author"] or ""}


def build_instagram_caption(metadata: dict[str, str | int | None]) -> str:
    quote = str(metadata.get("quote") or "").strip()
    author = str(metadata.get("author") or "").strip()
    lines = []
    if quote:
        lines.append(quote)
    if author:
        lines.append(f"— {author}")
    if lines:
        lines.append("")
    lines.append("Save this reel and come back when you need the reminder.")
    lines.append("")
    lines.append(DEFAULT_HASHTAGS)
    return "\n".join(lines).strip()


class InstagramUploadError(RuntimeError):
    pass


class InstagramUploader:
    def __init__(self, page, debug_dir: Path, *, verbose: bool = True):
        self.page = page
        self.debug_dir = debug_dir
        self.verbose = verbose

    async def snap(self, name: str) -> None:
        await self.page.screenshot(path=str(self.debug_dir / f"{name}.png"), full_page=True)

    async def body_text(self) -> str:
        return await self.page.locator("body").inner_text()

    async def ensure_authenticated(self) -> None:
        text = (await self.body_text())[:2000]
        url = self.page.url
        markers = (
            "Get started on Instagram",
            "I already have an account",
            "Mobile number or email",
            "Log in",
        )
        if "/accounts/login" in url or any(marker in text for marker in markers):
            raise InstagramUploadError("Instagram session is not authenticated on this server. Refresh VPS cookies/storage.")

    async def click_dialog_action(self, text: str, dialog_index: int = 0) -> tuple[int, dict]:
        candidates = []
        roots = []
        dialogs = self.page.locator('div[role="dialog"]')
        if await dialogs.count() > dialog_index:
            roots.append(dialogs.nth(dialog_index))
        roots.append(self.page)
        candidate_specs = []
        for root in roots:
            candidate_specs.extend(
                [
                    root.locator(f'[role="button"]:text-is("{text}")'),
                    root.locator(f'button:text-is("{text}")'),
                    root.locator('[role="button"]').filter(has_text=text),
                    root.locator("button").filter(has_text=text),
                    root.locator(f'text="{text}"'),
                ]
            )
        for locator in candidate_specs:
            count = await locator.count()
            for i in range(count):
                try:
                    box = await locator.nth(i).bounding_box()
                    if not box or box["width"] <= 0 or box["height"] <= 0:
                        continue
                    candidates.append((box["y"], -box["x"], locator, i, box))
                except Exception:
                    continue
        for _y, _neg_x, locator, index, box in sorted(candidates, key=lambda item: (item[0], item[1])):
            try:
                await self.page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                await self.page.wait_for_timeout(1800)
                return index, box
            except Exception:
                continue
        raise InstagramUploadError(f"No dialog action found for {text!r} in dialog {dialog_index}")

    async def open_create_post(self) -> None:
        await self.page.goto("https://www.instagram.com/create/select/", wait_until="domcontentloaded")
        await self.page.wait_for_timeout(2500)
        await self.ensure_authenticated()
        if await self.page.locator('input[type="file"]').count():
            return
        await self.page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        await self.page.wait_for_timeout(2500)
        await self.ensure_authenticated()
        create_candidates = [
            self.page.locator('a[href="#"]').filter(has_text="Create"),
            self.page.locator('[role="link"]').filter(has_text="Create"),
            self.page.locator('[role="button"]').filter(has_text="Create"),
        ]
        clicked = False
        for locator in create_candidates:
            if await locator.count():
                try:
                    await locator.first.click(force=True, timeout=8000)
                    clicked = True
                    break
                except Exception:
                    continue
        if not clicked:
            raise InstagramUploadError("Instagram Create entry was not found.")
        await self.page.wait_for_timeout(1200)
        post_candidates = [
            self.page.locator('a:has-text("Post")'),
            self.page.locator('[role="menuitem"]').filter(has_text="Post"),
            self.page.locator('[role="link"]').filter(has_text="Post"),
            self.page.locator('[role="button"]').filter(has_text="Post"),
        ]
        for locator in post_candidates:
            count = await locator.count()
            for index in range(count):
                try:
                    await locator.nth(index).click(force=True, timeout=8000)
                    await self.page.wait_for_timeout(2000)
                    if await self.page.locator('input[type="file"]').count():
                        return
                except Exception:
                    continue
        raise InstagramUploadError("Instagram Post entry was not found.")

    async def attach_video(self, video_path: Path) -> None:
        file_input = self.page.locator('input[type="file"]').first
        await file_input.set_input_files(str(video_path))
        await self.page.wait_for_timeout(5000)

    async def dialog_buttons(self, dialog_index: int) -> list[tuple[int, str, dict | None]]:
        dialog = self.page.locator('div[role="dialog"]').nth(dialog_index)
        buttons = dialog.locator("button")
        data: list[tuple[int, str, dict | None]] = []
        for i in range(await buttons.count()):
            try:
                box = await buttons.nth(i).bounding_box()
                text = (await buttons.nth(i).inner_text()).strip()
                data.append((i, text, box))
            except Exception:
                data.append((i, "", None))
        return data

    async def dismiss_reels_dialog(self) -> None:
        dialogs = self.page.locator('div[role="dialog"]')
        if await dialogs.count() < 2:
            raise InstagramUploadError("Expected reels info dialog after first Next.")
        await self.click_dialog_action("OK", dialog_index=1)

    async def has_reels_dialog(self) -> bool:
        dialogs = self.page.locator('div[role="dialog"]')
        if await dialogs.count() < 2:
            return False
        text = await dialogs.nth(1).inner_text()
        return "Video posts are now shared as reels" in text

    async def verify_profile(self, label: str) -> None:
        await self.page.goto(PROFILE_URL, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(5000)
        await self.snap(label)

    async def verify_reels_tab(self, label: str) -> None:
        await self.page.goto(f"{PROFILE_URL}reels/", wait_until="domcontentloaded")
        await self.page.wait_for_timeout(5000)
        await self.snap(label)

    async def open_crop_menu(self) -> None:
        crop_toggle = self.page.locator('svg[aria-label="Select crop"]').locator("xpath=ancestor::*[@role='button' or self::button][1]")
        if await crop_toggle.count() == 0:
            raise InstagramUploadError("Crop toggle was not found in the composer.")
        await crop_toggle.first.click(force=True)
        await self.page.wait_for_timeout(1500)

    async def crop_options(self) -> list[str]:
        dialog = self.page.locator('div[role="dialog"]').first
        options = []
        for label in ("Original", "1:1", "9:16", "16:9"):
            locator = dialog.locator('[role="button"]').filter(has_text=label)
            if await locator.count():
                box = await locator.first.bounding_box()
                if box and box["width"] > 0 and box["height"] > 0:
                    options.append(label)
        return options

    async def select_crop_ratio(self, label: str) -> None:
        await self.open_crop_menu()
        options = await self.crop_options()
        if label not in options:
            raise InstagramUploadError(
                f"Crop option {label!r} was not available. Found: {', '.join(options) or 'none'}"
            )
        dialog = self.page.locator('div[role="dialog"]').first
        option = dialog.locator('[role="button"]').filter(has_text=label).first
        box = await option.bounding_box()
        if not box:
            raise InstagramUploadError(f"Crop option {label!r} had no visible bounding box.")
        await self.page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        await self.page.wait_for_timeout(1500)
        await self.open_crop_menu()
        await self.page.wait_for_timeout(800)

    async def assert_crop_menu_closed(self) -> None:
        dialog = self.page.locator('div[role="dialog"]').first
        text = await dialog.inner_text()
        if "Original" in text and "1:1" in text and "9:16" in text and "16:9" in text:
            raise InstagramUploadError("Crop menu stayed open after selecting the ratio.")

    async def fill_caption(self, caption: str) -> None:
        dialog = self.page.locator('div[role="dialog"]').first
        candidates = [
            dialog.locator('textarea[placeholder="Write a caption..."]'),
            dialog.locator('[role="textbox"][aria-label="Write a caption..."]'),
            dialog.locator('[contenteditable="true"]').filter(has_text=""),
            dialog.locator('textarea'),
        ]
        target = None
        for locator in candidates:
            if await locator.count():
                for i in range(await locator.count()):
                    box = await locator.nth(i).bounding_box()
                    if box and box["width"] > 0 and box["height"] > 0:
                        target = locator.nth(i)
                        break
            if target is not None:
                break
        if target is None:
            raise InstagramUploadError("Caption field was not found on the New reel screen.")
        await target.click(force=True)
        try:
            await target.fill(caption)
        except Exception:
            await self.page.keyboard.insert_text(caption)
        await self.page.wait_for_timeout(1200)
        content = ""
        try:
            content = (await target.input_value()).strip()
        except Exception:
            try:
                content = (await target.inner_text()).strip()
            except Exception:
                content = ""
        leading = caption.splitlines()[0].strip()[:24]
        if leading and leading not in content:
            raise InstagramUploadError("Caption text did not appear in the composer after filling.")

    async def current_state(self, label: str) -> None:
        if self.verbose:
            dialog_count = await self.page.locator('div[role="dialog"]').count()
            print(f"{label}_URL: {self.page.url}")
            print(f"{label}_TEXT: {(await self.body_text())[:1500].replace(chr(10), ' | ')}")
            print(f"{label}_DIALOGS: {dialog_count}")
        await self.snap(label.lower())

    async def latest_reel_url(self) -> str:
        await self.page.goto(f"{PROFILE_URL}reels/", wait_until="domcontentloaded")
        await self.page.wait_for_timeout(5000)
        anchors = self.page.locator('a[href*="/reel/"]')
        count = await anchors.count()
        for index in range(count):
            href = await anchors.nth(index).get_attribute("href")
            if href and "/reel/" in href:
                if href.startswith("http"):
                    return href
                return f"https://www.instagram.com{href}"
        raise InstagramUploadError("Could not find the newest reel URL on the reels tab.")


async def run_upload(
    video_path: Path,
    caption: str,
    cookies_path: Path,
    storage_path: Path,
    profile_root: Path | None,
    profile_name: str,
    debug_dir: Path,
    headed: bool,
    browser_channel: str | None,
    *,
    json_mode: bool = False,
) -> dict[str, str]:
    if profile_root is None and not cookies_path.exists():
        raise SystemExit(f"Missing cookies file: {cookies_path}")
    if not video_path.exists():
        raise SystemExit(f"Missing video: {video_path}")

    debug_dir.mkdir(parents=True, exist_ok=True)
    temp_profile_dir: Path | None = None

    async with async_playwright() as p:
        browser = None
        context = None
        if profile_root is not None:
            temp_profile_dir = copy_user_data_tree(profile_root, profile_name)
            context = await p.chromium.launch_persistent_context(
                str(temp_profile_dir),
                headless=not headed,
                viewport={"width": 1280, "height": 720},
                channel=browser_channel,
                args=[f"--profile-directory={profile_name}"],
            )
            pages = context.pages
            page = pages[0] if pages else await context.new_page()
        else:
            browser = await p.chromium.launch(headless=not headed)
            context_kwargs = {"viewport": {"width": 1280, "height": 720}}
            if storage_path.exists():
                context_kwargs["storage_state"] = str(storage_path)
            context = await browser.new_context(**context_kwargs)
            if not storage_path.exists():
                await context.add_cookies(parse_cookie_file(cookies_path))
            page = await context.new_page()
        page.set_default_timeout(30000)
        uploader = InstagramUploader(page, debug_dir, verbose=not json_mode)

        try:
            await uploader.open_create_post()
            await uploader.current_state("OPEN_POST")

            await uploader.attach_video(video_path)
            await uploader.current_state("AFTER_FILE")

            if await uploader.has_reels_dialog():
                await uploader.dismiss_reels_dialog()
                await uploader.current_state("AFTER_REELS_OK")
            else:
                await uploader.click_dialog_action("Next")
                await uploader.current_state("AFTER_FIRST_NEXT")
                if await uploader.has_reels_dialog():
                    await uploader.dismiss_reels_dialog()
                    await uploader.current_state("AFTER_REELS_OK")

            await uploader.select_crop_ratio("9:16")
            await uploader.current_state("AFTER_CROP_9_16")
            await uploader.assert_crop_menu_closed()

            await uploader.click_dialog_action("Next")
            await uploader.current_state("AFTER_SECOND_NEXT")

            try:
                await uploader.click_dialog_action("Next")
                await uploader.current_state("AFTER_THIRD_NEXT")
            except InstagramUploadError:
                pass

            await uploader.fill_caption(caption)
            await uploader.current_state("AFTER_CAPTION")

            await uploader.click_dialog_action("Share")
            await page.wait_for_timeout(12000)
            await uploader.current_state("AFTER_SHARE")

            await context.storage_state(path=str(storage_path))

            if browser is None:
                await context.close()
                context = None
                verify_context = await p.chromium.launch_persistent_context(
                    str(temp_profile_dir),
                    headless=not headed,
                    viewport={"width": 1280, "height": 1200},
                    channel=browser_channel,
                    args=[f"--profile-directory={profile_name}"],
                )
                verify_pages = verify_context.pages
                verify_page = verify_pages[0] if verify_pages else await verify_context.new_page()
            else:
                verify_kwargs = {"viewport": {"width": 1280, "height": 1200}}
                if storage_path.exists():
                    verify_kwargs["storage_state"] = str(storage_path)
                verify_context = await browser.new_context(**verify_kwargs)
                if not storage_path.exists():
                    await verify_context.add_cookies(parse_cookie_file(cookies_path))
                verify_page = await verify_context.new_page()
            verify_page.set_default_timeout(25000)
            verifier = InstagramUploader(verify_page, debug_dir, verbose=not json_mode)
            await verifier.verify_profile("verify_profile_1")
            await verifier.verify_profile("verify_profile_2")
            await verifier.verify_reels_tab("verify_reels")
            reel_url = await verifier.latest_reel_url()
            await verify_context.close()
            return {
                "reelUrl": reel_url,
                "reelPath": reel_url.replace("https://www.instagram.com", ""),
                "caption": caption,
            }
        except PlaywrightTimeoutError as exc:
            await uploader.current_state("TIMEOUT_STATE")
            raise SystemExit(f"Instagram upload timed out: {exc}") from exc
        except InstagramUploadError as exc:
            await uploader.current_state("FAILED_STATE")
            raise SystemExit(str(exc)) from exc
        finally:
            if context is not None:
                await context.close()
            if browser is not None:
                await browser.close()
            if temp_profile_dir is not None:
                shutil.rmtree(temp_profile_dir, ignore_errors=True)


async def main() -> None:
    video_path = Path(os.getenv("IG_UPLOAD_FILE") or pick_video()).resolve()
    cookies_path = Path(os.getenv("IG_COOKIES_FILE") or DEFAULT_COOKIES_FILE).resolve()
    db_path = Path(os.getenv("IG_DB_PATH") or DEFAULT_DB_PATH).resolve()
    storage_path = Path(os.getenv("IG_STORAGE_FILE", "state/ig_storage.json")).resolve()
    profile_root_raw = os.getenv("IG_PROFILE_ROOT", "").strip()
    profile_root = Path(profile_root_raw).resolve() if profile_root_raw else None
    profile_name = os.getenv("IG_PROFILE_NAME", "Default").strip() or "Default"
    browser_channel = os.getenv("IG_BROWSER_CHANNEL", "").strip() or None
    debug_dir = Path(os.getenv("IG_DEBUG_DIR", "state")).resolve()
    headed = env_flag("IG_HEADED", default=False)
    json_mode = env_flag("IG_JSON", default=False)
    metadata = lookup_job_metadata(video_path, db_path)
    caption = os.getenv("IG_CAPTION_TEXT") or build_instagram_caption(metadata)
    try:
        result = await run_upload(video_path, caption, cookies_path, storage_path, profile_root, profile_name, debug_dir, headed, browser_channel, json_mode=json_mode)
    except SystemExit as exc:
        if json_mode:
            print(json.dumps({"ok": False, "message": str(exc)}))
            return
        raise
    if json_mode:
        print(json.dumps({"ok": True, **result}))
        return
    print(f"Instagram upload flow completed for {video_path}")


if __name__ == "__main__":
    asyncio.run(main())
