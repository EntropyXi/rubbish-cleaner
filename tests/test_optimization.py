"""Checkpoint, platform, scheduling, and multi-drive orchestration tests."""

from __future__ import annotations

import atexit
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from scripts import scanner, schedule
from scripts.lib import core, platform


def _tmp_path(tmp_path=None) -> Path:
    if tmp_path is not None:
        return Path(tmp_path)
    path = Path(tempfile.mkdtemp(prefix="rubbish-optimization-"))
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    return path


def test_parallel_throttle_one_and_two_have_identical_results(tmp_path=None):
    del tmp_path
    values = list(range(10))

    def square(value: int) -> int:
        return value * value

    expected = [value * value for value in values]
    assert core.parallel_for_each(values, square, throttle=1) == expected
    assert core.parallel_for_each(values, square, throttle=2) == expected


def test_checkpoint_resume_filters_only_the_scanned_frontier(tmp_path=None):
    root = _tmp_path(tmp_path)
    first = root / "a.tmp"
    second = root / "b.tmp"
    third = root / "c.tmp"
    for path in (first, second, third):
        path.write_text(path.name, encoding="utf-8")
    state = {"currentCategory": "root-temps", "lastPath": str(second), "drive": "/", "completedCategories": [], "totalBytesSoFar": 0}
    resumed = scanner._resume_files([third, first, second], "root-temps", state)
    assert resumed == [second, third]
    assert scanner._resume_files([first, second], "root-logs", state) == [first, second]


def test_platform_flags_and_temp_documents_helpers_are_consistent(tmp_path=None):
    del tmp_path
    assert platform.IS_WINDOWS == (sys.platform == "win32")
    assert platform.IS_LINUX == (sys.platform == "linux")
    assert platform.IS_MACOS == (sys.platform == "darwin")
    assert Path(platform.get_system_temp_dir()).is_dir()
    assert isinstance(platform.get_user_documents_dir(), Path)
    drives = platform.get_fixed_drives()
    assert drives
    assert all(Path(drive).exists() for drive in drives)
    cache_dir = platform.get_user_cache_dir()
    assert cache_dir is not None
    assert isinstance(cache_dir, (str, Path))
    assert platform.resolve_fixed_drive("definitely-not-a-drive") is None


def test_schedule_register_list_and_unregister_are_gated_to_injected_cron_path(tmp_path=None):
    root = _tmp_path(tmp_path)
    (root / "references" / "policies").mkdir(parents=True)
    (root / "references" / "policies" / "safe.json").write_text(
        json.dumps({"categories": ["root-temps", "root-logs"]}), encoding="utf-8"
    )
    cron = root / "cron"
    assert schedule.main(
        ["register", "--drive", "X:", "--policy", "safe", "--time", "03:04"],
        system="linux",
        repo_root=root,
        cron_path=cron,
        geteuid=lambda: 0,
    ) == 0
    text = cron.read_text(encoding="utf-8")
    assert "# rubbish-cleaner:X:" in text
    assert "4 3 * * * root" in text
    assert schedule.main(
        ["list"], system="linux", repo_root=root, cron_path=cron, geteuid=lambda: 0
    ) == 0
    assert schedule.main(
        ["unregister", "--drive", "X:"],
        system="linux",
        repo_root=root,
        cron_path=cron,
        geteuid=lambda: 0,
    ) == 0
    assert not cron.exists()
    assert schedule.main(
        ["register", "--drive", "X:", "--policy", "missing"],
        system="linux",
        repo_root=root,
        cron_path=cron,
        geteuid=lambda: 0,
    ) == 1

    calls = []

    def forbidden_runner(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    # Windows registration must stop at the administrator gate and must not
    # invoke schtasks when the injected privilege check denies access.
    assert schedule.main(
        ["register", "--drive", "X:", "--policy", "safe"],
        system="win32",
        repo_root=root,
        admin_check=lambda: False,
        runner=forbidden_runner,
    ) == 1
    assert calls == []


def test_scanner_multi_drive_summary_works_sequentially_and_in_parallel(tmp_path=None):
    root = _tmp_path(tmp_path)
    arguments = argparse.Namespace(OutDir=str(root), IncludeElevated=False, Categories=None, Resume=False, Parallel=False, Throttle=2)
    old_drive_id = scanner._drive_id
    old_run = scanner.subprocess.run
    scanner._drive_id = lambda drive: str(drive)

    def fake_run(command, check=False):
        drive = command[command.index("-Drive") + 1]
        run_dir = root / f"{drive}-run"
        run_dir.mkdir(exist_ok=True)
        (run_dir / "candidates.csv").write_text(
            "Category|Risk|Path|SizeBytes|FileCount|Action\nroot-logs|SAFE|x|4|1|delete\n", encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0)

    scanner.subprocess.run = fake_run
    try:
        assert scanner._run_many(["A", "B"], arguments) == 0
        sequential_summary = max(root.glob("multidrive-*/drives.csv"), key=lambda path: path.stat().st_mtime_ns)
        sequential_rows = sequential_summary.read_text(encoding="utf-8").splitlines()
        assert {row.split("|", 1)[0] for row in sequential_rows[1:]} == {"A", "B"}
        assert all(row.split("|")[-2:] == ["1", "4"] for row in sequential_rows[1:])
        arguments.Parallel = True
        assert scanner._run_many(["A", "B"], arguments) == 0
        parallel_summary = max(root.glob("multidrive-*/drives.csv"), key=lambda path: path.stat().st_mtime_ns)
        parallel_rows = parallel_summary.read_text(encoding="utf-8").splitlines()
        assert {row.split("|", 1)[0] for row in parallel_rows[1:]} == {"A", "B"}
        assert all(row.split("|")[-2:] == ["1", "4"] for row in parallel_rows[1:])
    finally:
        scanner._drive_id = old_drive_id
        scanner.subprocess.run = old_run
