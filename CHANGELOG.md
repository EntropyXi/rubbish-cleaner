# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v2.0.0] - 2026-08-01

### Changed

- Ported the scan → approve → clean → verify → report workflow from PowerShell to Python 3.10+.
- Added the cross-platform `psutil` runtime dependency; `pywin32` is gated to Windows for UAC and Task Scheduler integration.
- Replaced the PowerShell test harness with six pytest suites plus a dependency-free compileall/fallback runner.
- Unified Windows, Ubuntu, and macOS CI with Python 3.10–3.12 compatibility checks and read-only scanner smoke tests.
- Preserved quarantine-first cleanup, junction/symlink safety, seven-day freshness gates, checkpoint/resume, and native Windows/POSIX lock semantics.

## [v1.1.0] - 2026-08-01

### Added

- Added multi-drive batch execution with `-Drives` for [`scripts/scanner.py`](scripts/scanner.py), [`scripts/cleaner.py`](scripts/cleaner.py), and [`scripts/report.py`](scripts/report.py). The optional `scanner.py -Parallel` path scans multiple drives concurrently, and scan and cleanup deliver category-level progress; cleanup remains sequential and approval-gated.
- Added scan and cleanup checkpoints with `-Resume`, plus policy-based scheduling through [`scripts/schedule.py`](scripts/schedule.py) and the [`references/policies/`](references/policies/) profiles.

### Changed

- Added fixed-drive resolution and platform-specific default paths through [`scripts/lib/platform.py`](scripts/lib/platform.py), so supported Windows, Linux, and macOS hosts use the appropriate drive and runtime semantics.
- Updated [`scripts/lib/core.py`](scripts/lib/core.py) for POSIX link handling and locked-item behavior.

### Fixed

- Prevented duplicate root-temp evaluation and normalized system temp roots while preserving repeated path separators.
- Kept the production scripts parseable in Windows PowerShell 5.1.

### Testing

- Made pytest/fallback pass/fail results explicit in [`tests/test_runner.py`](tests/test_runner.py) and [`.github/workflows/test.yml`](.github/workflows/test.yml).
- Expanded CI to a Windows, Ubuntu, and macOS matrix, including the Windows PowerShell 5.1 test entry point.

## [v1.0.0] - 2026-07-31

### Added

- Repository scaffolded as the `rubbish-cleaner` skill and created on GitHub via the CLI (`gh`); development follows a feature-branch git workflow.
- [SKILL.md](SKILL.md): skill core with progressive disclosure.
- `scripts/lib/core.py`: safety function library (classification, quarantine, reporting helpers).
- Core scripts:
  - `scripts/scanner.py`: read-only drive scan + junk classification.
  - `scripts/cleaner.py`: approval-gated safe cleanup with quarantine.
  - `scripts/report.py`: post-clean verification + summary report.
- References: [junk-taxonomy.md](references/junk-taxonomy.md), [safety-rules.md](references/safety-rules.md), [per-app-path-map.md](references/per-app-path-map.md).
- Dual-mode test suite: dependency-free fallback runner (`tests/test_runner.py`) and six pytest files.
- [install.py](scripts/install.py): one-command installer; the skill is installed into the Claude Code, Codex and opencode skill directories.

### Changed

- [README.md](README.md) rewritten agent-first: quick start via agents, slash-command usage, manual install de-emphasized.
- English-only README cleanup: mixed-language headings and comments fixed.
- Bilingual README: added [README_zh.md](README_zh.md) mirror with a language switcher.
- Added a "Limitations & Roadmap" section to the README.
- Repository renamed to `rubbish_cleaning_skill`.

Key files: [SKILL.md](SKILL.md), [README.md](README.md), [install.py](scripts/install.py), [CHANGELOG](CHANGELOG.md).
