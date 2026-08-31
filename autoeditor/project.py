"""Convert between a VideoScript and the JSON the UI exchanges."""

from pathlib import Path

from .timeline import (
    BalanceSettings,
    Defaults,
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
        "audio_blend": clip.audio_blend,
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
        "defaults": {
            "join": defaults.join.value,
            "crossfade": defaults.crossfade,
            "fade": defaults.fade,
            "audio_overlap": defaults.audio_overlap,
            "trim_silence": defaults.trim_silence,
            "fade_in": defaults.fade_in,
            "fade_out": defaults.fade_out,
            "audio_blend": defaults.audio_blend,
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


# ---------------------------------------------------------------- tracks
#
# The app exchanges a track timeline; the flat-list pair above is what an older
# saved project still arrives as, and `project_from_json` converts it, so a
# project saved before the timeline existed opens in it.

def _settings_json(project) -> dict:
    """The parts of a project that are not its timeline."""
    return {
        k: v for k, v in to_json(_as_script_shell(project)).items()
        if k != "clips"
    }


def _as_script_shell(project):
    """A VideoScript carrying the project's settings and no clips.

    Only so the settings can be serialised by the one function that knows how;
    nothing reads its empty timeline.
    """
    return VideoScript(
        source=project.source, title=project.title, output=project.output,
        silence=project.silence, defaults=project.defaults,
        balance=project.balance, clips=[],
    )


def entry_to_json(entry) -> dict:
    from .tracks import Transition
    if isinstance(entry, Transition):
        return {
            "type": "transition",
            "kind": entry.kind,
            "duration": entry.duration,
            "audio_duration": entry.audio_duration,
            "audio_lead": entry.audio_lead,
        }
    return {
        "type": "clip",
        "path": str(entry.source),
        "label": entry.label,
        "source_in": entry.source_in,
        "source_out": entry.source_out,
        "start": entry.start,
        "link": entry.link,
        "missing": entry.missing,
        "effects": [{"name": e.name, "params": dict(e.params)} for e in entry.effects],
    }


def project_to_json(project) -> dict:
    data = _settings_json(project)
    data["tracks"] = [
        {
            "kind": track.kind.value,
            "name": track.name,
            "gain_db": track.gain_db,
            "muted": track.muted,
            "hidden": track.hidden,
            "entries": [entry_to_json(e) for e in track.entries],
        }
        for track in project.tracks
    ]
    return data


def entry_from_json(raw: dict):
    from .tracks import Clip, Effect, Transition
    if raw.get("type") == "transition":
        return Transition(
            kind=raw.get("kind", "cut"),
            duration=float(raw.get("duration", 0.0)),
            audio_duration=_optional(raw.get("audio_duration")),
            audio_lead=_optional(raw.get("audio_lead")),
        )
    path = Path(raw["path"])
    return Clip(
        source=path,
        label=raw.get("label") or path.stem,
        source_in=float(raw.get("source_in") or 0.0),
        source_out=_optional(raw.get("source_out")),
        start=_optional(raw.get("start")),
        link=raw.get("link"),
        missing=not path.is_file(),
        effects=[
            Effect(e["name"], dict(e.get("params") or {}))
            for e in raw.get("effects") or []
        ],
    )


def project_from_json(data: dict):
    """A Project from the app's JSON, converting an older flat-list save."""
    from .migrate import to_project
    from .tracks import Project, Track, TrackKind

    if "tracks" not in data:
        return to_project(from_json(data))

    shell = from_json({**data, "clips": []})
    tracks = []
    for raw in data.get("tracks") or []:
        tracks.append(Track(
            kind=TrackKind(raw.get("kind", "video")),
            name=raw.get("name") or "Track",
            entries=[entry_from_json(e) for e in raw.get("entries") or []],
            gain_db=float(raw.get("gain_db") or 0.0),
            muted=bool(raw.get("muted")),
            hidden=bool(raw.get("hidden")),
        ))
    return Project(
        source=shell.source, title=shell.title, output=shell.output,
        silence=shell.silence, defaults=shell.defaults, balance=shell.balance,
        tracks=tracks,
    )


def from_json(data: dict) -> VideoScript:
    out = data.get("output", {})
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
                audio_blend=_optional(raw.get("audio_blend")),
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
        defaults=Defaults(
            join=Join(defaults.get("join", "crossfade")),
            crossfade=float(defaults.get("crossfade", 0.3)),
            fade=float(defaults.get("fade", 0.5)),
            audio_overlap=float(defaults.get("audio_overlap", 2.0)),
            trim_silence=bool(defaults.get("trim_silence", False)),
            fade_in=float(defaults.get("fade_in", 0.5)),
            fade_out=float(defaults.get("fade_out", 0.5)),
            audio_blend=_optional(defaults.get("audio_blend")),
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
