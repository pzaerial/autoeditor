"""Local HTTP backend for the UI. Binds to 127.0.0.1 only."""

import json
import mimetypes
import os
import re
import subprocess
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import project
from .loudness import (
    DEFAULT_TARGET_LUFS, MAX_GAIN_DB, balance_gain, measure_loudness,
)
from .ffmpeg_ops import (
    audio_notes, encoder_available, encoder_default_quality, expand_regions,
    probe_clip, probe_script, render_script,
)
from .script_parser import ScriptError, check_output_path, parse_script
from .script_writer import to_markdown
from .sysmon import Sampler
from .timeline import VIDEO_EXTENSIONS

ROOT = Path(__file__).resolve().parent.parent
UI_DIR = ROOT / "ui"
TEMPLATE_DIR = ROOT / "templates"

# Containers the preview window will open at all.
PLAYABLE_CONTAINERS = {".mp4", ".m4v", ".webm", ".mov", ".mkv"}


def _preview_problem(suffix: str) -> str:
    """Why the preview cannot open this container, or "" when it can.

    Only the container is judged here. Whether a *codec* decodes is a property
    of the machine doing the decoding -- HEVC runs on the platform's hardware
    path or not at all -- so the UI asks its own decoder about `vcodec` instead
    of trusting a list written on someone else's computer.
    """
    if suffix not in PLAYABLE_CONTAINERS:
        return f"{suffix.lstrip('.').upper()} files cannot be opened by the preview."
    return ""


# The pipeline, in order, so the UI can show where a long render actually is.
# Probing and graph building are the slow, silent steps a bare progress bar hides.
STAGES = [
    ("validate", "Check output"),
    ("probe", "Probe clips"),
    ("silence", "Detect silence"),
    ("graph", "Build filter graph"),
    ("encode", "Encode"),
    ("finish", "Finalise"),
]

# ffmpeg repeats these once per input; one copy is informative, forty is noise.
_FFMPEG_NOISE = re.compile(
    r"^\s*(Stream mapping:|\[?(swscaler|swresampler)|"
    r"Last message repeated|frame=|Press \[q\])"
)

MAX_LOG = 4000


