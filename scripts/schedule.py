"""Register, unregister, or list rubbish-cleaner scheduled runs."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import plistlib
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence


_REPO_ROOT = Path(__file__).resolve().parents[1]
_CRON_PATH = Path("/etc/cron.d/rubbish-cleaner")
_LAUNCHD_NAME = "com.rubbish-cleaner.plist"
_POLICY_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_TIME = re.compile(r"^(\d{2}):(\d{2})$")
_MARKER_PREFIX = "# rubbish-cleaner:"

Runner = Callable[..., subprocess.CompletedProcess[str]]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("register", "unregister", "list"):
        command = subparsers.add_parser(action)
        command.add_argument("--drive")
        command.add_argument("--policy", default="safe")
        command.add_argument("--time", default="02:00")
    return parser


def load_policy(name: str, repo_root: Path = _REPO_ROOT) -> Mapping[str, Any]:
    """Load and validate one named policy without allowing path traversal."""
    if not _POLICY_NAME.fullmatch(name):
        raise ValueError(f"Invalid policy name: {name}")
    policy_path = Path(repo_root) / "references" / "policies" / f"{name}.json"
    try:
        with policy_path.open("r", encoding="utf-8-sig") as stream:
            policy = json.load(stream)
    except FileNotFoundError as error:
        raise ValueError(f"Policy file not found: {policy_path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Failed to parse policy file '{policy_path}': {error}") from error

    categories = policy.get("categories") if isinstance(policy, dict) else None
    if not isinstance(categories, list) or not categories:
        raise ValueError(f"Policy file '{policy_path}' does not define a categories array")
    if any(not isinstance(category, str) or not category.strip() for category in categories):
        raise ValueError(f"Policy file '{policy_path}' contains an invalid category")
    return policy


def _time_parts(value: str) -> tuple[int, int]:
    match = _TIME.fullmatch(value)
    if match is None:
        raise ValueError(f"Invalid time '{value}' (expected HH:MM)")
    hour, minute = (int(part) for part in match.groups())
    if hour > 23 or minute > 59:
        raise ValueError(f"Invalid time '{value}' (hour 00-23, minute 00-59)")
    return hour, minute


def _validate_drive(drive: Optional[str]) -> str:
    if drive is None or not drive.strip():
        raise ValueError("--drive is required for register")
    if any(character in drive for character in ("\r", "\n", "\0")):
        raise ValueError("Invalid drive value")
    return drive


def _shell_join(arguments: Sequence[str], windows: bool) -> str:
    if windows:
        return subprocess.list2cmdline(list(arguments))
    return shlex.join(arguments)


def build_pipeline_command(
    drive: str,
    categories: Sequence[str],
    repo_root: Path = _REPO_ROOT,
    *,
    python_executable: Optional[str] = None,
    windows: Optional[bool] = None,
) -> str:
    """Build the scan-then-clean pipeline stored in the scheduler."""
    executable = python_executable or sys.executable
    root = Path(repo_root)
    category_list = ",".join(categories)
    scan = [
        executable,
        str(root / "scripts" / "scanner.py"),
        "-Drive",
        drive,
        "-Categories",
        category_list,
    ]
    clean = [
        executable,
        str(root / "scripts" / "cleaner.py"),
        "-Drive",
        drive,
        "-Yes",
    ]
    use_windows_quoting = sys.platform == "win32" if windows is None else windows
    return f"{_shell_join(scan, use_windows_quoting)} && {_shell_join(clean, use_windows_quoting)}"


def _is_windows_admin() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _write_windows_event(message: str) -> None:
    """Write a best-effort Application event when pywin32 is available."""
    try:
        import win32evtlog
        import win32evtlogutil

        win32evtlogutil.ReportEvent(
            "rubbish-cleaner",
            1001,
            eventType=win32evtlog.EVENTLOG_INFORMATION_TYPE,
            strings=[message],
        )
    except Exception:
        return


def _completed_return_code(result: subprocess.CompletedProcess[str]) -> int:
    return int(getattr(result, "returncode", 1))


def _run_query(runner: Runner, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return runner(list(arguments), capture_output=True, text=True, check=False)


def _windows_task_names(output: str) -> list[str]:
    names: list[str] = []
    try:
        rows = csv.reader(io.StringIO(output))
        for row in rows:
            if not row:
                continue
            name = row[0].strip().lstrip("\\")
            if name.startswith("rubbish-cleaner-"):
                names.append(name)
    except csv.Error:
        return []
    return names


def _windows_query(runner: Runner) -> list[str]:
    result = _run_query(runner, ["schtasks.exe", "/Query", "/FO", "CSV", "/NH"])
    if _completed_return_code(result) != 0:
        return []
    return _windows_task_names(result.stdout or "")


def _register_windows(
    drive: str,
    time_value: str,
    command: str,
    runner: Runner,
    event_logger: Callable[[str], None],
) -> int:
    task_name = f"rubbish-cleaner-{drive}"
    arguments = [
        "schtasks.exe",
        "/Create",
        "/SC",
        "DAILY",
        "/ST",
        time_value,
        "/TN",
        task_name,
        "/TR",
        command,
        "/F",
    ]
    result = runner(arguments, capture_output=True, text=True, check=False)
    if _completed_return_code(result) != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        print(f"Failed to register scheduled task: {detail}", file=sys.stderr)
        return 1
    print(f"Registered scheduled task '{task_name}' (daily at {time_value}).")
    try:
        event_logger(f"rubbish-cleaner scheduled task registered for drive {drive}")
    except Exception:
        pass
    return 0


def _unregister_windows(drive: Optional[str], runner: Runner) -> int:
    task_names = _windows_query(runner)
    if drive:
        requested = f"rubbish-cleaner-{drive}"
        task_names = [name for name in task_names if name.casefold() == requested.casefold()]
    if not task_names:
        print("No rubbish-cleaner scheduled tasks found.")
        return 0

    failed = False
    for task_name in task_names:
        result = runner(
            ["schtasks.exe", "/Delete", "/TN", task_name, "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
        if _completed_return_code(result) == 0:
            print(f"Unregistered scheduled task '{task_name}'.")
        else:
            failed = True
            detail = (result.stderr or result.stdout or "unknown error").strip()
            print(f"Failed to unregister '{task_name}': {detail}", file=sys.stderr)
    return 1 if failed else 0


def _list_windows(runner: Runner) -> int:
    names = _windows_query(runner)
    if not names:
        print("No rubbish-cleaner scheduled tasks registered.")
        return 0
    print("rubbish-cleaner scheduled tasks:")
    for name in names:
        print(f"  {name}")
    return 0


def _marker(drive: str) -> str:
    return f"{_MARKER_PREFIX}{drive}"


def _remove_cron_entries(text: str, drive: Optional[str]) -> tuple[str, bool]:
    lines = text.splitlines()
    output: list[str] = []
    removed = False
    index = 0
    wanted = _marker(drive) if drive else None
    while index < len(lines):
        line = lines[index]
        matches = line.startswith(_MARKER_PREFIX) and (wanted is None or line == wanted)
        if matches:
            removed = True
            index += 2
            continue
        output.append(line)
        index += 1
    normalized = "\n".join(output).strip("\n")
    return (f"{normalized}\n" if normalized else ""), removed


def _add_cron_entry(current: str, drive: str, cron_line: str) -> str:
    filtered, _removed = _remove_cron_entries(current, drive)
    prefix = filtered.rstrip("\n")
    pieces = [piece for piece in (prefix, _marker(drive), cron_line) if piece]
    return "\n".join(pieces) + "\n"


def _read_crontab(runner: Runner) -> str:
    result = _run_query(runner, ["crontab", "-l"])
    if _completed_return_code(result) != 0:
        return ""
    return result.stdout or ""


def _write_crontab(runner: Runner, text: str) -> int:
    result = runner(
        ["crontab", "-"],
        input=text,
        capture_output=True,
        text=True,
        check=False,
    )
    if _completed_return_code(result) != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        print(f"Failed to update user crontab: {detail}", file=sys.stderr)
        return 1
    return 0


def _register_linux(
    drive: str,
    hour: int,
    minute: int,
    command: str,
    runner: Runner,
    root: bool,
    cron_path: Path,
) -> int:
    user_column = " root" if root else ""
    cron_line = f"{minute} {hour} * * *{user_column} {command}"
    if root:
        current = cron_path.read_text(encoding="utf-8") if cron_path.is_file() else ""
        cron_path.write_text(_add_cron_entry(current, drive, cron_line), encoding="utf-8")
        print(f"Wrote {cron_path}")
        return 0

    updated = _add_cron_entry(_read_crontab(runner), drive, cron_line)
    if _write_crontab(runner, updated) != 0:
        return 1
    print(f"Registered user crontab entry for drive {drive}.")
    return 0


def _unregister_linux(
    drive: Optional[str], runner: Runner, root: bool, cron_path: Path
) -> int:
    if root:
        if not cron_path.is_file():
            print("No rubbish-cleaner cron entries found.")
            return 0
        if drive is None:
            cron_path.unlink()
            print("Removed rubbish-cleaner cron entries.")
            return 0
        filtered, removed = _remove_cron_entries(cron_path.read_text(encoding="utf-8"), drive)
        if not removed:
            print("No rubbish-cleaner cron entries found.")
            return 0
        if filtered.strip():
            cron_path.write_text(filtered, encoding="utf-8")
        else:
            cron_path.unlink()
        print("Removed rubbish-cleaner cron entries.")
        return 0

    current = _read_crontab(runner)
    filtered, removed = _remove_cron_entries(current, drive)
    if not removed:
        print("No rubbish-cleaner cron entries found.")
        return 0
    if _write_crontab(runner, filtered) != 0:
        return 1
    print("Removed rubbish-cleaner cron entries.")
    return 0


def _list_linux(runner: Runner, root: bool, cron_path: Path) -> int:
    if root:
        current = cron_path.read_text(encoding="utf-8") if cron_path.is_file() else ""
    else:
        current = _read_crontab(runner)
    hits = [line for line in current.splitlines() if "rubbish-cleaner" in line]
    if not hits:
        print("No rubbish-cleaner cron entries found.")
        return 0
    print("rubbish-cleaner cron entries:")
    for line in hits:
        print(f"  {line}")
    return 0


def _launchd_path(launch_agents_dir: Path) -> Path:
    return launch_agents_dir / _LAUNCHD_NAME


def _register_macos(
    drive: str,
    hour: int,
    minute: int,
    command: str,
    runner: Runner,
    launch_agents_dir: Path,
) -> int:
    launch_agents_dir.mkdir(parents=True, exist_ok=True)
    plist_path = _launchd_path(launch_agents_dir)
    payload = {
        "Label": "com.rubbish-cleaner",
        "ProgramArguments": ["/bin/sh", "-c", command],
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
    }
    with plist_path.open("wb") as stream:
        plistlib.dump(payload, stream, sort_keys=False)
    result = runner(
        ["launchctl", "load", str(plist_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if _completed_return_code(result) != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        print(f"Failed to load launchd agent: {detail}", file=sys.stderr)
        return 1
    print(f"Loaded launchd agent from {plist_path} (daily at {hour:02d}:{minute:02d}).")
    return 0


def _unregister_macos(runner: Runner, launch_agents_dir: Path) -> int:
    plist_path = _launchd_path(launch_agents_dir)
    if not plist_path.is_file():
        print("No rubbish-cleaner launchd agent found.")
        return 0
    result = runner(
        ["launchctl", "unload", str(plist_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if _completed_return_code(result) != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        print(f"Failed to unload launchd agent: {detail}", file=sys.stderr)
        return 1
    plist_path.unlink()
    print(f"Removed launchd agent {plist_path}.")
    return 0


def _list_macos(launch_agents_dir: Path) -> int:
    plist_path = _launchd_path(launch_agents_dir)
    if plist_path.is_file():
        print(f"Registered launchd agent: {plist_path}")
    else:
        print("No rubbish-cleaner launchd agent found.")
    return 0


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    system: Optional[str] = None,
    runner: Optional[Runner] = None,
    admin_check: Optional[Callable[[], bool]] = None,
    event_logger: Optional[Callable[[str], None]] = None,
    geteuid: Optional[Callable[[], int]] = None,
    repo_root: Optional[Path] = None,
    cron_path: Optional[Path] = None,
    launch_agents_dir: Optional[Path] = None,
) -> int:
    """Run one explicit scheduling action; importing this module does nothing."""
    arguments = _build_parser().parse_args(argv)
    selected_system = system or sys.platform
    run = runner or subprocess.run
    root = Path(repo_root) if repo_root is not None else _REPO_ROOT
    cron_target = Path(cron_path) if cron_path is not None else _CRON_PATH
    launch_target = (
        Path(launch_agents_dir)
        if launch_agents_dir is not None
        else Path.home() / "Library" / "LaunchAgents"
    )

    try:
        if arguments.action == "register":
            drive = _validate_drive(arguments.drive)
            if selected_system == "win32":
                check_admin = admin_check or _is_windows_admin
                if not check_admin():
                    print("Administrator privileges required", file=sys.stderr)
                    return 1

            hour, minute = _time_parts(arguments.time)
            policy = load_policy(arguments.policy, root)
            categories = list(policy["categories"])
            command = build_pipeline_command(
                drive,
                categories,
                root,
                windows=selected_system == "win32",
            )

            if selected_system == "win32":
                return _register_windows(
                    drive,
                    arguments.time,
                    command,
                    run,
                    event_logger or _write_windows_event,
                )
            if selected_system == "linux":
                euid = geteuid or getattr(os, "geteuid", lambda: 1)
                return _register_linux(
                    drive, hour, minute, command, run, euid() == 0, cron_target
                )
            if selected_system == "darwin":
                return _register_macos(
                    drive, hour, minute, command, run, launch_target
                )
            print("Unknown platform; scheduled registration is not supported here.", file=sys.stderr)
            return 1

        if arguments.action == "unregister":
            if selected_system == "win32":
                return _unregister_windows(arguments.drive, run)
            if selected_system == "linux":
                euid = geteuid or getattr(os, "geteuid", lambda: 1)
                return _unregister_linux(arguments.drive, run, euid() == 0, cron_target)
            if selected_system == "darwin":
                return _unregister_macos(run, launch_target)
            print("Unknown platform; scheduled unregistration is not supported here.", file=sys.stderr)
            return 1

        if selected_system == "win32":
            return _list_windows(run)
        if selected_system == "linux":
            euid = geteuid or getattr(os, "geteuid", lambda: 1)
            return _list_linux(run, euid() == 0, cron_target)
        if selected_system == "darwin":
            return _list_macos(launch_target)
        print("Unknown platform; listing is not supported here.", file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
