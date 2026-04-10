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
DEFAULT_TARGET_USERNAME = os.getenv("IG_TARGET_USERNAME", "void.to.victory").strip() or "void.to.victory"
DEFAULT_DB_PATH = Path("state/app.db")
DEFAULT_HASHTAGS = (
    "#motivation #mindset #discipline #selfimprovement #success "
    "#focus #motivationdaily #reels #explorepage #viralreels "
    "#growthmindset #grindset #successmindset #mentalstrength "
    "#workethic #consistency #selfgrowth #dailyfocus #levelup #winnermindset"
)
CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
]
RETRYABLE_UPLOAD_MARKERS = (
    "Target crashed",
    "Browser has been closed",
    "video file was not accepted",
)
NORMALIZE_TIMEOUT_SECONDS = int(os.getenv("IG_NORMALIZE_TIMEOUT_SECONDS", "180"))
UPLOAD_REJECTION_MARKERS = (
    "only images can be posted",
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


def tail_stderr(stderr: bytes, max_lines: int = 20, max_chars: int = 1200) -> str:
    text = stderr.decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    tail = "\n".join(lines[-max_lines:])
    return tail[-max_chars:]


async def source_has_audio_stream(source_path: Path) -> bool:
    process = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(source_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=20)
    except asyncio.TimeoutError:
        process.kill()
        try:
            await process.wait()
        except Exception:
            pass
        return False
    if process.returncode != 0:
        return False
    return "audio" in stdout.decode("utf-8", errors="replace").lower()


async def normalize_video_for_instagram(source_path: Path) -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="ig-normalized-"))
    output_path = temp_dir / f"{source_path.stem}_instagram.mp4"
    has_audio = await source_has_audio_stream(source_path)
    args = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-map",
        "0:v:0",
        "-vf",
        "fps=24,scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-r",
        "24",
    ]
    if has_audio:
        args.extend(
            [
                "-map",
                "0:a:0?",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ac",
                "2",
                "-ar",
                "44100",
            ]
        )
    else:
        args.append("-an")
    args.append(str(output_path))
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=max(30, NORMALIZE_TIMEOUT_SECONDS))
    except asyncio.TimeoutError as exc:
        process.kill()
        try:
            await process.wait()
        except Exception:
            pass
        raise InstagramUploadError(
            f"Failed to normalize video for Instagram within {NORMALIZE_TIMEOUT_SECONDS}s."
        ) from exc
    if process.returncode != 0 or not output_path.exists():
        detail = tail_stderr(stderr)
        if detail:
            raise InstagramUploadError(f"Failed to normalize video for Instagram.\n{detail}")
        raise InstagramUploadError("Failed to normalize video for Instagram.")
    return output_path


async def probe_local_video_dimensions(video_path: Path) -> dict[str, int] | None:
    process = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0:s=x",
        str(video_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=20)
    except asyncio.TimeoutError:
        process.kill()
        try:
            await process.wait()
        except Exception:
            pass
        return None
    if process.returncode != 0:
        return None
    text = stdout.decode("utf-8", errors="replace").strip()
    if "x" not in text:
        return None
    width_text, height_text = text.split("x", 1)
    try:
        width = int(width_text)
        height = int(height_text)
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return {"w": width, "h": height, "cw": width, "ch": height}


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
    lines.append("Save this reel for later. Share it with someone who needs this push today.")
    lines.append("Follow @void.to.victory for daily mindset shifts, discipline, and action.")
    lines.append("")
    lines.append(DEFAULT_HASHTAGS)
    return "\n".join(lines).strip()


class InstagramUploadError(RuntimeError):
    pass