class RenderJob:
    """A render running on a background thread, pollable by the UI."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sampler = Sampler()
        self.reset()

    def reset(self) -> None:
        self.state = "idle"
        self.progress = {"elapsed": 0.0, "total": 0.0, "pct": 0.0, "speed": ""}
        self.log: list[dict] = []
        self.stages = [
            {"key": k, "label": label, "state": "pending", "detail": "", "seconds": 0.0}
            for k, label in STAGES
        ]
        self.error = ""
        self.output = ""
        self.started = time.monotonic()
        self.process: subprocess.Popen | None = None

    # -- log -------------------------------------------------------------

    def _write(self, text: str, source: str) -> None:
        with self.lock:
            self.log.append({
                "t": round(time.monotonic() - self.started, 1),
                "src": source,
                "text": text,
            })
            if len(self.log) > MAX_LOG:
                del self.log[: len(self.log) - MAX_LOG]

    def say(self, message: str) -> None:
        self._write(message, "app")

    def from_ffmpeg(self, line: str) -> None:
        if line.strip() and not _FFMPEG_NOISE.match(line):
            self._write(line, "ffmpeg")

    # -- stages ----------------------------------------------------------

    def _stage(self, key: str, state: str, detail: str = "") -> None:
        with self.lock:
            now = time.monotonic()
            for stage in self.stages:
                if stage["key"] != key:
                    continue
                if state == "running" and stage["state"] == "pending":
                    stage["_at"] = now
                if state in ("done", "failed"):
                    stage["seconds"] = round(now - stage.get("_at", now), 1)
                stage["state"] = state
                if detail:
                    stage["detail"] = detail

    def _detail(self, key: str, detail: str) -> None:
        with self.lock:
            for stage in self.stages:
                if stage["key"] == key:
                    stage["detail"] = detail

    def _fail_running_stages(self) -> None:
        with self.lock:
            for stage in self.stages:
                if stage["state"] == "running":
                    stage["state"] = "failed"

    def snapshot(self, since: int = 0) -> dict:
        """State plus only the log lines the caller has not seen yet."""
        with self.lock:
            total = len(self.log)
            since = max(0, min(since, total))
            return {
                "state": self.state,
                "progress": dict(self.progress),
                "stages": [
                    {k: v for k, v in stage.items() if not k.startswith("_")}
                    for stage in self.stages
                ],
                "log": self.log[since:],
                "log_from": since,
                "log_total": total,
                "samples": self.sampler.samples(),
                "has_gpu": self.sampler.has_gpu,
                "error": self.error,
                "output": self.output,
                "elapsed": round(time.monotonic() - self.started, 1),
            }

    # -- lifecycle -------------------------------------------------------

    def start(self, script) -> None:
        with self.lock:
            if self.state in ("probing", "rendering"):
                raise RuntimeError("a render is already running")
            self.reset()
            self.state = "probing"
        self.sampler.start()
        threading.Thread(target=self._run, args=(script,), daemon=True).start()

    def cancel(self) -> None:
        with self.lock:
            proc, running = self.process, self.state in ("probing", "rendering")
            if running:
                self.state = "cancelled"
        if proc and proc.poll() is None:
            proc.terminate()

    def _run(self, script) -> None:
        try:
            self._stage("validate", "running")
            check_output_path(script.output.file)
            self._stage("validate", "done", str(script.output.file))

            clips = expand_regions(script.clips)
            detecting = any(c.trim_silence for c in clips)
            if not detecting:
                self._stage("silence", "skipped", "no clip asks for it")

            self._stage("probe", "running", f"0 / {len(clips)}")
            if detecting:
                self._stage("silence", "running")
            self.say(f"Probing {len(clips)} clips...")

            def on_step(done, total, message):
                self._detail("probe", f"{done} / {total} - {message}")
                if detecting and "silence" in message:
                    self._detail("silence", message)

            infos = probe_script(script, verbose=False, on_step=on_step)
            self._stage("probe", "done", f"{len(clips)} clips")
            if detecting:
                self._stage("silence", "done")

            for note in audio_notes(script, infos):
                self.say(f"  warning: {note}")

            for clip, info in zip(clips, infos):
                if info.keep_intervals:
                    kept = sum(b - a for a, b in info.keep_intervals)
                    self.say(
                        f"  {clip.label}: {len(info.keep_intervals)} segment(s), "
                        f"{kept:.1f}s of {info.duration:.1f}s"
                    )

            with self.lock:
                if self.state == "cancelled":
                    return
                self.state = "rendering"

            def on_progress(elapsed, total, pct, speed):
                with self.lock:
                    self.progress = {
                        "elapsed": elapsed, "total": total, "pct": pct, "speed": speed
                    }

            def on_start(proc):
                with self.lock:
                    self.process = proc
                # The graph is finished before ffmpeg is spawned.
                self._stage("graph", "done")
                self._stage("encode", "running")

            def on_stage(key, detail):
                self._stage(key, "running", detail)
                self.say(
                    f"Building filter graph ({detail})..." if key == "graph"
                    else f"Encoding: {detail}"
                )

            render_script(
                script, infos, verbose=False,
                on_progress=on_progress, on_start=on_start,
                on_stderr=self.from_ffmpeg, on_stage=on_stage,
            )

            with self.lock:
                cancelled = self.state == "cancelled"
            if cancelled:
                self._fail_running_stages()
                self.say("Cancelled.")
                return

            self._stage("encode", "done")
            self._stage("finish", "running")
            with self.lock:
                self.state = "done"
                self.output = str(script.output.file)
                self.progress["pct"] = 100.0
            self._stage("finish", "done", str(script.output.file))
            self.say(f"Done: {script.output.file}")

        except Exception as exc:
            with self.lock:
                cancelled = self.state == "cancelled"
                if not cancelled:
                    self.state = "error"
                    self.error = str(exc)
            self._fail_running_stages()
            if cancelled:
                self.say("Cancelled.")
                return
            detail = getattr(exc, "stderr", "") or ""
            for line in detail.strip().splitlines()[-25:]:
                self.from_ffmpeg(line)
            self.say(f"Error: {exc}")
        finally:
            self.sampler.stop()


JOB = RenderJob()


class BalanceJob:
    """Loudness measurement running on a background thread, pollable by the UI.

    Measuring reads every clip's audio, which for a two-hour recording is a real
    wait. Doing it inside the request meant the page could only say "measuring"
    and hope; a job can say how far it has got and be called off.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.state = "idle"
        self.clips: list[dict] = []
        self.current = ""
        self.done_seconds = 0.0
        self.total_seconds = 0.0
        self.error = ""
        self.process: subprocess.Popen | None = None

    def snapshot(self) -> dict:
        with self.lock:
            total = self.total_seconds
            return {
                "state": self.state,
                "clips": list(self.clips),
                "current": self.current,
                # Weighted by how much audio each clip contributes, so one long
                # clip among short ones does not sit at 0% and then finish.
                "pct": round(min(100.0, 100.0 * self.done_seconds / total), 1) if total else 0.0,
                "done_seconds": round(self.done_seconds, 1),
                "total_seconds": round(total, 1),
                "error": self.error,
            }

    def start(self, script, target: float, only_unmeasured: bool) -> None:
        with self.lock:
            if self.state == "running":
                raise RuntimeError("a measurement is already running")
            self.reset()
            self.state = "running"
        threading.Thread(
            target=self._run, args=(script, target, only_unmeasured), daemon=True
        ).start()

    def cancel(self) -> None:
        with self.lock:
            proc = self.process
            if self.state == "running":
                self.state = "cancelled"
        if proc and proc.poll() is None:
            proc.terminate()

    def _plan(self, script, only_unmeasured: bool) -> list[dict]:
        """What has to be measured, and how much audio each one is."""
        work = []
        for index, clip in enumerate(script.clips):
            if only_unmeasured and clip.balance_db is not None:
                continue
            entry = {
                "index": index, "label": clip.label, "gain": None, "note": "",
                "path": clip.path, "spans": None, "seconds": 0.0,
            }
            if clip.missing or not clip.path.is_file():
                entry["note"] = "file not found"
            else:
                try:
                    info = probe_clip(clip.path)
                except Exception:
                    entry["note"] = "unreadable"
                else:
                    if not info.has_audio:
                        entry["note"] = "no audio track"
                    else:
                        spans = [r.as_tuple() for r in clip.regions] if clip.regions else None
                        entry["spans"] = spans
                        entry["seconds"] = (
                            sum(b - a for a, b in spans) if spans else info.duration
                        )
            work.append(entry)
        return work

    def _run(self, script, target: float, only_unmeasured: bool) -> None:
        try:
            work = self._plan(script, only_unmeasured)
            with self.lock:
                self.total_seconds = sum(w["seconds"] for w in work) or 1.0
                self.clips = [
                    {k: v for k, v in w.items() if k not in ("path", "spans")}
                    for w in work
                ]

            cache: dict[tuple, object] = {}
            for position, item in enumerate(work):
                with self.lock:
                    if self.state == "cancelled":
                        return
                    self.current = item["label"]
                if item["note"]:
                    with self.lock:
                        self.done_seconds += item["seconds"]
                    continue

                base = self.done_seconds
                key = (item["path"], tuple(item["spans"]) if item["spans"] else None)
                if key in cache:
                    measured = cache[key]
                else:
                    def progress(seconds: float, base=base) -> None:
                        with self.lock:
                            self.done_seconds = base + seconds

                    def started(proc) -> None:
                        with self.lock:
                            self.process = proc

                    measured = measure_loudness(
                        item["path"], item["spans"],
                        on_progress=progress, on_start=started,
                    )
                    cache[key] = measured

                with self.lock:
                    if self.state == "cancelled":
                        return
                    self.done_seconds = base + item["seconds"]
                    entry = self.clips[position]
                    if measured is None or not measured.usable:
                        entry["note"] = "silent or unmeasurable"
                    else:
                        gain = balance_gain(measured, target)
                        entry.update(
                            lufs=round(measured.lufs, 1),
                            peak=round(measured.peak_dbtp, 1),
                            gain=gain,
                        )
                        if abs(gain) >= MAX_GAIN_DB - 0.05:
                            entry["note"] = f"limited to {gain:+g} dB"

            with self.lock:
                if self.state == "running":
                    self.state = "done"
                    self.done_seconds = self.total_seconds
                    self.current = ""
        except Exception as exc:
            with self.lock:
                if self.state == "running":
                    self.state = "error"
                    self.error = str(exc)


