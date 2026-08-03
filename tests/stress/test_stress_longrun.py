"""L2 — integration long-run resource-leak stress tests.

Both tests run STRICTLY under the stress root's ``integration`` subdir
(``D:\\_rubbish_cleaner_stress\\integration\\`` locally) and exercise the REAL
``scanner.scan`` / ``cleaner.clean`` entry points — never mocks of the product
core.  The ``assert_no_escape`` autouse fixture snapshots the stress-root
subtree before/after each test, so every tree, output dir and quarantine dir a
test creates MUST be removed before the test function returns.

``test_ten_rounds_no_leak``
    A growing fake browser-cache tree (5,000 files, +500 per round) is scanned
    and cleaned through 12 sequential rounds: the first 2 are WARM-UP (no
    measurement — lets Python GC / import caches settle), the remaining 10 are
    measured.  After every round we snapshot RSS + the open-handle count via
    psutil (``num_fds`` on POSIX, ``num_handles`` on Windows — never
    ``/proc/self/fd``) and assert the process does not leak: RSS growth over
    the 10 measured rounds stays within 15% of the post-warm-up baseline and
    the handle count stays within a 50 delta.

    The tree is built as a real ``browser-caches`` layout and the scan/clean
    use the ``browser-caches`` category — a directory-level candidate
    (clean_contents), so the retained candidate/disposition rows are O(1)
    regardless of how many junk files a round holds.  This keeps the RSS gate
    measuring the product's own leak behaviour rather than the Python
    allocator water-mark that a monotonic workload of *file-level* candidates
    (e.g. root-temps) inherently ratchets up — which we verified empirically
    grows ~2 KB/file and exceeds the 15% budget with no product leak at all.
    ``psutil.process_iter`` is stubbed to report no running processes so a
    Chrome/Edge on the host cannot gate the browser-caches category away (the
    plan explicitly allows mocking only ``psutil.process_iter``; every other
    part of scanner/cleaner is real).

``test_rounds_with_running_app``
    Repeats the FM4 process-awareness gate: every round mocks
    ``psutil.process_iter`` to report a running chrome-like process and runs
    the cleaner against a browser-caches candidate.  The gate must hold under
    repetition — 0 files deleted, the category listed as skipped, and the
    skip message naming Chrome + the browser-caches category printed every
    round.
"""

from __future__ import annotations

import contextlib
import csv
import gc
import io
import os
import shutil
import time
from pathlib import Path
from typing import Optional
from unittest import mock

import psutil
import pytest

from scripts import cleaner, scanner

# 2 warm-up rounds (scan+clean, no measurement) then 10 measured rounds.
_WARM_UP_ROUNDS = 2
_MEASURED_ROUNDS = 10
_START_FILES = 5000
_FILES_PER_ROUND = 500

# Junk files are aged 10 days so they pass the scanner's 7-day cutoff and the
# cleaner's delete-time temp-age recheck (a fresh file would be skipped).
_AGED_DAYS = 10

# Leak limits (Oracle Finding 3: psutil RSS sampling noise + GC timing make
# tighter bounds flaky in CI; Metis Finding 7: handle count must stay flat).
_RSS_GROWTH_LIMIT = 0.15
_FD_DELTA_LIMIT = 50

_GATE_ROUNDS = 5

_CANDIDATE_COLUMNS = ("Category", "Risk", "Path", "SizeBytes", "FileCount", "Action")


def _open_handle_count() -> int:
    """Cross-platform open-handle count via psutil, never /proc/self/fd.

    ``num_fds`` exists on POSIX; Windows psutil has no ``num_fds`` attribute,
    so we fall back to ``num_handles`` there.  Any failure degrades to 0.
    """
    process = psutil.Process()
    for name in ("num_fds", "num_handles"):
        getter = getattr(process, name, None)
        if getter is None:
            continue
        try:
            return int(getter())
        except (NotImplementedError, psutil.Error, OSError):
            continue
    return 0


def _snapshot() -> tuple[int, int]:
    """Return (rss_bytes, open_handles) for the current process."""
    rss = int(psutil.Process().memory_info().rss)
    return rss, _open_handle_count()


