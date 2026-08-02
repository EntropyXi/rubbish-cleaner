"""Read-only verification and eight-section cleanup summary reporting."""

from __future__ import annotations

import argparse
import csv
import errno
import os
import re
import secrets
import stat
import sys
from collections import Counter, OrderedDict
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.lib import core, platform


_DEFAULT_OUT_DIR = Path(__file__).resolve().parents[1] / ".omo" / "evidence" / "python-migration"
_PREFLIGHT_LINE = re.compile(r"^([A-Za-z_]+)=(.*)$")
_CANDIDATE_COLUMNS = ("Category", "Risk", "Path", "SizeBytes", "FileCount", "Action")
_CLEANUP_COLUMNS = ("Timestamp", "Phase", "Action", "Path", "ErrorMessage", "Disposition")
_FREED_DISPOSITIONS = {"OK", "QUARANTINED"}
_TOLERANCE_BYTES = 500_000_000
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400


def _drive_id(drive: str) -> str:
    if platform.IS_WINDOWS:
        return drive.rstrip("\\/").rstrip(":").upper()
    return "ROOT"


def _path_key(path: object) -> str:
    value = os.path.normpath(os.fspath(path))
    return value.casefold() if platform.IS_WINDOWS else value


def _format_bytes(value: int) -> str:
    return f"{int(value):,}"


def _format_gib(value: int) -> str:
    return f"{int(value) / (1024 ** 3):,.2f} GiB"