BALANCE = BalanceJob()


ENCODER_CHOICES = [
    ("libx264", "libx264 - CPU"),
    ("h264_nvenc", "h264_nvenc - NVIDIA"),
    ("h264_amf", "h264_amf - AMD"),
    ("h264_qsv", "h264_qsv - Intel"),
    ("libx265", "libx265 - CPU, HEVC"),
    ("hevc_nvenc", "hevc_nvenc - NVIDIA, HEVC"),
]

_SAFE_NAME = re.compile(r"[^\w .#()\[\]&,+-]+")


def _template_target(raw: str, title: str) -> Path:
    """Where Save As writes: a bare name lands in templates/, a path is honoured."""
    text = (raw or title or "template").strip().strip('"')
    path = Path(text)
    if not path.suffix:
        path = path.with_suffix(".md")
    if path.parent == Path("."):
        path = TEMPLATE_DIR / _SAFE_NAME.sub("-", path.name)
    return path


def _probe_summary(path: Path) -> dict:
    """Metadata for one file, tolerating anything ffprobe cannot read."""
    suffix = path.suffix.lower()
    entry = {
        "path": str(path),
        "name": path.name,
        "size": path.stat().st_size if path.is_file() else 0,
    }
    info = None
    try:
        info = probe_clip(path)
        entry.update(
            duration=info.duration, width=info.width, height=info.height,
            fps=round(info.fps, 3), has_audio=info.has_audio,
            vcodec=info.vcodec, acodec=info.acodec, pix_fmt=info.pix_fmt,
        )
    except Exception as exc:
        entry["error"] = str(exc)

    problem = _preview_problem(suffix)
    entry["playable"] = not problem
    entry["preview_note"] = problem
    return entry


