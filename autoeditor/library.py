"""The files the app offers you: browsing them, and what it can say about each.

Kept apart from the HTTP layer because none of it is about HTTP -- these are
questions about the filesystem and about what the preview window can open.
"""

import re
import subprocess
import sys
from pathlib import Path

from .probe import probe_clip
from .script_parser import ScriptError, check_output_path
from .timeline import VIDEO_EXTENSIONS

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = ROOT / "templates"


PLAYABLE_CONTAINERS = {".mp4", ".m4v", ".webm", ".mov", ".mkv"}


def preview_problem(suffix: str) -> str:
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
_SAFE_NAME = re.compile(r"[^\w .#()\[\]&,+-]+")


def template_target(raw: str, title: str) -> Path:
    """Where Save As writes: a bare name lands in templates/, a path is honoured."""
    text = (raw or title or "template").strip().strip('"')
    path = Path(text)
    if not path.suffix:
        path = path.with_suffix(".md")
    if path.parent == Path("."):
        path = TEMPLATE_DIR / _SAFE_NAME.sub("-", path.name)
    return path


def probe_summary(path: Path) -> dict:
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

    problem = preview_problem(suffix)
    entry["playable"] = not problem
    entry["preview_note"] = problem
    return entry


def browse(raw: str) -> dict:
    """Folders and playable video files inside one directory."""
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
        "files": [probe_summary(f) for f in files],
    }


def check_output(raw: str) -> dict:
    """Whether this path can be written, and whether something is already there."""
    if not raw.strip():
        return {"ok": False, "error": "no output file set"}
    try:
        check_output_path(Path(raw))
    except ScriptError as exc:
        return {"ok": False, "error": str(exc)}
    parent = Path(raw).parent
    return {"ok": True, "exists": Path(raw).is_file(), "folder_exists": parent.is_dir()}


def templates() -> list[dict]:
    return [
        {"name": p.stem, "path": str(p)}
        for p in sorted(TEMPLATE_DIR.glob("*.md"))
    ]


def reveal(target: Path) -> bool:
    """Show a file in the desktop's file manager, selected where possible.

    The app prefers its Electron shell for this, because Windows only lets the
    *foreground* process pass focus on and that is the shell, not this backend --
    a folder opened from here can land behind the app window. This is the path a
    plain browser takes, and the fallback if the bridge is missing.
    """
    if not target.exists() and not target.parent.is_dir():
        return False

    if sys.platform == "win32":
        # /select opens the folder with the file highlighted, which beats
        # opening the folder and leaving them to find it.
        argument = f"/select,{target}" if target.exists() else str(target.parent)
        subprocess.Popen(["explorer", argument])
        return True

    opener = "open" if sys.platform == "darwin" else "xdg-open"
    folder = target if target.is_dir() else target.parent
    try:
        subprocess.Popen([opener, str(folder)])
    except OSError:
        return False
    return True