def _markdown(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _read_text_no_follow(path: Path) -> str:
    normalized = core._assert_no_traversal_components(os.fspath(path))
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(normalized, flags)
    with os.fdopen(descriptor, "r", encoding="utf-8-sig", newline="") as stream:
        return stream.read()


def _windows_close_handle(handle: int) -> None:
    if not platform.IS_WINDOWS:
        raise RuntimeError("Windows-only helper called on non-Windows")
    import ctypes
    from ctypes import wintypes

    close_handle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    close_handle(handle)


def _windows_final_path(handle: int) -> str:
    if not platform.IS_WINDOWS:
        raise RuntimeError("Windows-only helper called on non-Windows")
    import ctypes
    from ctypes import wintypes

    get_final_path = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
    get_final_path.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
    get_final_path.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_final_path(handle, buffer, len(buffer), 0)
    if length == 0 or length >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    final_path = buffer.value
    if final_path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + final_path[8:]
    if final_path.startswith("\\\\?\\"):
        return final_path[4:]
    return final_path


def _windows_assert_handle_path(handle: int, expected_path: str, message: str) -> None:
    if not platform.IS_WINDOWS:
        raise RuntimeError("Windows-only helper called on non-Windows")
    # Compare the two canonical filesystem identities, not their textual
    # spellings.  The user-supplied expected_path may use 8.3 short names
    # (e.g. C:\USERS\RUNNER~1\...) while GetFinalPathNameByHandle returns the
    # long form (C:\Users\runneradmin\...); a lexical comparison of those two
    # strings raises Errno 10062 even though they name the same directory.
    # os.path.realpath() on Windows resolves 8.3 short names to their long
    # equivalents (3.8+), so resolve BOTH sides before the casefolded compare.
    expected = os.path.normcase(os.path.realpath(expected_path))
    actual = os.path.normcase(os.path.realpath(_windows_final_path(handle)))
    if actual != expected:
        raise OSError(errno.ELOOP, message, expected_path)


def _windows_parent_guard(parent: str) -> int:
    """Open and verify a stable non-reparse directory handle on Windows."""
    if not platform.IS_WINDOWS:
        raise RuntimeError("Windows-only helper called on non-Windows")
    import ctypes
    from ctypes import wintypes

    file_read_attributes = 0x0080
    file_traverse = 0x0020
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    open_existing = 3
    file_flag_open_reparse_point = 0x00200000
    file_flag_backup_semantics = 0x02000000
    file_attribute_tag_info_class = 9

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
    get_information = kernel32.GetFileInformationByHandleEx
    get_information.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    get_information.restype = wintypes.BOOL

    class FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]

    handle = create_file(
        parent,
        file_read_attributes | file_traverse,
        file_share_read | file_share_write,  # Deliberately omit FILE_SHARE_DELETE.
        None,
        open_existing,
        file_flag_open_reparse_point | file_flag_backup_semantics,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())

    try:
        information = FileAttributeTagInfo()
        if not get_information(
            handle,
            file_attribute_tag_info_class,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if information.FileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError(errno.ELOOP, "Refusing a reparse-point output directory", parent)

        _windows_assert_handle_path(
            int(handle),
            parent,
            "Output directory handle resolves outside the audited path",
        )
        return int(handle)
    except BaseException:
        close_handle(handle)
        raise


def _windows_create_temp(parent_handle: int, output_leaf: str, text: str) -> int:
    """Create and write an exclusive temp relative to a stable parent handle."""
    if not platform.IS_WINDOWS:
        raise RuntimeError("Windows-only helper called on non-Windows")
    import ctypes
    from ctypes import wintypes

    generic_write = 0x40000000
    delete_access = 0x00010000
    file_read_attributes = 0x0080
    synchronize = 0x00100000
    share_all = 0x00000001 | 0x00000002 | 0x00000004
    file_attribute_normal = 0x0080
    file_create = 2
    file_non_directory_file = 0x0040
    file_synchronous_io_nonalert = 0x0020
    file_flag_open_reparse_point = 0x00200000

    class UnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(UnicodeString)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID),
            ("SecurityQualityOfService", wintypes.LPVOID),
        ]

    class StatusValue(ctypes.Union):
        _fields_ = [("Status", wintypes.LONG), ("Pointer", wintypes.LPVOID)]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Value", StatusValue), ("Information", ctypes.c_size_t)]

    ntdll = ctypes.WinDLL("ntdll")
    create_file = ntdll.NtCreateFile
    create_file.argtypes = (
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.ULONG,
        ctypes.POINTER(ObjectAttributes),
        ctypes.POINTER(IoStatusBlock),
        wintypes.LPVOID,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.LPVOID,
        wintypes.ULONG,
    )
    create_file.restype = wintypes.LONG
    temporary_leaf = f".{output_leaf}.{secrets.token_hex(12)}.tmp"
    name_buffer = ctypes.create_unicode_buffer(temporary_leaf)
    name = UnicodeString(
        len(temporary_leaf.encode("utf-16-le")),
        ctypes.sizeof(name_buffer),
        ctypes.cast(name_buffer, wintypes.LPWSTR),
    )
    attributes = ObjectAttributes(
        ctypes.sizeof(ObjectAttributes),
        parent_handle,
        ctypes.pointer(name),
        0x0040,  # OBJ_CASE_INSENSITIVE
        None,
        None,
    )
    io_status = IoStatusBlock()
    result_handle = wintypes.HANDLE()
    status = int(
        create_file(
            ctypes.byref(result_handle),
            generic_write | delete_access | file_read_attributes | synchronize,
            ctypes.byref(attributes),
            ctypes.byref(io_status),
            None,
            file_attribute_normal,
            share_all,
            file_create,
            file_non_directory_file | file_synchronous_io_nonalert | file_flag_open_reparse_point,
            None,
            0,
        )
    )
    if status < 0:
        to_windows_error = ntdll.RtlNtStatusToDosError
        to_windows_error.argtypes = (wintypes.LONG,)
        to_windows_error.restype = wintypes.ULONG
        raise ctypes.WinError(int(to_windows_error(status)))

    handle = int(result_handle.value)
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        write_file = kernel32.WriteFile
        write_file.argtypes = (
            wintypes.HANDLE,
            wintypes.LPCVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        )
        write_file.restype = wintypes.BOOL
        flush_file = kernel32.FlushFileBuffers
        flush_file.argtypes = (wintypes.HANDLE,)
        flush_file.restype = wintypes.BOOL
        payload = text.encode("utf-8")
        if payload:
            buffer = ctypes.create_string_buffer(payload)
            written = wintypes.DWORD()
            if not write_file(handle, buffer, len(payload), ctypes.byref(written), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if int(written.value) != len(payload):
                raise OSError(errno.EIO, "Incomplete Windows summary write")
        if not flush_file(handle):
            raise ctypes.WinError(ctypes.get_last_error())
        return handle
    except BaseException:
        try:
            _windows_delete_on_close(handle)
        finally:
            _windows_close_handle(handle)
        raise


def _windows_rename_relative(source_handle: int, parent_handle: int, destination_leaf: str) -> None:
    """Atomically replace one child entry relative to a stable parent handle."""
    if not platform.IS_WINDOWS:
        raise RuntimeError("Windows-only helper called on non-Windows")
    import ctypes
    from ctypes import wintypes

    class FileRenameInfo(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", wintypes.BOOLEAN),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * (len(destination_leaf) + 1)),
        ]

    information = FileRenameInfo()
    information.ReplaceIfExists = True
    information.RootDirectory = parent_handle
    information.FileNameLength = len(destination_leaf.encode("utf-16-le"))
    information.FileName = destination_leaf

    class StatusValue(ctypes.Union):
        _fields_ = [("Status", wintypes.LONG), ("Pointer", wintypes.LPVOID)]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Value", StatusValue), ("Information", ctypes.c_size_t)]

    io_status = IoStatusBlock()
    ntdll = ctypes.WinDLL("ntdll")
    set_information = ntdll.NtSetInformationFile
    set_information.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(IoStatusBlock),
        wintypes.LPVOID,
        wintypes.ULONG,
        ctypes.c_int,
    )
    set_information.restype = wintypes.LONG
    buffer_size = FileRenameInfo.FileName.offset + information.FileNameLength
    status = int(
        set_information(
            source_handle,
            ctypes.byref(io_status),
            ctypes.byref(information),
            buffer_size,
            10,  # FileRenameInformation
        )
    )
    if status < 0:
        to_windows_error = ntdll.RtlNtStatusToDosError
        to_windows_error.argtypes = (wintypes.LONG,)
        to_windows_error.restype = wintypes.ULONG
        raise ctypes.WinError(int(to_windows_error(status)))


