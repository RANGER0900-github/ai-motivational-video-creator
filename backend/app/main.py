from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import check_runtime, load_config
from .csv_store import QuoteStore
from .database import Database
from .jobs import JobContext, JobService
from .models import CreateJobRequest, VideoItem
from .storage import AssetStore

config = load_config()
database = Database(config.db_path)
quote_store = QuoteStore(config.quotes_csv)
asset_store = AssetStore(config)
job_service = JobService(JobContext(config=config, db=database, assets=asset_store, quotes=quote_store))


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.outputs_dir.mkdir(parents=True, exist_ok=True)
    quote_store.normalize()
    job_service.start()
    yield
    job_service.stop()


app = FastAPI(title="AI Motivational Video Creator", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/assets/images", StaticFiles(directory=config.images_dir), name="images")
app.mount("/assets/music", StaticFiles(directory=config.music_dir), name="music")
app.mount("/assets/fonts", StaticFiles(directory=config.fonts_dir), name="fonts")
app.mount("/assets/outputs", StaticFiles(directory=config.outputs_dir), name="outputs")


@app.get("/api/health")
def health():
    issues = check_runtime(config)
    return {
        "ok": not issues,
        "issues": issues,
        "database": str(config.db_path),
    }


@app.get("/api/library/quotes")
def list_quotes():
    return quote_store.list_quotes()


@app.get("/api/library/images")
def list_images():
    return asset_store.list_images()


@app.get("/api/library/music")
def list_music():
    return asset_store.list_music()


@app.get("/api/library/videos")
def list_videos():
    return _completed_video_library()


@app.get("/api/library/overview")
def library_overview():
    jobs = job_service.list_jobs()
    videos = _completed_video_library()
    recent = videos[:8]
    return {
        "quotes": len(quote_store.list_quotes()),
        "images": len(asset_store.list_images()),
        "music": len(asset_store.list_music()),
        "videos": len(videos),
        "jobs": {
            "active": sum(1 for job in jobs if job.status in {"queued", "preparing", "rendering", "finalizing"}),
            "completed": sum(1 for job in jobs if job.status == "completed"),
            "failed": sum(1 for job in jobs if job.status == "failed"),
        },
        "recent_videos": recent,
    }


def _completed_video_library() -> list[VideoItem]:
    items: list[VideoItem] = []
    for job in job_service.list_jobs():
        if job.status != "completed" or not job.output_path:
            continue
        output_path = config.root_dir / job.output_path
        if not output_path.exists():
            continue
        title = job.quote.strip()
        if len(title) > 84:
            title = f"{title[:81].rstrip()}..."
        items.append(
            VideoItem(
                job_id=job.id,
                name=Path(job.output_path).name,
                path=job.output_path,
                url=f"/assets/outputs/{Path(job.output_path).name}",
                created_at=(job.completed_at or job.updated_at).isoformat() if (job.completed_at or job.updated_at) else None,
                title=title or f"Job {job.id}",
                quote=job.quote,
                author=job.author,
            )
        )
    return sorted(items, key=lambda item: item.created_at or "", reverse=True)


@app.get("/api/jobs")
def list_jobs():
    return job_service.list_jobs()


@app.post("/api/jobs")
def create_jobs(payload: CreateJobRequest | None = None):
    jobs = job_service.create_jobs(payload or CreateJobRequest())
    return jobs


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    try:
        return job_service.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: int):
    try:
        job_service.cancel_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    return {"ok": True}


@app.get("/api/jobs/{job_id}/events")
def list_events(job_id: int, after_id: int = 0):
    return job_service.list_events(job_id, after_id=after_id)


@app.get("/api/jobs/{job_id}/stream")
async def stream_events(job_id: int, after_id: int = Query(default=0)):
    async def event_source():
        cursor = after_id
        while True:
            events = job_service.list_events(job_id, after_id=cursor)
            for event in events:
                cursor = event.id
                yield f"data: {json.dumps(event.model_dump(mode='json'))}\n\n"
            try:
                job = job_service.get_job(job_id)
            except KeyError:
                yield "event: end\ndata: {}\n\n"
                return
            if job.status in {"completed", "failed", "cancelled"}:
                yield "event: end\ndata: {}\n\n"
                return
            await asyncio.sleep(1)

    return StreamingResponse(event_source(), media_type="text/event-stream")


frontend_dist = config.root_dir / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/app", StaticFiles(directory=frontend_dist, html=True), name="frontend")


@app.get("/")
def root():
    index = frontend_dist / "index.html"
    if index.exists():
        return FileResponse(index)
    return {
        "message": "Frontend not built yet. Run npm install && npm run dev in frontend/ or npm run build for static serving.",
    }


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    icon = frontend_dist / "brand.svg"
    if icon.exists():
        return RedirectResponse(url="/app/brand.svg")
    raise HTTPException(status_code=404, detail="Favicon not found")
