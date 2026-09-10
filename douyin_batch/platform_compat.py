"""
Cross-platform compatibility utilities
跨平台兼容性工具
"""

import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


def get_os() -> str:
    """Get OS name: windows, macos, linux"""
    system = platform.system().lower()
    if system.startswith("win"):
        return "windows"
    elif system.startswith("darwin"):
        return "macos"
    elif system.startswith("linux"):
        return "linux"
    return "unknown"


def is_windows() -> bool:
    return get_os() == "windows"


def is_macos() -> bool:
    return get_os() == "macos"


def is_linux() -> bool:
    return get_os() == "linux"


def check_ffmpeg() -> bool:
    """Check if ffmpeg is installed and accessible"""
    return shutil.which("ffmpeg") is not None


def find_ffmpeg() -> Optional[Path]:
    """Find ffmpeg executable path"""
    # First try PATH
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        return Path(ffmpeg_path)

    # Common installation paths
    common_paths = {
        "windows": [
            r"C:\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
        ],
        "macos": [
            "/usr/local/bin/ffmpeg",
            "/opt/homebrew/bin/ffmpeg",
            "/usr/bin/ffmpeg",
        ],
        "linux": [
            "/usr/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            "/snap/bin/ffmpeg",
        ],
    }

    os_name = get_os()
    for p in common_paths.get(os_name, []):
        if Path(p).exists():
            return Path(p)

    return None


def install_ffmpeg_instructions() -> str:
    """Get OS-specific ffmpeg installation instructions"""
    os_name = get_os()

    if os_name == "windows":
        return (
            "FFmpeg is not found. To install:\n"
            "1. Download from https://www.gyan.dev/ffmpeg/builds/\n"
            "2. Extract to C:\\ffmpeg\\\n"
            "3. Add C:\\ffmpeg\\bin to PATH\n"
            "\nOr use chocolatey: choco install ffmpeg\n"
            "Or use scoop: scoop install ffmpeg"
        )
    elif os_name == "macos":
        return (
            "FFmpeg is not found. To install:\n"
            "1. Using Homebrew: brew install ffmpeg\n"
            "2. Using MacPorts: sudo port install ffmpeg"
        )
    elif os_name == "linux":
        return (
            "FFmpeg is not found. To install:\n"
            "1. Ubuntu/Debian: sudo apt install ffmpeg\n"
            "2. Fedora: sudo dnf install ffmpeg\n"
            "3. Arch: sudo pacman -S ffmpeg"
        )
    return "FFmpeg is not found. Please install it for your OS."


def normalize_path(path: str) -> Path:
    """
    Normalize a file path (handle Windows/Unix differences)
    """
    p = Path(path)
    # Expand ~ and environment variables
    p = p.expanduser()
    # Resolve to absolute path
    p = p.resolve()
    return p


def safe_filename(name: str) -> str:
    """
    Make a string safe for use as a filename on all platforms.
    Removes/replaces invalid characters.

    规范实现：被 ``mediascribe.inputs.safe_stem``、``mediascribe.mcp_server`` 以及
    ``douyin_batch.security.sanitize_filename`` 复用（后者在其基础上叠加安全规则）。
    行为契约稳定，改动需同步关注上述调用方与对应测试。
    """
    # Windows invalid chars
    invalid = '<>:"/\\|?*\x00'
    for ch in invalid:
        name = name.replace(ch, "_")

    # Strip control characters
    name = "".join(c for c in name if ord(c) >= 32)

    # Avoid reserved names on Windows
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }
    if name.upper() in reserved:
        name = f"_{name}"

    # Limit length (255 is max on most filesystems)
    if len(name) > 200:
        name = name[:200]

    return name.strip()


def get_temp_dir() -> Path:
    """Get OS-appropriate temp directory"""
    import tempfile

    return Path(tempfile.gettempdir())


def open_file_location(path: Path) -> bool:
    """
    Open the file manager at the given path (cross-platform).
    """
    try:
        if is_windows():
            subprocess.run(["explorer", "/select,", str(path)], check=False)
        elif is_macos():
            subprocess.run(["open", "-R", str(path)], check=False)
        else:  # Linux
            subprocess.run(["xdg-open", str(path.parent)], check=False)
        return True
    except Exception:
        return False


def check_python_version(min_version: tuple = (3, 8)) -> bool:
    """Check if Python version meets minimum requirements"""
    return sys.version_info >= min_version


def get_python_info() -> dict:
    """Get Python and platform info"""
    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "os": get_os(),
        "os_version": platform.version(),
        "machine": platform.machine(),
        "ffmpeg_available": check_ffmpeg(),
    }
