# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v1.1.0] - 2026-08-01

### Added

- Added multi-drive batch execution with `-Drives` for [`scripts/scan-drive.ps1`](scripts/scan-drive.ps1), [`scripts/clean-drive.ps1`](scripts/clean-drive.ps1), and [`scripts/verify-report.ps1`](scripts/verify-report.ps1); cleanup remains sequential and approval-gated.
- Added scan and cleanup checkpoints with `-Resume`, plus policy-based scheduling through [`scripts/schedule.ps1`](scripts/schedule.ps1) and the [`references/policies/`](references/policies/) profiles.

### Changed

- Added fixed-drive resolution, platform-specific default paths, and PowerShell host selection through [`scripts/lib/platform.ps1`](scripts/lib/platform.ps1), so supported Windows, Linux, and macOS hosts use the appropriate drive and runtime semantics.
- Updated [`scripts/lib/rubbish-core.ps1`](scripts/lib/rubbish-core.ps1) for POSIX link handling and locked-item behavior.

### Fixed

- Prevented duplicate root-temp evaluation and normalized system temp roots while preserving repeated path separators.
- Kept the production scripts parseable in Windows PowerShell 5.1.

### Testing

- Made Pester pass/fail results explicit in [`tests/run-tests.ps1`](tests/run-tests.ps1) and [`.github/workflows/test.yml`](.github/workflows/test.yml).
- Expanded CI to a Windows, Ubuntu, and macOS matrix, including the Windows PowerShell 5.1 test entry point.

## [v1.0.0] - 2026-07-31

### Added

- Repository scaffolded as the `rubbish-cleaner` skill and created on GitHub via the CLI (`gh`); development follows a feature-branch git workflow.
- [SKILL.md](SKILL.md): skill core with progressive disclosure.
- `lib/rubbish-core.ps1`: safety function library (classification, quarantine, reporting helpers).
- Core scripts:
  - `scripts/scan-drive.ps1`: read-only drive scan + junk classification.
  - `scripts/clean-drive.ps1`: approval-gated safe cleanup with quarantine.
  - `scripts/verify-report.ps1`: post-clean verification + summary report.
- References: [junk-taxonomy.md](references/junk-taxonomy.md), [safety-rules.md](references/safety-rules.md), [per-app-path-map.md](references/per-app-path-map.md).
- Dual-mode test suite: zero-dependency sandbox harness (`tests/sandbox/run-sandbox-tests.ps1`) and Pester 5 unit tests (`tests/unit/`).
- [install.ps1](scripts/install.ps1): one-command installer; the skill is installed into the Claude Code, Codex and opencode skill directories, and the opencode skill index is updated (external config).

### Changed

- [README.md](README.md) rewritten agent-first: quick start via agents, slash-command usage, manual install de-emphasized.
- English-only README cleanup: mixed-language headings and comments fixed.
- Bilingual README: added [README_zh.md](README_zh.md) mirror with a language switcher.
- Added a "Limitations & Roadmap" section to the README.
- Repository renamed to `rubbish_cleaning_skill`.

Key files: [SKILL.md](SKILL.md), [README.md](README.md), [install.ps1](scripts/install.ps1), [CHANGELOG](CHANGELOG.md).
