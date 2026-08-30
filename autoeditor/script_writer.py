"""Render a VideoScript back to the markdown script format."""

from .timecode import format_timecode
from .timeline import Defaults, Join, VideoScript


def _fmt(value: float) -> str:
    return f"{value:g}"


def _item_options(clip, defaults: Defaults) -> list[str]:
    """The options needed to reproduce this clip, omitting anything default."""
    options: list[str] = []

    if clip.join is not defaults.join:
        if clip.join is Join.CUT:
            options.append("cut")
        else:
            options.append(f"{clip.join.value} {_fmt(clip.join_duration)}")
    elif clip.join is not Join.CUT:
        default_duration = (
            defaults.crossfade if clip.join is Join.CROSSFADE else defaults.fade
        )
        if abs(clip.join_duration - default_duration) > 1e-9:
            options.append(f"{clip.join.value} {_fmt(clip.join_duration)}")

    for i, region in enumerate(clip.regions or []):
        # A join before a range binds that range to the one before it.
        if i and region.join is not Join.CUT:
            options.append(f"{region.join.value} {_fmt(region.join_duration)}")
        options.append(f"{format_timecode(region.start)}-{format_timecode(region.end)}")

    if clip.trim_silence != defaults.trim_silence:
        options.append("trim silence" if clip.trim_silence else "keep silence")

    if clip.audio_gain_db:
        options.append(f"volume {clip.audio_gain_db:+g} dB")

    if clip.balance_db is not None:
        options.append(f"balance {clip.balance_db:+g} dB")

    if clip.audio_overlap != defaults.audio_overlap:
        options.append(
            "audio overlap auto" if clip.audio_overlap is None
            else f"audio overlap {_fmt(clip.audio_overlap)}"
        )
    if abs(clip.audio_lead - defaults.audio_lead) > 1e-9:
        options.append(f"audio lead {_fmt(clip.audio_lead)}")

    return options


def to_markdown(script: VideoScript, *, notes: str = "") -> str:
    """Serialise a script to markdown that parses back to the same edit."""
    out, silence, defaults = script.output, script.silence, script.defaults
    lines: list[str] = [f"# {script.title}", ""]

    if notes:
        lines += [notes.strip(), ""]

    lines += [
        "## Output",
        "",
        f"- file: `{out.file}`",
        f"- resolution: {out.resolution}",
        f"- fps: {out.fps}",
        f"- encoder: {out.encoder}",
        *([f"- quality: {_fmt(out.quality)}"] if out.quality is not None else []),
        "",
        "## Auto Editor",
        "",
        f"- balance audio: {'yes' if script.balance.enabled else 'no'}",
        f"- audio target: {_fmt(script.balance.target_lufs)} LUFS",
        "",
        "## Defaults",
        "",
        f"- join: {defaults.join.value}",
        f"- crossfade: {_fmt(defaults.crossfade)}",
        f"- fade: {_fmt(defaults.fade)}",
        f"- fade in: {_fmt(defaults.fade_in)}",
        f"- fade out: {_fmt(defaults.fade_out)}",
        f"- trim silence: {'yes' if defaults.trim_silence else 'no'}",
        f"- audio lead: {_fmt(defaults.audio_lead)}",
        "",
        "## Silence",
        "",
        f"- threshold: {_fmt(silence.threshold_db)} dB",
        f"- padding: {_fmt(silence.padding)}",
        f"- min silence: {_fmt(silence.min_silence)}",
        f"- min segment: {_fmt(silence.min_segment)}",
        "",
        "## Timeline",
        "",
    ]

    if defaults.audio_overlap is not None:
        lines.insert(
            lines.index(f"- audio lead: {_fmt(defaults.audio_lead)}"),
            f"- audio overlap: {_fmt(defaults.audio_overlap)}",
        )

    for i, clip in enumerate(script.clips, 1):
        options = _item_options(clip, defaults)
        # The first clip has nothing before it, so its join is noise.
        if i == 1 and options and options[0].split()[0] in ("cut", "crossfade", "fade"):
            options = options[1:]
        suffix = f" -- {', '.join(options)}" if options else ""
        lines.append(f"{i}. `{clip.path}`{suffix}")

    return "\n".join(lines) + "\n"

