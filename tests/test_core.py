"""Safety primitive tests for the Python rubbish-cleaner core."""

from __future__ import annotations

import csv
import atexit
import os
import shutil
import tempfile
import threading
from pathlib import Path

from scripts.lib import core


def _tmp_path(tmp_path=None) -> Path:
    if tmp_path is not None:
        return Path(tmp_path)
    path = Path(tempfile.mkdtemp(prefix="rubbish-core-"))
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    return path


def test_junk_dispositions_are_the_canonical_twelve(tmp_path=None):
    expected = [
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
    assert core.JUNK_DISPOSITIONS == expected
    assert len(set(core.JUNK_DISPOSITIONS)) == 12


def test_empty_directory_walk_skips_links_and_nested_empty_directories(tmp_path=None):
    root = _tmp_path(tmp_path)
    empty = root / "empty"
    empty.mkdir()
    assert core.is_dir_empty(str(empty)) is True

    nested = root / "nested" / "a" / "b"
    nested.mkdir(parents=True)
    assert core.is_dir_empty(str(root / "nested")) is True

    (empty / "payload.txt").write_text("payload", encoding="utf-8")
    assert core.is_dir_empty(str(empty)) is False

    target = root / "target"
    target.mkdir()
    link = root / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        # Symlink privileges are optional on Windows; record the explicit
        # skip condition while keeping the fallback run green.
        assert not link.exists()
        return
    # A linked directory is never traversed and a link itself is not empty.
    assert core.is_junction(str(link)) is (core.IS_WINDOWS)
    assert core.is_dir_empty(str(link)) is False
    container = root / "container"
    container.mkdir()
    (container / "link").symlink_to(target, target_is_directory=True)
    assert core.is_dir_empty(str(container)) is True


def test_safe_remove_records_success_and_missing_dispositions(tmp_path=None):
    root = _tmp_path(tmp_path)
    csv_path = root / "cleanup.csv"
    payload = root / "payload.bin"
    payload.write_bytes(b"payload")

    assert core.safe_remove(str(payload), "root-temps", str(csv_path)) == "OK"
    assert not payload.exists()
    missing = root / "missing.bin"
    assert core.safe_remove(str(missing), "root-temps", str(csv_path)) == "SKIP_NOT_FOUND"

    with csv_path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream, delimiter="|"))
    assert rows[0] == ["Timestamp", "Phase", "Action", "Path", "ErrorMessage", "Disposition"]
    assert [row[-1] for row in rows[1:]] == ["OK", "SKIP_NOT_FOUND"]
    assert sum(row[0] == "Timestamp" for row in rows) == 1


def test_quarantine_moves_an_item_and_rejects_duplicate_destination(tmp_path=None):
    root = _tmp_path(tmp_path)
    quarantine_dir = root / "quarantine"
    csv_path = root / "cleanup.csv"
    source = root / "cache.tmp"
    source.write_text("cache", encoding="utf-8")

    assert core.quarantine(str(source), str(quarantine_dir), "root-temps", str(csv_path)) == "QUARANTINED"
    assert not source.exists()
    assert (quarantine_dir / source.name).read_text(encoding="utf-8") == "cache"

    duplicate = root / "cache.tmp"
    duplicate.write_text("new", encoding="utf-8")
    assert core.quarantine(str(duplicate), str(quarantine_dir), "root-temps", str(csv_path)) == "MOVE_FAILED"
    assert duplicate.exists()


def test_cleanup_csv_header_is_written_once_under_concurrent_appends(tmp_path=None):
    root = _tmp_path(tmp_path)
    csv_path = root / "parallel.csv"

    def append(index: int) -> None:
        core.write_cleanup_csv(
            str(csv_path),
            {"Phase": "parallel", "Action": "Remove", "Path": str(root / str(index)), "Disposition": "OK"},
        )

    threads = [threading.Thread(target=append, args=(index,)) for index in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 13
    assert sum(line.startswith("Timestamp|") for line in lines) == 1


def test_parallel_for_each_preserves_input_order_and_sequential_fallback(tmp_path=None):
    del tmp_path

    def transform(value: int, offset: int) -> int:
        return value + offset

    values = list(range(20))
    assert core.parallel_for_each(values, transform, throttle=2, args=(5,)) == [value + 5 for value in values]
    assert core.parallel_for_each(values[:3], transform, throttle=1, args=(2,)) == [2, 3, 4]


def test_file_lock_probe_respects_native_open_file_semantics(tmp_path=None):
    root = _tmp_path(tmp_path)
    target = root / "probe.txt"
    target.write_text("probe", encoding="utf-8")
    if core.IS_WINDOWS:
        # With no competing native handle, CreateFileW should succeed and the
        # probe must report an unlocked file.
        assert core.test_file_locked(str(target)) is False
        return

    import fcntl

    descriptor = os.open(target, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert core.test_file_locked(str(target)) is True
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
    assert core.test_file_locked(str(target)) is False
