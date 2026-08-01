"""Safety primitives shared by the rubbish-cleaner Python pipeline."""

from __future__ import annotations

import concurrent.futures
import os
import shutil
from datetime import datetime
from typing import Any, Callable

from scripts.lib.platform import IS_WINDOWS, IS_LINUX, IS_MACOS, get_fixed_drives


JUNK_DISPOSITIONS = [
    "OK",
    "SKIP_LOCKED",
    "SKIP_ACCESS_DENIED",
    "SKIP_NOT_FOUND",
    "SKIP_NOT_EMPTY",
    "SKIP_JUNCTION",
    "SKIP_TOO_RECENT",
    "SKIP_WSL_REGISTERED",
    "SKIP_ELEVATION_DENIED",
    "SKIP_SERVICE_RUNNING",
    "QUARANTINED",
    "MOVE_FAILED",
]

_CSV_HEADER = "Timestamp|Phase|Action|Path|ErrorMessage|Disposition"
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
_ERROR_SHARING_VIOLATION = 32


def _is_windows_reparse_point(path: str) -> bool:
    """Return whether *path* has the Windows reparse-point attribute."""
    if not IS_WINDOWS:
        return False

    import ctypes
    from ctypes import wintypes

    get_attributes = ctypes.WinDLL("kernel32", use_last_error=True).GetFileAttributesW
    get_attributes.argtypes = (wintypes.LPCWSTR,)
    get_attributes.restype = wintypes.DWORD
    attributes = get_attributes(os.fspath(path))
    return (
        attributes != _INVALID_FILE_ATTRIBUTES
        and bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)
    )


def is_junction(path: str) -> bool:
    """Return whether *path* is a Windows junction or other reparse point."""
    if not IS_WINDOWS:
        return False

    try:
        return os.path.isjunction(path)
    except AttributeError:
        return _is_windows_reparse_point(path)
    except OSError:
        return False


def _entry_is_traversal_link(entry: os.DirEntry[str]) -> bool:
    try:
        if entry.is_symlink():
            return True
        if not IS_WINDOWS:
            return False

        entry_is_junction = getattr(entry, "is_junction", None)
        if entry_is_junction is not None:
            return bool(entry_is_junction())
        return _is_windows_reparse_point(entry.path)
    except OSError:
        return False


def is_dir_empty(path: str) -> bool:
    """Return True when a directory tree has no non-link entries."""
    try:
        if not os.path.isdir(path) or os.path.islink(path) or is_junction(path):
            return False
    except OSError:
        return False

    stack = [os.fspath(path)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if _entry_is_traversal_link(entry):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    else:
                        return False
        except OSError:
            return False
    return True


def _csv_field(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def write_cleanup_csv(csv_path: str, row: dict[str, Any]) -> None:
    """Append a cleanup outcome to the pipe-delimited audit CSV."""
    needs_header = not os.path.exists(csv_path)
    fields = [
        datetime.now().astimezone().isoformat(),
        row.get("Phase", ""),
        row.get("Action", ""),
        row.get("Path", ""),
        row.get("ErrorMessage", ""),
        row.get("Disposition", ""),
    ]
    with open(csv_path, "a", encoding="utf-8", newline="") as stream:
        if needs_header:
            stream.write(_CSV_HEADER + "\n")
        stream.write("|".join(_csv_field(field) for field in fields) + "\n")


def safe_remove(path: str, phase: str, csv_path: str) -> str:
    """Remove exactly one named item and record its disposition."""
    error_message = ""
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            os.rmdir(path)
        else:
            os.remove(path)
        disposition = "OK"
    except FileNotFoundError as error:
        disposition = "SKIP_NOT_FOUND"
        error_message = str(error)
    except OSError as error:
        if getattr(error, "winerror", None) == _ERROR_SHARING_VIOLATION:
            disposition = "SKIP_LOCKED"
        elif isinstance(error, PermissionError):
            disposition = "SKIP_ACCESS_DENIED"
        else:
            disposition = "SKIP_LOCKED"
        error_message = str(error)

    write_cleanup_csv(
        csv_path,
        {
            "Phase": phase,
            "Action": "Remove",
            "Path": path,
            "ErrorMessage": error_message,
            "Disposition": disposition,
        },
    )
    return disposition


def quarantine(path: str, quarantine_dir: str, phase: str, csv_path: str) -> str:
    """Move exactly one named item into quarantine and record the outcome."""
    error_message = ""
    try:
        os.makedirs(quarantine_dir, exist_ok=True)
        shutil.move(path, quarantine_dir)
        disposition = "QUARANTINED"
    except OSError as error:
        disposition = "MOVE_FAILED"
        error_message = str(error)

    write_cleanup_csv(
        csv_path,
        {
            "Phase": phase,
            "Action": "Quarantine",
            "Path": path,
            "ErrorMessage": error_message,
            "Disposition": disposition,
        },
    )
    return disposition


def test_file_locked(path: str) -> bool:
    """Probe whether *path* can be opened exclusively without acquiring a lock."""
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        handle = create_file(
            os.fspath(path),
            0x80000000,  # GENERIC_READ
            0,  # FileShare.None
            None,
            3,  # OPEN_EXISTING
            0x02000000,  # FILE_FLAG_BACKUP_SEMANTICS
            None,
        )
        if handle == ctypes.c_void_p(-1).value:
            return True
        kernel32.CloseHandle(handle)
        return False

    import fcntl

    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return True
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return False
    finally:
        os.close(descriptor)


def parallel_for_each(
    items: list[Any],
    func: Callable[..., Any],
    throttle: int = 4,
    args: tuple[Any, ...] = (),
) -> list[Any]:
    """Apply *func* concurrently while returning results in input order."""
    if throttle <= 1 or len(items) <= 1:
        return [func(item, *args) for item in items]

    results: list[Any] = [None] * len(items)
    with concurrent.futures.ThreadPoolExecutor(max_workers=throttle) as executor:
        futures = {
            executor.submit(func, item, *args): index
            for index, item in enumerate(items)
        }
        for future in concurrent.futures.as_completed(futures):
            results[futures[future]] = future.result()
    return results
