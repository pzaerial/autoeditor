"""Local HTTP backend for the UI. Binds to 127.0.0.1 only.

Only HTTP lives here: reading requests, writing responses, and routing. What
each route *means* belongs to the modules it calls -- `library` for the files on
disk, `jobs` for work that outlives a request, `project` for the JSON shape the
UI speaks.
"""

import json
import mimetypes
import subprocess
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import effects, library, project
from .encoders import ENCODER_CHOICES, encoder_available, encoder_default_quality
from .jobs import BALANCE, JOB
from .library import ROOT, TEMPLATE_DIR, probe_summary, template_target
from .script_parser import ScriptError, check_output_path, parse_script
from .track_script import parse_project, to_markdown

UI_DIR = ROOT / "ui"


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
                self._json({"dir": str(TEMPLATE_DIR), "templates": library.templates()})

            elif route == "/api/template":
                # Reads either shape of script: an older one is migrated on the
                # way in, so opening it in the timeline is all it takes to
                # convert, and saving writes it back in the new one.
                self._json(project.project_to_json(
                    parse_project(Path(query["path"]), strict=False)))

            elif route == "/api/library":
                # The effect and transition library, so the inspector builds its
                # controls from the same declaration the renderer reads.
                self._json(effects.describe_library())

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
                self._json(library.browse(query.get("path", "")))

            elif route == "/api/probe":
                self._json(probe_summary(Path(query["path"])))

            elif route == "/api/render":
                self._json(JOB.snapshot(int(query.get("since", 0) or 0)))

            elif route == "/api/check-output":
                self._json(library.check_output(query.get("path", "")))

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
                script = project.project_from_json(data["project"])
                target = template_target(data.get("path", ""), script.title)
                check_output_path(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    to_markdown(script, notes=data.get("notes", "")), encoding="utf-8"
                )
                self._json({"path": str(target), "name": target.stem})

            elif route == "/api/balance-audio":
                data = self._body()
                script = project.project_from_json(data["project"])
                target = float(data.get("target", script.balance.target_lufs))
                BALANCE.start(script, target, bool(data.get("only_unmeasured")))
                self._json({"target": target, **BALANCE.snapshot()})

            elif route == "/api/balance-audio/cancel":
                BALANCE.cancel()
                self._json(BALANCE.snapshot())

            elif route == "/api/render":
                script = project.project_from_json(self._body()["project"])
                if not script.clips:
                    self._fail("the timeline is empty")
                    return
                JOB.start(script)
                self._json(JOB.snapshot())

            elif route == "/api/render/cancel":
                JOB.cancel()
                self._json(JOB.snapshot())

            elif route == "/api/reveal":
                self._json({"ok": library.reveal(Path(self._body()["path"]))})

            else:
                self._fail("unknown endpoint", 404)

        except ScriptError as exc:
            self._fail(str(exc))
        except OSError:
            return
        except Exception as exc:
            traceback.print_exc()
            self._fail(str(exc), 500)


def serve(host: str = "127.0.0.1", port: int = 8420) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Handler)
