"""Approval and safety gates for the cleaner."""

from __future__ import annotations

import atexit
import csv
import shutil
import tempfile
from pathlib import Path

from scripts import cleaner


def _tmp_path(tmp_path=None) -> Path:
    if tmp_path is not None:
        return Path(tmp_path)
    path = Path(tempfile.mkdtemp(prefix="rubbish-cleaner-"))
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    return path


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


def test_safe_delete_requires_approval_then_removes_and_records_ok(tmp_path=None):
    root = _tmp_path(tmp_path)
    target = root / "root.log"
    target.write_text("log", encoding="utf-8")
    candidates = root / "candidates.csv"
    _write_candidates(
        candidates,
        [{"Category": "root-logs", "Risk": "SAFE", "Path": str(target), "SizeBytes": 3, "FileCount": 1, "Action": "delete"}],
    )

    result = cleaner.clean(
        "X:",
        volume=_volume(root),
        candidates_csv=candidates,
        yes=True,
        quarantine_dir=root / "quarantine",
        is_user_drive=False,
        is_system_drive=False,
    )
    assert not target.exists()
    assert result["dispositions"] == [{"Category": "root-logs", "Path": str(target), "Disposition": "OK"}]
    assert "OK" in (root / "cleanup-errors.csv").read_text(encoding="utf-8")


def test_locked_gate_preserves_file_and_records_skip_locked(tmp_path=None):
    root = _tmp_path(tmp_path)
    target = root / "locked.log"
    target.write_text("locked", encoding="utf-8")
    csv_path = root / "cleanup.csv"
    row = {"Category": "root-logs", "Risk": "SAFE", "Path": str(target), "SizeBytes": "6", "FileCount": "1", "Action": "delete"}

    original = cleaner.core.test_file_locked
    cleaner.core.test_file_locked = lambda path: True
    try:
        disposition = cleaner._process_row(row, "root-logs", csv_path, root / "quarantine")
    finally:
        cleaner.core.test_file_locked = original
    assert disposition == "SKIP_LOCKED"
    assert target.exists()
    assert "SKIP_LOCKED" in csv_path.read_text(encoding="utf-8")


def test_quarantine_action_moves_item_and_non_empty_directory_is_not_removed(tmp_path=None):
    root = _tmp_path(tmp_path)
    quarantined = root / "suspicious.dll"
    quarantined.write_text("dll", encoding="utf-8")
    non_empty = root / "non-empty"
    non_empty.mkdir()
    (non_empty / "keep.txt").write_text("keep", encoding="utf-8")
    empty = root / "empty"
    empty.mkdir()
    candidates = root / "candidates.csv"
    _write_candidates(
        candidates,
        [
            {"Category": "root-suspicious", "Risk": "CAUTION", "Path": str(quarantined), "SizeBytes": 3, "FileCount": 1, "Action": "quarantine"},
            {"Category": "empty-dirs", "Risk": "SAFE", "Path": str(non_empty), "SizeBytes": 0, "FileCount": 0, "Action": "delete"},
            {"Category": "empty-dirs", "Risk": "SAFE", "Path": str(empty), "SizeBytes": 0, "FileCount": 0, "Action": "delete"},
        ],
    )
    quarantine_dir = root / "quarantine"
    result = cleaner.clean(
        "X:",
        volume=_volume(root),
        candidates_csv=candidates,
        yes=True,
        quarantine_dir=quarantine_dir,
        is_user_drive=False,
        is_system_drive=False,
    )
    assert not quarantined.exists()
    assert (quarantine_dir / quarantined.name).exists()
    assert not empty.exists()
    assert non_empty.exists()
    assert any(item["Disposition"] == "QUARANTINED" for item in result["dispositions"])
    assert any(item["Disposition"] == "SKIP_NOT_EMPTY" for item in result["dispositions"])


def test_ask_category_is_skipped_without_yes_or_explicit_approval(tmp_path=None):
    root = _tmp_path(tmp_path)
    target = root / "archive.zip"
    target.write_text("archive", encoding="utf-8")
    candidates = root / "candidates.csv"
    _write_candidates(
        candidates,
        [{"Category": "duplicate-archives", "Risk": "ASK", "Path": str(target), "SizeBytes": 7, "FileCount": 1, "Action": "ask"}],
    )
    result = cleaner.clean(
        "X:",
        volume=_volume(root),
        candidates_csv=candidates,
        yes=False,
        approvals=None,
        input_func=lambda prompt: "n",
        quarantine_dir=root / "quarantine",
        is_user_drive=False,
        is_system_drive=False,
    )
    assert target.exists()
    assert result["dispositions"] == []
    assert result["skipped_categories"] == ["duplicate-archives"]


def test_category_approval_mapping_is_case_insensitive(tmp_path=None):
    root = _tmp_path(tmp_path)
    target = root / "cache.log"
    target.write_text("cache", encoding="utf-8")
    candidates = root / "candidates.csv"
    _write_candidates(
        candidates,
        [{"Category": "root-logs", "Risk": "SAFE", "Path": str(target), "SizeBytes": 5, "FileCount": 1, "Action": "delete"}],
    )
    cleaner.clean(
        "X:",
        volume=_volume(root),
        candidates_csv=candidates,
        approvals={"ROOT-LOGS": True},
        quarantine_dir=root / "quarantine",
        is_user_drive=False,
        is_system_drive=False,
    )
    assert not target.exists()
