from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import AppConfig
from .models import JobDetail

logger = logging.getLogger(__name__)

INSTAGRAM_DESCRIPTION_VERSION = "v3"
UPLOAD_RETRY_COOLDOWN_SECONDS = 300
INSTAGRAM_HASHTAG_POOL = [
    "motivation",
    "mindset",
    "discipline",
    "selfimprovement",
    "success",
    "focus",
    "motivationdaily",
    "reels",
    "explorepage",
    "viralreels",
    "growthmindset",
    "grindset",
    "successmindset",
    "mentalstrength",
    "workethic",
    "consistency",
    "selfgrowth",
    "hardtruths",
    "dailyfocus",
    "levelup",
    "mindsetshift",
    "winnermindset",
]


class InstagramUploadError(RuntimeError):
    pass


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_utc_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_instagram_caption(quote: str, author: str | None) -> str:
    quote_text = (quote or "").strip()
    author_text = (author or "").strip()
    tags = " ".join(f"#{tag}" for tag in INSTAGRAM_HASHTAG_POOL[:18])
    lines: list[str] = []
    if quote_text:
        lines.append(quote_text)
    if author_text:
        lines.append(f"— {author_text}")
    if lines:
        lines.append("")
    lines.append("Save this reel for later. Share it with someone who needs this push today.")
    lines.append("Follow @void.to.victory for daily mindset shifts, discipline, and action.")
    lines.append("")
    lines.append(tags)
    return "\n".join(lines).strip()


@dataclass(slots=True)
class InstagramUploadResult:
    reel_url: str
    reel_path: str
    caption: str
    video_width: int
    video_height: int


