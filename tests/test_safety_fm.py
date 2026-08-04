"""FM1-FM3 + FM4/FM5/FM0 safety regression tests (POSIX default-skip,
quarantine lock-probe, candidates-driven elevated batch, process-awareness
gate, dual-action execution, conservative default posture + dry-run).

Each ``test_fmN_*`` test FAILS on the pre-fix code and PASSES on the fixed code:
- FM1: POSIX rows return ``SKIP_POSIX_UNSAFE`` without ever calling the flock
  probe unless ``--allow-posix-unlink`` is passed.
- FM2: quarantine runs the SAME lock probe as delete (no early-return bypass);
  a locked file is never moved.
- FM3: the elevated batch is generated from approved candidate rows only, every
  deletion line is age-gated with ``forfiles /d +7``, there is no bare wildcard
  ``del``, and ``wuauserv`` is restarted after the cleanup steps.
- FM8: ``get_fixed_drives`` returns only fixed local drives; removable, CD and
  network volumes are filtered out via the ``fixed`` partition opt.
- FM4: an owner process running (e.g. Chrome) gates its whole category at clean
  time - 0 files deleted + a clear skip message - and ``--close-apps`` prompts
  the user without ever killing a process.
- FM5: ``clean_contents`` deletes only the FILES inside a cache directory (the
  directory survives); ``remove_if_empty`` deletes only verified-empty dirs.
- FM0: the default scan/clean posture is conservative (no app-owned caches),
  explicit ``--categories`` re-includes them, ``--dry-run`` previews without
  deleting, and the policy JSONs parse with a conservative safe profile.
- FM9: the default quarantine dir resolves onto the SAME drive as the source
  (``X:\\.rubbish-quarantine\\run-<ts>`` on Windows), so ``core.quarantine``
  never crosses a volume boundary (no EXDEV -> no silent ``MOVE_FAILED``);
  an explicit ``-QuarantineDir`` still wins over the same-volume default.
- FM6: taxonomy mutual exclusion — root-logs drops ``*.tmp`` (leaves .tmp to
  the age-gated root-temps path) and a scanner claims set enforces
  single-ownership (a path claimed by one category is never re-added).
- FM7: path semantic validation — a static-map cache dir whose sampled content
  carries a data-file suffix (.db/.sqlite/.sqlite3/.sqlitedb/.db-shm/.db-wal/
  .index/.dat) is upgraded to CAUTION (quarantine), never SAFE/delete;
  .json/.xml/.ini/.conf/.bak do NOT count as data-like.
- FM7 x FM5 interaction: a CAUTION (data-signature) cache-dir row is dispatched
  by its ROW Action — quarantined whole, never clean_contents'd in place — and
  a category may mix SAFE + CAUTION rows in one run.
- FM13: the dry-run preview prints a per-file line containing
  path | size | category | action, the interactive confirmation shows
  "将删除 N 个文件 / X MB" plus the first rows, and the post-clean report lists
  deleted + skipped + reasons.
- FM14: root-logs system-owner exclusion (v2.1.1) — a root-level .log whose
  owner is the OS (SYSTEM on Windows via FILE_ATTRIBUTE_SYSTEM or an unreadable
  owner ACL, uid 0 on POSIX) is never a candidate; a Hidden-only (0x2) file
  with a readable user ACL is still flagged (no over-exclusion).
- FM15: user-temp installer exemption (v2.1.1) — installer/uninstaller
  artifacts (``.exe``/``.msi``/``.msu``/``.msp``/``.cab`` suffix or a whole-word
  setup/install/unins/uninstall/updater in the casefolded name, ``install.log``
  included BY DESIGN) stay exempt past the 7-day gate; generic ``.tmp`` junk
  remains a candidate.
"""

from __future__ import annotations

import atexit
import contextlib
import csv
import ctypes
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

import psutil

from scripts import cleaner, scanner
from scripts.lib.platform import IS_WINDOWS


def _tmp_path(tmp_path=None) -> Path:
    if tmp_path is not None:
        return Path(tmp_path)
    path = Path(tempfile.mkdtemp(prefix="rubbish-safety-fm-"))
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    return path


def _aged_file(path: Path, days_old: int) -> Path:
    path.write_text("payload", encoding="utf-8")
    stamp = datetime.now().timestamp() - timedelta(days=days_old).total_seconds()
    os.utime(path, (stamp, stamp))
    return path


def _restore_globals(cleaner_module, original_is_windows, original_probe):
    cleaner_module.IS_WINDOWS = original_is_windows
    cleaner_module.core.test_file_locked = original_probe


# --------------------------------------------------------------------------- #
# FM1 — POSIX lock-probe default-skip
# --------------------------------------------------------------------------- #

def test_fm1_posix_default_skips_without_allow_flag(tmp_path=None):
    root = _tmp_path(tmp_path)
    target = root / "open.log"
    target.write_text("payload", encoding="utf-8")
    csv_path = root / "cleanup.csv"
    row = {"Category": "root-logs", "Risk": "SAFE", "Path": str(target),
           "SizeBytes": "7", "FileCount": "1", "Action": "delete"}

    original_is_windows = cleaner.IS_WINDOWS
    original_probe = cleaner.core.test_file_locked

    def probe_must_not_be_called(path):
        raise AssertionError("FM1 regression: flock probe called on POSIX default path")

    cleaner.IS_WINDOWS = False
    cleaner.core.test_file_locked = probe_must_not_be_called
    try:
        disposition = cleaner._process_row(row, "root-logs", csv_path, root / "quarantine")
    finally:
        _restore_globals(cleaner, original_is_windows, original_probe)

    assert disposition == "SKIP_POSIX_UNSAFE"
    assert target.exists(), "FM1 regression: file must not be removed on POSIX default"
    assert "SKIP_POSIX_UNSAFE" in csv_path.read_text(encoding="utf-8")


def test_fm1_allow_posix_unlink_flag_proceeds_to_probe(tmp_path=None):
    root = _tmp_path(tmp_path)
    target = root / "open.log"
    target.write_text("payload", encoding="utf-8")
    csv_path = root / "cleanup.csv"
    row = {"Category": "root-logs", "Risk": "SAFE", "Path": str(target),
           "SizeBytes": "7", "FileCount": "1", "Action": "delete"}

    original_is_windows = cleaner.IS_WINDOWS
    original_probe = cleaner.core.test_file_locked
    probe_calls: list[str] = []

    def probe_recorder(path):
        probe_calls.append(str(path))
        return False  # not locked -> deletion proceeds

    cleaner.IS_WINDOWS = False
    cleaner.core.test_file_locked = probe_recorder
    try:
        disposition = cleaner._process_row(
            row, "root-logs", csv_path, root / "quarantine",
            allow_posix_unlink=True,
        )
    finally:
        _restore_globals(cleaner, original_is_windows, original_probe)

    assert probe_calls == [str(target)], "FM1 regression: probe must run only with the allow flag"
    assert disposition == "OK"
    assert not target.exists()


def test_fm1_parser_exposes_allow_posix_unlink_flag():
    parser = cleaner._build_parser()
    arguments = parser.parse_args(["--allow-posix-unlink"])
    assert arguments.allow_posix_unlink is True
    arguments = parser.parse_args([])
    assert arguments.allow_posix_unlink is False


