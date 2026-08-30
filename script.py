"""Render a video from a markdown script: python script.py myvideo.md"""

import subprocess
import sys
from pathlib import Path

from autoeditor.graph import audio_notes
from autoeditor.probe import probe_script
from autoeditor.render import render_script
from autoeditor.timecode import format_time
from autoeditor.script_parser import ScriptError, parse_script

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
        script = parse_script(Path(argv[0]))
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
    print(f"Timeline ({len(script.clips)} clips):")
    print(script.describe())

    if script.output.dry_run:
        print("\nDry run -- nothing rendered.")
        return 0

    _check_tools()

    print("\n  Probing clips...")
    infos = probe_script(script)
    print(f"  Source material: {format_time(sum(i.effective_duration for i in infos))}")
    for note in audio_notes(script, infos):
        print(f"  warning: {note}")

    try:
        output = render_script(script, infos)
    except subprocess.CalledProcessError as exc:
        print("\nffmpeg failed:", file=sys.stderr)
        print("\n".join((exc.stderr or "").strip().splitlines()[-20:]), file=sys.stderr)
        return 1

    print(f"Done -> {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