def _windows_delete_on_close(handle: int) -> None:
    """Mark the exact opened temporary object for deletion on handle close."""
    if not platform.IS_WINDOWS:
        raise RuntimeError("Windows-only helper called on non-Windows")
    import ctypes
    from ctypes import wintypes

    class FileDispositionInfo(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOLEAN)]

    information = FileDispositionInfo(True)
    set_information = ctypes.WinDLL("kernel32", use_last_error=True).SetFileInformationByHandle
    set_information.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    set_information.restype = wintypes.BOOL
    if not set_information(handle, 4, ctypes.byref(information), ctypes.sizeof(information)):
        raise ctypes.WinError(ctypes.get_last_error())


def _write_text_no_follow(path: Path, text: str) -> None:
    normalized = core._assert_no_traversal_components(os.fspath(path))
    if platform.IS_WINDOWS:
        # Windows does not expose O_NOFOLLOW.  Never truncating-open the final
        # output entry: write an exclusive sibling, re-audit the destination,
        # then replace its directory entry atomically.  A symlink/reparse race
        # therefore cannot redirect writes into its target.
        parent = os.path.dirname(normalized)
        parent_handle = _windows_parent_guard(parent)
        source_handle = 0
        renamed = False
        try:
            source_handle = _windows_create_temp(
                parent_handle,
                os.path.basename(normalized),
                text,
            )
            core._assert_no_traversal_components(normalized)
            _windows_assert_handle_path(
                parent_handle,
                parent,
                "Output directory changed before atomic replacement",
            )
            _windows_rename_relative(
                source_handle,
                parent_handle,
                os.path.basename(normalized),
            )
            renamed = True
        finally:
            if source_handle:
                try:
                    if not renamed:
                        _windows_delete_on_close(source_handle)
                finally:
                    _windows_close_handle(source_handle)
            _windows_close_handle(parent_handle)
        return

    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(normalized, flags, 0o666)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
        stream.write(text)


