"""
Hardware & Platform Auto-Detection Engine
Detects:
- Host OS & Architecture (Ubuntu x86_64 vs Termux Android aarch64 SDM636)
- CPU Cores & RAM limits
- Hardware Video Encoders (Intel VAAPI, NVIDIA NVENC, or Multi-threaded CPU libx264)
"""

import os
import sys
import platform
import subprocess
import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class HardwareProfile:
    os_name: str
    is_termux: bool
    arch: str
    cpu_cores: int
    total_ram_mb: int
    available_ram_mb: int
    cpu_model: str
    video_encoder: str
    hevc_encoder: str
    hwaccel_type: str
    hwaccel_device: Optional[str]
    description: str

    def get_ffmpeg_video_args(self, target_codec: str = "h264", quality_preset: str = "medium", crf: int = 23, maxrate_kbps: Optional[int] = None) -> List[str]:
        """
        Generates optimal FFmpeg video encoding arguments based on detected hardware.
        """
        args = []

        if self.hwaccel_type == "vaapi" and self.hwaccel_device:
            # VAAPI Hardware Acceleration (Intel / AMD GPU)
            args.extend(["-init_hw_device", f"vaapi=va:{self.hwaccel_device}", "-filter_hw_device", "va"])
            
            # Hardware filter upload
            vf_chain = "format=nv12,hwupload"
            args.extend(["-vf", vf_chain])
            
            encoder = self.video_encoder if target_codec == "h264" else self.hevc_encoder
            args.extend(["-c:v", encoder])
            
            # VAAPI quality control (-qp or -b:v)
            if maxrate_kbps:
                args.extend(["-b:v", f"{maxrate_kbps}k", "-maxrate", f"{int(maxrate_kbps * 1.2)}k", "-bufsize", f"{maxrate_kbps * 2}k"])
            else:
                args.extend(["-qp", str(crf)])
                
        elif self.hwaccel_type == "nvenc":
            # NVIDIA NVENC Hardware Acceleration
            encoder = "h264_nvenc" if target_codec == "h264" else "hevc_nvenc"
            args.extend(["-c:v", encoder, "-preset", "p4"])
            if maxrate_kbps:
                args.extend(["-b:v", f"{maxrate_kbps}k", "-maxrate", f"{int(maxrate_kbps * 1.2)}k", "-bufsize", f"{maxrate_kbps * 2}k"])
            else:
                args.extend(["-cq", str(crf)])
        else:
            # Multi-threaded CPU Software Encoding (libx264 / libx265)
            # Optimized for Termux (Nokia 6.1 Plus SDM636) and general CPU
            encoder = "libx264" if target_codec == "h264" else "libx265"
            args.extend(["-c:v", encoder])
            
            # Mobile Termux vs Desktop CPU preset
            if self.is_termux:
                args.extend(["-preset", "veryfast", "-tune", "zerolatency"])
            else:
                preset_map = {
                    "ultrafast": "ultrafast",
                    "veryfast": "veryfast",
                    "fast": "fast",
                    "medium": "medium"
                }
                args.extend(["-preset", preset_map.get(quality_preset, "fast")])

            args.extend(["-crf", str(crf), "-threads", str(self.cpu_cores)])
            
            if maxrate_kbps:
                args.extend(["-maxrate", f"{maxrate_kbps}k", "-bufsize", f"{maxrate_kbps * 2}k"])
                
            # Pixel format for maximum device compatibility
            args.extend(["-pix_fmt", "yuv420p"])

        return args


def is_running_in_termux() -> bool:
    """Checks if the script is executing inside an Android Termux environment."""
    return (
        "com.termux" in os.environ.get("PREFIX", "")
        or "com.termux" in os.environ.get("HOME", "")
        or os.path.exists("/data/data/com.termux")
    )


