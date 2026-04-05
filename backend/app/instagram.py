from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import AppConfig
from .models import JobDetail

logger = logging.getLogger(__name__)

INSTAGRAM_DESCRIPTION_VERSION = "v1"
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
]


class InstagramUploadError(RuntimeError):
    pass


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_instagram_caption(quote: str, author: str | None) -> str:
    quote_text = (quote or "").strip()
    author_text = (author or "").strip()
    tags = " ".join(f"#{tag}" for tag in INSTAGRAM_HASHTAG_POOL[:10])
    lines: list[str] = []
    if quote_text:
        lines.append(quote_text)
    if author_text:
        lines.append(f"— {author_text}")
    if lines:
        lines.append("")
    lines.append("Save this reel and come back when you need the reminder.")
    lines.append("")
    lines.append(tags)
    return "\n".join(lines).strip()


@dataclass(slots=True)
class InstagramUploadResult:
    reel_url: str
    reel_path: str
    caption: str


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
        return json.loads(self.path.read_text(encoding="utf-8"))

    def load(self) -> dict[str, Any]:
        with self._lock:
            return self._load_unlocked()

    def status_summary(self) -> dict[str, Any]:
        data = self.load()
        items = data["items"]
        return {
            "pending": sum(1 for item in items if item.get("instagram_status") in {"pending", "uploading"}),
            "uploaded": sum(1 for item in items if item.get("instagram_status") == "uploaded"),
            "failed": sum(1 for item in items if item.get("instagram_status") == "failed"),
            "auth_blocked": bool(data.get("auth_blocked")),
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
            auth_blocked = bool(data.get("auth_blocked"))
            items = data["items"]
            existing = next((item for item in items if int(item.get("job_id")) == int(job.id)), None)
            if existing:
                existing["output_path"] = job.output_path
                existing["current_filename"] = job.output_path
                existing["chat_id"] = job.chat_id
                if existing.get("instagram_status") == "failed":
                    existing["instagram_status"] = "auth_blocked" if auth_blocked else "pending"
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
                "instagram_status": "auth_blocked" if auth_blocked else "pending",
                "instagram_url": None,
                "instagram_reel_path": None,
                "quote": job.quote,
                "author": job.author,
                "caption": build_instagram_caption(job.quote, job.author),
                "hashtags": list(INSTAGRAM_HASHTAG_POOL[:10]),
                "attempt_count": 0,
                "last_attempt_at": None,
                "last_error": None,
                "uploaded_at": None,
            }
            items.append(item)
            self._write(data)
            return deepcopy(item)

    def next_ready_item(self) -> dict[str, Any] | None:
        data = self.load()
        if data.get("auth_blocked"):
            return None
        for item in data["items"]:
            if item.get("instagram_status") in {"pending", "failed"} and int(item.get("attempt_count", 0)) < self.config.instagram_retry_limit:
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
            item["last_error"] = None
            item["uploaded_at"] = utcnow_iso()
            self._write(data)
            return deepcopy(item)

    def mark_failed(self, job_id: int, error: str, *, auth_blocked: bool = False) -> dict[str, Any]:
        with self._lock:
            data = self._load_unlocked()
            item = self._item_by_job_id(data, job_id)
            item["instagram_status"] = "auth_blocked" if auth_blocked else "failed"
            item["last_error"] = error[:500]
            if auth_blocked:
                data["auth_blocked"] = True
                data["auth_blocked_at"] = utcnow_iso()
            self._write(data)
            return deepcopy(item)

    def _item_by_job_id(self, data: dict[str, Any], job_id: int) -> dict[str, Any]:
        for item in data["items"]:
            if int(item.get("job_id")) == int(job_id):
                return item
        raise KeyError(job_id)


async def upload_to_instagram(config: AppConfig, *, video_path: Path, caption: str) -> InstagramUploadResult:
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
        "python3",
        str(config.instagram_upload_script),
        cwd=str(config.root_dir),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
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
    )