class InstagramQueueStore:
    def __init__(self, config: AppConfig):
        self.config = config
        self.path = config.instagram_queue_json
        self._lock = threading.Lock()

    def _default_state(self) -> dict[str, Any]:
        return {
            "version": 1,
            "description_version": INSTAGRAM_DESCRIPTION_VERSION,
            "auth_blocked": False,
            "auth_blocked_at": None,
            "blocked_reason": None,
            "blocked_at": None,
            "items": [],
        }

    def _write(self, data: dict[str, Any]) -> None:
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")
        temp_path.replace(self.path)

    def _load_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            data = self._default_state()
            self._write(data)
            return data
        return self._normalize_unlocked(json.loads(self.path.read_text(encoding="utf-8")), persist=False)

    def load(self) -> dict[str, Any]:
        with self._lock:
            data = self._load_unlocked()
            self._write(data)
            return data

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(self.load())

    def recover_stale_state(self) -> dict[str, Any]:
        with self._lock:
            data = self._normalize_unlocked(self._load_unlocked(), persist=False, recover_uploading=True)
            self._write(data)
            return deepcopy(data)

    def _normalize_unlocked(
        self,
        data: dict[str, Any],
        *,
        persist: bool,
        recover_uploading: bool = False,
    ) -> dict[str, Any]:
        changed = False
        defaults = self._default_state()
        for key, value in defaults.items():
            if key not in data:
                data[key] = deepcopy(value)
                changed = True
        data["description_version"] = INSTAGRAM_DESCRIPTION_VERSION
        items = data.setdefault("items", [])
        for item in items:
            if item.get("caption") in {None, ""} or data.get("description_version") != INSTAGRAM_DESCRIPTION_VERSION:
                item["caption"] = build_instagram_caption(str(item.get("quote") or ""), str(item.get("author") or ""))
                changed = True
            if "hashtags" not in item or not item.get("hashtags") or data.get("description_version") != INSTAGRAM_DESCRIPTION_VERSION:
                item["hashtags"] = list(INSTAGRAM_HASHTAG_POOL[:18])
                changed = True
            if "video_width" not in item:
                item["video_width"] = None
                changed = True
            if "video_height" not in item:
                item["video_height"] = None
                changed = True
            if item.get("instagram_status") == "auth_blocked":
                item["instagram_status"] = "blocked"
                data["blocked_reason"] = data.get("blocked_reason") or "auth"
                data["auth_blocked"] = True
                changed = True
            if recover_uploading and item.get("instagram_status") == "uploading":
                item["instagram_status"] = "failed"
                item["last_error"] = "Recovered stale Instagram upload after bot restart"
                changed = True
        if data.get("auth_blocked") and not data.get("blocked_reason"):
            data["blocked_reason"] = "auth"
            changed = True
        if persist and changed:
            self._write(data)
        return data

    def status_summary(self) -> dict[str, Any]:
        data = self.load()
        items = data["items"]
        return {
            "pending": sum(1 for item in items if item.get("instagram_status") in {"pending", "uploading", "blocked"}),
            "uploaded": sum(1 for item in items if item.get("instagram_status") == "uploaded"),
            "failed": sum(1 for item in items if item.get("instagram_status") == "failed"),
            "blocked": bool(data.get("blocked_reason")) or bool(data.get("auth_blocked")),
            "blocked_reason": str(data.get("blocked_reason") or ("auth" if data.get("auth_blocked") else "")),
        }

    def get_item(self, job_id: int) -> dict[str, Any] | None:
        data = self.load()
        for item in data["items"]:
            if int(item.get("job_id")) == int(job_id):
                return deepcopy(item)
        return None

    def enqueue_job(self, job: JobDetail, *, instagram_enabled_for_origin: bool) -> dict[str, Any]:
        with self._lock:
            data = self._load_unlocked()
            blocked = bool(data.get("blocked_reason")) or bool(data.get("auth_blocked"))
            items = data["items"]
            existing = next((item for item in items if int(item.get("job_id")) == int(job.id)), None)
            if existing:
                existing["output_path"] = job.output_path
                existing["current_filename"] = job.output_path
                existing["chat_id"] = job.chat_id
                if existing.get("instagram_status") == "failed":
                    existing["instagram_status"] = "blocked" if blocked else "pending"
                    existing["attempt_count"] = 0
                    existing["last_attempt_at"] = None
                    existing["last_error"] = None
                self._write(data)
                return deepcopy(existing)
            item = {
                "job_id": job.id,
                "chat_id": job.chat_id,
                "output_path": job.output_path,
                "current_filename": job.output_path,
                "created_at": job.created_at.isoformat(),
                "telegram_sent_at": job.delivered_at.isoformat() if job.delivered_at else utcnow_iso(),
                "telegram_message_id": job.telegram_message_id,
                "instagram_enabled_for_origin": instagram_enabled_for_origin,
                "instagram_status": "blocked" if blocked else "pending",
                "instagram_url": None,
                "instagram_reel_path": None,
                "quote": job.quote,
                "author": job.author,
                "caption": build_instagram_caption(job.quote, job.author),
                "hashtags": list(INSTAGRAM_HASHTAG_POOL[:18]),
                "attempt_count": 0,
                "last_attempt_at": None,
                "last_error": None,
                "uploaded_at": None,
                "video_width": None,
                "video_height": None,
            }
            items.append(item)
            self._write(data)
            return deepcopy(item)

    def prepare_manual_retry(self, job: JobDetail) -> dict[str, Any]:
        with self._lock:
            data = self._load_unlocked()
            item = next((entry for entry in data["items"] if int(entry.get("job_id")) == int(job.id)), None)
            if item is None:
                blocked = bool(data.get("blocked_reason")) or bool(data.get("auth_blocked"))
                item = {
                    "job_id": job.id,
                    "chat_id": job.chat_id,
                    "output_path": job.output_path,
                    "current_filename": job.output_path,
                    "created_at": job.created_at.isoformat(),
                    "telegram_sent_at": job.delivered_at.isoformat() if job.delivered_at else utcnow_iso(),
                    "telegram_message_id": job.telegram_message_id,
                    "instagram_enabled_for_origin": False,
                    "instagram_status": "blocked" if blocked else "pending",
                    "instagram_url": None,
                    "instagram_reel_path": None,
                    "quote": job.quote,
                    "author": job.author,
                    "caption": build_instagram_caption(job.quote, job.author),
                    "hashtags": list(INSTAGRAM_HASHTAG_POOL[:18]),
                    "attempt_count": 0,
                    "last_attempt_at": None,
                    "last_error": None,
                    "uploaded_at": None,
                    "video_width": None,
                    "video_height": None,
                }
                data["items"].append(item)
                self._write(data)
                return deepcopy(item)

            item["output_path"] = job.output_path
            item["current_filename"] = job.output_path
            item["chat_id"] = job.chat_id

            global_block_reason = str(data.get("blocked_reason") or "")
            if str(item.get("instagram_status")) != "uploaded":
                if global_block_reason == "auth" or data.get("auth_blocked"):
                    item["instagram_status"] = "blocked"
                    if not item.get("last_error"):
                        item["last_error"] = "Instagram auth is blocked on VPS; refresh cookies/session."
                else:
                    if global_block_reason:
                        data["blocked_reason"] = None
                        data["blocked_at"] = None
                        data["auth_blocked"] = False
                    item["instagram_status"] = "pending"
                    item["attempt_count"] = 0
                    item["last_attempt_at"] = None
                    item["last_error"] = None

            self._write(data)
            return deepcopy(item)

    def recover_stalled_uploading(self, stale_after_seconds: int) -> list[int]:
        now = datetime.now(timezone.utc)
        stale_job_ids: list[int] = []
        changed = False
        with self._lock:
            data = self._load_unlocked()
            for item in data.get("items", []):
                if str(item.get("instagram_status")) != "uploading":
                    continue
                last_attempt = parse_utc_iso(str(item.get("last_attempt_at") or ""))
                if last_attempt is None:
                    continue
                age_seconds = (now - last_attempt).total_seconds()
                if age_seconds <= stale_after_seconds:
                    continue
                item["instagram_status"] = "failed"
                item["last_error"] = (
                    f"Recovered stale Instagram upload after {int(age_seconds)}s "
                    f"(watchdog limit {stale_after_seconds}s)."
                )
                stale_job_ids.append(int(item.get("job_id")))
                changed = True
            if changed:
                self._write(data)
        return stale_job_ids

    def next_ready_item(self) -> dict[str, Any] | None:
        data = self.load()
        if data.get("auth_blocked") or data.get("blocked_reason"):
            return None
        now = datetime.now(timezone.utc)
        for item in data["items"]:
            status = str(item.get("instagram_status") or "")
            if status not in {"pending", "failed"}:
                continue
            if status == "failed":
                last_attempt = parse_utc_iso(str(item.get("last_attempt_at") or ""))
                if last_attempt is not None and (now - last_attempt).total_seconds() < UPLOAD_RETRY_COOLDOWN_SECONDS:
                    continue
            if bool(item.get("instagram_enabled_for_origin")) or int(item.get("attempt_count", 0)) < self.config.instagram_retry_limit:
                return deepcopy(item)
        return None

    def mark_uploading(self, job_id: int) -> dict[str, Any]:
        with self._lock:
            data = self._load_unlocked()
            item = self._item_by_job_id(data, job_id)
            item["instagram_status"] = "uploading"
            item["attempt_count"] = int(item.get("attempt_count", 0)) + 1
            item["last_attempt_at"] = utcnow_iso()
            self._write(data)
            return deepcopy(item)

    def mark_uploaded(self, job_id: int, *, result: InstagramUploadResult) -> dict[str, Any]:
        with self._lock:
            data = self._load_unlocked()
            item = self._item_by_job_id(data, job_id)
            item["instagram_status"] = "uploaded"
            item["instagram_url"] = result.reel_url
            item["instagram_reel_path"] = result.reel_path
            item["caption"] = result.caption
            item["video_width"] = result.video_width
            item["video_height"] = result.video_height
            item["last_error"] = None
            item["uploaded_at"] = utcnow_iso()
            self._write(data)
            return deepcopy(item)

    def mark_failed(self, job_id: int, error: str, *, blocked_reason: str | None = None) -> dict[str, Any]:
        with self._lock:
            data = self._load_unlocked()
            item = self._item_by_job_id(data, job_id)
            item["instagram_status"] = "blocked" if blocked_reason else "failed"
            item["last_error"] = error[:500]
            if blocked_reason:
                now = utcnow_iso()
                data["blocked_reason"] = blocked_reason
                data["blocked_at"] = now
                data["auth_blocked"] = blocked_reason == "auth"
                data["auth_blocked_at"] = now if blocked_reason == "auth" else data.get("auth_blocked_at")
            self._write(data)
            return deepcopy(item)

    def _item_by_job_id(self, data: dict[str, Any], job_id: int) -> dict[str, Any]:
        for item in data["items"]:
            if int(item.get("job_id")) == int(job_id):
                return item
        raise KeyError(job_id)


