"""End-to-end scanner -> cleaner -> report subprocess coverage."""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _tmp_path(tmp_path=None) -> Path:
    if tmp_path is not None:
        return Path(tmp_path)
    path = Path(tempfile.mkdtemp(prefix="rubbish-integration-"))
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    return path


def _run_api_subprocess(code: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(cwd) + os.pathsep + environment.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_scanner_cleaner_report_pipeline_on_fake_tree(tmp_path=None):
    root = _tmp_path(tmp_path)
    fake = root / "fake"
    fake.mkdir()
    temp_dir = fake / "Temp"
    temp_dir.mkdir()
    old_file = temp_dir / "old.tmp"
    old_file.write_text("temporary", encoding="utf-8")
    old = time.time() - 30 * 24 * 60 * 60
    os.utime(old_file, (old, old))
    suspicious = fake / "suspicious.dll"
    suspicious.write_text("suspicious", encoding="utf-8")
    out_dir = root / "out"
    quarantine_dir = root / "quarantine"
    volume = {"Root": str(fake), "FreeBytes": 1000, "TotalBytes": 5000}
    workspace = Path(__file__).resolve().parents[1]

    scanner_code = (
        "from pathlib import Path; "
        "import runpy, sys; "
        "from scripts.lib import core, platform; "
        "platform.IS_WINDOWS = True; "
        "core.IS_WINDOWS = (sys.platform == 'win32'); "
        f"platform.resolve_fixed_drive = lambda drive: {volume!r}; "
        f"sys.argv = ['scanner.py','-Drive','X:','-OutDir',{str(out_dir)!r},'-Categories','root-temps,root-suspicious']; "
        f"runpy.run_path({str(workspace / 'scripts' / 'scanner.py')!r}, run_name='__main__')"
    )
    scan_result = _run_api_subprocess(scanner_code, workspace)
    assert scan_result.returncode == 0, scan_result.stderr
    assert "SCAN COMPLETE:" in scan_result.stdout
    run_dir = max(out_dir.glob("*-*"), key=lambda path: path.stat().st_mtime_ns)
    candidates = run_dir / "candidates.csv"
    assert candidates.is_file()
    candidate_text = candidates.read_text(encoding="utf-8")
    assert "root-temps|SAFE|" in candidate_text
    assert "root-suspicious|CAUTION|" in candidate_text

    cleaner_code = (
        "from pathlib import Path; "
        "import runpy, sys; "
        "from scripts.lib import core, platform; "
        "platform.IS_WINDOWS = True; "
        "core.IS_WINDOWS = (sys.platform == 'win32'); "
        f"platform.resolve_fixed_drive = lambda drive: {volume!r}; "
        f"sys.argv = ['cleaner.py','-Drive','X:','-CandidatesCsv',{str(candidates)!r},'-QuarantineDir',{str(quarantine_dir)!r},'-Yes']; "
        f"runpy.run_path({str(workspace / 'scripts' / 'cleaner.py')!r}, run_name='__main__')"
    )
    clean_result = _run_api_subprocess(cleaner_code, workspace)
    assert clean_result.returncode == 0, clean_result.stderr
    assert "CLEAN COMPLETE:" in clean_result.stdout
    cleanup_csv = run_dir / "cleanup-errors.csv"
    cleanup_text = cleanup_csv.read_text(encoding="utf-8")
    assert "OK" in cleanup_text
    assert "QUARANTINED" in cleanup_text
    assert not old_file.exists()
    assert not suspicious.exists()
    assert (quarantine_dir / suspicious.name).exists()

    report_code = (
        "from pathlib import Path; "
        "import runpy, sys; "
        "from scripts.lib import core, platform; "
        "platform.IS_WINDOWS = True; "
        "core.IS_WINDOWS = (sys.platform == 'win32'); "
        f"platform.resolve_fixed_drive = lambda drive: {{'Root': {str(fake)!r}, 'FreeBytes': 1000, 'TotalBytes': 5000}}; "
        f"sys.argv = ['report.py','-Drive','X:','-RunDir',{str(run_dir)!r},'-QuarantineDir',{str(quarantine_dir)!r}]; "
        f"runpy.run_path({str(workspace / 'scripts' / 'report.py')!r}, run_name='__main__')"
    )
    report_result = _run_api_subprocess(report_code, workspace)
    assert report_result.returncode == 0, report_result.stderr
    assert "VERIFY COMPLETE: PASS" in report_result.stdout
    summary = (run_dir / "summary.md").read_text(encoding="utf-8")
    assert "Status: **PASS**" in summary
    assert "Freed (Safe)" in summary and "Freed (Quarantined)" in summary
    assert "| **Total** |" in summary
    assert all(f"## {index}." in summary for index in range(1, 9))
