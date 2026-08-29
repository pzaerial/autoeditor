# FullControl MTG – Auto Editor

Python tool that assembles MTG content videos from raw recordings using ffmpeg.
A markdown script describes the edit (paths, order, format); the tool renders it
to a single upload-ready mp4.

    python script.py myvideo.md

No command-line flags — every knob lives in the markdown file.

**Stack:** Python 3.10+ (stdlib only), ffmpeg/ffprobe on PATH
**Input:** `.mp4` `.mov` `.mkv` `.avi` `.m4v` `.webm` `.wmv` `.flv` — **Output:** h264/aac mp4

See also:
- [architecture.md](architecture.md) — modules and what each one does
- [pipeline.md](pipeline.md) — how a script becomes a filter graph
- [script-format.md](script-format.md) — the markdown script language reference
