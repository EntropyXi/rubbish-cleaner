"""Fake-tree scanner coverage mirroring the PowerShell behavior matrix."""

from __future__ import annotations

import atexit
import shutil
import tempfile
import time
from pathlib import Path

from scripts import scanner


def _tmp_path(tmp_path=None) -> Path:
    if tmp_path is not None:
        return Path(tmp_path)
    path = Path(tempfile.mkdtemp(prefix="rubbish-scanner-"))
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    return path


def _write_fake_tree(root: Path) -> None:
    (root / "Temp").mkdir(parents=True)
    (root / "tmp").mkdir()
    old = time.time() - 30 * 24 * 60 * 60
    for path, content in ((root / "Temp" / "a.tmp", "old temp"), (root / "tmp" / "b.log", "old log")):
        path.write_text(content, encoding="utf-8")
        import os

        os.utime(path, (old, old))
    (root / "empty1").mkdir()
    (root / "MyApp" / "cache").mkdir(parents=True)
    (root / "MyApp" / "cache" / "data.bin").write_text("cache", encoding="utf-8")
    (root / "archive").mkdir()
    (root / "archive" / "content.txt").write_text("expanded", encoding="utf-8")
    (root / "archive.zip").write_text("archive", encoding="utf-8")
    (root / "root-suspicious.dll").write_text("dll", encoding="utf-8")
    (root / "keep").mkdir()
    (root / "keep" / "userfile.txt").write_text("user data", encoding="utf-8")


def test_fake_tree_classification_and_risk_action_mapping(tmp_path=None):
    root = _tmp_path(tmp_path) / "fake"
    root.mkdir()
    _write_fake_tree(root)
    out_dir = root.parent / "out"

    # The PS matrix intentionally exercises Windows root categories even on
    # POSIX.  The scanner's flag is a module seam, so keep the host untouched.
    old_is_windows = scanner.IS_WINDOWS
    scanner.IS_WINDOWS = True
    try:
        result = scanner.scan(
            "X:",
            root_path=root,
            out_dir=out_dir,
            categories=["root-temps", "root-logs", "duplicate-archives", "empty-dirs", "root-suspicious", "app-caches", "recycle-bin"],
            is_user_drive=False,
        )
    finally:
        scanner.IS_WINDOWS = old_is_windows

    rows = result["rows"]
    by_category = {}
    for row in rows:
        by_category.setdefault(row["Category"], []).append(row)
        assert row["Action"] == scanner.RISK_ACTION_MAP[row["Risk"]]

    assert {"root-temps", "duplicate-archives", "empty-dirs", "root-suspicious"}.issubset(by_category)
    assert "root-logs" in result["report"]
    assert {row["Path"] for row in by_category["root-temps"]} == {
        str(root / "Temp" / "a.tmp"),
        str(root / "tmp" / "b.log"),
    }
    assert [row["Path"] for row in by_category["duplicate-archives"]] == [str(root / "archive.zip")]
    assert [row["Path"] for row in by_category["empty-dirs"]] == [str(root / "empty1")]
    assert [row["Path"] for row in by_category["root-suspicious"]] == [str(root / "root-suspicious.dll")]
    assert not any(Path(row["Path"]).name == "userfile.txt" for row in rows)
    assert not any(Path(row["Path"]).as_posix().endswith("MyApp/cache") for row in rows)

    assert by_category["root-suspicious"][0]["Risk"] == "CAUTION"
    assert by_category["root-suspicious"][0]["Action"] == "quarantine"
    assert by_category["duplicate-archives"][0]["Risk"] == "ASK"
    assert by_category["duplicate-archives"][0]["Action"] == "ask"


def test_scan_checkpoint_resume_and_candidate_csv_schema(tmp_path=None):
    root = _tmp_path(tmp_path) / "fake"
    root.mkdir()
    _write_fake_tree(root)
    out_dir = root.parent / "out"
    run_dir = out_dir / "ROOT-test"

    old_is_windows = scanner.IS_WINDOWS
    scanner.IS_WINDOWS = False
    try:
        first = scanner.scan(
            "/",
            root_path=root,
            out_dir=out_dir,
            run_dir=run_dir,
            categories=["root-temps"],
            system_temp_dir=root / "Temp",
            is_user_drive=False,
        )
        resumed = scanner.scan(
            "/",
            root_path=root,
            out_dir=out_dir,
            run_dir=run_dir,
            categories=["root-temps"],
            system_temp_dir=root / "Temp",
            is_user_drive=False,
            resume=True,
        )
    finally:
        scanner.IS_WINDOWS = old_is_windows

    assert len(first["rows"]) == 1
    assert resumed["evaluated"] == []
    header = (run_dir / "candidates.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header == "Category|Risk|Path|SizeBytes|FileCount|Action"
    checkpoint = (run_dir / "scan-checkpoint.json").read_text(encoding="utf-8")
    assert '"completedCategories"' in checkpoint


def test_category_validation_rejects_unknown_values(tmp_path=None):
    root = _tmp_path(tmp_path)
    try:
        scanner.scan("/", root_path=root, out_dir=root / "out", categories=["not-a-category"])
    except ValueError as error:
        assert "Unknown category" in str(error)
    else:
        raise AssertionError("unknown scanner category was accepted")
