# FullControl MTG – Auto Editor

Python tool that assembles MTG content videos from raw recordings using ffmpeg.
A markdown script describes the edit (paths, order, format); the tool renders it
to a single upload-ready mp4.

    python script.py myvideo.md    # headless render
    npm start                      # desktop app (Electron)
    python app.py                  # same UI, in a browser

The CLI has no flags — every knob lives in the markdown file. The app builds the same
model and can export it back to markdown.

**Stack:** Python 3.10+ (stdlib only), ffmpeg/ffprobe on PATH
**Input:** `.mp4` `.mov` `.mkv` `.avi` `.m4v` `.webm` `.wmv` `.flv` — **Output:** h264/aac mp4

**Layout:** `autoeditor/` is the engine plus the local server; `ui/js/` is the front end as
ES modules; `desktop/` is the Electron shell. The engine never imports the server, which is
what keeps a CLI render and an app render the same render.

See also:
- [architecture.md](architecture.md) — modules and what each one does
- [pipeline.md](pipeline.md) — how a script becomes a filter graph
- [script-format.md](script-format.md) — the markdown script language reference
- [ui.md](ui.md) — the app: pieces, backend API, regions