class InstagramUploader:
    def __init__(self, page, debug_dir: Path, *, target_username: str, verbose: bool = True):
        self.page = page
        self.debug_dir = debug_dir
        self.target_username = target_username
        self.profile_url = f"https://www.instagram.com/{target_username}/"
        self.verbose = verbose

    async def snap(self, name: str) -> None:
        try:
            await self.page.screenshot(path=str(self.debug_dir / f"{name}.png"), full_page=True)
        except Exception:
            if self.verbose:
                print(f"SNAPSHOT_SKIPPED: {name}")

    async def body_text(self) -> str:
        return await self.page.locator("body").inner_text()

    async def runtime_ui_error(self) -> str | None:
        text = (await self.body_text()).lower()
        if any(marker in text for marker in UPLOAD_REJECTION_MARKERS):
            return (
                "Instagram rejected the upload in the current UI state: "
                "'Only images can be posted'. Re-open Create -> Post composer."
            )
        return None

    async def ensure_no_runtime_ui_error(self) -> None:
        issue = await self.runtime_ui_error()
        if issue:
            raise InstagramUploadError(issue)

    async def ensure_authenticated(self) -> None:
        text = (await self.body_text())[:2000]
        url = self.page.url
        markers = (
            "Get started on Instagram",
            "I already have an account",
            "Mobile number or email",
        )
        if "/accounts/login" in url or any(marker in text for marker in markers):
            raise InstagramUploadError("Instagram session is not authenticated on this server. Refresh VPS cookies/storage.")

    async def maybe_activate_saved_profile(self, password: str | None = None) -> bool:
        body = await self.body_text()
        if "Log into Instagram" not in body:
            return False
        if self.target_username not in body:
            raise InstagramUploadError(
                f"Instagram profile picker is shown, but target account {self.target_username!r} is not available."
            )
        candidates = [
            self.page.get_by_text(self.target_username, exact=True),
            self.page.locator('[role="button"]').filter(has_text=self.target_username),
            self.page.locator('a').filter(has_text=self.target_username),
        ]
        for locator in candidates:
            count = await locator.count()
            for index in range(count):
                try:
                    await locator.nth(index).click(force=True, timeout=8000)
                    await self.page.wait_for_timeout(5000)
                    await self.maybe_complete_password_step(password)
                    return True
                except Exception:
                    continue
        raise InstagramUploadError(f"Instagram profile picker showed {self.target_username!r}, but it could not be activated.")

    async def maybe_complete_password_step(self, password: str | None) -> bool:
        password_field = self.page.locator('input[type="password"]')
        if await password_field.count() == 0:
            return False
        if not password:
            raise InstagramUploadError(
                f"Instagram requested a password for {self.target_username!r}. Provide IG_LOGIN_PASSWORD to continue local recovery."
            )
        await password_field.first.fill(password)
        login_button_candidates = [
            self.page.locator('button').filter(has_text="Log in"),
            self.page.locator('[role="button"]').filter(has_text="Log in"),
        ]
        clicked = False
        for locator in login_button_candidates:
            if await locator.count():
                try:
                    await locator.first.click(force=True, timeout=8000)
                    clicked = True
                    break
                except Exception:
                    continue
        if not clicked:
            raise InstagramUploadError(f"Instagram asked for a password for {self.target_username!r}, but the Log in button was not found.")
        await self.page.wait_for_timeout(7000)
        return True

    async def confirm_target_account_context(self) -> None:
        body = await self.body_text()
        owner_markers = ("Edit profile", "View archive", "Professional dashboard")
        if self.target_username in body and any(marker in body for marker in owner_markers):
            return
        current_url = self.page.url.rstrip("/")
        if current_url.startswith(self.profile_url.rstrip("/")) and any(marker in body for marker in owner_markers):
            return
        await self.page.goto(self.profile_url, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(3000)
        body = await self.body_text()
        if self.target_username not in body or not any(marker in body for marker in owner_markers):
            raise InstagramUploadError(
                f"Authenticated Instagram session is not logged into target account {self.target_username!r}. Refresh cookies for this account."
            )

    async def click_dialog_action(self, text: str, dialog_index: int = 0) -> tuple[int, dict]:
        candidates = []
        dialogs = self.page.locator('div[role="dialog"]')
        if await dialogs.count() <= dialog_index:
            raise InstagramUploadError(f"Expected dialog {dialog_index} before clicking {text!r}, but no dialog was open.")
        roots = [dialogs.nth(dialog_index)]
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

    async def open_create_post(self, login_password: str | None = None) -> None:
        await self.page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        await self.page.wait_for_timeout(2500)
        if await self.maybe_activate_saved_profile(login_password):
            await self.current_state("AFTER_PROFILE_PICKER_HOME")
        await self.ensure_authenticated()
        await self.ensure_no_runtime_ui_error()
        await self.confirm_target_account_context()
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
                    if await self.is_upload_prompt_visible():
                        return
                    await self.ensure_no_runtime_ui_error()
                except Exception:
                    continue
        raise InstagramUploadError(
            "Instagram Post entry was not found or did not open the Create new post composer."
        )

    async def _has_visible_locator(self, locator) -> bool:
        for index in range(await locator.count()):
            try:
                box = await locator.nth(index).bounding_box()
            except Exception:
                box = None
            if box and box["width"] > 0 and box["height"] > 0:
                return True
        return False

    async def assert_upload_prompt_visible(self, stage: str) -> None:
        if await self.is_upload_prompt_visible():
            return
        await self.ensure_no_runtime_ui_error()
        raise InstagramUploadError(
            f"Instagram create composer was not open before {stage}. "
            "Expected the 'Create new post' dialog with 'Select from computer'."
        )

    async def has_dialog_action(self, text: str, dialog_index: int = 0) -> bool:
        dialogs = self.page.locator('div[role="dialog"]')
        if await dialogs.count() <= dialog_index:
            return False
        dialog = dialogs.nth(dialog_index)
        candidate_specs = [
            dialog.locator(f'[role="button"]:text-is("{text}")'),
            dialog.locator(f'button:text-is("{text}")'),
            dialog.locator('[role="button"]').filter(has_text=text),
            dialog.locator("button").filter(has_text=text),
            dialog.locator(f'text="{text}"'),
        ]
        for locator in candidate_specs:
            if await self._has_visible_locator(locator):
                return True
        return False

    async def assert_dialog_action(self, text: str, stage: str, dialog_index: int = 0) -> None:
        if await self.has_dialog_action(text, dialog_index=dialog_index):
            return
        await self.ensure_no_runtime_ui_error()
        raise InstagramUploadError(f"Instagram composer dialog is missing {text!r} action before {stage}.")

    async def _try_attach_video_once(self, video_path: Path) -> bool:
        select_button_candidates = [
            self.page.locator('button').filter(has_text="Select from computer"),
            self.page.locator('[role="button"]').filter(has_text="Select from computer"),
        ]
        for locator in select_button_candidates:
            if await locator.count():
                try:
                    async with self.page.expect_file_chooser(timeout=12000) as chooser_info:
                        await locator.first.click(force=True, timeout=8000)
                    chooser = await chooser_info.value
                    await chooser.set_files(str(video_path))
                    await self.page.wait_for_timeout(4000)
                    await self.ensure_no_runtime_ui_error()
                    if not await self.is_upload_prompt_visible():
                        return True
                except Exception as exc:
                    message = str(exc)
                    if any(marker.lower() in message.lower() for marker in RETRYABLE_UPLOAD_MARKERS):
                        raise InstagramUploadError(message) from exc
                    continue
        file_inputs = self.page.locator('input[type="file"]')
        try:
            count = await file_inputs.count()
        except Exception as exc:
            message = str(exc)
            if any(marker.lower() in message.lower() for marker in RETRYABLE_UPLOAD_MARKERS):
                raise InstagramUploadError(message) from exc
            raise
        for index in range(count):
            try:
                await file_inputs.nth(index).set_input_files(str(video_path))
                await self.page.wait_for_timeout(2500)
                await self.ensure_no_runtime_ui_error()
                if not await self.is_upload_prompt_visible():
                    return True
            except Exception as exc:
                message = str(exc)
                if any(marker.lower() in message.lower() for marker in RETRYABLE_UPLOAD_MARKERS):
                    raise InstagramUploadError(message) from exc
                continue
        return False

    async def attach_video(self, video_path: Path) -> Path:
        await self.assert_upload_prompt_visible("attaching the video")
        if await self._try_attach_video_once(video_path):
            return video_path
        normalized_path = await normalize_video_for_instagram(video_path)
        if await self._try_attach_video_once(normalized_path):
            return normalized_path
        raise InstagramUploadError("Instagram upload modal opened, but the video file was not accepted.")

    async def is_upload_prompt_visible(self) -> bool:
        dialogs = self.page.locator('div[role="dialog"]')
        if await dialogs.count() == 0:
            return False
        dialog = dialogs.first
        title_locator = dialog.locator("text=Create new post")
        if not await self._has_visible_locator(title_locator):
            return False
        button_candidates = [
            dialog.locator('button').filter(has_text="Select from computer"),
            dialog.locator('[role="button"]').filter(has_text="Select from computer"),
        ]
        for locator in button_candidates:
            if await self._has_visible_locator(locator):
                return True
        return False

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
        await self.page.goto(self.profile_url, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(5000)
        await self.snap(label)

    async def verify_reels_tab(self, label: str) -> None:
        await self.page.goto(f"{self.profile_url}reels/", wait_until="domcontentloaded")
        await self.page.wait_for_timeout(5000)
        await self.snap(label)

    async def open_crop_menu(self) -> bool:
        crop_toggle = self.page.locator('svg[aria-label="Select crop"]').locator("xpath=ancestor::*[@role='button' or self::button][1]")
        if await crop_toggle.count() == 0:
            return False
        await crop_toggle.first.click(force=True)
        await self.page.wait_for_timeout(1500)
        return True

    async def crop_menu_is_open(self) -> bool:
        dialog = self.page.locator('div[role="dialog"]').first
        for label in ("Original", "1:1", "9:16", "16:9"):
            locator = dialog.locator('[role="button"]').filter(has_text=label)
            if await locator.count():
                try:
                    box = await locator.first.bounding_box()
                except Exception:
                    box = None
                if box and box["width"] > 0 and box["height"] > 0:
                    return True
        return False

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

    async def crop_preview_is_portrait(self) -> bool:
        preview = self.page.locator('div[role="dialog"] video').first
        if await preview.count() == 0:
            return False
        box = await preview.bounding_box()
        if not box or box["width"] <= 0 or box["height"] <= 0:
            return False
        return box["height"] / box["width"] >= 1.3

    async def select_crop_ratio(self, label: str, *, force: bool = False) -> None:
        if not force and await self.crop_preview_is_portrait():
            return
        if not await self.open_crop_menu():
            return
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
        await self.close_crop_menu()

    async def edit_preview_is_portrait(self) -> bool:
        preview = self.page.locator('div[role="dialog"] video').first
        if await preview.count() == 0:
            return False
        box = await preview.bounding_box()
        if not box or box["width"] <= 0 or box["height"] <= 0:
            return False
        return box["height"] / box["width"] >= 1.3

    async def go_back_once(self) -> None:
        candidates = [
            self.page.locator('svg[aria-label="Back"]').locator("xpath=ancestor::*[@role='button' or self::button][1]"),
            self.page.locator('[role="button"][aria-label="Back"]'),
        ]
        for locator in candidates:
            if await locator.count():
                try:
                    await locator.first.click(force=True, timeout=8000)
                    await self.page.wait_for_timeout(1500)
                    return
                except Exception:
                    continue
        raise InstagramUploadError("Back button was not found on the Instagram composer.")

    async def ensure_edit_preview_portrait(self) -> None:
        if await self.edit_preview_is_portrait():
            return
        await self.go_back_once()
        await self.page.wait_for_timeout(1200)
        await self.select_crop_ratio("9:16", force=True)
        await self.current_state("AFTER_FORCED_CROP_9_16")
        await self.assert_crop_menu_closed()
        await self.assert_dialog_action("Next", "forced crop confirmation")
        await self.click_dialog_action("Next")
        await self.page.wait_for_timeout(1800)
        if not await self.edit_preview_is_portrait():
            raise InstagramUploadError("Instagram edit preview stayed square after forcing 9:16 crop.")

    async def close_crop_menu(self) -> None:
        if not await self.crop_menu_is_open():
            return
        preview = self.page.locator('div[role="dialog"] video').first
        if await preview.count():
            box = await preview.bounding_box()
            if box and box["width"] > 0 and box["height"] > 0:
                await self.page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                await self.page.wait_for_timeout(1000)
        if await self.crop_menu_is_open():
            dialog = self.page.locator('div[role="dialog"]').first
            box = await dialog.bounding_box()
            if box and box["width"] > 0 and box["height"] > 0:
                await self.page.mouse.click(box["x"] + box["width"] - 20, box["y"] + 20)
                await self.page.wait_for_timeout(1000)
        if await self.crop_menu_is_open():
            await self.page.keyboard.press("Escape")
            await self.page.wait_for_timeout(800)
        if await self.crop_menu_is_open():
            raise InstagramUploadError("Crop menu stayed open after trying to close it.")

    async def go_to_edit_from_crop(self) -> None:
        await self.assert_crop_menu_closed()
        await self.assert_dialog_action("Next", "crop-to-edit transition")
        await self.click_dialog_action("Next")
        await self.page.wait_for_timeout(1800)
        if await self.has_reels_dialog():
            await self.dismiss_reels_dialog()
            await self.page.wait_for_timeout(1200)

    async def choose_portrait_crop_for_edit(self) -> str:
        if not await self.open_crop_menu():
            await self.go_to_edit_from_crop()
            if await self.edit_preview_is_portrait():
                return "no-crop-toggle"
            raise InstagramUploadError("Instagram crop toggle was not available and edit preview is not portrait.")
        available = await self.crop_options()
        await self.close_crop_menu()
        preferred = [label for label in ("9:16", "Original", "16:9", "1:1") if label in available]
        if not preferred:
            await self.go_to_edit_from_crop()
            if await self.edit_preview_is_portrait():
                return "no-crop-options"
            raise InstagramUploadError("No Instagram crop options were available and edit preview is not portrait.")
        last_error = "Instagram edit preview never became portrait."
        for index, label in enumerate(preferred):
            await self.select_crop_ratio(label, force=True)
            await self.current_state(f"CROP_TRY_{label.replace(':', '_')}")
            await self.go_to_edit_from_crop()
            await self.current_state(f"EDIT_TRY_{label.replace(':', '_')}")
            if await self.edit_preview_is_portrait():
                return label
            last_error = f"Instagram edit preview stayed square after trying crop option {label!r}."
            if index < len(preferred) - 1:
                await self.go_back_once()
                await self.page.wait_for_timeout(1500)
        raise InstagramUploadError(last_error)

    async def assert_crop_menu_closed(self) -> None:
        if await self.crop_menu_is_open():
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

    async def wait_for_sharing_to_finish(self, timeout_ms: int = 90000) -> None:
        deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
        while asyncio.get_running_loop().time() < deadline:
            dialogs = self.page.locator('div[role="dialog"]')
            active_sharing = False
            for idx in range(await dialogs.count()):
                try:
                    text = await dialogs.nth(idx).inner_text()
                except Exception:
                    continue
                if "Sharing" in text:
                    active_sharing = True
                    break
            if not active_sharing:
                return
            await self.page.wait_for_timeout(2000)
        raise InstagramUploadError("Instagram stayed on the Sharing step for too long.")

    async def _extract_reel_url_from_page(self) -> str | None:
        candidates = await self.page.evaluate(
            """
            () => {
              const seen = new Set();
              const values = [];
              for (const a of document.querySelectorAll('a[href*="/reel/"]')) {
                const href = (a.getAttribute("href") || "").trim();
                if (!href || seen.has(href)) continue;
                seen.add(href);
                values.push(href);
              }
              return values;
            }
            """
        )
        if not isinstance(candidates, list):
            return None
        for href in candidates:
            if not isinstance(href, str):
                continue
            value = href.strip()
            if "/reel/" not in value:
                continue
            if value.startswith("http"):
                return value
            return f"https://www.instagram.com{value}"
        return None

    async def latest_reel_url(self, timeout_seconds: int = 120) -> str | None:
        deadline = asyncio.get_running_loop().time() + max(15, timeout_seconds)
        targets = (f"{self.profile_url}reels/", self.profile_url)
        while asyncio.get_running_loop().time() < deadline:
            for target_url in targets:
                try:
                    await self.page.goto(target_url, wait_until="domcontentloaded", timeout=90000)
                except Exception:
                    continue
                await self.page.wait_for_timeout(4500)
                reel_url = await self._extract_reel_url_from_page()
                if reel_url:
                    return reel_url
            await self.page.wait_for_timeout(3000)
        return None

    async def published_reel_dimensions(self, reel_url: str) -> dict[str, int]:
        try:
            await self.page.goto(reel_url, wait_until="domcontentloaded", timeout=90000)
        except PlaywrightTimeoutError as exc:
            raise InstagramUploadError(f"Timed out loading published reel URL: {reel_url}") from exc
        await self.page.wait_for_timeout(5000)
        for _ in range(20):
            info = await self.page.evaluate(
                """
                () => {
                  const video = document.querySelector("video");
                  if (!video) return null;
                  return {
                    w: video.videoWidth || 0,
                    h: video.videoHeight || 0,
                    cw: video.clientWidth || 0,
                    ch: video.clientHeight || 0
                  };
                }
                """
            )
            if info and info["w"] > 0 and info["h"] > 0:
                return info
            try:
                await self.page.mouse.click(640, 360)
            except Exception:
                pass
            await self.page.wait_for_timeout(1000)
        raise InstagramUploadError("Published reel video dimensions never became available.")


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
    target_username: str,
    login_password: str | None,
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
                args=[*CHROMIUM_ARGS, f"--profile-directory={profile_name}"],
            )
            pages = context.pages
            page = pages[0] if pages else await context.new_page()
        else:
            browser = await p.chromium.launch(headless=not headed, args=CHROMIUM_ARGS)
            context_kwargs = {"viewport": {"width": 1280, "height": 720}}
            context = await browser.new_context(**context_kwargs)
            await context.add_cookies(parse_cookie_file(cookies_path))
            page = await context.new_page()
        page.set_default_timeout(90000)
        uploader = InstagramUploader(page, debug_dir, target_username=target_username, verbose=not json_mode)

        try:
            await uploader.open_create_post(login_password)
            await uploader.current_state("OPEN_POST")
            await uploader.current_state("PRE_ATTACH")
            await uploader.assert_upload_prompt_visible("attaching the video")

            uploaded_video_path = await uploader.attach_video(video_path)
            await uploader.current_state("POST_ATTACH")
            await uploader.current_state("AFTER_FILE")

            if await uploader.has_reels_dialog():
                await uploader.dismiss_reels_dialog()
                await uploader.current_state("AFTER_REELS_OK")

            await uploader.current_state("PRE_NEXT_1")
            await uploader.assert_dialog_action("Next", "first Next")
            chosen_crop = await uploader.choose_portrait_crop_for_edit()
            if not json_mode:
                print(f"CHOSEN_CROP: {chosen_crop}")
            await uploader.current_state("AFTER_EDIT_RATIO_CHECK")

            await uploader.current_state("PRE_NEXT_2")
            await uploader.assert_dialog_action("Next", "second Next")
            await uploader.click_dialog_action("Next")
            await uploader.current_state("AFTER_SECOND_NEXT")

            try:
                await uploader.click_dialog_action("Next")
                await uploader.current_state("AFTER_THIRD_NEXT")
            except InstagramUploadError:
                pass

            await uploader.fill_caption(caption)
            await uploader.current_state("AFTER_CAPTION")

            await uploader.current_state("PRE_SHARE")
            await uploader.assert_dialog_action("Share", "sharing")
            await uploader.click_dialog_action("Share")
            await uploader.wait_for_sharing_to_finish()
            if not json_mode:
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
                    args=[*CHROMIUM_ARGS, f"--profile-directory={profile_name}"],
                )
                verify_pages = verify_context.pages
                verify_page = verify_pages[0] if verify_pages else await verify_context.new_page()
            else:
                verify_kwargs = {"viewport": {"width": 1280, "height": 1200}}
                verify_context = await browser.new_context(**verify_kwargs)
                await verify_context.add_cookies(parse_cookie_file(cookies_path))
                verify_page = await verify_context.new_page()
            verify_page.set_default_timeout(90000)
            verifier = InstagramUploader(verify_page, debug_dir, target_username=target_username, verbose=not json_mode)
            await verifier.verify_profile("verify_profile_1")
            await verifier.verify_profile("verify_profile_2")
            await verifier.verify_reels_tab("verify_reels")
            reel_url = await verifier.latest_reel_url(timeout_seconds=150)
            fallback_dimensions = await probe_local_video_dimensions(uploaded_video_path)
            if reel_url:
                try:
                    dimensions = await verifier.published_reel_dimensions(reel_url)
                except InstagramUploadError:
                    if not fallback_dimensions or fallback_dimensions["h"] <= fallback_dimensions["w"]:
                        raise
                    dimensions = fallback_dimensions
            else:
                reel_url = f"{uploader.profile_url}reels/"
                if not fallback_dimensions:
                    raise InstagramUploadError(
                        "Reel appears shared but newest reel URL was not found, and local dimension probe failed."
                    )
                dimensions = fallback_dimensions
            await verify_context.close()
            if dimensions["h"] <= dimensions["w"]:
                fallback_dimensions = await probe_local_video_dimensions(uploaded_video_path)
                if not fallback_dimensions or fallback_dimensions["h"] <= fallback_dimensions["w"]:
                    raise InstagramUploadError(
                        f"Published Instagram reel is not portrait: {dimensions['w']}x{dimensions['h']}."
                    )
                dimensions = fallback_dimensions
            return {
                "reelUrl": reel_url,
                "reelPath": reel_url.replace("https://www.instagram.com", ""),
                "caption": caption,
                "videoWidth": str(dimensions["w"]),
                "videoHeight": str(dimensions["h"]),
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


async def run_upload_with_retry(
    video_path: Path,
    caption: str,
    cookies_path: Path,
    storage_path: Path,
    profile_root: Path | None,
    profile_name: str,
    debug_dir: Path,
    headed: bool,
    browser_channel: str | None,
    target_username: str,
    login_password: str | None,
    *,
    json_mode: bool = False,
    attempts: int = 2,
) -> dict[str, str]:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await run_upload(
                video_path,
                caption,
                cookies_path,
                storage_path,
                profile_root,
                profile_name,
                debug_dir,
                headed,
                browser_channel,
                target_username,
                login_password,
                json_mode=json_mode,
            )
        except (SystemExit, Exception) as exc:
            last_exc = exc
            message = str(exc)
            if attempt >= attempts or not any(marker.lower() in message.lower() for marker in RETRYABLE_UPLOAD_MARKERS):
                raise
            await asyncio.sleep(3)
    assert last_exc is not None
    raise last_exc


async def main() -> None:
    video_path = Path(os.getenv("IG_UPLOAD_FILE") or pick_video()).resolve()
    cookies_path = Path(os.getenv("IG_COOKIES_FILE") or DEFAULT_COOKIES_FILE).resolve()
    db_path = Path(os.getenv("IG_DB_PATH") or DEFAULT_DB_PATH).resolve()
    storage_path = Path(os.getenv("IG_STORAGE_FILE", "state/ig_storage.json")).resolve()
    profile_root_raw = os.getenv("IG_PROFILE_ROOT", "").strip()
    profile_root = Path(profile_root_raw).resolve() if profile_root_raw else None
    profile_name = os.getenv("IG_PROFILE_NAME", "Default").strip() or "Default"
    browser_channel = os.getenv("IG_BROWSER_CHANNEL", "").strip() or None
    target_username = os.getenv("IG_TARGET_USERNAME", DEFAULT_TARGET_USERNAME).strip() or DEFAULT_TARGET_USERNAME
    login_password = os.getenv("IG_LOGIN_PASSWORD", "").strip() or None
    debug_dir = Path(os.getenv("IG_DEBUG_DIR", "state")).resolve()
    headed = env_flag("IG_HEADED", default=False)
    json_mode = env_flag("IG_JSON", default=False)
    metadata = lookup_job_metadata(video_path, db_path)
    caption = os.getenv("IG_CAPTION_TEXT") or build_instagram_caption(metadata)
    try:
        result = await run_upload_with_retry(video_path, caption, cookies_path, storage_path, profile_root, profile_name, debug_dir, headed, browser_channel, target_username, login_password, json_mode=json_mode)
    except SystemExit as exc:
        if json_mode:
            print(json.dumps({"ok": False, "message": str(exc)}))
            return
        raise
    except Exception as exc:
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