class Handler(BaseHTTPRequestHandler):
    server_version = "AutoEditor"
    # 1.1 keeps the connection alive, which matters when a browser seeks a lot.
    protocol_version = "HTTP/1.1"

    responded = False

    def handle_one_request(self) -> None:
        self.responded = False
        try:
            super().handle_one_request()
        except OSError:
            # A dropped keep-alive connection: routine while scrubbing video.
            self.close_connection = True

    def log_message(self, *args) -> None:
        pass

    # -- helpers ---------------------------------------------------------

    def _json(self, data, status: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self.responded = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, message: str, status: int = 400) -> None:
        # Headers already went out (a media stream, say) -- nothing left to say.
        if getattr(self, "responded", False):
            return
        self._json({"error": message}, status)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def _query(self) -> dict:
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    # -- static ----------------------------------------------------------

    def _serve_ui(self, rel: str) -> None:
        target = (UI_DIR / (rel or "index.html")).resolve()
        if not target.is_file() or UI_DIR.resolve() not in target.parents:
            self._fail("not found", 404)
            return
        body = target.read_bytes()
        self.responded = True
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_media(self, path: Path) -> None:
        """Serve a video, honouring Range so the browser can seek in big files."""
        if not path.is_file():
            self._fail("not found", 404)
            return

        size = path.stat().st_size
        start, end = 0, size - 1
        header = self.headers.get("Range", "")
        partial = header.startswith("bytes=")

        if partial:
            spec = header[6:].split(",")[0].strip()
            first, _, last = spec.partition("-")
            if first:
                start = int(first)
                end = int(last) if last else end
            elif last:  # suffix range: last N bytes
                start = max(0, size - int(last))
            if start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(end, size - 1)

        length = end - start + 1
        self.responded = True
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()

        try:
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except OSError:
            return  # the browser seeked away and dropped the connection

    def _serve_thumb(self, path: Path, at: float) -> None:
        """One JPEG frame, seeking before the input so it stays fast."""
        result = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-ss", f"{at:.3f}", "-i", str(path),
                "-frames:v", "1", "-vf", "scale=160:-2",
                "-f", "image2pipe", "-vcodec", "mjpeg", "-",
            ],
            capture_output=True,
        )
        if result.returncode != 0 or not result.stdout:
            self._fail("no frame", 404)
            return
        self.responded = True
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(result.stdout)))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(result.stdout)

    # -- routes ----------------------------------------------------------

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        query = self._query()
        try:
            if route == "/api/templates":
                self._json({
                    "dir": str(TEMPLATE_DIR),
                    "templates": [
                        {"name": p.stem, "path": str(p)}
                        for p in sorted(TEMPLATE_DIR.glob("*.md"))
                    ],
                })

            elif route == "/api/template":
                script = parse_script(Path(query["path"]), strict=False)
                self._json(project.to_json(script))

            elif route == "/api/balance-audio":
                self._json(BALANCE.snapshot())

            elif route == "/api/encoders":
                self._json({"encoders": [
                    {
                        "name": name,
                        "label": label,
                        "hardware": name != "libx264" and name != "libx265",
                        "available": encoder_available(name),
                        "default_quality": encoder_default_quality(name),
                    }
                    for name, label in ENCODER_CHOICES
                ]})

            elif route == "/api/browse":
                self._json(self._browse(query.get("path", "")))

            elif route == "/api/probe":
                self._json(_probe_summary(Path(query["path"])))

            elif route == "/api/render":
                self._json(JOB.snapshot(int(query.get("since", 0) or 0)))

            elif route == "/api/check-output":
                self._json(self._check_output(query.get("path", "")))

            elif route == "/media":
                self._serve_media(Path(unquote(query["path"])))

            elif route == "/thumb":
                self._serve_thumb(Path(unquote(query["path"])), float(query.get("t", 0)))

            else:
                self._serve_ui(route.lstrip("/"))

        except ScriptError as exc:
            self._fail(str(exc))
        except KeyError as exc:
            self._fail(f"missing parameter {exc}")
        except OSError:
            return
        except Exception as exc:
            traceback.print_exc()
            self._fail(str(exc), 500)

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        try:
            if route == "/api/save-template":
                data = self._body()
                script = project.from_json(data["project"])
                target = _template_target(data.get("path", ""), script.title)
                check_output_path(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    to_markdown(script, notes=data.get("notes", "")), encoding="utf-8"
                )
                self._json({"path": str(target), "name": target.stem})

            elif route == "/api/balance-audio":
                data = self._body()
                script = project.from_json(data["project"])
                target = float(data.get("target", script.balance.target_lufs))
                BALANCE.start(script, target, bool(data.get("only_unmeasured")))
                self._json({"target": target, **BALANCE.snapshot()})

            elif route == "/api/balance-audio/cancel":
                BALANCE.cancel()
                self._json(BALANCE.snapshot())

            elif route == "/api/render":
                script = project.from_json(self._body()["project"])
                if not script.clips:
                    self._fail("the timeline is empty")
                    return
                JOB.start(script)
                self._json(JOB.snapshot())

            elif route == "/api/render/cancel":
                JOB.cancel()
                self._json(JOB.snapshot())

            elif route == "/api/reveal":
                target = Path(self._body()["path"])
                folder = target if target.is_dir() else target.parent
                if folder.is_dir():
                    os.startfile(folder)  # noqa: S606 -- local desktop tool
                self._json({"ok": True})

            else:
                self._fail("unknown endpoint", 404)

        except ScriptError as exc:
            self._fail(str(exc))
        except OSError:
            return
        except Exception as exc:
            traceback.print_exc()
            self._fail(str(exc), 500)

    # -- data ------------------------------------------------------------

    def _browse(self, raw: str) -> dict:
        folder = Path(raw).expanduser() if raw else Path.home()
        if not folder.is_dir():
            folder = Path.home()
        folder = folder.resolve()

        dirs, files = [], []
        try:
            for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    dirs.append({"name": entry.name, "path": str(entry)})
                elif entry.suffix.lower() in VIDEO_EXTENSIONS:
                    files.append(entry)
        except PermissionError:
            pass

        files.sort(key=lambda p: (p.stat().st_mtime, p.name))
        return {
            "path": str(folder),
            "parent": str(folder.parent) if folder.parent != folder else "",
            "dirs": dirs,
            "files": [_probe_summary(f) for f in files],
        }

    def _check_output(self, raw: str) -> dict:
        if not raw.strip():
            return {"ok": False, "error": "no output file set"}
        try:
            check_output_path(Path(raw))
        except ScriptError as exc:
            return {"ok": False, "error": str(exc)}
        parent = Path(raw).parent
        return {"ok": True, "exists": Path(raw).is_file(), "folder_exists": parent.is_dir()}


def serve(host: str = "127.0.0.1", port: int = 8420) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), Handler)
    return httpd
