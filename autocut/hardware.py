"""Detect OS and compute hardware, and pick model sizes accordingly."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass
from functools import lru_cache


@dataclass
class Hardware:
    os: str                 # "linux" | "darwin" | "windows"
    os_pretty: str
    arch: str
    cpu_cores: int
    ram_gb: float
    accelerator: str        # "cuda" | "apple_silicon" | "cpu"
    gpu_name: str | None
    gpu_vram_gb: float | None
    torch_device: str       # "cuda" | "mps" | "cpu"
    whisper_device: str     # faster-whisper / CTranslate2: "cuda" | "cpu"
    whisper_compute: str
    whisper_model: str
    ffmpeg_hwenc: str | None  # e.g. "h264_nvenc", "h264_videotoolbox"

    def as_dict(self) -> dict:
        return asdict(self)


def _ram_gb() -> float:
    try:
        if hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names:
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
    except (ValueError, OSError):
        pass
    try:  # Windows
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))  # type: ignore[attr-defined]
        return m.ullTotalPhys / 1e9
    except Exception:
        return 0.0


def _nvidia() -> tuple[str, float] | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10).stdout.strip().splitlines()
        if out:
            name, mem = out[0].rsplit(",", 1)
            return name.strip(), float(mem) / 1024
    except Exception:
        return None
    return None


def _ffmpeg_encoders() -> str:
    try:
        return subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True,
                              text=True, timeout=10).stdout
    except Exception:
        return ""


@lru_cache(maxsize=1)
def detect() -> Hardware:
    system = platform.system().lower()
    arch = platform.machine().lower()
    cores = os.cpu_count() or 1
    ram = round(_ram_gb(), 1)
    pretty = platform.platform()
    if system == "darwin":
        pretty = f"macOS {platform.mac_ver()[0]}"
    elif system == "linux":
        try:
            pretty = platform.freedesktop_os_release().get("PRETTY_NAME", pretty)
        except OSError:
            pass

    nv = _nvidia()
    encoders = _ffmpeg_encoders()
    hwenc = None
    if nv:
        accel, torch_dev, gpu_name, vram = "cuda", "cuda", nv[0], round(nv[1], 1)
        w_dev, w_comp = "cuda", "float16"
        w_model = "large-v3" if vram >= 8 else ("medium" if vram >= 5 else "small")
        if "h264_nvenc" in encoders:
            hwenc = "h264_nvenc"
    elif system == "darwin" and arch in ("arm64", "aarch64"):
        accel, torch_dev, gpu_name, vram = "apple_silicon", "mps", "Apple GPU", None
        # CTranslate2 has no Metal backend; Apple CPUs are fast enough for medium int8.
        w_dev, w_comp = "cpu", "int8"
        w_model = "medium" if ram >= 16 else "small"
        if "h264_videotoolbox" in encoders:
            hwenc = "h264_videotoolbox"
    else:
        accel, torch_dev, gpu_name, vram = "cpu", "cpu", None, None
        w_dev, w_comp = "cpu", "int8"
        w_model = "medium" if (cores >= 12 and ram >= 16) else "small"

    return Hardware(os=system, os_pretty=pretty, arch=arch, cpu_cores=cores, ram_gb=ram,
                    accelerator=accel, gpu_name=gpu_name, gpu_vram_gb=vram,
                    torch_device=torch_dev, whisper_device=w_dev, whisper_compute=w_comp,
                    whisper_model=os.environ.get("AUTOCUT_WHISPER_MODEL", w_model),
                    ffmpeg_hwenc=hwenc)