# --------------------------------------------------------------------------- #
# FM2 — quarantine lock-probe (no early-return bypass)
# --------------------------------------------------------------------------- #

class _LockedHandle:
    """An exclusive handle on *path*: CreateFileW share-none on Windows,
    fcntl flock on POSIX."""

    def __init__(self, path):
        self.path = Path(path)
        self._win_handle = None
        self._posix_fd = None
        if IS_WINDOWS:
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
            handle = create_file(str(path), 0x80000000, 0, None, 3, 0, None)
            if handle == ctypes.c_void_p(-1).value:
                raise OSError(ctypes.get_last_error(), "could not open exclusive handle")
            self._win_handle = handle
        else:
            import fcntl
            descriptor = os.open(str(path), os.O_RDONLY)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._posix_fd = descriptor

    def close(self):
        if self._win_handle is not None:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(self._win_handle)
            self._win_handle = None
        if self._posix_fd is not None:
            os.close(self._posix_fd)
            self._posix_fd = None


def test_fm2_locked_file_is_not_quarantined(tmp_path=None):
    root = _tmp_path(tmp_path)
    target = root / "suspicious.dll"
    target.write_text("dll", encoding="utf-8")
    quarantine_dir = root / "quarantine"
    csv_path = root / "cleanup.csv"
    row = {"Category": "root-suspicious", "Risk": "CAUTION", "Path": str(target),
           "SizeBytes": "3", "FileCount": "1", "Action": "quarantine"}

    handle = _LockedHandle(target)
    try:
        disposition = cleaner._process_row(
            row, "root-suspicious", csv_path, quarantine_dir,
            allow_posix_unlink=True,
        )
    finally:
        handle.close()

    assert disposition == "SKIP_LOCKED"
    assert target.exists(), "FM2 regression: locked file must not be moved"
    assert not (quarantine_dir / target.name).exists()
    assert "SKIP_LOCKED" in csv_path.read_text(encoding="utf-8")


def test_fm2_unlocked_file_is_quarantined(tmp_path=None):
    root = _tmp_path(tmp_path)
    target = root / "suspicious.dll"
    target.write_text("dll", encoding="utf-8")
    quarantine_dir = root / "quarantine"
    csv_path = root / "cleanup.csv"
    row = {"Category": "root-suspicious", "Risk": "CAUTION", "Path": str(target),
           "SizeBytes": "3", "FileCount": "1", "Action": "quarantine"}

    disposition = cleaner._process_row(
        row, "root-suspicious", csv_path, quarantine_dir,
        allow_posix_unlink=True,
    )

    assert disposition == "QUARANTINED"
    assert not target.exists(), "FM2 regression: unlocked file should move"
    assert (quarantine_dir / target.name).exists()
    assert "QUARANTINED" in csv_path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# FM3 — candidates-driven elevated batch with forfiles age gate
# --------------------------------------------------------------------------- #

def test_fm3_batch_age_gate_omits_recent_file(tmp_path=None):
    root = _tmp_path(tmp_path)
    recent = _aged_file(root / "recent.tmp", 5)   # 5-day-old -> excluded
    old = _aged_file(root / "old.tmp", 10)        # 10-day-old -> included
    batch = cleaner._elevated_batch_text("C:", [str(recent), str(old)])

    assert "forfiles /d +7" in batch
    assert "net start wuauserv" in batch
    assert "net stop wuauserv" in batch
    assert "if errorlevel 1 exit /b 1" in batch
    assert old.name in batch, "FM3 regression: 10-day-old approved file must be referenced"
    assert recent.name not in batch, "FM3 regression: 5-day-old file must not be referenced"
    for line in batch.splitlines():
        bare = line.strip().startswith("del ") and "*" in line
        assert not bare, f"FM3 regression: bare wildcard delete present: {line}"
    assert 'del /f /q "C:\\Windows\\Temp\\*"' not in batch


def test_fm3_batch_is_candidates_driven_from_approved_rows(tmp_path=None):
    root = _tmp_path(tmp_path)
    approved_a = _aged_file(root / "a.cab", 10)
    approved_b = _aged_file(root / "b.cab", 10)
    rejected_c = _aged_file(root / "c.cab", 10)
    batch = cleaner._elevated_batch_text(
        "C:", [str(approved_a), str(approved_b), str(rejected_c)][:2]
    )

    assert approved_a.name in batch
    assert approved_b.name in batch
    assert rejected_c.name not in batch, "FM3 regression: unapproved row leaked into batch"
    for line in batch.splitlines():
        if "del /f /q" in line:
            assert line.startswith("if exist "), "FM3 regression: deletion line is not gated"
            assert "forfiles /d +7" in line


def test_fm3_batch_no_bare_wildcard_and_service_restored(tmp_path=None):
    root = _tmp_path(tmp_path)
    old = _aged_file(root / "tempfile.tmp", 10)
    batch = cleaner._elevated_batch_text(
        "C:", [str(old), "DISM StartComponentCleanup (no /ResetBase)"]
    )

    assert "dism.exe /online /cleanup-image /startcomponentcleanup" in batch
    assert "net stop wuauserv" in batch
    assert "net start wuauserv" in batch
    assert "if errorlevel 1 exit /b 1" in batch
    assert batch.rstrip().endswith("exit /b 0")
    # No bare wildcard deletion command anywhere in the batch.
    for line in batch.splitlines():
        bare = line.strip().startswith("del ") and "*" in line
        assert not bare, f"FM3 regression: bare wildcard delete present: {line}"
    assert 'del /f /q "C:\\Windows\\Temp\\*"' not in batch
    assert 'del /f /q "C:\\Windows\\Prefetch\\*.pf"' not in batch


# --------------------------------------------------------------------------- #
# FM8 — fixed-drive filter (no removable/CD/network drives)
# --------------------------------------------------------------------------- #

def test_fm8_removable_drive_excluded(tmp_path=None):
    """get_fixed_drives must list only fixed local volumes.

    Mocks psutil.disk_partitions to return a fixed C:, a removable E: and a
    cdrom D:; only the fixed drive may appear in the result. Fails on the
    pre-fix code (no ``fixed`` opt filter) and passes on the fixed code.
    """
    from scripts.lib import platform as platform_lib

    fixed = SimpleNamespace(
        device="C:\\", mountpoint="C:\\", fstype="NTFS", opts="rw,fixed"
    )
    removable = SimpleNamespace(
        device="E:\\", mountpoint="E:\\", fstype="FAT32", opts="rw,removable"
    )
    cdrom = SimpleNamespace(
        device="D:\\", mountpoint="D:\\", fstype="CDFS", opts="rw,cdrom"
    )
    usage = SimpleNamespace(free=1024 * 1024, total=1024 * 1024 * 2)

    original_is_windows = platform_lib.IS_WINDOWS
    try:
        platform_lib.IS_WINDOWS = True
        with mock.patch.object(
            psutil, "disk_partitions", return_value=[fixed, removable, cdrom]
        ), mock.patch.object(os.path, "exists", return_value=True), mock.patch.object(
            psutil, "disk_usage", return_value=usage
        ):
            drives = platform_lib.get_fixed_drives()
    finally:
        platform_lib.IS_WINDOWS = original_is_windows

    assert "C:\\" in drives, "FM8 regression: fixed C: drive must be listed"
    assert "E:\\" not in drives, "FM8 regression: removable drive leaked into fixed list"
    assert "D:\\" not in drives, "FM8 regression: cdrom drive leaked into fixed list"
    assert drives == ["C:\\"], "FM8 regression: only fixed drive should be returned"


