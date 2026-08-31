"""Render a video from a markdown script: python script.py myvideo.md"""

import subprocess
import sys
from pathlib import Path

from autoeditor.compositor import audio_notes, clip_lengths
from autoeditor.probe import probe_project
from autoeditor.render import render_project
from autoeditor.timecode import format_time
from autoeditor.script_parser import ScriptError
from autoeditor.track_script import parse_project

USAGE = "usage: python script.py <script.md>"


def _check_tools() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        try:
            subprocess.run([tool, "-version"], capture_output=True, check=True)
        except (OSError, subprocess.CalledProcessError):
            print(f"Error: {tool} not found on PATH. Install ffmpeg first.", file=sys.stderr)
            sys.exit(1)


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] in ("-h", "--help", "/?"):
        print(USAGE, file=sys.stderr)
        return 0 if argv and argv[0] in ("-h", "--help", "/?") else 2

    try:
        # Reads a track timeline, and an older flat script by migrating it --
        # the same reader the app uses, so both render the same file.
        script = parse_project(Path(argv[0]))
    except ScriptError as exc:
        print(f"Error in {argv[0]}: {exc}", file=sys.stderr)
        return 1

    print(f"Script : {script.source}")
    print(f"Title  : {script.title}")
    print(f"Output : {script.output.file}")
    print(
        f"Format : {script.output.resolution} @ {script.output.fps}fps"
        f"  ({script.output.encoder})"
    )
    clips = script.all_clips()
    print(f"Timeline ({len(clips)} clips on {len(script.tracks)} track(s)):")
    print(script.describe())

    if script.output.dry_run:
        print("\nDry run -- nothing rendered.")
        return 0

    _check_tools()

    print("\n  Probing clips...")
    infos = probe_project(script)
    print(f"  Source material: {format_time(sum(i.effective_duration for i in infos))}")
    # Lengths are only known once the clips have been probed, so the rundown is
    # shown again -- this time with the time each clip actually plays at.
    print(script.describe(dict(enumerate(clip_lengths(script, infos)))))
    for note in audio_notes(script, infos):
        print(f"  warning: {note}")

    try:
        output = render_project(script, infos)
    except subprocess.CalledProcessError as exc:
        print("\nffmpeg failed:", file=sys.stderr)
        print("\n".join((exc.stderr or "").strip().splitlines()[-20:]), file=sys.stderr)
        return 1

    print(f"Done -> {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
