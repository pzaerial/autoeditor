from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
import os


@dataclass
class Config:
    intro_path: Path | None
    outro_path: Path | None
    transition_path: Path | None
    midroll_ad_path_1: Path | None
    midroll_ad_path_2: Path | None
    midroll_ad_1_enabled: bool
    midroll_ad_2_enabled: bool
    project_folder_path: Path
    output_folder: Path
    process_multi: bool
    target_resolution: str
    target_fps: int
    fade_duration: float
    output_fade_duration: float
    video_encoder: str
    remove_dead_space: bool
    dead_space_padding: float
    dead_space_threshold_db: float
    dead_space_min_silence: float
    dead_space_min_segment: float


def load_config() -> Config:
    load_dotenv()

    def opt_path(key: str) -> Path | None:
        val = os.getenv(key)
        return Path(val) if val else None

    return Config(
        intro_path=opt_path("INTRO_PATH"),
        outro_path=opt_path("OUTRO_PATH"),
        transition_path=opt_path("TRANSITION_PATH"),
        midroll_ad_path_1=opt_path("MIDROLL_AD_PATH_1"),
        midroll_ad_path_2=opt_path("MIDROLL_AD_PATH_2"),
        midroll_ad_1_enabled=os.getenv("MIDROLL_AD_1_ENABLED", "true").lower() == "true",
        midroll_ad_2_enabled=os.getenv("MIDROLL_AD_2_ENABLED", "true").lower() == "true",
        project_folder_path=Path(os.getenv("PROJECT_FOLDER_PATH", "projects")),
        output_folder=Path(os.getenv("OUTPUT_FOLDER", "output")),
        process_multi=os.getenv("PROCESS_MULTI", "true").lower() == "true",
        target_resolution=os.getenv("TARGET_RESOLUTION", "1920x1080"),
        target_fps=int(os.getenv("TARGET_FPS", "30")),
        fade_duration=float(os.getenv("FADE_DURATION", "0.5")),
        output_fade_duration=float(os.getenv("OUTPUT_FADE_DURATION", "1.0")),
        video_encoder=os.getenv("VIDEO_ENCODER", "libx264"),
        remove_dead_space=os.getenv("REMOVE_DEAD_SPACE", "true").lower() == "true",
        dead_space_padding=float(os.getenv("DEAD_SPACE_PADDING", "0.5")),
        dead_space_threshold_db=float(os.getenv("DEAD_SPACE_THRESHOLD_DB", "-30")),
        dead_space_min_silence=float(os.getenv("DEAD_SPACE_MIN_SILENCE", "1.0")),
        dead_space_min_segment=float(os.getenv("DEAD_SPACE_MIN_SEGMENT", "0.5")),
    )
