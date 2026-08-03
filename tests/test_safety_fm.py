"""FM1-FM3 safety regression tests (POSIX default-skip, quarantine lock-probe,
candidates-driven elevated batch with forfiles age gate).

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
"""

from __future__ import annotations

import atexit
import ctypes
import os
import shutil
import tempfile
from ctypes import wintypes
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import psutil

from scripts import cleaner
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
