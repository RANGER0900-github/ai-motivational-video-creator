from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


JobStatus = Literal["queued", "preparing", "rendering", "finalizing", "completed", "failed", "cancelled"]


class QuoteRecord(BaseModel):
    row_id: int
    quote: str
    author: str = ""
    status: str = ""
    used_time: str = ""
    output: str = ""
    error: str = ""


class CreateJobRequest(BaseModel):
    row_ids: list[int] = Field(default_factory=list)
    custom_quote: str | None = None
    custom_author: str | None = None
    image_name: str | None = None
    music_name: str | None = None
    darken: float | None = None


class JobSummary(BaseModel):
    id: int
    status: JobStatus
    progress: float
    phase: str
    message: str
    quote: str
    author: str | None = None
    image_name: str | None = None
    music_name: str | None = None
    output_path: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


class JobDetail(JobSummary):
    source_row_id: int | None = None
    darken: float


class ProgressEvent(BaseModel):
    id: int
    job_id: int
    status: JobStatus
    phase: str
    progress: float
    message: str
    created_at: datetime


class AssetItem(BaseModel):
    name: str
    path: str
    url: str


class VideoItem(BaseModel):
    job_id: int | None = None
    name: str
    path: str
    url: str
    created_at: str | None = None
    title: str | None = None
    quote: str | None = None
    author: str | None = None