async def upload_to_instagram(config: AppConfig, *, video_path: Path, caption: str) -> InstagramUploadResult:
    cookies_exists = config.instagram_cookies_path.exists()
    storage_exists = config.instagram_storage_path.exists()
    script_exists = config.instagram_upload_script.exists()
    profile_dir = config.instagram_profile_dir
    profile_dir_exists = profile_dir.exists() if profile_dir is not None else False
    logger.info(
        "Instagram preflight: script=%s exists=%s cookies=%s exists=%s storage=%s exists=%s profile_dir=%s profile_exists=%s target=%s timeout_s=%s channel=%s playwright_path=%s",
        config.instagram_upload_script,
        script_exists,
        config.instagram_cookies_path,
        cookies_exists,
        config.instagram_storage_path,
        storage_exists,
        profile_dir,
        profile_dir_exists,
        config.instagram_target_username,
        config.instagram_upload_timeout_seconds,
        os.getenv("IG_BROWSER_CHANNEL", ""),
        os.getenv("PLAYWRIGHT_BROWSERS_PATH", ""),
    )
    env = dict(os.environ)
    env["IG_UPLOAD_FILE"] = str(video_path)
    env["IG_COOKIES_FILE"] = str(config.instagram_cookies_path)
    env["IG_STORAGE_FILE"] = str(config.instagram_storage_path)
    if config.instagram_profile_dir is not None:
        env["IG_PROFILE_ROOT"] = str(config.instagram_profile_dir)
        env["IG_PROFILE_NAME"] = config.instagram_profile_name
    env["IG_DB_PATH"] = str(config.db_path)
    env["IG_CAPTION_TEXT"] = caption
    env["IG_TARGET_USERNAME"] = config.instagram_target_username
    env["IG_JSON"] = "1"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(config.instagram_upload_script),
        cwd=str(config.root_dir),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=max(60, int(config.instagram_upload_timeout_seconds)),
        )
    except asyncio.CancelledError:
        process.kill()
        try:
            await process.wait()
        except Exception:
            pass
        raise
    except asyncio.TimeoutError as exc:
        process.kill()
        try:
            await process.wait()
        except Exception:
            pass
        raise InstagramUploadError(
            f"Instagram upload exceeded {config.instagram_upload_timeout_seconds}s and was terminated."
        ) from exc
    output = stdout.decode("utf-8", errors="replace").strip()
    error_output = stderr.decode("utf-8", errors="replace").strip()
    payload: dict[str, Any] | None = None
    if output:
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            payload = None
    if process.returncode != 0 or not payload or not payload.get("ok"):
        message = (payload or {}).get("message") or error_output or output or "Instagram upload failed"
        raise InstagramUploadError(str(message))
    return InstagramUploadResult(
        reel_url=str(payload["reelUrl"]),
        reel_path=str(payload["reelPath"]),
        caption=str(payload.get("caption", caption)),
        video_width=int(payload.get("videoWidth", 0) or 0),
        video_height=int(payload.get("videoHeight", 0) or 0),
    )
