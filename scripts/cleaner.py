"""Approval-gated, sequential cleanup of scanner candidate rows."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.lib import core, platform

import psutil


IS_WINDOWS = platform.IS_WINDOWS

_CANDIDATE_COLUMNS = ("Category", "Risk", "Path", "SizeBytes", "FileCount", "Action")
_CLEANUP_HEADER = "Timestamp|Phase|Action|Path|ErrorMessage|Disposition\n"
_VALID_RISKS = {"SAFE", "CAUTION", "ASK", "ELEVATED"}
_VALID_ACTIONS = {"delete", "quarantine", "ask", "report-only"}
_TEMP_CATEGORIES = {"root-temps", "user-temp"}
_DEFAULT_OUT_DIR = Path(__file__).resolve().parents[1] / ".omo" / "evidence" / "python-migration"
_RISK_ACTION_MAP = {
    "SAFE": "delete",
    "CAUTION": "quarantine",
    "ASK": "ask",
    "ELEVATED": "report-only",
}
_CATEGORY_RISK_MAP = {
    "root-temps": "SAFE",
    "root-logs": "SAFE",
    "duplicate-archives": "ASK",
    "empty-dirs": "SAFE",
    "recycle-bin": "ASK",
    "root-suspicious": "CAUTION",
    "app-caches": "SAFE",
    "browser-caches": "SAFE",
    "gpu-shader": "SAFE",
    "dev-caches": "SAFE",
    "ide-caches": "SAFE",
    "crash-dumps": "SAFE",
    "thumbnail-cache": "SAFE",
    "user-temp": "SAFE",
    "elevated-system": "ELEVATED",
}
_CANONICAL_CATEGORY = {category.casefold(): category for category in _CATEGORY_RISK_MAP}

# FM4: category -> owner-process specs (mirror of scanner.CATEGORY_OWNER_PROCESSES).
# A running owner gates the WHOLE category: it is skipped with a clear message,
# never killed. "jetbrains*" is a prefix match.
_CATEGORY_OWNER_PROCESSES = {
    "browser-caches": ["chrome", "msedge"],
    "app-caches": ["wechat", "weixin", "wechatapp"],
    "gpu-shader": [
        "steam",
        "wegame",
        "epicgameslauncher",
        "battle.net",
        "valorant",
        "cs2",
        "dota2",
        "overwatch",
        "apexlegends",
        "fortnite",
        "minecraft",
    ],
    "dev-caches": ["pip", "npm", "python", "node"],
    "ide-caches": ["jetbrains*", "zotero", "code"],
    "crash-dumps": [],
}

_OWNER_DISPLAY = {
    "chrome": "Chrome",
    "msedge": "Edge",
    "wechat": "微信",
    "weixin": "微信",
    "wechatapp": "微信",
    "steam": "Steam",
    "wegame": "WeGame",
    "epicgameslauncher": "Epic Games",
    "battle.net": "Battle.net",
    "valorant": "Valorant",
    "cs2": "CS2",
    "dota2": "Dota 2",
    "overwatch": "Overwatch",
    "apexlegends": "Apex Legends",
    "fortnite": "Fortnite",
    "minecraft": "Minecraft",
    "pip": "pip",
    "npm": "npm",
    "python": "Python",
    "node": "Node.js",
    "jetbrains*": "JetBrains IDE",
    "zotero": "Zotero",
    "code": "VS Code",
}

_CATEGORY_DISPLAY = {
    "browser-caches": "浏览器缓存",
    "app-caches": "应用缓存",
    "gpu-shader": "GPU 着色器缓存",
    "dev-caches": "开发工具缓存",
    "ide-caches": "IDE 缓存",
    "crash-dumps": "崩溃转储",
}

# FM5: execution action enum (mirror of scanner.CATEGORY_ACTION_MAP). Cache
# categories -> clean_contents (delete files inside, KEEP the directory);
# empty-dirs -> remove_if_empty (only delete verified-empty dirs).
_CATEGORY_EXECUTION_MAP = {
    "app-caches": "clean_contents",
    "browser-caches": "clean_contents",
    "gpu-shader": "clean_contents",
    "dev-caches": "clean_contents",
    "ide-caches": "clean_contents",
    "crash-dumps": "clean_contents",
    "empty-dirs": "remove_if_empty",
}


def _drive_id(drive: str) -> str:
    if IS_WINDOWS:
        return drive.rstrip("\\/").rstrip(":").upper()
    return "ROOT"


def _split_values(values: object) -> list[str]:
    if values is None:
        return []
    source = values if isinstance(values, (list, tuple, set)) else [values]
    result: list[str] = []
    for value in source:
        result.extend(part.strip() for part in str(value).split(",") if part.strip())
    return result


def _snapshot_process_stems(process_iter: Any = None) -> set[str]:
    """Return the lowercased stem of every currently running process name."""
    stems: set[str] = set()
    iter_func = process_iter or psutil.process_iter
    try:
        processes = iter_func(["name"])
        for process in processes:
            try:
                name = str(process.info.get("name") or "")
            except (psutil.Error, OSError):
                continue
            stems.add(Path(name).stem.casefold())
    except (psutil.Error, OSError):
        pass
    return stems


def _process_spec_matches(spec: str, stems: set[str]) -> bool:
    """Whether a running stem satisfies an owner spec (``jetbrains*`` prefix)."""
    if spec.endswith("*"):
        prefix = spec[:-1].casefold()
        return any(stem.startswith(prefix) for stem in stems)
    return spec.casefold() in stems


def _owners_running(category: str, stems: set[str]) -> list[str]:
    """Return the owner-process specs of *category* that are currently running."""
    owners = _CATEGORY_OWNER_PROCESSES.get(category) or []
    return [spec for spec in owners if _process_spec_matches(spec, stems)]


def _fm4_skip_message(category: str, owners: Sequence[str]) -> str:
    display = "、".join(sorted({_OWNER_DISPLAY.get(spec, spec.title()) for spec in owners}))
    category_display = _CATEGORY_DISPLAY.get(category, category)
    return f"检测到 {display} 运行中，{category_display}清理已跳过。关闭后重跑该类别即可。"


def _write_text_no_follow(path: Path, text: str) -> None:
    """Write one output file while rejecting linked path components."""
    normalized = Path(core._assert_no_traversal_components(os.fspath(path)))
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(normalized, flags, 0o666)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
        stream.write(text)


def _ensure_cleanup_csv(path: Path) -> None:
    normalized = core._assert_no_traversal_components(os.fspath(path))
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(normalized, flags, 0o666)
    with os.fdopen(descriptor, "a", encoding="utf-8", newline="") as stream:
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(_CLEANUP_HEADER)


def _read_candidates(path: Path) -> list[dict[str, str]]:
    normalized = core._assert_no_traversal_components(os.fspath(path))
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(normalized, flags)
    with os.fdopen(descriptor, "r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="|")
        if tuple(reader.fieldnames or ()) != _CANDIDATE_COLUMNS:
            actual = "|".join(reader.fieldnames or ())
            raise ValueError(
                f"Unexpected candidates.csv header: '{actual}'. Expected: {'|'.join(_CANDIDATE_COLUMNS)}"
            )
        rows: list[dict[str, str]] = []
        for index, source in enumerate(reader):
            if None in source:
                raise ValueError(f"Malformed candidates.csv row {index + 2}: too many columns")
            row = {column: str(source.get(column, "")) for column in _CANDIDATE_COLUMNS}
            if not row["Category"] or not row["Path"]:
                raise ValueError(f"Malformed candidates.csv row {index + 2}: Category and Path are required")
            row["Risk"] = row["Risk"].upper()
            row["Action"] = row["Action"].lower()
            if row["Risk"] not in _VALID_RISKS:
                raise ValueError(f"Malformed candidates.csv row {index + 2}: unknown Risk '{row['Risk']}'")
            if row["Action"] not in _VALID_ACTIONS:
                raise ValueError(f"Malformed candidates.csv row {index + 2}: unknown Action '{row['Action']}'")
            canonical_category = _CANONICAL_CATEGORY.get(row["Category"].casefold())
            if canonical_category is None:
                raise ValueError(
                    f"Malformed candidates.csv row {index + 2}: unknown Category '{row['Category']}'"
                )
            expected_risk = _CATEGORY_RISK_MAP[canonical_category]
            expected_action = _RISK_ACTION_MAP[expected_risk]
            if row["Risk"] != expected_risk or row["Action"] != expected_action:
                raise ValueError(
                    f"Malformed candidates.csv row {index + 2}: category '{row['Category']}' "
                    f"requires Risk '{expected_risk}' and Action '{expected_action}'"
                )
            row["Category"] = canonical_category
            try:
                int(row["SizeBytes"])
                int(row["FileCount"])
            except ValueError as error:
                raise ValueError(f"Malformed candidates.csv row {index + 2}: invalid numeric field") from error
            rows.append(row)
    return rows


def _group_rows(rows: list[dict[str, str]]) -> OrderedDict[str, list[tuple[int, dict[str, str]]]]:
    groups: OrderedDict[str, list[tuple[int, dict[str, str]]]] = OrderedDict()
    for index, row in enumerate(rows):
        groups.setdefault(row["Category"], []).append((index, row))
    for category, entries in groups.items():
        risks = {row["Risk"] for _, row in entries}
        if len(risks) != 1:
            raise ValueError(f"Category '{category}' has inconsistent Risk values")
    return groups


def _latest_candidates(out_dir: Path, drive: str) -> Path:
    prefix = f"{_drive_id(drive)}-"
    candidates = [
        directory / "candidates.csv"
        for directory in out_dir.iterdir()
        if directory.is_dir()
        and directory.name.startswith(prefix)
        and (directory / "candidates.csv").is_file()
    ] if out_dir.is_dir() else []
    if not candidates:
        raise ValueError(f"No candidates.csv found under '{out_dir}' for drive '{drive}'")
    return max(candidates, key=lambda path: path.parent.stat().st_mtime_ns)


def _read_checkpoint(path: Path) -> dict[str, Any]:
    normalized = core._assert_no_traversal_components(os.fspath(path))
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(normalized, flags)
    with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
        data = json.load(stream)
    last_index = int(data.get("lastCleanedRowIndex", -1))
    if last_index < -1:
        raise ValueError("clean checkpoint lastCleanedRowIndex must be >= -1")
    return {
        "completedCategories": [str(item) for item in data.get("completedCategories", [])],
        "lastCleanedRowIndex": last_index,
    }


def _write_checkpoint(path: Path, completed: list[str], last_index: int) -> None:
    payload = {
        "completedCategories": completed,
        "lastCleanedRowIndex": int(last_index),
    }
    try:
        _write_text_no_follow(path, json.dumps(payload, ensure_ascii=False, indent=2))
    except OSError as error:
        print(f"WARNING: clean checkpoint write failed (continuing): {error}", file=sys.stderr)


def _checkpoint_frontier(rows: list[dict[str, str]], completed: list[str]) -> int:
    """Return the first row not covered by a fully completed category."""
    completed_keys = {category.casefold() for category in completed}
    for index, row in enumerate(rows):
        if row["Category"].casefold() not in completed_keys:
            return index
    return len(rows)


def _record(
    csv_path: Path,
    category: str,
    action: str,
    path: object,
    disposition: str,
    message: str = "",
) -> str:
    core.write_cleanup_csv(
        os.fspath(csv_path),
        {
            "Phase": category,
            "Action": action,
            "Path": os.fspath(path),
            "ErrorMessage": message,
            "Disposition": disposition,
        },
    )
    return disposition


def _has_traversal_link(path: str) -> bool:
    try:
        core._assert_no_traversal_components(path)
        return False
    except (OSError, ValueError):
        return True


def _iter_regular_files(directory: Path) -> Iterable[Path]:
    """Yield regular files under *directory*, never following links."""
    stack = [directory]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as iterator:
                entries = list(iterator)
        except OSError:
            continue
        for entry in entries:
            child = Path(entry.path)
            try:
                if entry.is_symlink() or _has_traversal_link(str(child)):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(child)
                    continue
                yield child
            except OSError:
                continue


def _clean_contents(
    category: str,
    target_dir: str,
    csv_path: Path,
    *,
    running_stems: set[str],
    allow_posix_unlink: bool,
) -> str:
    """FM5: recursively delete FILES inside *target_dir*, KEEPING the directory.

    Every file re-enters the Layer-2 verification chain (junction guard,
    process gate, lock probe, temp age re-check) before removal. FM4's
    category gate normally prevents this from running while an owner process
    is active; the owner check here is defense-in-depth for apps started
    between the scan and this clean run.
    """
    target = Path(target_dir)
    if not os.path.isdir(target_dir) or _has_traversal_link(target_dir):
        return _record(
            csv_path,
            category,
            "Remove",
            target_dir,
            "SKIP_NOT_FOUND",
            "re-verify failed: directory is missing or a traversal link",
        )
    if _owners_running(category, running_stems):
        return _record(
            csv_path,
            category,
            "Remove",
            target_dir,
            "SKIP_LOCKED",
            "owner process started between scan and clean (defense-in-depth)",
        )

    deleted = 0
    for file_path in _iter_regular_files(target):
        if category in _TEMP_CATEGORIES:
            try:
                too_recent = (
                    datetime.now().timestamp()
                    - os.stat(file_path, follow_symlinks=False).st_mtime
                    < timedelta(days=7).total_seconds()
                )
            except OSError:
                too_recent = False
            if too_recent:
                _record(
                    csv_path,
                    category,
                    "Remove",
                    str(file_path),
                    "SKIP_TOO_RECENT",
                    "re-verify failed: last write is within the 7-day window",
                )
                continue
        if core.test_file_locked(str(file_path)):
            _record(
                csv_path,
                category,
                "Remove",
                str(file_path),
                "SKIP_LOCKED",
                "re-verify failed: file is locked (in use by a process)",
            )
            continue
        disposition = core.safe_remove(str(file_path), category, os.fspath(csv_path))
        if disposition == "OK":
            deleted += 1
    return "OK"


def _remove_if_empty(category: str, target_dir: str, csv_path: Path) -> str:
    """FM5: delete *target_dir* only when it is verified empty (junction-aware)."""
    if not os.path.isdir(target_dir) or _has_traversal_link(target_dir):
        return _record(
            csv_path,
            category,
            "Remove",
            target_dir,
            "SKIP_NOT_FOUND",
            "re-verify failed: directory is missing or a traversal link",
        )
    if not core.is_dir_empty(target_dir):
        return _record(
            csv_path,
            category,
            "Remove",
            target_dir,
            "SKIP_NOT_EMPTY",
            "re-verify failed: directory is not empty",
        )
    return core.safe_remove(target_dir, category, os.fspath(csv_path))


def _process_row(
    row: dict[str, str],
    category: str,
    csv_path: Path,
    quarantine_dir: Path,
    *,
    allow_posix_unlink: bool = False,
    running_stems: Optional[set[str]] = None,
) -> str:
    target = row["Path"]
    action = row["Action"]
    running = running_stems if running_stems is not None else set()

    # FM5 dispatch: cache categories clean only their contents (keeping the
    # directory); empty-dirs removes only verified-empty directories.
    execution = _CATEGORY_EXECUTION_MAP.get(category, "delete")
    if execution == "clean_contents" and os.path.isdir(target):
        return _clean_contents(
            category,
            target,
            csv_path,
            running_stems=running,
            allow_posix_unlink=allow_posix_unlink,
        )
    if execution == "remove_if_empty":
        return _remove_if_empty(category, target, csv_path)

    if _has_traversal_link(target):
        return _record(
            csv_path,
            category,
            "Quarantine" if action == "quarantine" else "Remove",
            target,
            "SKIP_JUNCTION",
            "refusing a symlink, junction, or linked ancestor",
        )
    if action not in {"quarantine", "delete", "ask"}:
        print(f"  SKIP: '{target}' has unhandled Action '{action}' (report-only, nothing touched)")
        return "SKIP_REPORT_ONLY"

    # FM1/FM2: quarantine and delete run the SAME lock probe. The quarantine
    # early-return must NOT bypass it, and on POSIX an unlink decision never
    # consults the advisory flock probe unless the user opted in explicitly.
    if os.path.isdir(target):
        if not core.is_dir_empty(target):
            return _record(
                csv_path,
                category,
                "Remove",
                target,
                "SKIP_NOT_EMPTY",
                "re-verify failed: directory is not empty",
            )
    elif os.path.isfile(target):
        if category in _TEMP_CATEGORIES:
            try:
                too_recent = datetime.now().timestamp() - os.stat(target, follow_symlinks=False).st_mtime < timedelta(days=7).total_seconds()
            except OSError:
                too_recent = False
            if too_recent:
                return _record(
                    csv_path,
                    category,
                    "Quarantine" if action == "quarantine" else "Remove",
                    target,
                    "SKIP_TOO_RECENT",
                    "re-verify failed: last write is within the 7-day window",
                )
        if not IS_WINDOWS and not allow_posix_unlink:
            return _record(
                csv_path,
                category,
                "Quarantine" if action == "quarantine" else "Remove",
                target,
                "SKIP_POSIX_UNSAFE",
                "POSIX unlink is disabled by default; pass --allow-posix-unlink to override",
            )
        if core.test_file_locked(target):
            return _record(
                csv_path,
                category,
                "Quarantine" if action == "quarantine" else "Remove",
                target,
                "SKIP_LOCKED",
                "re-verify failed: file is locked",
            )

    if action == "quarantine":
        return core.quarantine(target, os.fspath(quarantine_dir), category, os.fspath(csv_path))
    if _has_traversal_link(target):
        return _record(
            csv_path,
            category,
            "Remove",
            target,
            "SKIP_JUNCTION",
            "refusing a symlink, junction, or linked ancestor",
        )
    return core.safe_remove(target, category, os.fspath(csv_path))


def _approval_from_mapping(approvals: object, category: str) -> Optional[bool]:
    if approvals is None:
        return None
    if callable(approvals):
        return bool(approvals(category))
    if isinstance(approvals, Mapping):
        for key, value in approvals.items():
            if str(key).casefold() == category.casefold():
                return bool(value)
        return None
    approved = {value.casefold() for value in _split_values(approvals)}
    return True if category.casefold() in approved else None


def _category_approved(
    category: str,
    risk: str,
    entries: list[tuple[int, dict[str, str]]],
    *,
    yes: bool,
    approvals: object,
    input_func: Callable[[str], str],
) -> bool:
    if yes:
        return True
    explicit = _approval_from_mapping(approvals, category)
    if explicit is not None:
        return explicit
    if risk == "ASK":
        return False
    total = sum(int(row["SizeBytes"]) for _, row in entries)
    print(f"SUMMARY: category {category} - {len(entries)} item(s), {total} byte(s)")
    try:
        answer = input_func(f"Clean category {category}? (y/n)")
    except EOFError:
        return False
    return answer.strip().lower().startswith("y")


def _elevated_batch_text(drive: str, paths: Sequence[object]) -> str:
    """Render the UAC-elevated cleanup batch from APPROVED candidate rows.

    Every deletion line is generated per approved file and carries a
    ``forfiles /d +7`` age gate as defense-in-depth; there is never a bare
    ``del /f /q <dir>\\*`` command.  ``wuauserv`` is stopped for the DISM
    step and restarted afterwards, and failures propagate via
    ``if errorlevel 1 exit /b 1`` instead of an unconditional ``exit /b 0``.
    """
    cutoff = timedelta(days=7).total_seconds()
    now = datetime.now().timestamp()
    lines = ["@echo off", "setlocal"]
    has_dism = False
    for value in paths:
        path = os.fspath(value)
        if not path:
            continue
        if "StartComponentCleanup" in path:
            has_dism = True
            continue
        if not os.path.isfile(path):
            continue
        try:
            if now - os.stat(path, follow_symlinks=False).st_mtime < cutoff:
                continue  # only files older than the 7-day window
        except OSError:
            continue
        parent, name = os.path.split(path)
        if not name or not parent:
            continue
        lines.append(
            f'if exist "{path}" forfiles /d +7 /p "{parent}" /m "{name}" /c "cmd /c del /f /q @path"'
        )
    lines.append("net stop wuauserv >nul 2>&1")
    if has_dism:
        lines.append("dism.exe /online /cleanup-image /startcomponentcleanup")
        lines.append("if errorlevel 1 exit /b 1")
    lines.append("net start wuauserv >nul 2>&1")
    lines.append("if errorlevel 1 exit /b 1")
    lines.append("exit /b 0")
    lines.append("")
    return "\r\n".join(lines)


def _shell_execute_elevated(batch_path: Path) -> int:
    if not IS_WINDOWS:
        raise OSError("UAC elevation is unavailable on this platform")
    import ctypes
    from ctypes import wintypes

    shell_execute = ctypes.WinDLL("shell32", use_last_error=True).ShellExecuteW
    shell_execute.argtypes = (
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.c_int,
    )
    shell_execute.restype = wintypes.HINSTANCE
    result = shell_execute(
        None,
        "runas",
        "cmd.exe",
        f'/c ""{batch_path}""',
        os.fspath(batch_path.parent),
        0,
    )
    code = int(result)
    if code <= 32:
        raise OSError(code, "ShellExecuteW could not launch the elevated batch")
    return code


def _handle_elevated(
    category: str,
    *,
    drive: str,
    run_dir: Path,
    csv_path: Path,
    yes: bool,
    skip_elevated: bool,
    is_user_drive: bool,
    is_system_drive: bool,
    shell_execute: Callable[[Path], object],
    rows: Sequence[dict[str, str]],
) -> bool:
    if not (yes or skip_elevated):
        print(f"SKIP: category {category} requires -Yes or -SkipElevated")
        return False
    if not IS_WINDOWS:
        _record(
            csv_path,
            category,
            "Elevated",
            category,
            "SKIP_ELEVATION_DENIED",
            "elevated cleanup is unavailable on POSIX",
        )
        return True
    if not is_user_drive or not is_system_drive:
        _record(
            csv_path,
            category,
            "Elevated",
            category,
            "SKIP_ELEVATION_DENIED",
            f"refusing elevated cleanup: {drive} is not the user-profile system drive",
        )
        return True

    batch_path = run_dir / "elevated.bat"
    _write_text_no_follow(batch_path, _elevated_batch_text(drive, [row["Path"] for row in rows]))
    print(f"PREPARED: elevated batch written to {batch_path}")
    if skip_elevated:
        _record(
            csv_path,
            category,
            "Elevated",
            category,
            "SKIP_ELEVATION_DENIED",
            "elevated batch prepared but not launched (-SkipElevated)",
        )
        return True

    try:
        shell_execute(batch_path)
        _record(
            csv_path,
            category,
            "Elevated",
            category,
            "ELEVATED_LAUNCHED",
            "ShellExecuteW accepted the batch launch; this does not confirm completion",
        )
    except (OSError, ValueError) as error:
        _record(csv_path, category, "Elevated", category, "SKIP_ELEVATION_DENIED", str(error))
    return True


def clean(drive: str, **kwargs: Any) -> dict[str, Any]:
    """Clean one fixed drive's candidates after category-level approval."""
    volume = kwargs.get("volume")
    if volume is None:
        volume = platform.resolve_fixed_drive(drive)
        if volume is None:
            raise ValueError(f"Drive '{drive}' is not an available fixed local volume")

    out_dir = Path(kwargs.get("out_dir") or _DEFAULT_OUT_DIR)
    candidate_value = kwargs.get("candidates_csv")
    candidates_path = Path(candidate_value) if candidate_value is not None else _latest_candidates(out_dir, drive)
    if not candidates_path.is_file():
        raise ValueError(f"Candidates CSV not found: {candidates_path}")
    rows = _read_candidates(candidates_path)
    groups = _group_rows(rows)
    run_dir = candidates_path.parent
    cleanup_csv = run_dir / "cleanup-errors.csv"
    checkpoint_path = run_dir / "clean-checkpoint.json"
    _ensure_cleanup_csv(cleanup_csv)

    yes = bool(kwargs.get("yes", False))
    skip_elevated = bool(kwargs.get("skip_elevated", False))
    resume = bool(kwargs.get("resume", False))
    categories = {item.casefold() for item in _split_values(kwargs.get("categories"))}
    approvals = kwargs.get("approvals")
    input_func = kwargs.get("input_func", input)
    shell_execute = kwargs.get("shell_execute", _shell_execute_elevated)
    allow_posix_unlink = bool(kwargs.get("allow_posix_unlink", False))
    close_apps = bool(kwargs.get("close_apps", False))
    process_iter = kwargs.get("process_iter")

    if "is_user_drive" in kwargs:
        is_user_drive = bool(kwargs["is_user_drive"])
    elif IS_WINDOWS:
        is_user_drive = Path.home().drive.casefold() == drive.rstrip("\\/").casefold()
    else:
        is_user_drive = drive == "/"
    if "is_system_drive" in kwargs:
        is_system_drive = bool(kwargs["is_system_drive"])
    elif IS_WINDOWS:
        system_drive = os.environ.get("SystemDrive") or Path(os.environ.get("SystemRoot", "")).drive
        is_system_drive = bool(system_drive) and system_drive.rstrip("\\/").casefold() == drive.rstrip("\\/").casefold()
    else:
        is_system_drive = False

    resume_state = {"completedCategories": [], "lastCleanedRowIndex": -1}
    if resume:
        if not checkpoint_path.is_file():
            raise ValueError(f"No checkpoint found for resume: {checkpoint_path}")
        resume_state = _read_checkpoint(checkpoint_path)
    completed = list(resume_state["completedCategories"])
    skipped_categories: list[str] = []
    dispositions: list[dict[str, str]] = []

    # FM4: clean-time process snapshot. --close-apps prompts the user to close
    # the running owner apps (never auto-kill) and then re-snapshots.
    running_stems = _snapshot_process_stems(process_iter=process_iter)
    if close_apps:
        pending_owners: set[str] = set()
        for category in groups:
            if category == "elevated-system":
                continue
            pending_owners.update(_owners_running(category, running_stems))
        if pending_owners:
            display = "、".join(sorted({_OWNER_DISPLAY.get(spec, spec.title()) for spec in pending_owners}))
            input_func(f"检测到以下应用正在运行：{display}。请关闭它们后按 Enter 继续（本工具不会自动关闭应用）")
            running_stems = _snapshot_process_stems(process_iter=process_iter)

    for category, entries in groups.items():
        if categories and category.casefold() not in categories:
            skipped_categories.append(category)
            print(f"SKIP: category {category} excluded by -Categories filter")
            continue
        if category.casefold() in {item.casefold() for item in completed}:
            skipped_categories.append(category)
            print(f"SKIP: category {category} already completed (resume)")
            continue
        # FM4 category-level gate: owner running -> the whole category is
        # skipped with a clear message. elevated-system is never gated.
        owners = [] if category == "elevated-system" else _owners_running(category, running_stems)
        if owners:
            print(f"SKIP: {_fm4_skip_message(category, owners)}")
            skipped_categories.append(category)
            continue
        pending = entries

        risk = entries[0][1]["Risk"]
        handled = False
        if risk == "ELEVATED":
            handled = _handle_elevated(
                category,
                drive=drive,
                run_dir=run_dir,
                csv_path=cleanup_csv,
                yes=yes,
                skip_elevated=skip_elevated,
                is_user_drive=is_user_drive,
                is_system_drive=is_system_drive,
                shell_execute=shell_execute,
                rows=[row for _, row in pending],
            )
        else:
            approved = _category_approved(
                category,
                risk,
                pending,
                yes=yes,
                approvals=approvals,
                input_func=input_func,
            )
            if not approved:
                skipped_categories.append(category)
                if risk == "ASK":
                    print(f"SKIP: category {category} requires -Yes or explicit approval")
                else:
                    print(f"SKIP: category {category} declined by user")
                continue
            print(f"CLEAN: category {category} ({risk})")
            for _, row in pending:
                quarantine_value = kwargs.get("quarantine_dir")
                default_quarantine = Path.home() / "Desktop" / ".omo" / "quarantine" / _drive_id(drive)
                disposition = _process_row(
                    row,
                    category,
                    cleanup_csv,
                    Path(quarantine_value or default_quarantine),
                    allow_posix_unlink=allow_posix_unlink,
                    running_stems=running_stems,
                )
                dispositions.append({"Category": category, "Path": row["Path"], "Disposition": disposition})
            handled = True

        if handled:
            if category not in completed:
                completed.append(category)
            _write_checkpoint(checkpoint_path, completed, _checkpoint_frontier(rows, completed))

    return {
        "drive": drive,
        "run_dir": os.fspath(run_dir),
        "candidates_csv": os.fspath(candidates_path),
        "cleanup_csv": os.fspath(cleanup_csv),
        "checkpoint": os.fspath(checkpoint_path),
        "completed_categories": completed,
        "skipped_categories": skipped_categories,
        "dispositions": dispositions,
    }


