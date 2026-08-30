"""Video encoders: how each one spells "quality", and whether it works here.

A hardware encoder can be compiled into ffmpeg and still fail to open on a
machine without that card, so availability is a question only this machine can
answer -- see `encoder_available`.
"""

import subprocess


# What the app offers, in the order it offers them.
ENCODER_CHOICES = [
    ("libx264", "libx264 - CPU"),
    ("h264_nvenc", "h264_nvenc - NVIDIA"),
    ("h264_amf", "h264_amf - AMD"),
    ("h264_qsv", "h264_qsv - Intel"),
    ("libx265", "libx265 - CPU, HEVC"),
    ("hevc_nvenc", "hevc_nvenc - NVIDIA, HEVC"),
]


def _cpu(quality: int) -> list[str]:
    return ["-preset", "fast", "-crf", str(quality)]


def _nvenc(quality: int) -> list[str]:
    # The rate control has to be named. Left to its default, nvenc honours -cq
    # only loosely and writes roughly 2.4x the size of libx264 for the same
    # wall time -- measured, and the reason this is not just ["-cq", n].
    return ["-preset", "p4", "-rc", "vbr", "-cq", str(quality), "-b:v", "0"]


def _amf(quality: int) -> list[str]:
    return ["-quality", "balanced", "-rc", "cqp", "-qp_i", str(quality), "-qp_p", str(quality)]


def _qsv(quality: int) -> list[str]:
    return ["-preset", "fast", "-global_quality", str(quality)]


# Default quality per encoder, then how that number is spelled for it. The
# scale is CRF-like throughout: lower is better and bigger.
_ENCODER_PROFILES: dict[str, tuple[int, object]] = {
    "libx264":    (18, _cpu),
    "libx265":    (22, _cpu),
    "h264_nvenc": (23, _nvenc),
    "hevc_nvenc": (25, _nvenc),
    "av1_nvenc":  (25, _nvenc),
    "h264_amf":   (22, _amf),
    "hevc_amf":   (24, _amf),
    "h264_qsv":   (22, _qsv),
    "hevc_qsv":   (24, _qsv),
}


def encoder_default_quality(encoder: str) -> int:
    return _ENCODER_PROFILES.get(encoder, (18, _cpu))[0]


def encode_args(encoder: str, quality: float | None = None) -> list[str]:
    default, build = _ENCODER_PROFILES.get(encoder, (18, _cpu))
    return ["-c:v", encoder] + build(int(default if quality is None else quality))


_ENCODER_SUPPORT: dict[str, bool] = {}


def encoder_available(encoder: str) -> bool:
    """Whether this machine can actually encode with it, cached per process.

    A hardware encoder can be compiled into ffmpeg and still fail to open --
    the dropdown offered AMD and Intel encoders on a machine with neither,
    which only came out as a failed render.
    """
    if encoder not in _ENCODER_SUPPORT:
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-v", "error",
                    "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=0.2",
                    *encode_args(encoder), "-f", "null", "-",
                ],
                capture_output=True, timeout=30,
            )
            _ENCODER_SUPPORT[encoder] = result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            _ENCODER_SUPPORT[encoder] = False
    return _ENCODER_SUPPORT[encoder]


