"""Running the render: one ffmpeg call, watched while it works."""

import subprocess
import threading
from pathlib import Path

from .compositor import build_filter_complex as compose, project_duration
from .encoders import encode_args
from .probe import ClipInfo
from .timecode import format_time


def _run_with_progress(
    cmd: list[str], total_dur: float, on_progress=None, on_start=None, on_stderr=None
) -> None:
    """Run ffmpeg, reporting progress to a callback or the console."""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if on_start:
        on_start(proc)

    # Drain stderr concurrently; ffmpeg can fill the pipe before stdout progress starts.
    stderr_lines: list[str] = []

    def _drain_stderr() -> None:
        for line in proc.stderr:  # type: ignore[union-attr]
            stderr_lines.append(line)
            if on_stderr:
                on_stderr(line.rstrip())

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

    out_time_us = 0
    speed = ""

    for raw in proc.stdout:  # type: ignore[union-attr]
        line = raw.strip()
        if line.startswith("out_time_us="):
            try:
                out_time_us = int(line.split("=", 1)[1])
            except ValueError:
                pass
        elif line.startswith("speed="):
            speed = line.split("=", 1)[1].strip()
        elif line.startswith("progress="):
            elapsed = out_time_us / 1_000_000
            pct = min(100.0, (elapsed / total_dur) * 100) if total_dur > 0 else 0
            if on_progress:
                on_progress(elapsed, total_dur, pct, speed)
                continue
            timing = f"{format_time(elapsed)} / {format_time(total_dur)} ({pct:.0f}%)"
            speed_str = f"  @ {speed}" if speed not in ("", "N/A") else ""
            print(f"\r  Rendering: {timing}{speed_str}   ", end="", flush=True)

    proc.wait()
    stderr_thread.join()
    if not on_progress:
        print()  # move past the in-place progress line

    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, cmd, stderr="".join(stderr_lines)
        )



def render_project(
    project,
    infos: list[ClipInfo],
    *,
    verbose: bool = True,
    on_progress=None,
    on_start=None,
    on_stderr=None,
    on_stage=None,
) -> Path:
    """Render a track timeline in one ffmpeg pass, writing no intermediate files.

    One ffmpeg call for the whole edit, however many tracks it has. This is the
    only renderer, and both `script.py` and the app's `RenderJob` walk it, which
    is what keeps a CLI render and an app render the same render.
    """
    output_path = project.output.file
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pairs = project.all_clips()
    tracks = len(project.tracks)
    if verbose and not on_progress:
        print(f"  Building filter graph ({len(pairs)} clips on {tracks} track(s))...")
    if on_stage:
        on_stage("graph", f"{len(pairs)} clips on {tracks} track(s)")

    filter_complex, v_out, a_out = compose(project, infos)

    # -progress and -nostats are global options and must precede all -i flags.
    cmd = ["ffmpeg", "-hide_banner", "-y", "-progress", "pipe:1", "-nostats"]
    for _, clip in pairs:
        cmd += ["-i", str(clip.source)]

    cmd += [
        "-filter_complex", filter_complex,
        "-map", f"[{v_out}]",
        "-map", f"[{a_out}]",
        *encode_args(project.output.encoder, project.output.quality),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
    ]
    if output_path.suffix.lower() in (".mp4", ".m4v", ".mov"):
        cmd += ["-movflags", "+faststart"]
    cmd += [str(output_path)]

    if on_stage:
        on_stage("encode", f"{project.output.encoder} -> {output_path.name}")

    if verbose or on_progress:
        _run_with_progress(
            cmd, project_duration(project, infos), on_progress, on_start, on_stderr
        )
    else:
        subprocess.run(cmd, check=True, capture_output=True)

    return output_path