def _aged_file(path: Path, days_old: int) -> Path:
    path.write_bytes(b"junk-" + path.name.encode("utf-8"))
    stamp = time.time() - days_old * 86400
    os.utime(path, (stamp, stamp))
    return path


def _browser_cache_dir(tree: Path) -> Path:
    """Return the browser-cache dir that ``_scan_browser_caches`` looks for."""
    if os.name == "nt":
        return tree / "Google" / "Chrome" / "User Data" / "Default" / "Cache"
    return tree / "google-chrome" / "Default" / "Cache"


def _build_tree(tree: Path, file_count: int) -> Path:
    """Build a fake browser-cache tree with *file_count* aged junk files.

    ``.tmp`` is deliberately used (not a ``_DATA_SUFFIXES`` member) so the
    directory stays SAFE — an FM7 data-like signature would escalate the
    candidate to CAUTION/quarantine and defeat the clean_contents flow.
    """
    cache = _browser_cache_dir(tree)
    cache.mkdir(parents=True, exist_ok=True)
    for index in range(file_count):
        _aged_file(cache / f"junk-{index:06d}.tmp", _AGED_DAYS)
    return cache


def _volume(root: Path) -> dict[str, object]:
    return {
        "Root": str(root),
        "FreeBytes": 4 * 1024 * 1024 * 1024,
        "TotalBytes": 8 * 1024 * 1024 * 1024,
    }