def _read_preflight(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in _read_text_no_follow(path).splitlines():
        match = _PREFLIGHT_LINE.fullmatch(line)
        if match is not None:
            values[match.group(1)] = match.group(2)
    if "BASELINE_FREE_BYTES" not in values:
        raise ValueError("preflight.txt is missing the BASELINE_FREE_BYTES key")
    try:
        int(values["BASELINE_FREE_BYTES"])
        if "TOTAL_BYTES" in values:
            int(values["TOTAL_BYTES"])
    except ValueError as error:
        raise ValueError("preflight.txt contains an invalid integer value") from error
    return values


def _read_pipe_rows(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    text = _read_text_no_follow(path)
    reader = csv.DictReader(text.splitlines(), delimiter="|")
    if tuple(reader.fieldnames or ()) != columns:
        actual = "|".join(reader.fieldnames or ())
        expected = "|".join(columns)
        raise ValueError(f"Unexpected {path.name} header: '{actual}'. Expected: {expected}")
    rows: list[dict[str, str]] = []
    for index, source in enumerate(reader, start=2):
        if None in source:
            raise ValueError(f"Malformed {path.name} row {index}: too many columns")
        row = {column: str(source.get(column, "")) for column in columns}
        if not row.get("Path"):
            raise ValueError(f"Malformed {path.name} row {index}: Path is required")
        rows.append(row)
    return rows


def _read_candidates(path: Path) -> list[dict[str, Any]]:
    rows = _read_pipe_rows(path, _CANDIDATE_COLUMNS)
    parsed: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=2):
        if not row["Category"] or not row["Risk"] or not row["Action"]:
            raise ValueError(f"Malformed candidates.csv row {index}: category, risk, and action are required")
        try:
            size = int(row["SizeBytes"])
            files = int(row["FileCount"])
        except ValueError as error:
            raise ValueError(f"Malformed candidates.csv row {index}: invalid numeric field") from error
        if size < 0 or files < 0:
            raise ValueError(f"Malformed candidates.csv row {index}: negative numeric field")
        parsed.append({**row, "SizeBytes": size, "FileCount": files})
    return parsed


def _latest_run_dir(out_dir: Path, drive: str) -> Path:
    prefix = f"{_drive_id(drive)}-"
    runs = [
        path
        for path in out_dir.iterdir()
        if path.is_dir() and path.name.startswith(prefix) and (path / "preflight.txt").is_file()
    ] if out_dir.is_dir() else []
    if not runs:
        raise ValueError(f"No run directory found under '{out_dir}' for drive '{drive}'")
    return max(runs, key=lambda path: path.stat().st_mtime_ns)


def _quarantine_leaf(path: str) -> str:
    return path.rstrip("\\/").replace("\\", "/").rsplit("/", 1)[-1]


def _no_follow_state(path: object) -> dict[str, Any]:
    """Inspect one directory entry without following links or reparse points."""
    value = os.fspath(path)
    try:
        status = os.lstat(value)
    except FileNotFoundError:
        return {"exists": False, "is_directory": False, "is_reparse": False, "error": ""}
    except OSError as error:
        return {"exists": None, "is_directory": False, "is_reparse": False, "error": str(error)}

    path_is_junction = getattr(os.path, "isjunction", None)
    try:
        junction = bool(path_is_junction(value)) if path_is_junction is not None else False
    except OSError:
        junction = True
    reparse = bool(
        os.path.islink(value)
        or junction
        or (getattr(status, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)
    )
    return {
        "exists": True,
        "is_directory": stat.S_ISDIR(status.st_mode) and not reparse,
        "is_reparse": reparse,
        "error": "",
    }


def _assertion_rows(cleanup_rows: list[dict[str, str]], quarantine_dir: Path) -> list[dict[str, str]]:
    assertions: list[dict[str, str]] = []
    counters: Counter[str] = Counter()
    prefixes = {
        "QUARANTINED_ORIGINAL": "a",
        "QUARANTINED_COPY": "b",
        "SKIP_NOT_EMPTY": "c",
        "SKIP_LOCKED": "d",
        "OK": "e",
    }

    def add(kind: str, description: str, passed: bool, good: str, bad: str) -> None:
        counters[kind] += 1
        assertions.append(
            {
                "id": f"{prefixes[kind]}{counters[kind]}",
                "description": description,
                "result": "PASS" if passed else "FAIL",
                "detail": good if passed else bad,
            }
        )

    for row in cleanup_rows:
        disposition = row["Disposition"].upper()
        target = row["Path"]
        if disposition == "QUARANTINED":
            original = _no_follow_state(target)
            original_absent = original["exists"] is False
            copy_path = quarantine_dir / _quarantine_leaf(target)
            copy = _no_follow_state(copy_path)
            copy_present = copy["exists"] is True and not copy["is_reparse"]
            add(
                "QUARANTINED_ORIGINAL",
                f"Quarantine original absent: {target}",
                original_absent,
                "original not found on disk",
                "original STILL PRESENT"
                if original["exists"] is True
                else f"original could not be inspected: {original['error']}",
            )
            add(
                "QUARANTINED_COPY",
                f"Quarantine copy present: {copy_path}",
                copy_present,
                "copy found in quarantine dir",
                "copy is a link/reparse entry, not a quarantined copy"
                if copy["is_reparse"]
                else (
                    "copy NOT found in quarantine dir"
                    if copy["exists"] is False
                    else f"copy could not be inspected: {copy['error']}"
                ),
            )
        elif disposition == "SKIP_NOT_EMPTY":
            target_state = _no_follow_state(target)
            survived = target_state["exists"] is True and target_state["is_directory"]
            add(
                "SKIP_NOT_EMPTY",
                f"Non-empty dir survived (SKIP_NOT_EMPTY): {target}",
                survived,
                "dir still exists",
                "entry is a link/reparse point, not the surviving directory"
                if target_state["is_reparse"]
                else (
                    "dir MISSING"
                    if target_state["exists"] is False
                    else f"dir could not be verified: {target_state['error'] or 'entry is not a directory'}"
                ),
            )
        elif disposition == "SKIP_LOCKED":
            target_state = _no_follow_state(target)
            survived = target_state["exists"] is True
            add(
                "SKIP_LOCKED",
                f"Locked item survived (SKIP_LOCKED): {target}",
                survived,
                "entry still exists (link/reparse entry)"
                if target_state["is_reparse"]
                else "item still exists",
                "item MISSING (removed despite the skip?)"
                if target_state["exists"] is False
                else f"item could not be inspected: {target_state['error']}",
            )
        elif disposition == "OK":
            target_state = _no_follow_state(target)
            gone = target_state["exists"] is False
            add(
                "OK",
                f"Deleted item absent (OK): {target}",
                gone,
                "item gone as expected",
                "item STILL PRESENT"
                if target_state["exists"] is True
                else f"item could not be inspected: {target_state['error']}",
            )
    return assertions


def _category_statistics(
    candidates: list[dict[str, Any]],
    disposition_by_path: dict[str, str],
    *,
    scan_only: bool,
) -> list[dict[str, Any]]:
    grouped: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for row in candidates:
        category = row["Category"]
        stats = grouped.setdefault(
            category,
            {
                "category": category,
                "risks": [],
                "actions": [],
                "candidates": 0,
                "files": 0,
                "freed_safe": 0,
                "freed_quarantined": 0,
                "skipped": 0,
            },
        )
        if row["Risk"] not in stats["risks"]:
            stats["risks"].append(row["Risk"])
        if row["Action"] not in stats["actions"]:
            stats["actions"].append(row["Action"])
        stats["candidates"] += 1
        stats["files"] += int(row["FileCount"])
        if scan_only:
            continue
        disposition = disposition_by_path.get(_path_key(row["Path"]), "")
        if disposition == "OK":
            stats["freed_safe"] += int(row["SizeBytes"])
        elif disposition == "QUARANTINED":
            stats["freed_quarantined"] += int(row["SizeBytes"])
        else:
            stats["skipped"] += 1

    result: list[dict[str, Any]] = []
    for category in sorted(grouped, key=str.casefold):
        stats = grouped[category]
        result.append(
            {
                "category": stats["category"],
                "risk": "/".join(stats["risks"]),
                "action": "/".join(stats["actions"]),
                "candidates": stats["candidates"],
                "files": stats["files"],
                "freed_safe": stats["freed_safe"],
                "freed_quarantined": stats["freed_quarantined"],
                "skipped": stats["skipped"],
            }
        )
    return result


def _summary_text(
    *,
    drive: str,
    run_dir: Path,
    preflight_path: Path,
    preflight: dict[str, str],
    volume: Mapping[str, Any],
    scan_only: bool,
    cleanup_rows: list[dict[str, str]],
    disposition_counts: Counter[str],
    categories: list[dict[str, Any]],
    quarantine_dir: Path,
    assertions: list[dict[str, str]],
) -> str:
    baseline_free = int(preflight["BASELINE_FREE_BYTES"])
    baseline_total = int(preflight.get("TOTAL_BYTES", 0))
    final_free = int(volume["FreeBytes"])
    final_total = int(volume["TotalBytes"])
    freed_safe = sum(int(item["freed_safe"]) for item in categories)
    freed_quarantined = sum(int(item["freed_quarantined"]) for item in categories)
    estimated_total = freed_safe + freed_quarantined
    total_freed = None if scan_only else final_free - baseline_free
    pass_count = sum(item["result"] == "PASS" for item in assertions)
    fail_count = sum(item["result"] == "FAIL" for item in assertions)
    status = "SCAN_ONLY" if scan_only else ("FAIL" if fail_count else "PASS")
    quarantined = [row for row in cleanup_rows if row["Disposition"].upper() == "QUARANTINED"]

    lines = [
        f"# {_markdown(_drive_id(drive))}-Drive Cleanup - Post-Run Summary",
        "",
        f"Generated: {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S%z')} - Mode: READ-ONLY verification (no deletions, no re-runs)",
        f"Run directory: `{_markdown(run_dir)}`",
        f"Status: **{status}**",
        "",
        "## 1. Baseline Free Space",
        "",
        f"Source: `{_markdown(preflight_path)}`",
        f"- BASELINE_FREE_BYTES = **{_format_bytes(baseline_free)} bytes** ({_format_gib(baseline_free)})",
        f"- TOTAL_BYTES = {_format_bytes(baseline_total)} bytes",
    ]
    if preflight.get("PROCESSES"):
        lines.append(f"- PROCESSES = {_markdown(preflight['PROCESSES'])}")
    lines.extend(
        [
            "",
            "## 2. Final Free Space",
            "",
            f"Measured live from fixed volume `{_markdown(volume['Root'])}`",
            f"- **{_format_bytes(final_free)} bytes** ({_format_gib(final_free)})",
            f"- Volume Size = {_format_bytes(final_total)} bytes",
            "",
            "## 3. Total Freed",
            "",
        ]
    )
    if scan_only:
        lines.extend(
            [
                "**Scan-only run** - `cleanup-errors.csv` was not found, so no cleanup-freed figure is computed.",
                f"- Final - Baseline = {_format_bytes(final_free)} - {_format_bytes(baseline_free)} = **n/a (ambient delta only; not cleanup freed)**",
                "- NOTE: ±500 MB tolerance applies only when cleanup dispositions are available for reconciliation.",
            ]
        )
    else:
        variance = int(total_freed) - estimated_total
        variance_text = f"+{_format_bytes(variance)}" if variance >= 0 else _format_bytes(variance)
        if abs(variance) <= _TOLERANCE_BYTES:
            tolerance = "discrepancy is within tolerance, OK"
        else:
            tolerance = f"discrepancy of {_format_bytes(variance)} bytes exceeds tolerance; this is informational, not a verification failure"
        freed_text = f"+{_format_bytes(total_freed)}" if int(total_freed) >= 0 else _format_bytes(total_freed)
        lines.extend(
            [
                f"- Final - Baseline = {_format_bytes(final_free)} - {_format_bytes(baseline_free)} = **{freed_text} bytes ({_format_gib(int(total_freed))})**",
                f"- Sum of per-category freed estimates = {_format_bytes(estimated_total)} bytes ({_format_gib(estimated_total)})",
                f"- Variance = **{variance_text} bytes**",
                f"- NOTE: ±500 MB tolerance (500,000,000 bytes) - {tolerance}.",
            ]
        )

    lines.extend(
        [
            "",
            "## 4. Per-Category Freed",
            "",
            "Freed estimates join `candidates.csv` to cleanup disposition by Path; only `OK` and `QUARANTINED` contribute bytes.",
            "",
            "| Category | Risk | Action | Candidates | Files | Freed (Safe) | Freed (Quarantined) | Skipped |",
            "|----------|------|--------|------------|-------|--------------|---------------------|---------|",
        ]
    )
    for item in categories:
        lines.append(
            "| {category} | {risk} | {action} | {candidates} | {files} | {freed_safe} | {freed_quarantined} | {skipped} |".format(
                category=_markdown(item["category"]),
                risk=_markdown(item["risk"]),
                action=_markdown(item["action"]),
                candidates=item["candidates"],
                files=item["files"],
                freed_safe=_format_bytes(item["freed_safe"]),
                freed_quarantined=_format_bytes(item["freed_quarantined"]),
                skipped=item["skipped"],
            )
        )
    lines.append(
        f"| **Total** | - | - | **{sum(item['candidates'] for item in categories)}** | **{sum(item['files'] for item in categories)}** | **{_format_bytes(freed_safe)}** | **{_format_bytes(freed_quarantined)}** | **{sum(item['skipped'] for item in categories)}** |"
    )

    lines.extend(["", "## 5. Skipped Items Table", ""])
    if scan_only:
        lines.append("**Scan-only run** - no cleanup dispositions were recorded.")
    elif not cleanup_rows:
        lines.append("`cleanup-errors.csv` contains no data rows.")
    else:
        lines.extend(
            [
                f"Total rows: **{len(cleanup_rows)}**",
                "",
                "| Disposition | Count |",
                "|-------------|-------|",
            ]
        )
        for disposition in sorted(disposition_counts, key=str.casefold):
            lines.append(f"| {_markdown(disposition)} | {disposition_counts[disposition]} |")

    lines.extend(["", "## 6. Quarantine Note", ""])
    if not quarantined:
        lines.append("No quarantined files in this run (no `QUARANTINED` disposition rows).")
    else:
        lines.extend(
            [
                f"Quarantine directory: `{_markdown(quarantine_dir)}`",
                "",
                "| File | Original path | Quarantine path |",
                "|------|---------------|-----------------|",
            ]
        )
        for row in quarantined:
            leaf = _quarantine_leaf(row["Path"])
            lines.append(
                f"| {_markdown(leaf)} | {_markdown(row['Path'])} | {_markdown(quarantine_dir / leaf)} |"
            )
        lines.extend(["", "Quarantined items were MOVED (not deleted) and remain recoverable."])

    lines.extend(["", "## 7. Verification Assertions", ""])
    if not assertions:
        if scan_only:
            lines.append("**Scan-only run** - no cleanup rows to verify live.")
        else:
            lines.append("No live assertions applicable (no `QUARANTINED`, `SKIP_NOT_EMPTY`, `SKIP_LOCKED`, or `OK` rows).")
        lines.append(f"Status: **{status}**")
    else:
        lines.extend(
            [
                "Live filesystem checks are recomputed from disk, not trusted from the CSV.",
                "",
                "| # | Assertion | Result | Detail |",
                "|---|-----------|--------|--------|",
            ]
        )
        for assertion in assertions:
            lines.append(
                f"| {assertion['id']} | {_markdown(assertion['description'])} | **{assertion['result']}** | {_markdown(assertion['detail'])} |"
            )
        lines.extend(["", f"Result: **{pass_count}/{len(assertions)} PASS**", f"Status: **{status}**"])

    recommendations: list[str] = []
    if scan_only:
        recommendations.append("Review `candidates.csv` and obtain approval before running cleanup.")
    else:
        if disposition_counts.get("SKIP_LOCKED", 0):
            recommendations.append("Close applications holding `SKIP_LOCKED` items before a later approved cleanup run.")
        if disposition_counts.get("SKIP_NOT_EMPTY", 0):
            recommendations.append("Review `SKIP_NOT_EMPTY` directories manually; they were intentionally left intact.")
        if quarantined:
            recommendations.append(f"Review recoverable items in `{_markdown(quarantine_dir)}`.")
        if fail_count:
            recommendations.append(f"Investigate {fail_count} failed live assertion(s) before further cleanup.")
        recommendations.append("Caches and temporary files can refill during normal use; scan again before any later cleanup.")
    lines.extend(["", "## 8. Recommendations", ""])
    lines.extend(f"- {item}" for item in recommendations)
    lines.append("")
    return "\n".join(lines)


def verify_report(drive: str, **kwargs: Any) -> dict[str, Any]:
    """Verify one run from live state and write its read-only summary report."""
    volume = kwargs.get("volume")
    if volume is None:
        volume = platform.resolve_fixed_drive(drive)
    if volume is None:
        raise ValueError(f"Drive '{drive}' is not an available fixed local volume")
    for key in ("Root", "FreeBytes", "TotalBytes"):
        if key not in volume:
            raise ValueError(f"Volume information is missing '{key}'")

    out_dir = Path(kwargs.get("out_dir") or _DEFAULT_OUT_DIR)
    run_value = kwargs.get("run_dir")
    run_dir = Path(run_value) if run_value is not None else _latest_run_dir(out_dir, drive)
    if not run_dir.is_dir():
        raise ValueError(f"RunDir not found: {run_dir}")
    preflight_path = run_dir / "preflight.txt"
    if not preflight_path.is_file():
        raise ValueError(f"preflight.txt not found in RunDir: {preflight_path}")
    preflight = _read_preflight(preflight_path)

    cleanup_path = run_dir / "cleanup-errors.csv"
    scan_only = not cleanup_path.is_file()
    cleanup_rows = [] if scan_only else _read_pipe_rows(cleanup_path, _CLEANUP_COLUMNS)
    for row in cleanup_rows:
        row["Disposition"] = row["Disposition"].upper()
    disposition_counts: Counter[str] = Counter(row["Disposition"] for row in cleanup_rows)
    disposition_by_path = {_path_key(row["Path"]): row["Disposition"] for row in cleanup_rows}

    candidates_path = run_dir / "candidates.csv"
    candidates = _read_candidates(candidates_path) if candidates_path.is_file() else []
    categories = _category_statistics(candidates, disposition_by_path, scan_only=scan_only)

    quarantine_value = kwargs.get("quarantine_dir")
    quarantine_dir = Path(
        quarantine_value
        or (Path.home() / "Desktop" / ".omo" / "quarantine" / _drive_id(drive))
    )
    assertions = _assertion_rows(cleanup_rows, quarantine_dir)
    pass_count = sum(item["result"] == "PASS" for item in assertions)
    fail_count = sum(item["result"] == "FAIL" for item in assertions)
    status = "SCAN_ONLY" if scan_only else ("FAIL" if fail_count else "PASS")
    freed_safe = sum(int(item["freed_safe"]) for item in categories)
    freed_quarantined = sum(int(item["freed_quarantined"]) for item in categories)
    skipped = sum(int(item["skipped"]) for item in categories)
    total_freed = None if scan_only else int(volume["FreeBytes"]) - int(preflight["BASELINE_FREE_BYTES"])

    summary_path = run_dir / "summary.md"
    summary = _summary_text(
        drive=drive,
        run_dir=run_dir,
        preflight_path=preflight_path,
        preflight=preflight,
        volume=volume,
        scan_only=scan_only,
        cleanup_rows=cleanup_rows,
        disposition_counts=disposition_counts,
        categories=categories,
        quarantine_dir=quarantine_dir,
        assertions=assertions,
    )
    _write_text_no_follow(summary_path, summary)
    return {
        "drive": drive,
        "run_dir": os.fspath(run_dir),
        "summary_path": os.fspath(summary_path),
        "scan_only": scan_only,
        "status": status,
        "baseline_free": int(preflight["BASELINE_FREE_BYTES"]),
        "final_free": int(volume["FreeBytes"]),
        "total_freed": total_freed,
        "freed_safe": freed_safe,
        "freed_quarantined": freed_quarantined,
        "estimated_freed": freed_safe + freed_quarantined,
        "skipped": skipped,
        "disposition_counts": dict(disposition_counts),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "categories": categories,
    }


def _mapping_value(mapping: object, key: str) -> object:
    if not isinstance(mapping, Mapping):
        return None
    for candidate, value in mapping.items():
        if str(candidate).casefold() == key.casefold():
            return value
    return None


def verify_reports(drives: Sequence[str], **kwargs: Any) -> dict[str, Any]:
    """Verify drives sequentially and write one combined multi-drive report."""
    drive_list = [str(drive) for drive in drives]
    if not drive_list:
        raise ValueError("No drives specified")
    out_dir = Path(kwargs.get("out_dir") or _DEFAULT_OUT_DIR)
    run_dirs = kwargs.get("run_dirs")
    volumes = kwargs.get("volumes")
    quarantine_dirs = kwargs.get("quarantine_dirs")
    results: list[dict[str, Any]] = []
    for drive in drive_list:
        per_drive: dict[str, Any] = {"out_dir": out_dir}
        run_dir = _mapping_value(run_dirs, drive)
        if run_dir is not None:
            per_drive["run_dir"] = run_dir
        volume = _mapping_value(volumes, drive)
        if volume is not None:
            per_drive["volume"] = volume
        quarantine_dir = _mapping_value(quarantine_dirs, drive)
        if quarantine_dir is None:
            quarantine_dir = kwargs.get("quarantine_dir")
        if quarantine_dir is not None:
            per_drive["quarantine_dir"] = quarantine_dir
        results.append(verify_report(drive, **per_drive))

    summary_value = kwargs.get("summary_path")
    summary_path = Path(summary_value) if summary_value is not None else out_dir / "multidrive-summary.md"
    lines = [
        "# Multi-Drive Verification Summary",
        "",
        f"Generated: {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S%z')}",
        "Per-drive verification was invoked sequentially.",
        "",
        "| Drive | RunDir | Scan-Only | Status | Freed Bytes | Assertions | Summary |",
        "|-------|--------|-----------|--------|-------------|------------|---------|",
    ]
    for result in results:
        freed = "n/a" if result["total_freed"] is None else _format_bytes(result["total_freed"])
        assertions = f"{result['pass_count']}/{result['pass_count'] + result['fail_count']} PASS"
        lines.append(
            f"| {_markdown(result['drive'])} | {_markdown(result['run_dir'])} | {'yes' if result['scan_only'] else 'no'} | {result['status']} | {freed} | {assertions} | {_markdown(result['summary_path'])} |"
        )
    lines.extend(
        [
            "",
            f"Total estimated freed across drives: **{_format_bytes(sum(item['estimated_freed'] for item in results))} bytes**",
            "Each drive's detailed report contains the exact eight-section summary.",
            "",
        ]
    )
    _write_text_no_follow(summary_path, "\n".join(lines))
    return {"summary_path": os.fspath(summary_path), "results": results}


def _split_values(values: Optional[Sequence[str]]) -> list[str]:
    result: list[str] = []
    for value in values or []:
        result.extend(part.strip() for part in value.split(",") if part.strip())
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, prefix_chars="-")
    parser.add_argument("-Drive")
    parser.add_argument("-Drives", nargs="+")
    parser.add_argument("-RunDir")
    parser.add_argument("-OutDir")
    parser.add_argument("-QuarantineDir")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    drives = _split_values(arguments.Drives)
    if bool(arguments.Drive) == bool(drives):
        parser.error("specify exactly one of -Drive or -Drives")
    if drives and arguments.RunDir:
        parser.error("-RunDir is valid only with single-drive -Drive mode")
    try:
        if drives:
            result = verify_reports(
                drives,
                out_dir=arguments.OutDir,
                quarantine_dir=arguments.QuarantineDir,
            )
            print(f"VERIFY COMPLETE: multi-drive summary written to {result['summary_path']}")
            return 0
        result = verify_report(
            arguments.Drive,
            run_dir=arguments.RunDir,
            out_dir=arguments.OutDir,
            quarantine_dir=arguments.QuarantineDir,
        )
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"VERIFY COMPLETE: {result['status']} - summary written to {result['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
