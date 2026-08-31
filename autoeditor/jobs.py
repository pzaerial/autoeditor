"""Long work on background threads, pollable by the UI.

Rendering and loudness measurement both take minutes on real footage. Doing
either inside a request means the page can only say "working" and hope; a job
can say how far it has got, and be called off.
"""

import re
import subprocess
import threading
import time
from pathlib import Path

from .loudness import MAX_GAIN_DB, balance_gain, measure_loudness
from .compositor import audio_notes
from .probe import probe_clip, probe_project
from .render import render_project
from .script_parser import check_output_path
from .sysmon import Sampler



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

            clips = [clip for _, clip in script.all_clips()]
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

            infos = probe_project(script, verbose=False, on_step=on_step)
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

            render_project(
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
                # Absolute, so "open output folder" does not have to guess
                # which working directory a relative path was meant against.
                self.output = str(Path(script.output.file).resolve())
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
        """What has to be measured, and how much audio each one is.

        Indexed across the whole project in `all_clips()` order, which is the
        order the app lays its tracks out in -- so a row here names the same
        clip the app will write the level back to.
        """
        work = []
        for index, (_, clip) in enumerate(script.all_clips()):
            # "Only unmeasured" now means a clip nobody has set a level on:
            # there is one number, so a non-zero one is either a measurement
            # already taken or a person's own choice. Neither wants overwriting.
            if only_unmeasured and clip.gain_db:
                continue
            entry = {
                "index": index, "label": clip.label, "gain": None, "note": "",
                "path": clip.source, "spans": None, "seconds": 0.0,
            }
            if clip.missing or not clip.source.is_file():
                entry["note"] = "file not found"
            else:
                try:
                    info = probe_clip(clip.source, allow_audio_only=True)
                except Exception:
                    entry["note"] = "unreadable"
                else:
                    if not info.has_audio:
                        entry["note"] = "no audio track"
                    else:
                        spans = None
                        if clip.source_in > 0 or clip.source_out is not None:
                            end = clip.source_out if clip.source_out is not None                                 else info.duration
                            spans = [(clip.source_in, end)]
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
                        # The cap keeps a bad measurement from turning a clip
                        # into a wall of hiss, but a capped clip does NOT reach
                        # the target -- say by how much, or the results box
                        # claims a match that the render will not deliver.
                        if abs(gain) >= MAX_GAIN_DB - 0.05:
                            short = abs(target - measured.lufs) - MAX_GAIN_DB
                            entry["capped"] = round(short, 1)
                            entry["note"] = (
                                f"capped at {gain:+g} dB -- still {short:.1f} dB "
                                f"{'under' if gain > 0 else 'over'} target"
                            )

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
