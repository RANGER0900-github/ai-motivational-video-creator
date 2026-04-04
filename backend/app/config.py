from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class AppConfig:
    root_dir: Path
    state_dir: Path
    db_path: Path
    images_dir: Path
    music_dir: Path
    fonts_dir: Path
    outputs_dir: Path
    quotes_csv: Path
    images_usage_json: Path
    max_duration: float = 60.0
    fps: int = 30
    width: int = 1080
    height: int = 1920
    text_fade: float = 0.5
    crf: str = "20"
    encoder_preset: str = "medium"
    encoder_threads: int = 2
    default_darken: float = 0.78
    default_workers: int = 1

    @property
    def process_log(self) -> Path:
        return self.outputs_dir / "process.log"


def load_config(root_dir: Path | None = None) -> AppConfig:
    root = Path(root_dir or os.getenv("AI_VIDEO_GEN_ROOT") or Path(__file__).resolve().parents[2]).resolve()
    state_dir = root / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    return AppConfig(
        root_dir=root,
        state_dir=state_dir,
        db_path=state_dir / "app.db",
        images_dir=root / "images",
        music_dir=root / "music",
        fonts_dir=root / "fonts",
        outputs_dir=root / "outputs",
        quotes_csv=root / "quotes.csv",
        images_usage_json=root / "images_usage.json",
    )


def check_runtime(config: AppConfig) -> list[str]:
    issues: list[str] = []
    for directory in (config.images_dir, config.music_dir, config.fonts_dir, config.outputs_dir, config.state_dir):
        if not directory.exists():
            issues.append(f"Missing directory: {directory}")
    if not config.quotes_csv.exists():
        issues.append(f"Missing quotes CSV: {config.quotes_csv}")
    if shutil.which("ffmpeg") is None:
        issues.append("ffmpeg is not available on PATH")
    return issues
