"""Read-only report reconciliation and eight-section summary tests."""

from __future__ import annotations

import atexit
import csv
import shutil
import tempfile
from pathlib import Path

from scripts import report


def _tmp_path(tmp_path=None) -> Path:
    if tmp_path is not None:
        return Path(tmp_path)
    path = Path(tempfile.mkdtemp(prefix="rubbish-report-"))
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    return path


def _write_run(run_dir: Path, quarantine_dir: Path) -> tuple[Path, Path]:
    run_dir.mkdir(parents=True)
    preflight = run_dir / "preflight.txt"
    preflight.write_text("BASELINE_FREE_BYTES=1000\nTOTAL_BYTES=5000\nPROCESSES=\n", encoding="utf-8")
    safe = run_dir / "deleted.log"
    quarantined = run_dir / "suspicious.dll"
    safe.write_bytes(b"x" * 100)
    quarantined.write_bytes(b"y" * 200)
    candidates = run_dir / "candidates.csv"
    with candidates.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="|", lineterminator="\n")
        writer.writerow(("Category", "Risk", "Path", "SizeBytes", "FileCount", "Action"))
        writer.writerow(("root-logs", "SAFE", str(safe), "100", "1", "delete"))
        writer.writerow(("root-suspicious", "CAUTION", str(quarantined), "200", "1", "quarantine"))

    quarantine_dir.mkdir()
    safe.unlink()
    quarantined.rename(quarantine_dir / quarantined.name)
    cleanup = run_dir / "cleanup-errors.csv"
    with cleanup.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="|", lineterminator="\n")
        writer.writerow(("Timestamp", "Phase", "Action", "Path", "ErrorMessage", "Disposition"))
        writer.writerow(("now", "root-logs", "Remove", str(safe), "", "OK"))
        writer.writerow(("now", "root-suspicious", "Quarantine", str(quarantined), "", "QUARANTINED"))
    return safe, quarantined


def test_verify_report_writes_eight_sections_and_reconciles_freed_bytes(tmp_path=None):
    root = _tmp_path(tmp_path)
    run_dir = root / "ROOT-run"
    quarantine_dir = root / "quarantine"
    _write_run(run_dir, quarantine_dir)
    result = report.verify_report(
        "/",
        run_dir=run_dir,
        volume={"Root": str(root), "FreeBytes": 1300, "TotalBytes": 5000},
        quarantine_dir=quarantine_dir,
    )

    assert result["status"] == "PASS"
    assert result["total_freed"] == 300
    assert result["estimated_freed"] == 300
    assert result["freed_safe"] == 100
    assert result["freed_quarantined"] == 200
    summary = (run_dir / "summary.md").read_text(encoding="utf-8")
    for index, title in enumerate(
        (
            "Baseline Free Space",
            "Final Free Space",
            "Total Freed",
            "Per-Category Freed",
            "Skipped Items Table",
            "Quarantine Note",
            "Verification Assertions",
            "Recommendations",
        ),
        start=1,
    ):
        assert f"## {index}. {title}" in summary
    assert "Status: **PASS**" in summary
    assert "QUARANTINED" in summary
    assert "Variance = **+0 bytes**" in summary


def test_verify_report_scan_only_has_no_cleanup_delta(tmp_path=None):
    root = _tmp_path(tmp_path)
    run_dir = root / "ROOT-scan"
    run_dir.mkdir()
    (run_dir / "preflight.txt").write_text("BASELINE_FREE_BYTES=500\nTOTAL_BYTES=1000\n", encoding="utf-8")
    (run_dir / "candidates.csv").write_text(
        "Category|Risk|Path|SizeBytes|FileCount|Action\n"
        f"root-logs|SAFE|{root / 'candidate.log'}|10|1|delete\n",
        encoding="utf-8",
    )
    result = report.verify_report(
        "/",
        run_dir=run_dir,
        volume={"Root": str(root), "FreeBytes": 600, "TotalBytes": 1000},
        quarantine_dir=root / "quarantine",
    )
    assert result["scan_only"] is True
    assert result["total_freed"] is None
    assert result["status"] == "SCAN_ONLY"
    assert "Scan-only run" in (run_dir / "summary.md").read_text(encoding="utf-8")


def test_multi_drive_report_is_sequential_and_contains_each_summary(tmp_path=None):
    root = _tmp_path(tmp_path)
    run_a = root / "a"
    run_b = root / "b"
    quarantine_a = root / "qa"
    quarantine_b = root / "qb"
    _write_run(run_a, quarantine_a)
    _write_run(run_b, quarantine_b)
    summary_path = root / "multi.md"
    result = report.verify_reports(
        ["A:", "B:"],
        run_dirs={"A:": run_a, "B:": run_b},
        volumes={
            "A:": {"Root": str(root), "FreeBytes": 1300, "TotalBytes": 5000},
            "B:": {"Root": str(root), "FreeBytes": 1300, "TotalBytes": 5000},
        },
        quarantine_dirs={"A:": quarantine_a, "B:": quarantine_b},
        summary_path=summary_path,
    )
    assert len(result["results"]) == 2
    text = summary_path.read_text(encoding="utf-8")
    assert "# Multi-Drive Verification Summary" in text
    assert "A:" in text and "B:" in text
    assert "Total estimated freed across drives: **600 bytes**" in text