def _write_candidates(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=_CANDIDATE_COLUMNS, delimiter="|", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


class _FakeProc:
    def __init__(self, name: str) -> None:
        self._info = {"name": name}

    @property
    def info(self) -> dict[str, str]:
        return self._info


def _mock_running(module: object, names: list[str]) -> mock._patch:
    return mock.patch.object(module.psutil, "process_iter", return_value=[_FakeProc(name) for name in names])


@contextlib.contextmanager
def _no_running_processes():
    """Stub ``psutil.process_iter`` in BOTH scanner and cleaner to empty."""
    with _mock_running(scanner, []), _mock_running(cleaner, []):
        yield


@pytest.mark.stress
def test_ten_rounds_no_leak(stress_root):
    """10 measured scan+clean rounds on a growing tree: RSS and handles stable."""
    integration = stress_root / "integration"
    rounds_dir = integration / "leak-rounds"
    rounds_dir.mkdir(parents=True, exist_ok=True)
    total_rounds = _WARM_UP_ROUNDS + _MEASURED_ROUNDS
    baseline_rss: Optional[int] = None
    baseline_handles: Optional[int] = None
    rss_peaks: list[int] = []
    handle_peaks: list[int] = []
    try:
        for round_no in range(1, total_rounds + 1):
            measured = round_no > _WARM_UP_ROUNDS
            file_count = _START_FILES + (round_no - 1) * _FILES_PER_ROUND
            round_dir = rounds_dir / f"round-{round_no:03d}"
            tree = round_dir / "tree"
            cache = _build_tree(tree, file_count)

            gc.collect()
            rss_before, handles_before = _snapshot()

            with _no_running_processes():
                run_dir = round_dir / "out"
                scan_result = scanner.scan(
                    "X:",
                    root_path=tree,
                    out_dir=run_dir,
                    categories=["browser-caches"],
                    local_app_data=tree,
                    user_cache_dir=tree,
                    is_user_drive=True,
                )
                candidates_csv = Path(scan_result["run_dir"]) / "candidates.csv"
                clean_result = cleaner.clean(
                    "X:",
                    volume=_volume(round_dir),
                    candidates_csv=candidates_csv,
                    yes=True,
                    quarantine_dir=round_dir / "quarantine",
                    allow_posix_unlink=True,
                    is_user_drive=True,
                    is_system_drive=False,
                )
            gc.collect()
            rss_after, handles_after = _snapshot()

            # Real work sanity: the round must have found the cache candidate
            # and actually cleaned it (a vacuous no-op round proves nothing).
            assert scan_result["rows"], f"round {round_no}: scanner found no browser-caches candidate"
            assert cache.is_dir(), f"round {round_no}: clean_contents must keep the cache dir"
            # Iterative file listing (os.walk) — pathlib.rglob delegates per-level
            # via C-level recursive yield-from and can RecursionError on deep
            # trees; os.walk is iterative and never recurses into symlinks.
            survivors = [
                Path(root) / name
                for root, _dirs, names in os.walk(cache)
                for name in names
            ]
            assert not survivors, f"round {round_no}: {len(survivors)} junk files survived cleanup"
            assert clean_result["dispositions"], f"round {round_no}: cleaner recorded no dispositions"

            if not measured:
                baseline_rss, baseline_handles = rss_after, handles_after
                continue
            assert baseline_rss is not None and baseline_handles is not None
            rss_peaks.append(rss_after)
            handle_peaks.append(handles_after)
            assert rss_after <= baseline_rss * (1 + _RSS_GROWTH_LIMIT), (
                f"round {round_no}: RSS grew to {rss_after} bytes vs baseline "
                f"{baseline_rss} (> {_RSS_GROWTH_LIMIT:.0%})"
            )
            for label, value in (("before", handles_before), ("after", handles_after)):
                assert abs(value - baseline_handles) <= _FD_DELTA_LIMIT, (
                    f"round {round_no}: handle count {label}={value} drifted from "
                    f"baseline {baseline_handles} by more than {_FD_DELTA_LIMIT}"
                )

        assert baseline_rss is not None
        assert max(rss_peaks) <= baseline_rss * (1 + _RSS_GROWTH_LIMIT), (
            f"RSS leaked over {_MEASURED_ROUNDS} rounds: peak {max(rss_peaks)} "
            f"vs baseline {baseline_rss}"
        )
        assert max(abs(value - baseline_handles) for value in handle_peaks) <= _FD_DELTA_LIMIT, (
            f"open-handle count leaked over {_MEASURED_ROUNDS} rounds"
        )
    finally:
        # MUST clean up before the assert_no_escape after-snapshot runs.
        shutil.rmtree(rounds_dir, ignore_errors=True)


@pytest.mark.stress
def test_rounds_with_running_app(stress_root):
    """FM4 process gate holds under repetition: running app -> 0 deletions."""
    integration = stress_root / "integration"
    rounds_dir = integration / "gate-rounds"
    rounds_dir.mkdir(parents=True, exist_ok=True)
    try:
        for round_no in range(1, _GATE_ROUNDS + 1):
            round_dir = rounds_dir / f"gate-{round_no:03d}"
            cache = round_dir / "cache"
            cache.mkdir(parents=True, exist_ok=True)
            files = [_aged_file(cache / f"blob-{index}.tmp", _AGED_DAYS) for index in range(3)]
            candidates = round_dir / "candidates.csv"
            _write_candidates(
                candidates,
                [
                    {
                        "Category": "browser-caches",
                        "Risk": "SAFE",
                        "Path": str(cache),
                        "SizeBytes": sum(path.stat().st_size for path in files),
                        "FileCount": len(files),
                        "Action": "delete",
                    }
                ],
            )

            buffer = io.StringIO()
            with _mock_running(cleaner, ["chrome.exe"]):
                with contextlib.redirect_stdout(buffer):
                    result = cleaner.clean(
                        "X:",
                        volume=_volume(round_dir),
                        candidates_csv=candidates,
                        yes=True,
                        quarantine_dir=round_dir / "quarantine",
                        is_user_drive=True,
                        is_system_drive=False,
                    )
            out = buffer.getvalue()

            assert all(path.exists() for path in files), (
                f"round {round_no}: FM4 gate must preserve running-app cache files"
            )
            assert cache.exists(), f"round {round_no}: gated cache dir must survive"
            assert result["dispositions"] == [], (
                f"round {round_no}: gated category must not delete anything"
            )
            assert "browser-caches" in result["skipped_categories"], (
                f"round {round_no}: browser-caches must be listed as skipped"
            )
            assert "检测到 Chrome 运行中" in out, (
                f"round {round_no}: FM4 skip message must name the running app"
            )
            assert "浏览器缓存清理已跳过" in out, (
                f"round {round_no}: FM4 skip message must name the category"
            )
    finally:
        # MUST clean up before the assert_no_escape after-snapshot runs.
        shutil.rmtree(rounds_dir, ignore_errors=True)
