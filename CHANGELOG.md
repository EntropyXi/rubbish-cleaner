# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