# --------------------------------------------------------------------------- #
# Shared helpers for FM4/FM5/FM0
# --------------------------------------------------------------------------- #

def _write_candidates(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=("Category", "Risk", "Path", "SizeBytes", "FileCount", "Action"),
            delimiter="|",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _volume(root: Path) -> dict[str, object]:
    return {"Root": str(root), "FreeBytes": 1000, "TotalBytes": 5000}


def _run_with_stdout(func):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        value = func()
    return value, buffer.getvalue()


class _FakeProc:
    def __init__(self, name):
        self._info = {"name": name}

    @property
    def info(self):
        return self._info


def _mock_running(module, names):
    return mock.patch.object(module.psutil, "process_iter", return_value=[_FakeProc(name) for name in names])


def _clean_browser_cache(root: Path, file_names: list[str]) -> tuple[Path, list[Path], Path]:
    cache = root / "cache"
    cache.mkdir()
    files = [_aged_file(cache / name, 10) for name in file_names]
    candidates = root / "candidates.csv"
    _write_candidates(
        candidates,
        [{"Category": "browser-caches", "Risk": "SAFE", "Path": str(cache),
          "SizeBytes": 7 * len(files), "FileCount": len(files), "Action": "delete"}],
    )
    return cache, files, candidates


# --------------------------------------------------------------------------- #
# FM4 — process-awareness gate (owner running -> category skip, never kill)
# --------------------------------------------------------------------------- #

def test_fm4_process_gate_skips_running_app(tmp_path=None):
    root = _tmp_path(tmp_path)
    cache, files, candidates = _clean_browser_cache(root, ["f1.bin"])

    with _mock_running(cleaner, ["chrome.exe"]):
        result, out = _run_with_stdout(lambda: cleaner.clean(
            "X:", volume=_volume(root), candidates_csv=candidates, yes=True,
            quarantine_dir=root / "quarantine", is_user_drive=False, is_system_drive=False,
        ))

    assert files[0].exists(), "FM4 regression: running-app cache file must survive"
    assert cache.exists()
    assert result["dispositions"] == [], "FM4 regression: gated category must not delete anything"
    assert "browser-caches" in result["skipped_categories"]
    assert "检测到 Chrome 运行中" in out, "FM4 regression: skip message must name the running app"
    assert "浏览器缓存清理已跳过" in out, "FM4 regression: skip message must name the category"


def test_fm4_process_stopped_cleans_normally(tmp_path=None):
    root = _tmp_path(tmp_path)
    cache, files, candidates = _clean_browser_cache(root, ["f1.bin"])

    with _mock_running(cleaner, []):
        result, _ = _run_with_stdout(lambda: cleaner.clean(
            "X:", volume=_volume(root), candidates_csv=candidates, yes=True,
            quarantine_dir=root / "quarantine", allow_posix_unlink=True,
            is_user_drive=False, is_system_drive=False,
        ))

    assert not files[0].exists(), "FM4 regression: stopped-app cache contents should be cleaned"
    assert cache.exists(), "FM4/FM5: clean_contents keeps the directory"
    assert any(item["Disposition"] == "OK" for item in result["dispositions"])


def test_fm4_close_apps_prompts_and_never_kills(tmp_path=None):
    root = _tmp_path(tmp_path)
    cache, files, candidates = _clean_browser_cache(root, ["f1.bin"])
    prompts: list[str] = []

    def fake_input(prompt):
        prompts.append(prompt)
        return ""  # user presses Enter without actually closing anything

    with _mock_running(cleaner, ["chrome.exe"]):
        result, _ = _run_with_stdout(lambda: cleaner.clean(
            "X:", volume=_volume(root), candidates_csv=candidates, yes=True,
            quarantine_dir=root / "quarantine", input_func=fake_input,
            close_apps=True, is_user_drive=False, is_system_drive=False,
        ))

    assert prompts, "FM4 regression: --close-apps must prompt the user"
    assert "关闭" in prompts[0] and "Chrome" in prompts[0]
    assert files[0].exists(), "FM4 regression: files must survive (never auto-kill)"
    assert result["dispositions"] == []
    assert "browser-caches" in result["skipped_categories"]


# --------------------------------------------------------------------------- #
# FM5 — dual-action execution (clean_contents vs remove_if_empty)
# --------------------------------------------------------------------------- #

def test_fm5_clean_contents_keeps_directory(tmp_path=None):
    root = _tmp_path(tmp_path)
    cache, files, candidates = _clean_browser_cache(root, ["f1.bin", "f2.bin", "f3.bin", "f4.bin", "f5.bin"])

    with _mock_running(cleaner, []):
        result, _ = _run_with_stdout(lambda: cleaner.clean(
            "X:", volume=_volume(root), candidates_csv=candidates, yes=True,
            quarantine_dir=root / "quarantine", allow_posix_unlink=True,
            is_user_drive=False, is_system_drive=False,
        ))

    assert cache.exists(), "FM5 regression: clean_contents must keep the directory"
    assert all(not f.exists() for f in files), "FM5 regression: all 5 cache files must be deleted"
    cleanup_text = (root / "cleanup-errors.csv").read_text(encoding="utf-8")
    assert cleanup_text.count("|OK\n") == 5, "FM5 regression: every deleted file must record OK"
    assert any(item["Disposition"] == "OK" for item in result["dispositions"])


def test_fm5_remove_if_empty_only_empty(tmp_path=None):
    root = _tmp_path(tmp_path)
    empty = root / "empty"
    empty.mkdir()
    non_empty = root / "non-empty"
    non_empty.mkdir()
    (non_empty / "keep.txt").write_text("keep", encoding="utf-8")
    candidates = root / "candidates.csv"
    _write_candidates(
        candidates,
        [
            {"Category": "empty-dirs", "Risk": "SAFE", "Path": str(empty), "SizeBytes": 0, "FileCount": 0, "Action": "delete"},
            {"Category": "empty-dirs", "Risk": "SAFE", "Path": str(non_empty), "SizeBytes": 0, "FileCount": 0, "Action": "delete"},
        ],
    )

    result, _ = _run_with_stdout(lambda: cleaner.clean(
        "X:", volume=_volume(root), candidates_csv=candidates, yes=True,
        quarantine_dir=root / "quarantine", allow_posix_unlink=True,
        is_user_drive=False, is_system_drive=False,
    ))

    assert not empty.exists(), "FM5 regression: verified-empty dir must be removed"
    assert non_empty.exists(), "FM5 regression: non-empty dir must survive"
    dispositions = {item["Path"]: item["Disposition"] for item in result["dispositions"]}
    assert dispositions[str(empty)] == "OK"
    assert dispositions[str(non_empty)] == "SKIP_NOT_EMPTY"


def test_fm5_scanner_action_enum_maps_cache_categories(tmp_path=None):
    del tmp_path
    for category in ("app-caches", "browser-caches", "gpu-shader", "dev-caches", "ide-caches", "crash-dumps"):
        assert scanner.CATEGORY_ACTION_MAP[category] == "clean_contents"
        assert cleaner._CATEGORY_EXECUTION_MAP[category] == "clean_contents"
    assert scanner.CATEGORY_ACTION_MAP["empty-dirs"] == "remove_if_empty"
    assert cleaner._CATEGORY_EXECUTION_MAP["empty-dirs"] == "remove_if_empty"


# --------------------------------------------------------------------------- #
# FM0 — conservative default posture + --dry-run previews
# --------------------------------------------------------------------------- #

def test_fm0_applicable_categories_default_is_conservative(tmp_path=None):
    del tmp_path
    old_is_windows = scanner.IS_WINDOWS
    scanner.IS_WINDOWS = True
    try:
        applicable = scanner._applicable_categories(None, is_user_drive=True, include_elevated=False)
    finally:
        scanner.IS_WINDOWS = old_is_windows
    assert set(applicable) == {"root-temps", "root-logs", "empty-dirs", "user-temp"}
    assert "browser-caches" not in applicable, "FM0 regression: app caches must be opt-in"
    assert "crash-dumps" not in applicable, "FM0 regression: crash dumps must be opt-in"


def test_fm0_default_scan_excludes_app_caches(tmp_path=None):
    root = _tmp_path(tmp_path)
    fake = root / "fake"
    fake.mkdir()
    (fake / "Temp").mkdir()
    old = time.time() - 30 * 24 * 60 * 60
    aged = fake / "Temp" / "a.tmp"
    aged.write_text("old", encoding="utf-8")
    os.utime(aged, (old, old))
    out_dir = root / "out"
    old_is_windows = scanner.IS_WINDOWS
    scanner.IS_WINDOWS = True
    try:
        with _mock_running(scanner, []):
            result = scanner.scan("X:", root_path=fake, out_dir=out_dir, is_user_drive=False)
    finally:
        scanner.IS_WINDOWS = old_is_windows

    categories = {row["Category"] for row in result["rows"]}
    assert "root-temps" in categories, "FM0: conservative default keeps age-gated temp files"
    for excluded in ("browser-caches", "app-caches", "gpu-shader", "dev-caches", "ide-caches", "crash-dumps"):
        assert excluded not in categories, f"FM0 regression: default scan must exclude {excluded}"


def test_fm0_explicit_categories_include_browser_caches(tmp_path=None):
    root = _tmp_path(tmp_path)
    fake = root / "fake"
    fake.mkdir()
    local = fake / "Local"
    cache = local / "Google" / "Chrome" / "User Data" / "Default" / "Cache"
    cache.mkdir(parents=True)
    (cache / "c.bin").write_text("x", encoding="utf-8")
    out_dir = root / "out"
    old_is_windows = scanner.IS_WINDOWS
    scanner.IS_WINDOWS = True
    try:
        with _mock_running(scanner, []):
            result = scanner.scan(
                "X:", root_path=fake, out_dir=out_dir, local_app_data=local,
                categories=["browser-caches"], is_user_drive=True,
            )
    finally:
        scanner.IS_WINDOWS = old_is_windows

    assert any(row["Category"] == "browser-caches" for row in result["rows"]), (
        "FM0 regression: explicit --categories must re-include browser-caches"
    )


def test_fm0_dry_run_previews_without_deleting(tmp_path=None):
    root = _tmp_path(tmp_path)
    target = _aged_file(root / "root.log", 10)
    candidates = root / "candidates.csv"
    _write_candidates(
        candidates,
        [{"Category": "root-logs", "Risk": "SAFE", "Path": str(target), "SizeBytes": 7, "FileCount": 1, "Action": "delete"}],
    )

    result, out = _run_with_stdout(lambda: cleaner.clean(
        "X:", volume=_volume(root), candidates_csv=candidates, yes=True,
        quarantine_dir=root / "quarantine", dry_run=True,
        is_user_drive=False, is_system_drive=False,
    ))

    assert target.exists(), "FM0 regression: dry-run must not delete"
    assert "DRY-RUN:" in out
    assert all(item["Disposition"] == "DRY_RUN" for item in result["dispositions"])
    assert not (root / "cleanup-errors.csv").exists(), "FM0: dry-run must not mutate run outputs"


def test_fm0_scanner_dry_run_prints_preview(tmp_path=None):
    root = _tmp_path(tmp_path)
    fake = root / "fake"
    fake.mkdir()
    (fake / "Temp").mkdir()
    old = time.time() - 30 * 24 * 60 * 60
    aged = fake / "Temp" / "a.tmp"
    aged.write_text("old", encoding="utf-8")
    os.utime(aged, (old, old))
    out_dir = root / "out"
    old_is_windows = scanner.IS_WINDOWS
    scanner.IS_WINDOWS = True
    try:
        with _mock_running(scanner, []):
            result, out = _run_with_stdout(lambda: scanner.scan(
                "X:", root_path=fake, out_dir=out_dir,
                categories=["root-temps"], is_user_drive=False, dry_run=True,
            ))
    finally:
        scanner.IS_WINDOWS = old_is_windows

    assert "DRY-RUN:" in out, "FM0 regression: scanner dry-run must print a preview"
    assert result["rows"], "scanner dry-run still returns the candidate rows"


def test_fm0_policy_jsons_conservative_safe_and_aggressive(tmp_path=None):
    del tmp_path
    repo = Path(__file__).resolve().parents[1]
    safe = json.loads((repo / "references" / "policies" / "safe.json").read_text(encoding="utf-8"))
    aggressive = json.loads((repo / "references" / "policies" / "aggressive.json").read_text(encoding="utf-8"))
    assert set(safe["categories"]) == {"root-temps", "root-logs", "empty-dirs", "user-temp"}
    assert "browser-caches" in aggressive["categories"]
    assert "crash-dumps" in aggressive["categories"]
    assert "recycle-bin" not in aggressive["categories"], "FM0: aggressive still excludes recycle-bin"


# --------------------------------------------------------------------------- #
# FM9 — same-volume quarantine (no cross-volume EXDEV / MOVE_FAILED)
# --------------------------------------------------------------------------- #

def test_fm9_same_volume_quarantine(tmp_path=None):
    """FM9: the DEFAULT quarantine dir resolves onto the SOURCE drive and the
    move succeeds — a same-volume move never raises EXDEV / MOVE_FAILED."""
    root = _tmp_path(tmp_path)
    old_is_windows = cleaner.IS_WINDOWS
    cleaner.IS_WINDOWS = True
    try:
        quarantine = cleaner._default_quarantine_dir("D:")
    finally:
        cleaner.IS_WINDOWS = old_is_windows

    # FM9: the default must live on the source drive (same volume), NOT on the
    # system/Desktop volume — that is exactly the cross-device case that used
    # to fail silently with MOVE_FAILED.
    assert str(quarantine).startswith("D:" + os.sep), (
        f"FM9 regression: default quarantine must resolve to the drive ROOT "
        f"(D:{os.sep}.rubbish-quarantine...), got {quarantine}"
    )
    assert ".rubbish-quarantine" in quarantine.parts, (
        f"FM9 regression: default quarantine must live under .rubbish-quarantine, got {quarantine}"
    )
    assert quarantine.name.startswith("run-"), "FM9: default quarantine is per-run scoped"

    # A source and its quarantine dir on the SAME volume move cleanly.
    fake_drive = root / "D"
    fake_drive.mkdir()
    source = fake_drive / "suspect.tmp"
    source.write_text("payload", encoding="utf-8")
    csv_path = root / "cleanup.csv"
    quarantine_dir = fake_drive / ".rubbish-quarantine" / "run-test"
    disposition = cleaner.core.quarantine(
        str(source), str(quarantine_dir), "root-suspicious", str(csv_path)
    )
    assert disposition == "QUARANTINED", (
        f"FM9 regression: same-volume quarantine must succeed, got {disposition}"
    )
    assert (quarantine_dir / source.name).read_text(encoding="utf-8") == "payload"
    assert not source.exists()


def test_fm9_quarantine_custom_dir_override(tmp_path=None):
    """FM9: an explicit --quarantine-dir still wins over the same-volume default."""
    root = _tmp_path(tmp_path)
    custom = root / "custom-quarantine"
    target = root / "suspect.dll"
    target.write_text("payload", encoding="utf-8")
    candidates = root / "candidates.csv"
    _write_candidates(
        candidates,
        [{"Category": "root-suspicious", "Risk": "CAUTION", "Path": str(target),
          "SizeBytes": 7, "FileCount": 1, "Action": "quarantine"}],
    )
    old_is_windows = cleaner.IS_WINDOWS
    cleaner.IS_WINDOWS = True
    try:
        with _mock_running(cleaner, []):
            result, _ = _run_with_stdout(lambda: cleaner.clean(
                "D:", volume=_volume(root), candidates_csv=candidates, yes=True,
                quarantine_dir=custom, allow_posix_unlink=True,
                is_user_drive=False, is_system_drive=False,
            ))
    finally:
        cleaner.IS_WINDOWS = old_is_windows

    assert (custom / target.name).exists(), "FM9: explicit --quarantine-dir must win"
    assert not target.exists()
    assert any(item["Disposition"] == "QUARANTINED" for item in result["dispositions"])
    assert result["quarantine_dir"] == os.fspath(custom), (
        "FM9: the resolved quarantine_dir surfaced in the result must be the override"
    )


# --------------------------------------------------------------------------- #
# FM6 — taxonomy mutual exclusion (root-logs drops *.tmp, claims set)
# --------------------------------------------------------------------------- #

def test_fm6_root_tmp_single_ownership(tmp_path=None):
    """FM6: a root-level aged .tmp belongs to EXACTLY ONE category — root-temps
    (age-gated), never root-logs. Pre-fix root-logs matched ``*.tmp`` and
    double-claimed it (un-gated)."""
    root = _tmp_path(tmp_path)
    fake = root / "fake"
    fake.mkdir()
    aged = _aged_file(fake / "foo.tmp", 10)

    old_is_windows = scanner.IS_WINDOWS
    scanner.IS_WINDOWS = False  # root-temps enumerates context["system_temp"]
    try:
        context = {
            "root": fake,
            "system_temp": fake,
            "cutoff": datetime.now() - timedelta(days=7),
            "rows": [],
            "checkpoint": {"fileCounter": 0, "totalBytesSoFar": 0, "lastPath": "", "completedCategories": []},
            "resume_state": None,
        }
        scanner._scan_root_temps(context)
        scanner._scan_root_logs(context)
    finally:
        scanner.IS_WINDOWS = old_is_windows

    matches = [row for row in context["rows"] if row["Path"] == str(aged)]
    assert len(matches) == 1, f"FM6 regression: .tmp claimed by multiple categories: {matches}"
    assert matches[0]["Category"] == "root-temps", (
        f"FM6 regression: expected root-temps ownership, got {matches}"
    )
    assert matches[0]["Risk"] == "SAFE"


def test_fm6_claims_set_prevents_double_ownership(tmp_path=None):
    """FM6: the claims set guarantees single ownership — a path claimed by an
    earlier category is skipped by any later category."""
    root = _tmp_path(tmp_path)
    target = root / "c.log"
    target.write_text("payload", encoding="utf-8")
    context: dict = {"rows": []}
    scanner._add_candidate(context, "root-temps", target, 7, 1)
    scanner._add_candidate(context, "root-logs", target, 7, 1)
    assert len(context["rows"]) == 1, "FM6 regression: claims set must enforce single ownership"
    assert context["rows"][0]["Category"] == "root-temps"
    assert context["rows"][0]["Risk"] == "SAFE"


# --------------------------------------------------------------------------- #
# FM7 — path semantic validation (data-signature -> CAUTION, never delete)
# --------------------------------------------------------------------------- #

def test_fm7_data_signature_upgrades_to_caution(tmp_path=None):
    """FM7: a static-map cache dir whose sampled content includes a data file
    (e.g. .sqlite) is escalated to CAUTION/quarantine — never SAFE/delete."""
    root = _tmp_path(tmp_path)
    fake = root / "fake"
    fake.mkdir()
    local = fake / "Local"
    cache = local / "Google" / "Chrome" / "User Data" / "Default" / "Cache"
    cache.mkdir(parents=True)
    (cache / "data.sqlite").write_text("sqlite db", encoding="utf-8")
    out_dir = root / "out"
    old_is_windows = scanner.IS_WINDOWS
    scanner.IS_WINDOWS = True
    try:
        with _mock_running(scanner, []):
            result = scanner.scan(
                "X:", root_path=fake, out_dir=out_dir, local_app_data=local,
                categories=["browser-caches"], is_user_drive=True,
            )
    finally:
        scanner.IS_WINDOWS = old_is_windows

    rows = [row for row in result["rows"] if row["Category"] == "browser-caches"]
    assert len(rows) == 1, f"FM7: expected one browser-caches row, got {rows}"
    assert Path(rows[0]["Path"]) == cache
    assert rows[0]["Risk"] == "CAUTION", "FM7 regression: data-like cache dir must be CAUTION"
    assert rows[0]["Action"] == "quarantine", "FM7 regression: CAUTION maps to quarantine, never delete"
    assert rows[0].get("Reason") == "路径语义可疑，请人工确认"


def test_fm7_clean_cache_stays_safe(tmp_path=None):
    """FM7: a cache dir of small cache files (and .json metadata) passes the
    signature check and KEEPS its SAFE classification (no false positive)."""
    root = _tmp_path(tmp_path)
    fake = root / "fake"
    fake.mkdir()
    local = fake / "Local"
    cache = local / "Google" / "Chrome" / "User Data" / "Default" / "Cache"
    cache.mkdir(parents=True)
    for name in ("f_000001", "f_000002", "meta.json"):
        (cache / name).write_text("x", encoding="utf-8")
    out_dir = root / "out"
    old_is_windows = scanner.IS_WINDOWS
    scanner.IS_WINDOWS = True
    try:
        with _mock_running(scanner, []):
            result = scanner.scan(
                "X:", root_path=fake, out_dir=out_dir, local_app_data=local,
                categories=["browser-caches"], is_user_drive=True,
            )
    finally:
        scanner.IS_WINDOWS = old_is_windows

    rows = [row for row in result["rows"] if row["Category"] == "browser-caches"]
    assert len(rows) == 1, f"FM7: expected one browser-caches row, got {rows}"
    assert Path(rows[0]["Path"]) == cache
    assert rows[0]["Risk"] == "SAFE", "FM7 regression: clean cache dir must keep SAFE"
    assert rows[0]["Action"] == "delete"
    assert "Reason" not in rows[0]


def test_fm7_signature_helper_identifies_data_suffixes(tmp_path=None):
    """FM7 helper: every suffix in the data-file set triggers the signature;
    the explicitly non-data suffixes (.json/.xml/.ini/.conf/.bak) do not."""
    root = _tmp_path(tmp_path)
    directory = root / "cache"
    directory.mkdir()
    data_files = ("x.db", "y.sqlite", "z.sqlite3", "a.sqlitedb", "b.db-shm", "c.db-wal", "d.index", "e.dat")
    for name in data_files:
        (directory / name).write_text("db", encoding="utf-8")
    assert scanner._content_signature_data_like(directory) is True
    for name in data_files:
        (directory / name).unlink()
    for name in ("meta.json", "conf.xml", "app.ini", "settings.conf", "backup.bak", "f_0001"):
        (directory / name).write_text("x", encoding="utf-8")
    assert scanner._content_signature_data_like(directory) is False


def test_fm7_caution_row_is_quarantined_not_clean_contents(tmp_path=None):
    """FM7 x FM5: a data-like cache dir escalated to CAUTION must be MOVED to
    quarantine — the row's Action wins over the category's clean_contents
    default — so its files are never deleted in place. Pre-fix, the
    category->clean_contents dispatch fired first and deleted the files."""
    root = _tmp_path(tmp_path)
    cache = root / "cache"
    cache.mkdir()
    payload = _aged_file(cache / "data.sqlite", 10)
    quarantine_dir = root / "quarantine"
    candidates = root / "candidates.csv"
    _write_candidates(
        candidates,
        [{"Category": "browser-caches", "Risk": "CAUTION", "Path": str(cache),
          "SizeBytes": 10, "FileCount": 1, "Action": "quarantine"}],
    )

    with _mock_running(cleaner, []):
        result, _ = _run_with_stdout(lambda: cleaner.clean(
            "X:", volume=_volume(root), candidates_csv=candidates, yes=True,
            quarantine_dir=quarantine_dir, allow_posix_unlink=True,
            is_user_drive=False, is_system_drive=False,
        ))

    assert not cache.exists(), "FM7 x FM5: data-like cache dir must not be cleaned in place"
    assert not payload.exists()
    moved = quarantine_dir / "cache"
    assert moved.exists(), "FM7 x FM5: the whole cache dir must be moved to quarantine"
    assert (moved / "data.sqlite").read_text(encoding="utf-8") == "payload"
    assert any(item["Disposition"] == "QUARANTINED" for item in result["dispositions"])


def test_fm7_mixed_safe_and_caution_rows_in_category(tmp_path=None):
    """FM7 x FM5: one category may mix plain SAFE rows (clean_contents) and
    FM7-escalated CAUTION rows (quarantine) in the same run; grouping must not
    reject the mix and each row honors its own action. Pre-fix the CAUTION row
    was rejected as malformed and the whole clean aborted."""
    root = _tmp_path(tmp_path)
    safe_cache = root / "safe-cache"
    safe_cache.mkdir()
    safe_file = _aged_file(safe_cache / "f.bin", 10)
    caution_cache = root / "caution-cache"
    caution_cache.mkdir()
    _aged_file(caution_cache / "data.sqlite", 10)
    quarantine_dir = root / "quarantine"
    candidates = root / "candidates.csv"
    _write_candidates(
        candidates,
        [
            {"Category": "browser-caches", "Risk": "SAFE", "Path": str(safe_cache),
             "SizeBytes": 10, "FileCount": 1, "Action": "delete"},
            {"Category": "browser-caches", "Risk": "CAUTION", "Path": str(caution_cache),
             "SizeBytes": 10, "FileCount": 1, "Action": "quarantine"},
        ],
    )

    with _mock_running(cleaner, []):
        result, _ = _run_with_stdout(lambda: cleaner.clean(
            "X:", volume=_volume(root), candidates_csv=candidates, yes=True,
            quarantine_dir=quarantine_dir, allow_posix_unlink=True,
            is_user_drive=False, is_system_drive=False,
        ))

    by_path = {item["Path"]: item["Disposition"] for item in result["dispositions"]}
    assert by_path[str(safe_cache)] == "OK", "SAFE cache row must still clean_contents"
    assert not safe_file.exists()
    assert safe_cache.exists(), "clean_contents keeps the directory"
    assert by_path[str(caution_cache)] == "QUARANTINED", "CAUTION row must quarantine"
    assert not caution_cache.exists()
    assert (quarantine_dir / "caution-cache").exists()


# --------------------------------------------------------------------------- #
# FM13 — dry-run per-file preview + confirmation upgrade
# --------------------------------------------------------------------------- #

def test_fm13_dry_run_per_file_preview(tmp_path=None):
    """FM13: --dry-run prints a per-file preview line for every candidate
    containing path | size | category | action, and deletes nothing. Pre-fix
    the preview mixed only category+size+path and skipped the action field."""
    root = _tmp_path(tmp_path)
    cache = root / "cache"
    cache.mkdir()
    files = [_aged_file(cache / name, 10) for name in ("f1.bin", "f2.bin")]
    candidates = root / "candidates.csv"
    _write_candidates(
        candidates,
        [{"Category": "browser-caches", "Risk": "SAFE", "Path": str(cache),
          "SizeBytes": 2, "FileCount": 2, "Action": "delete"}],
    )

    with _mock_running(cleaner, []):
        result, out = _run_with_stdout(lambda: cleaner.clean(
            "X:", volume=_volume(root), candidates_csv=candidates, yes=True,
            quarantine_dir=root / "quarantine", dry_run=True,
            is_user_drive=False, is_system_drive=False,
        ))

    for file_path in files:
        line = next(line for line in out.splitlines() if str(file_path) in line)
        assert "DRY-RUN:" in line
        # FM13: the line leads with the path, then size | category | action |
        # reason. Pre-fix the category led the line and the action trailed in
        # parens — this order assertion fails on the old format.
        assert line.startswith(f"DRY-RUN: {file_path} |"), (
            "FM13: preview line must lead with the path, got: " + line
        )
        assert "bytes" in line, "FM13: preview line must contain the size"
        assert "browser-caches" in line, "FM13: preview line must contain the category"
        assert "clean_contents" in line, "FM13: preview line must contain the action"
        assert "| keeps directory" in line, "FM13: preview line must carry the reason field"
    assert all(file_path.exists() for file_path in files), "FM13: dry-run must not delete"
    assert all(item["Disposition"] == "DRY_RUN" for item in result["dispositions"])
    assert not (root / "cleanup-errors.csv").exists(), "FM13: dry-run must not mutate run outputs"


def test_fm13_confirmation_shows_counts_and_patterns(tmp_path=None):
    """FM13: the interactive per-category confirmation shows '将删除 N 个文件 /
    X MB' plus the first rows before asking. Pre-fix it printed only the bare
    SUMMARY line with no file count."""
    root = _tmp_path(tmp_path)
    cache = root / "cache"
    cache.mkdir()
    _aged_file(cache / "f1.bin", 10)
    _aged_file(cache / "f2.bin", 10)
    candidates = root / "candidates.csv"
    _write_candidates(
        candidates,
        [{"Category": "browser-caches", "Risk": "SAFE", "Path": str(cache),
          "SizeBytes": 2, "FileCount": 2, "Action": "delete"}],
    )
    answers = ["y"]

    def fake_input(prompt):
        answers.append(prompt)
        return answers.pop(0)

    with _mock_running(cleaner, []):
        _, out = _run_with_stdout(lambda: cleaner.clean(
            "X:", volume=_volume(root), candidates_csv=candidates, yes=False,
            quarantine_dir=root / "quarantine", input_func=fake_input,
            is_user_drive=False, is_system_drive=False,
        ))

    assert "将删除 2 个文件" in out, "FM13: confirmation must show the file count"
    assert "MB" in out, "FM13: confirmation must show the size"
    assert "cache" in out, "FM13: confirmation must show the first rows/patterns"


# --------------------------------------------------------------------------- #
# FM12 / Todo 12 — real-app clean integration (scanner -> cleaner subprocess)
# --------------------------------------------------------------------------- #

def _run_api_subprocess(code: str, cwd: Path, extra_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run one scanner/cleaner/marker script as a real subprocess (like
    test_integration.py) with the workspace on PYTHONPATH."""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(cwd) + os.pathsep + environment.get("PYTHONPATH", "")
    environment.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_fm12_real_app_clean_integration(tmp_path=None):
    """FM12: end-to-end fake-app clean — scanner -> cleaner (clean_contents).

    Builds a small fake "app" (Ubisoft Game Launcher) with a Cache dir (5 files)
    + a data file, runs the REAL scanner.py and cleaner.py as subprocesses
    against it, then verifies (a) cache files gone, (b) the Cache dir still
    exists, (c) the app's data file untouched, (d) a "still starts" marker
    script reads the data file and exits 0. Uses only a tmp_path fake app —
    no real apps, no elevated cleanup.
    """
    root = _tmp_path(tmp_path)
    fake_app = root / "fake_app"
    cache = fake_app / "Ubisoft Game Launcher" / "cache"
    cache.mkdir(parents=True)
    cache_files = [_aged_file(cache / f"f_{index:06d}", 10) for index in range(5)]
    data_file = fake_app / "data.txt"
    data_content = "app-user-data-0123456789"
    data_file.write_text(data_content, encoding="utf-8")
    marker = fake_app / "start_marker.py"
    marker.write_text(
        "import pathlib, sys; "
        f"sys.exit(0 if pathlib.Path({str(data_file)!r}).read_text(encoding='utf-8') == {data_content!r} else 1)\n",
        encoding="utf-8",
    )
    out_dir = root / "out"
    quarantine_dir = root / "quarantine"
    volume = {"Root": str(fake_app), "FreeBytes": 1000, "TotalBytes": 5000}
    workspace = Path(__file__).resolve().parents[1]

    # -- scanner subprocess: app-caches -> the Cache dir becomes one SAFE row.
    scanner_code = "\n".join([
        "from pathlib import Path",
        "import runpy, sys",
        "from unittest import mock",
        "import psutil",
        "from scripts.lib import core, platform",
        "platform.IS_WINDOWS = True",
        "core.IS_WINDOWS = (sys.platform == 'win32')",
        f"platform.resolve_fixed_drive = lambda drive: {volume!r}",
        f"sys.argv = ['scanner.py','-Drive','X:','-OutDir',{str(out_dir)!r},'-Categories','app-caches']",
        "with mock.patch.object(psutil, 'process_iter', return_value=[]):",
        f"    runpy.run_path({str(workspace / 'scripts' / 'scanner.py')!r}, run_name='__main__')",
    ])
    scan_result = _run_api_subprocess(scanner_code, workspace, {})
    assert scan_result.returncode == 0, scan_result.stderr
    assert "SCAN COMPLETE:" in scan_result.stdout
    run_dir = max(out_dir.glob("*-*"), key=lambda path: path.stat().st_mtime_ns)
    candidates = run_dir / "candidates.csv"
    assert candidates.is_file()
    candidate_text = candidates.read_text(encoding="utf-8")
    assert "app-caches|SAFE|" in candidate_text
    assert str(cache) in candidate_text

    # -- cleaner subprocess: clean_contents frees the 5 files, keeps the dir.
    cleaner_code = "\n".join([
        "from pathlib import Path",
        "import runpy, sys",
        "from unittest import mock",
        "import psutil",
        "from scripts.lib import core, platform",
        "platform.IS_WINDOWS = True",
        "core.IS_WINDOWS = (sys.platform == 'win32')",
        f"platform.resolve_fixed_drive = lambda drive: {volume!r}",
        f"sys.argv = ['cleaner.py','-Drive','X:','-CandidatesCsv',{str(candidates)!r},'-QuarantineDir',{str(quarantine_dir)!r},'-Yes']",
        "with mock.patch.object(psutil, 'process_iter', return_value=[]):",
        f"    runpy.run_path({str(workspace / 'scripts' / 'cleaner.py')!r}, run_name='__main__')",
    ])
    clean_result = _run_api_subprocess(cleaner_code, workspace, {})
    assert clean_result.returncode == 0, clean_result.stderr
    assert "CLEAN COMPLETE:" in clean_result.stdout
    cleanup_text = (run_dir / "cleanup-errors.csv").read_text(encoding="utf-8")
    assert cleanup_text.count("|OK\n") == 5, "FM12: every cache file must record OK"

    # (a) cache files gone, (b) Cache dir still exists, (c) data file intact.
    assert all(not file.exists() for file in cache_files), "FM12: cache files must be freed"
    assert cache.is_dir(), "FM12: clean_contents must keep the Cache dir"
    assert data_file.read_text(encoding="utf-8") == data_content, "FM12: app data must be intact"

    # (d) app still starts: the marker reads the data file and exits 0.
    marker_result = _run_api_subprocess(
        f"import runpy; runpy.run_path({str(marker)!r}, run_name='__main__')", workspace, {}
    )
    assert marker_result.returncode == 0, f"FM12: app no longer starts: {marker_result.stderr}"
    assert marker_result.stdout == ""


# --------------------------------------------------------------------------- #
# FM14 — root-logs system-owner exclusion (v2.1.1, two-tier attribute + ACL)
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(not IS_WINDOWS, reason="ctypes.windll is Windows-only")
def test_fm14_root_logs_system_owned_skipped(tmp_path=None):
    """FM14: a root-level .log owned by the OS is NOT a root-logs candidate.

    Pre-fix ``_scan_root_logs`` had no owner check, so ``C:\\DumpStack.log``
    (SYSTEM-owned) was flagged SAFE/delete and only survived at clean time via
    the lock probe. Post-fix the ``_is_system_owned`` gate skips it; a user
    .log is still a candidate (control).

    Also exercises the ``_is_system_owned`` helper's Windows two-tier check and
    its no-over-exclusion edge (Metis Finding 10): a Hidden-only (0x2) file
    whose owner ACL is readable and owned by a normal user is NOT system-owned
    — a user's own hidden .log must still be flagged.
    """
    root = _tmp_path(tmp_path)
    fake = root / "fake"
    fake.mkdir()
    user_log = _aged_file(fake / "rubbish_user_control.log", 10)

    old_is_windows = scanner.IS_WINDOWS
    scanner.IS_WINDOWS = False  # _scan_root_logs only needs context["root"] here
    try:
        context = {
            "root": fake,
            "cutoff": datetime.now() - timedelta(days=7),
            "rows": [],
            "checkpoint": {"fileCounter": 0, "totalBytesSoFar": 0, "lastPath": "", "completedCategories": []},
            "resume_state": None,
        }
        # System-owned -> skipped entirely.
        with mock.patch.object(scanner, "_is_system_owned", return_value=True):
            scanner._scan_root_logs(context)
        assert context["rows"] == [], (
            f"FM14: system-owned root log must NOT be a candidate, got {context['rows']}"
        )
        # User-owned -> one candidate.
        with mock.patch.object(scanner, "_is_system_owned", return_value=False):
            scanner._scan_root_logs(context)
        rows = [row for row in context["rows"] if row["Path"] == str(user_log)]
        assert len(rows) == 1, f"FM14: user-owned root log must stay a candidate, got {rows}"
        assert rows[0]["Category"] == "root-logs"
        assert rows[0]["Risk"] == "SAFE"
    finally:
        scanner.IS_WINDOWS = old_is_windows

    # -- Helper tier checks (all use a fake root .log + mocked stat/ACL).
    target = fake / "DumpStack.log"
    target.write_text("payload", encoding="utf-8")

    # Tier 1 — FILE_ATTRIBUTE_SYSTEM (0x4) -> True without any ACL query.
    system_stat = SimpleNamespace(st_file_attributes=0x4, st_uid=0)
    with mock.patch.object(scanner.os, "stat", return_value=system_stat):
        assert scanner._is_system_owned(target) is True

    # Tier 2 primary signal — owner ACL read fails with ERROR_ACCESS_DENIED
    # (5, the live C:\\DumpStack.log case where Attributes=0x20) -> True.
    scanner.IS_WINDOWS = True
    try:
        archive_stat = SimpleNamespace(st_file_attributes=0x20, st_uid=0)
        fake_denied = mock.MagicMock()
        fake_denied.GetNamedSecurityInfoW.return_value = 5  # ERROR_ACCESS_DENIED
        with mock.patch.object(scanner.os, "stat", return_value=archive_stat), mock.patch.object(
            ctypes.windll, "advapi32", fake_denied
        ):
            assert scanner._is_system_owned(target) is True
    finally:
        scanner.IS_WINDOWS = old_is_windows

    # Metis Finding 10 edge — Hidden-only (0x2, no System flag) + a readable
    # ACL owned by a normal user SID (not S-1-5-18) -> False, never over-excluded.
    scanner.IS_WINDOWS = True
    try:
        hidden_stat = SimpleNamespace(st_file_attributes=0x2, st_uid=0)
        fake_acl = mock.MagicMock()
        fake_acl.GetNamedSecurityInfoW.return_value = 0  # owner ACL readable
        # Successful SID render that writes no SYSTEM SID text -> plain user.
        fake_acl.ConvertSidToStringSidW.return_value = True
        with mock.patch.object(scanner.os, "stat", return_value=hidden_stat), mock.patch.object(
            ctypes.windll, "advapi32", fake_acl
        ):
            assert scanner._is_system_owned(target) is False
    finally:
        scanner.IS_WINDOWS = old_is_windows

    # POSIX branch — uid 0 is system-owned, a regular uid is not.
    scanner.IS_WINDOWS = False
    try:
        root_stat = SimpleNamespace(st_uid=0)
        with mock.patch.object(scanner.os, "stat", return_value=root_stat):
            assert scanner._is_system_owned(target) is True
        user_stat = SimpleNamespace(st_uid=1000)
        with mock.patch.object(scanner.os, "stat", return_value=user_stat):
            assert scanner._is_system_owned(target) is False
    finally:
        scanner.IS_WINDOWS = old_is_windows


# --------------------------------------------------------------------------- #
# FM15 — user-temp installer/uninstaller exemption (v2.1.1, whole-word)
# --------------------------------------------------------------------------- #

def test_fm15_user_temp_installer_exempt(tmp_path=None):
    """FM15: installer/uninstaller artifacts in user Temp are exempt past the
    7-day gate; generic junk stays a candidate (control).

    ``unins000.exe`` (exact .exe suffix) and ``setup-x64.exe`` (exact .exe
    suffix) are exempt; ``install.log`` is exempt BY DESIGN (whole-word
    ``install`` in the casefolded name — Oracle Finding A resolution); only
    ``wctCDFA.tmp`` remains a candidate. Pre-fix only the 7-day gate applied,
    so all four aged files became candidates."""
    root = _tmp_path(tmp_path)
    local_app_data = root / "fake" / "Local"
    temp_dir = local_app_data / "Temp"
    temp_dir.mkdir(parents=True)
    unins = _aged_file(temp_dir / "unins000.exe", 10)
    setup = _aged_file(temp_dir / "setup-x64.exe", 10)
    install_log = _aged_file(temp_dir / "install.log", 10)
    junk = _aged_file(temp_dir / "wctCDFA.tmp", 10)

    old_is_windows = scanner.IS_WINDOWS
    scanner.IS_WINDOWS = True  # user-temp scans local_app_data / Temp on Windows
    try:
        context = {
            "local_app_data": local_app_data,
            "cutoff": datetime.now() - timedelta(days=7),
            "rows": [],
            "checkpoint": {"fileCounter": 0, "totalBytesSoFar": 0, "lastPath": "", "completedCategories": []},
            "resume_state": None,
        }
        scanner._scan_user_temp(context)
    finally:
        scanner.IS_WINDOWS = old_is_windows

    candidate_paths = {row["Path"] for row in context["rows"]}
    assert str(junk) in candidate_paths, "FM15: generic .tmp must stay a candidate"
    assert str(unins) not in candidate_paths, "FM15: unins000.exe must be exempt"
    assert str(setup) not in candidate_paths, "FM15: setup-x64.exe must be exempt"
    assert str(install_log) not in candidate_paths, (
        "FM15: install.log must be exempt by design (whole-word 'install')"
    )
    assert len(candidate_paths) == 1, (
        f"FM15: expected only wctCDFA.tmp, got {sorted(candidate_paths)}"
    )
