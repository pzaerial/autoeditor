"""Running the render: one ffmpeg call, watched while it works."""

import subprocess
import threading
from pathlib import Path

from .encoders import encode_args
from .graph import build_filter_complex, split_into_groups, total_duration
from .probe import ClipInfo
from .timecode import format_time
from .timeline import VideoScript, expand_regions


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



def render_script(
    script: VideoScript,
    infos: list[ClipInfo],
    *,
    verbose: bool = True,
    on_progress=None,
    on_start=None,
    on_stderr=None,
    on_stage=None,
) -> Path:
    """Render the script in one ffmpeg pass, writing no intermediate files."""
    output_path = script.output.file
    output_path.parent.mkdir(parents=True, exist_ok=True)

    clips = expand_regions(script.clips)
    groups = split_into_groups(clips)

    if verbose and not on_progress:
        print(f"  Building filter graph ({len(clips)} clips, {len(groups)} group(s))...")
    if on_stage:
        on_stage("graph", f"{len(clips)} clips in {len(groups)} group(s)")

    filter_complex, v_out, a_out = build_filter_complex(infos, clips, groups, script)

    # -progress and -nostats are global options and must precede all -i flags.
    cmd = ["ffmpeg", "-hide_banner", "-y", "-progress", "pipe:1", "-nostats"]
    for clip in clips:
        cmd += ["-i", str(clip.path)]

    cmd += [
        "-filter_complex", filter_complex,
        "-map", f"[{v_out}]",
        "-map", f"[{a_out}]",
        *encode_args(script.output.encoder, script.output.quality),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
    ]
    # faststart moves the index to the front so a file streams before it has
    # finished downloading. Only mp4-family containers have one to move.
    if output_path.suffix.lower() in (".mp4", ".m4v", ".mov"):
        cmd += ["-movflags", "+faststart"]
    cmd += [str(output_path)]

    if on_stage:
        on_stage("encode", f"{script.output.encoder} -> {output_path.name}")

    if verbose or on_progress:
        _run_with_progress(
            cmd, total_duration(groups, infos, clips), on_progress, on_start, on_stderr
        )
    else:
        subprocess.run(cmd, check=True, capture_output=True)

    return output_path