def get_cpu_model() -> str:
    """Extracts CPU model name from /proc/cpuinfo or platform."""
    try:
        if os.path.exists("/proc/cpuinfo"):
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "model name" in line:
                        return line.split(":", 1)[1].strip()
                    elif "Hardware" in line:
                        return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or "Multi-Core CPU"


def test_ffmpeg_encoder(encoder_name: str, extra_args: List[str] = None) -> bool:
    """Tests if a specific FFmpeg encoder works by attempting a tiny frame encode."""
    cmd = ["ffmpeg", "-y"]
    if extra_args:
        cmd.extend(extra_args)
    cmd.extend([
        "-f", "lavfi",
        "-i", "color=c=black:s=64x64:d=0.04",
        "-c:v", encoder_name,
        "-f", "null",
        "-"
    ])
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3
        )
        return proc.returncode == 0
    except Exception:
        return False


def test_vaapi_device(device_path: str = "/dev/dri/renderD128") -> bool:
    """Tests if VAAPI hardware encoding functions on the given DRI device."""
    if not os.path.exists(device_path):
        return False
    cmd = [
        "ffmpeg", "-y",
        "-init_hw_device", f"vaapi=va:{device_path}",
        "-f", "lavfi",
        "-i", "color=c=black:s=64x64:d=0.04",
        "-filter_hw_device", "va",
        "-vf", "format=nv12,hwupload",
        "-c:v", "h264_vaapi",
        "-f", "null",
        "-"
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3
        )
        return proc.returncode == 0
    except Exception:
        return False


def detect_hardware() -> HardwareProfile:
    """
    Auto-detects host system capabilities, memory, cores, and GPU hardware acceleration.
    """
    is_termux = is_running_in_termux()
    arch = platform.machine()
    cores = os.cpu_count() or 4
    cpu_model = get_cpu_model()

    # Memory detection
    try:
        import psutil
        mem = psutil.virtual_memory()
        total_ram = int(mem.total / (1024 * 1024))
        avail_ram = int(mem.available / (1024 * 1024))
    except Exception:
        total_ram = 2048
        avail_ram = 1024

    os_label = "Android (Termux)" if is_termux else f"{platform.system()} {platform.release()}"

    # Hardware acceleration probe
    hwaccel_type = "cpu"
    hwaccel_device = None
    video_encoder = "libx264"
    hevc_encoder = "libx265"
    desc = f"CPU Software ({cores} cores)"

    # Check 1: VAAPI (Intel / AMD GPU on Linux)
    for dri_dev in ["/dev/dri/renderD128", "/dev/dri/renderD129", "/dev/dri/card0"]:
        if test_vaapi_device(dri_dev):
            hwaccel_type = "vaapi"
            hwaccel_device = dri_dev
            video_encoder = "h264_vaapi"
            hevc_encoder = "hevc_vaapi"
            desc = f"Intel/AMD GPU (VAAPI @ {dri_dev})"
            break

    # Check 2: NVIDIA NVENC (if VAAPI not used)
    if hwaccel_type == "cpu" and test_ffmpeg_encoder("h264_nvenc"):
        hwaccel_type = "nvenc"
        video_encoder = "h264_nvenc"
        hevc_encoder = "hevc_nvenc"
        desc = "NVIDIA GPU (NVENC)"

    # Profile summary
    if is_termux:
        desc = f"Snapdragon {arch} (Termux {cores}-Core NEON)"

    profile = HardwareProfile(
        os_name=os_label,
        is_termux=is_termux,
        arch=arch,
        cpu_cores=cores,
        total_ram_mb=total_ram,
        available_ram_mb=avail_ram,
        cpu_model=cpu_model,
        video_encoder=video_encoder,
        hevc_encoder=hevc_encoder,
        hwaccel_type=hwaccel_type,
        hwaccel_device=hwaccel_device,
        description=desc
    )

    logger.info(f"Hardware Profile detected: {profile.description} ({profile.total_ram_mb}MB RAM, {cores} cores)")
    return profile


# Global singleton profile
current_hardware: HardwareProfile = detect_hardware()
