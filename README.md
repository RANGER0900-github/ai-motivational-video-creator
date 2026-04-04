# AI Motivational Video Creator

Linux-first motivational video generator with a FastAPI backend, SQLite render queue, and a React web app for one-click generation, live progress, preview, download, and gallery browsing.

## What It Does

- generates vertical motivational quote videos from local assets
- keeps rendering jobs alive on the server even if the browser closes
- shows live progress in the web app
- lets you preview and download completed videos
- exposes a clean gallery of completed videos only
- supports local Linux development and CLI-based generation

## Stack

- Backend: FastAPI, SQLite, MoviePy, Pillow, FFmpeg
- Frontend: React, Vite, Lenis
- Storage: local filesystem for assets and outputs, SQLite for jobs/events

## Project Layout

```text
backend/         FastAPI app, queue, renderer, storage, models
frontend/        React web app
images/          source background images
music/           source music tracks
fonts/           source font files
quotes.csv       quote library used for generation
outputs/         generated videos (local only, gitignored)
state/           SQLite job/event database (local only, gitignored)
```

## Features

- one-click video generation
- persistent backend queue
- live SSE progress updates
- browser preview and download
- completed-video gallery
- Linux-compatible rendering flow
- random quote/image/music selection from local assets

## Requirements

- Python 3.11+
- Node.js 22+
- FFmpeg available on `PATH`

Check FFmpeg:

```bash
ffmpeg -version
```

## Local Setup

### 1. Create Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

### 2. Install frontend dependencies

```bash
cd frontend
npm install
cd ..
```

## Run The App

### Development flow

Backend:

```bash
source .venv/bin/activate
uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000 --reload
```

Frontend dev server:

```bash
cd frontend
npm run dev
```

- backend: `http://127.0.0.1:8000`
- frontend dev server: `http://127.0.0.1:5173`

### Single-server local run

Build the frontend and let FastAPI serve it:

```bash
cd frontend
npm run build
cd ..
source .venv/bin/activate
uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000
```

Then open:

```text
http://127.0.0.1:8000
```

## How Generation Works

1. A job is created through the web app or CLI.
2. The job is stored in SQLite.
3. The backend worker picks one job at a time.
4. A quote, image, and music track are selected.
5. MoviePy and FFmpeg render a vertical MP4.
6. Progress events are streamed to the frontend.
7. Completed videos appear in the gallery.

## API Overview

- `GET /api/health` : runtime health and dependency checks
- `GET /api/library/overview` : counts for quotes, assets, and jobs
- `GET /api/library/videos` : completed playable videos only
- `GET /api/jobs` : all jobs
- `POST /api/jobs` : create a new generation job
- `GET /api/jobs/{job_id}` : fetch one job
- `GET /api/jobs/{job_id}/stream` : live progress stream

## CLI Usage

Generate videos from the terminal:

```bash
source .venv/bin/activate
python -m app.cli --count 2 --workers 1 --darken 0.78
```

## Notes

- `quotes.csv` is the quote source of truth
- generated outputs are intentionally not committed
- SQLite job state is intentionally not committed
- orphan or failed output files are not shown in the gallery
- the app is tuned for Linux; FFmpeg availability is required

## GitHub

Repository target:

```text
https://github.com/RANGER0900-github/ai-motivational-video-creator.git
```
