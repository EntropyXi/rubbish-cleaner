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
import tempfile
import time
from ctypes import wintypes
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

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
            quarantine_dir=root / "quarantine", is_user_drive=False, is_system_drive=False,
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
            quarantine_dir=root / "quarantine", is_user_drive=False, is_system_drive=False,
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
        quarantine_dir=root / "quarantine", is_user_drive=False, is_system_drive=False,
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
                quarantine_dir=custom, is_user_drive=False, is_system_drive=False,
            ))
    finally:
        cleaner.IS_WINDOWS = old_is_windows

    assert (custom / target.name).exists(), "FM9: explicit --quarantine-dir must win"
    assert not target.exists()
    assert any(item["Disposition"] == "QUARANTINED" for item in result["dispositions"])
    assert result["quarantine_dir"] == os.fspath(custom), (
        "FM9: the resolved quarantine_dir surfaced in the result must be the override"
    )
