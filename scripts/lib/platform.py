"""Read-only, cross-platform filesystem location and drive helpers."""

import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional, Union

import psutil


IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform == "linux"
IS_MACOS = sys.platform == "darwin"

_DRIVE_LETTER = re.compile(r"^[A-Za-z]:$")
_DRIVE_ROOT = re.compile(r"^([A-Za-z]):\\?$")


def get_fixed_drives() -> list[str]:
    """Return ready fixed-drive roots, or the POSIX filesystem root."""
    if not IS_WINDOWS:
        return ["/"]

    drives: list[str] = []
    for partition in psutil.disk_partitions():
        if not partition.fstype:
            continue

        match = _DRIVE_ROOT.fullmatch(partition.mountpoint)
        if match is None:
            continue

        root = f"{match.group(1).upper()}:\\"
        if root in drives or not os.path.exists(root):
            continue

        try:
            psutil.disk_usage(root)
        except OSError:
            continue
        drives.append(root)

    return drives


def get_user_cache_dir() -> Optional[Union[str, Path]]:
    """Return the conventional per-user cache directory for this platform."""
    if IS_WINDOWS:
        return os.environ.get("LOCALAPPDATA")
    if IS_MACOS:
        return Path.home() / "Library" / "Caches"
    return os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"


def get_system_temp_dir() -> str:
    """Return the platform-selected temporary directory without creating it."""
    return tempfile.gettempdir()


def get_user_documents_dir() -> Path:
    """Return the standard documents location for the current user."""
    if IS_WINDOWS:
        return Path.home() / "Documents"
    return Path.home()


def resolve_fixed_drive(drive: str) -> Optional[Dict[str, Union[str, int]]]:
    """Return capacity data for a usable fixed drive, or ``None`` if invalid."""
    if IS_WINDOWS:
        normalized_drive = drive.rstrip("\\/") if isinstance(drive, str) else ""
        if _DRIVE_LETTER.fullmatch(normalized_drive) is None:
            return None
        root = f"{normalized_drive[0].upper()}:\\"
        if root not in get_fixed_drives():
            return None
    else:
        if drive != "/":
            return None
        root = "/"

    try:
        usage = psutil.disk_usage(root)
    except OSError:
        return None

    return {
        "Root": root,
        "FreeBytes": usage.free,
        "TotalBytes": usage.total,
    }
