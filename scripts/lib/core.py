"""Safety primitives shared by the rubbish-cleaner Python pipeline."""

from __future__ import annotations

import concurrent.futures
import csv
import errno
import os
import threading
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
    "SKIP_POSIX_UNSAFE",
    "QUARANTINED",
    "MOVE_FAILED",
]

_CSV_COLUMNS = ("Timestamp", "Phase", "Action", "Path", "ErrorMessage", "Disposition")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
_ERROR_SHARING_VIOLATION = 32
_CSV_LOCKS: dict[str, threading.Lock] = {}
_CSV_LOCKS_GUARD = threading.Lock()


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
    if attributes == _INVALID_FILE_ATTRIBUTES:
        error_code = ctypes.get_last_error()
        raise OSError(error_code, "GetFileAttributesW could not inspect path", path)
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def is_junction(path: str) -> bool:
    """Return whether *path* is a Windows reparse point traversal must skip.

    This covers junctions (reparse tag IO_REPARSE_TAG_MOUNT_POINT) AND
    symlinks (IO_REPARSE_TAG_SYMLINK): ``os.path.isjunction`` (3.12+) only
    reports true junctions, so a directory symlink would otherwise be a false
    negative and slip past the guard.  Callers use this to decide whether to
    skip a path as a traversal link, so any reparse point must be True.
    """
    if not IS_WINDOWS:
        return False

    try:
        os.lstat(path)
    except FileNotFoundError:
        return False

    if os.path.islink(path):
        return True
    path_is_junction = getattr(os.path, "isjunction", None)
    if path_is_junction is not None:
        try:
            if path_is_junction(path):
                return True
        except OSError:
            pass
    return _is_windows_reparse_point(path)


def _normalized_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(os.fspath(path))))


def _path_is_traversal_link(path: str) -> bool:
    """Return link/reparse status, raising when the status cannot be known."""
    status = os.lstat(path)
    if os.path.islink(path):
        return True
    if not IS_WINDOWS:
        return False

    attributes = getattr(status, "st_file_attributes", 0)
    if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        return True
    return is_junction(path)


def _filesystem_root(path: str) -> str:
    """Return the filesystem root that *path* lives on.

    POSIX roots are ``/``; Windows roots are ``<drive>:\\`` (or the UNC
    ``\\\\server\\share`` prefix for network paths).
    """
    drive, _ = os.path.splitdrive(os.path.abspath(os.fspath(path)))
    if drive:
        return drive + os.sep
    return os.sep


def _assert_no_traversal_components(path: str) -> str:
    """Normalize *path* and reject link/reparse components that escape the root.

    The upward walk stops at the filesystem root itself: components at or above
    the root are the operating system's fixed layout (e.g. macOS ``/var`` ->
    ``/private/var``) and are never audited.  Each audited component below the
    root is only refused when it is a link whose real target resolves outside
    the filesystem root -- an OS-builtin link such as ``/var`` on macOS stays
    within the system, while a user link or Windows junction pointing at a
    different drive/root is a traversal vector and fails closed.
    """
    normalized = _normalized_path(path)
    root_realpath = os.path.normcase(os.path.realpath(_filesystem_root(normalized)))

    components: list[str] = []
    current = normalized
    while True:
        parent = os.path.dirname(current)
        if parent == current:
            # ``current`` is the filesystem root: OS layout, never audited.
            break
        components.append(current)
        current = parent

    for component in reversed(components):
        try:
            if not _path_is_traversal_link(component):
                continue
        except FileNotFoundError:
            continue
        if os.path.normcase(os.path.realpath(component)).startswith(root_realpath):
            # Link resolves inside the filesystem root: system layout, allowed.
            continue
        raise OSError(
            errno.ELOOP,
            "Refusing a traversal-link path component",
            component,
        )
    return normalized


def _entry_is_traversal_link(entry: os.DirEntry[str]) -> bool:
    if entry.is_symlink():
        return True
    if not IS_WINDOWS:
        return False

    entry_is_junction = getattr(entry, "is_junction", None)
    if entry_is_junction is not None:
        return bool(entry_is_junction())
    return _path_is_traversal_link(entry.path)


def is_dir_empty(path: str) -> bool:
    """Return True when a directory tree has no non-link entries."""
    try:
        if not os.path.isdir(path) or _path_is_traversal_link(path):
            return False
    except OSError:
        return False

    stack = [_normalized_path(path)]
    while stack:
        current = stack.pop()
        try:
            if _path_is_traversal_link(current):
                return False
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


def _csv_lock(csv_path: str) -> threading.Lock:
    key = _normalized_path(csv_path)
    with _CSV_LOCKS_GUARD:
        return _CSV_LOCKS.setdefault(key, threading.Lock())


def write_cleanup_csv(csv_path: str, row: dict[str, Any]) -> None:
    """Append a cleanup outcome to the pipe-delimited audit CSV."""
    fields = [
        datetime.now().astimezone().isoformat(),
        row.get("Phase", ""),
        row.get("Action", ""),
        row.get("Path", ""),
        row.get("ErrorMessage", ""),
        row.get("Disposition", ""),
    ]
    normalized = _normalized_path(csv_path)
    with _csv_lock(normalized):
        _assert_no_traversal_components(normalized)
        needs_header = not os.path.exists(normalized) or os.path.getsize(normalized) == 0
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(normalized, flags, 0o666)
        with os.fdopen(descriptor, "a", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, delimiter="|", lineterminator="\n")
            if needs_header:
                writer.writerow(_CSV_COLUMNS)
            writer.writerow([_csv_field(field) for field in fields])


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
        source = _assert_no_traversal_components(path)
        destination_dir = _assert_no_traversal_components(quarantine_dir)
        source_status = os.lstat(source)
        source_name = os.path.basename(source)
        if not source_name:
            raise OSError(errno.EINVAL, "Refusing to quarantine a filesystem root", source)

        os.makedirs(destination_dir, exist_ok=True)
        _assert_no_traversal_components(destination_dir)
        if not os.path.isdir(destination_dir):
            raise OSError(
                errno.ENOTDIR,
                "Quarantine destination is not a directory",
                destination_dir,
            )

        destination = os.path.join(destination_dir, source_name)
        _assert_no_traversal_components(destination)
        if os.path.lexists(destination):
            raise OSError(errno.EEXIST, "Quarantine destination already exists", destination)
        if os.path.isdir(source) and source_status.st_dev != os.stat(destination_dir).st_dev:
            raise OSError(errno.EXDEV, "Refusing cross-device directory quarantine", source)

        os.rename(source, destination)
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
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
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
        try:
            return False
        finally:
            close_handle(handle)

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