def _subprocess_args(drive: str, arguments: argparse.Namespace) -> list[str]:
    command = [sys.executable, os.fspath(Path(__file__).resolve()), "-Drive", drive]
    if arguments.OutDir:
        command.extend(["-OutDir", arguments.OutDir])
    if arguments.QuarantineDir:
        command.extend(["-QuarantineDir", arguments.QuarantineDir])
    if arguments.Yes:
        command.append("-Yes")
    if arguments.SkipElevated:
        command.append("-SkipElevated")
    if arguments.Resume:
        command.append("-Resume")
    if arguments.allow_posix_unlink:
        command.append("--allow-posix-unlink")
    if getattr(arguments, "close_apps", False):
        command.append("--close-apps")
    if arguments.Categories:
        command.extend(["-Categories", ",".join(_split_values(arguments.Categories))])
    return command


def _run_many(drives: list[str], arguments: argparse.Namespace) -> int:
    if arguments.CandidatesCsv:
        raise ValueError("-CandidatesCsv cannot be shared across -Drives; use per-drive output directories")
    failed = False
    for drive in drives:
        print(f"[CLEAN START {drive}] {datetime.now().astimezone().isoformat()}", flush=True)
        completed = subprocess.run(_subprocess_args(drive, arguments), check=False)
        print(
            f"[CLEAN END {drive}] {datetime.now().astimezone().isoformat()}",
            flush=True,
        )
        failed = failed or completed.returncode != 0
    return 1 if failed else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, prefix_chars="-")
    parser.add_argument("-Drive")
    parser.add_argument("-Drives", nargs="+")
    parser.add_argument("-CandidatesCsv")
    parser.add_argument("-OutDir")
    parser.add_argument("-QuarantineDir")
    parser.add_argument("-Yes", action="store_true")
    parser.add_argument("-Categories", nargs="+")
    parser.add_argument("-SkipElevated", action="store_true")
    parser.add_argument("-Resume", action="store_true")
    parser.add_argument("-Parallel", action="store_true")
    parser.add_argument(
        "--allow-posix-unlink",
        action="store_true",
        default=False,
        help="explicitly allow POSIX unlink of files (default: POSIX files are skipped as unsafe)",
    )
    parser.add_argument(
        "--close-apps",
        action="store_true",
        dest="close_apps",
        help="prompt the user to close running owner apps (never auto-kill) instead of skipping their categories",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    drives = _split_values(arguments.Drives)
    if bool(arguments.Drive) == bool(drives):
        parser.error("specify exactly one of -Drive or -Drives")
    if arguments.Parallel:
        print("parallel clean is disabled for safety - cleaning drives sequentially")
    try:
        if drives:
            return _run_many(drives, arguments)
        result = clean(
            arguments.Drive,
            candidates_csv=arguments.CandidatesCsv,
            out_dir=arguments.OutDir,
            quarantine_dir=arguments.QuarantineDir,
            yes=arguments.Yes,
            categories=arguments.Categories,
            skip_elevated=arguments.SkipElevated,
            resume=arguments.Resume,
            allow_posix_unlink=arguments.allow_posix_unlink,
            close_apps=getattr(arguments, "close_apps", False),
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"CLEAN COMPLETE: cleanup CSV at {result['cleanup_csv']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
