"""CPU and GPU utilisation sampling, for the render page's load graph.

Stdlib only: Windows CPU comes from `GetSystemTimes` through ctypes, Linux from
`/proc/stat`, and GPU from `nvidia-smi` when it happens to be on PATH. Anything
that cannot be read reports None, which the UI draws as a missing trace rather
than a zero.
"""

import os
import shutil
import subprocess
import threading
import time

# Every sample is a process spawn for the GPU, so keep the cadence gentle.
INTERVAL = 1.0
# 20 minutes at the default interval; long renders drop their oldest samples.
LIMIT = 1200


class _CpuCounter:
    """Busy percentage since the previous call, or None where unsupported."""

    def __init__(self) -> None:
        self._previous: tuple[float, float] | None = None
        self._read = self._pick_reader()

    def _pick_reader(self):
        if os.name == "nt":
            try:
                import ctypes
                from ctypes import wintypes

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                get_times = kernel32.GetSystemTimes
                get_times.argtypes = [
                    ctypes.POINTER(wintypes.FILETIME)
                ] * 3
                get_times.restype = wintypes.BOOL

                def read() -> tuple[float, float] | None:
                    idle, kernel, user = (wintypes.FILETIME() for _ in range(3))
                    if not get_times(
                        ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
                    ):
                        return None
                    def whole(ft) -> float:
                        return (ft.dwHighDateTime << 32) | ft.dwLowDateTime
                    # Kernel time already includes idle time.
                    return whole(idle), whole(kernel) + whole(user)

                return read
            except Exception:
                return None

        def read_proc() -> tuple[float, float] | None:
            try:
                with open("/proc/stat", encoding="ascii") as handle:
                    fields = [float(v) for v in handle.readline().split()[1:]]
            except OSError:
                return None
            return fields[3], sum(fields)

        return read_proc if os.path.exists("/proc/stat") else None

    def percent(self) -> float | None:
        if self._read is None:
            return None
        now = self._read()
        if now is None:
            return None
        before, self._previous = self._previous, now
        if before is None:
            return None
        idle = now[0] - before[0]
        total = now[1] - before[1]
        if total <= 0:
            return None
        return max(0.0, min(100.0, 100.0 * (1.0 - idle / total)))


class _GpuCounter:
    """NVIDIA load, split by engine, or None when nvidia-smi is absent.

    The graphics engine and the encoder are separate hardware. NVENC is a
    fixed-function block that barely touches the shaders, so a card encoding
    flat out still reports `utilization.gpu` in the low teens -- which reads as
    an idle GPU unless the encoder engine is reported alongside it.
    """

    def __init__(self) -> None:
        self.available = shutil.which("nvidia-smi") is not None

    def sample(self) -> tuple[float | None, float | None]:
        """(graphics %, encoder %)"""
        if not self.available:
            return None, None
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,utilization.encoder",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True, text=True, timeout=4,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            self.available = False  # do not keep paying for a tool that fails
            return None, None

        graphics: list[float] = []
        encoder: list[float] = []
        for line in result.stdout.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                continue
            try:
                graphics.append(float(parts[0]))
                encoder.append(float(parts[1]))
            except ValueError:
                continue
        if not graphics:
            self.available = False
            return None, None
        return max(graphics), max(encoder)


class Sampler:
    """Samples utilisation on a background thread while a render runs."""

    def __init__(self, interval: float = INTERVAL, limit: int = LIMIT) -> None:
        self.interval = interval
        self.limit = limit
        self._lock = threading.Lock()
        self._samples: list[dict] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._gpu = _GpuCounter()

    @property
    def has_gpu(self) -> bool:
        return self._gpu.available

    def start(self) -> None:
        self.stop()
        with self._lock:
            self._samples = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None

    def samples(self) -> list[dict]:
        with self._lock:
            return list(self._samples)

    def _loop(self) -> None:
        stop = self._stop
        cpu = _CpuCounter()
        cpu.percent()  # first reading only primes the delta
        started = time.monotonic()
        while not stop.wait(self.interval):
            graphics, encoder = self._gpu.sample()
            entry = {
                "t": round(time.monotonic() - started, 2),
                "cpu": cpu.percent(),
                "gpu": graphics,
                "enc": encoder,
            }
            with self._lock:
                self._samples.append(entry)
                if len(self._samples) > self.limit:
                    del self._samples[: len(self._samples) - self.limit]
