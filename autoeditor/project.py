"""Convert between a VideoScript and the JSON the UI exchanges."""

from pathlib import Path

from .timeline import (
    BalanceSettings,
    Defaults,
    GlobalEdits,
    Join,
    OutputSettings,
    Region,
    SilenceSettings,
    TimelineClip,
    VideoScript,
)


def _optional(value) -> float | None:
    """None and "" both mean "not set"; 0 is a real setting."""
    if value is None or value == "":
        return None
    return float(value)


def _regions_from_json(raw) -> list[Region] | None:
    """Accept either region objects or the older [start, end] pairs."""
    if not raw:
        return None
    regions = []
    for item in raw:
        if isinstance(item, dict):
            regions.append(Region(
                start=float(item["start"]),
                end=float(item["end"]),
                join=Join(item.get("join", "cut")),
                join_duration=float(item.get("join_duration", 0.0)),
            ))
        else:
            regions.append(Region(float(item[0]), float(item[1])))
    regions.sort(key=lambda r: r.start)
    if regions:
        regions[0].join, regions[0].join_duration = Join.CUT, 0.0
    return regions


def clip_to_json(clip: TimelineClip) -> dict:
    return {
        "path": str(clip.path),
        "label": clip.label,
        "join": clip.join.value,
        "join_duration": clip.join_duration,
        "trim_silence": clip.trim_silence,
        "audio_gain_db": clip.audio_gain_db,
        "balance_db": clip.balance_db,
        "audio_overlap": clip.audio_overlap,
        "audio_lead": clip.audio_lead,
        "regions": [
            {"start": r.start, "end": r.end, "join": r.join.value,
             "join_duration": r.join_duration}
            for r in (clip.regions or [])
        ],
        "missing": clip.missing,
    }


def to_json(script: VideoScript) -> dict:
    out, defaults, silence = script.output, script.defaults, script.silence
    edits = script.globals
    return {
        "title": script.title,
        "source": str(script.source),
        "output": {
            "file": str(out.file),
            "resolution": out.resolution,
            "fps": out.fps,
            "encoder": out.encoder,
            "quality": out.quality,
        },
        "globals": {
            "fade_in": edits.fade_in,
            "fade_out": edits.fade_out,
            "audio_gain_db": edits.audio_gain_db,
        },
        "defaults": {
            "join": defaults.join.value,
            "crossfade": defaults.crossfade,
            "fade": defaults.fade,
            "trim_silence": defaults.trim_silence,
            "audio_overlap": defaults.audio_overlap,
            "audio_lead": defaults.audio_lead,
        },
        "silence": {
            "threshold_db": silence.threshold_db,
            "padding": silence.padding,
            "min_silence": silence.min_silence,
            "min_segment": silence.min_segment,
        },
        "balance": {
            "enabled": script.balance.enabled,
            "target_lufs": script.balance.target_lufs,
        },
        "clips": [clip_to_json(c) for c in script.clips],
    }


def from_json(data: dict) -> VideoScript:
    out = data.get("output", {})
    edits = data.get("globals", {})
    defaults = data.get("defaults", {})
    silence = data.get("silence", {})

    clips = []
    for raw in data.get("clips", []):
        path = Path(raw["path"])
        regions = _regions_from_json(raw.get("regions"))
        clips.append(
            TimelineClip(
                path=path,
                label=raw.get("label") or path.stem,
                join=Join(raw.get("join", "crossfade")),
                join_duration=float(raw.get("join_duration", 0.3)),
                trim_silence=bool(raw.get("trim_silence", False)),
                audio_gain_db=float(raw.get("audio_gain_db", 0.0)),
                balance_db=_optional(raw.get("balance_db")),
                audio_overlap=_optional(raw.get("audio_overlap")),
                audio_lead=float(raw.get("audio_lead") or 0.0),
                regions=regions,
                missing=not path.is_file(),
            )
        )

    return VideoScript(
        source=Path(data.get("source") or "untitled.md"),
        title=data.get("title") or "Untitled",
        output=OutputSettings(
            file=Path(out.get("file") or "output.mp4"),
            resolution=out.get("resolution", "1920x1080"),
            fps=int(out.get("fps", 60)),
            encoder=out.get("encoder", "libx264"),
            quality=_optional(out.get("quality")),
        ),
        globals=GlobalEdits(
            fade_in=float(edits.get("fade_in", out.get("fade_in", 0.5))),
            fade_out=float(edits.get("fade_out", out.get("fade_out", 0.5))),
            audio_gain_db=float(edits.get("audio_gain_db", 0.0)),
        ),
        defaults=Defaults(
            join=Join(defaults.get("join", "crossfade")),
            crossfade=float(defaults.get("crossfade", 0.3)),
            fade=float(defaults.get("fade", 0.5)),
            trim_silence=bool(defaults.get("trim_silence", False)),
            audio_overlap=_optional(defaults.get("audio_overlap")),
            audio_lead=float(defaults.get("audio_lead") or 0.0),
        ),
        silence=SilenceSettings(
            threshold_db=float(silence.get("threshold_db", -30.0)),
            padding=float(silence.get("padding", 0.5)),
            min_silence=float(silence.get("min_silence", 1.0)),
            min_segment=float(silence.get("min_segment", 0.5)),
        ),
        balance=BalanceSettings(
            enabled=bool(data.get("balance", {}).get("enabled", False)),
            target_lufs=float(data.get("balance", {}).get("target_lufs", -14.0)),
        ),
        clips=clips,
    )
