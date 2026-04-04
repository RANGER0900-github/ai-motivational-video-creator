from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .models import JobDetail, JobSummary, ProgressEvent


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  status TEXT NOT NULL,
  progress REAL NOT NULL,
  phase TEXT NOT NULL,
  message TEXT NOT NULL,
  quote TEXT NOT NULL,
  author TEXT,
  source_row_id INTEGER,
  image_name TEXT,
  music_name TEXT,
  darken REAL NOT NULL,
  output_path TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL,
  status TEXT NOT NULL,
  phase TEXT NOT NULL,
  progress REAL NOT NULL,
  message TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(job_id) REFERENCES jobs(id)
);
"""


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def create_job(self, quote: str, author: str | None, source_row_id: int | None, image_name: str | None, music_name: str | None, darken: float, message: str = "Job accepted") -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO jobs (status, progress, phase, message, quote, author, source_row_id, image_name, music_name, darken, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("queued", 0.0, "Queued", message, quote, author, source_row_id, image_name, music_name, darken, now, now),
            )
            job_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT INTO events (job_id, status, phase, progress, message, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (job_id, "queued", "Queued", 0.0, message, now),
            )
            return job_id

    def count_active_jobs(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'preparing', 'rendering', 'finalizing')"
            ).fetchone()
        return int(row[0]) if row else 0

    def update_job(self, job_id: int, *, status: str, progress: float, phase: str, message: str, output_path: str | None = None, error: str | None = None, started: bool = False, completed: bool = False) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            current = conn.execute("SELECT started_at FROM jobs WHERE id = ?", (job_id,)).fetchone()
            started_at = current["started_at"] if current else None
            if started and not started_at:
                started_at = now
            completed_at = now if completed else None
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, progress = ?, phase = ?, message = ?, output_path = COALESCE(?, output_path), error = ?, updated_at = ?, started_at = COALESCE(?, started_at), completed_at = COALESCE(?, completed_at)
                WHERE id = ?
                """,
                (status, progress, phase, message, output_path, error, now, started_at, completed_at, job_id),
            )
            conn.execute(
                "INSERT INTO events (job_id, status, phase, progress, message, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (job_id, status, phase, progress, message, now),
            )

    def cancel_job(self, job_id: int) -> None:
        self.update_job(job_id, status="cancelled", progress=0.0, phase="Cancelled", message="Job cancelled before rendering", completed=True)

    def get_job_row(self, job_id: int):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return row

    def list_job_rows(self):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()

    def list_events(self, job_id: int, after_id: int = 0):
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM events WHERE job_id = ? AND id > ? ORDER BY id ASC", (job_id, after_id)
            ).fetchall()

    def list_pending_job_ids(self) -> list[int]:
        with self.connect() as conn:
            rows = conn.execute("SELECT id FROM jobs WHERE status IN ('queued', 'preparing', 'rendering', 'finalizing') ORDER BY created_at ASC").fetchall()
        return [int(row[0]) for row in rows]


def row_to_job(row) -> JobDetail:
    return JobDetail(
        id=int(row["id"]),
        status=row["status"],
        progress=float(row["progress"]),
        phase=row["phase"],
        message=row["message"],
        quote=row["quote"],
        author=row["author"],
        image_name=row["image_name"],
        music_name=row["music_name"],
        output_path=row["output_path"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
        completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
        error=row["error"],
        source_row_id=row["source_row_id"],
        darken=float(row["darken"]),
    )


def row_to_summary(row) -> JobSummary:
    job = row_to_job(row)
    return JobSummary(**job.model_dump(exclude={"source_row_id", "darken"}))


def row_to_event(row) -> ProgressEvent:
    return ProgressEvent(
        id=int(row["id"]),
        job_id=int(row["job_id"]),
        status=row["status"],
        phase=row["phase"],
        progress=float(row["progress"]),
        message=row["message"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )
