# rubbish-cleaner

[English](README.md) | [简体中文](README_zh.md)

Drive-scoped junk cleanup skill: scan a drive for junk files (temp files, caches, logs, empty directories) and clean them safely with explicit approval. A lightweight skill callable from Claude Code, Codex, and opencode — no Python runtime dependencies, built on Windows PowerShell 5.1.

The workflow is **scan → approve → clean → verify → report**: every deletion is quarantined (moved to a backup folder) instead of destroyed, and a verification report is generated so you can audit exactly what changed. Cleanup is per-drive and per-run-scoped, so it never touches anything outside the directory you point it at.

## Installation

Clone or copy the repo, then run the installer (no admin rights needed):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\rubbish_cleaning\scripts\install.ps1
```

The installer copies the whole skill (SKILL.md, scripts/, references/, agents/, tests/, README.md, LICENSE, requirements.txt — everything except `.git`, `.omo`, `.codegraph`) to the target platform directory. It is idempotent: re-running overwrites existing copies. `-Target` selects the platform (`all` | `claude` | `codex` | `opencode`, default `all`).

### Claude Code

Installed to:

```
%USERPROFILE%\.claude\skills\rubbish-cleaner\
```

### Codex

Installed to:

```
%USERPROFILE%\.codex\skills\rubbish-cleaner\
```

### opencode

Installed to:

```
%USERPROFILE%\.config\opencode\skills\automation\rubbish-cleaner\
```

## Usage

Run the four phases in order, replacing `X:` with the target drive and `<run>` with the run ID printed by the scan (a timestamp, e.g. `20260731-153000`):

```powershell
# 1. Scan (READ-ONLY): inventories junk candidates into candidates.csv + scan-report.json
powershell -NoProfile -ExecutionPolicy Bypass -File D:\rubbish_cleaning\scripts\scan-drive.ps1 -Drive X:

# 2. Review the candidates before approving anything
#    (open candidates.csv / scan-report.json and eyeball the listed items)

# 3. Clean (approval-gated): quarantines approved candidates to a backup dir
powershell -NoProfile -ExecutionPolicy Bypass -File D:\rubbish_cleaning\scripts\clean-drive.ps1 -Drive X: -Yes

# 4. Verify + report: confirms the drive state and writes verify-report
powershell -NoProfile -ExecutionPolicy Bypass -File D:\rubbish_cleaning\scripts\verify-report.ps1 -Drive X: -RunDir <run>
```

Nothing is deleted on step 1 — scanning is inventory-only. Step 3 requires the explicit `-Yes` flag and quarantines rather than deletes, so anything you regret can be restored from the backup directory.

## Project Structure

```
rubbish-cleaner/
├── SKILL.md                            # Skill core (progressive disclosure)
├── README.md                           # This file
├── LICENSE                             # MIT
├── requirements.txt                    # Dependency note (no third-party runtime deps)
├── agents/
│   └── openai.yaml                     # Codex UI metadata; ignored by Claude Code
├── scripts/
│   ├── install.ps1                     # One-command installer to all 3 platform skill dirs
│   ├── scan-drive.ps1                  # Read-only scan + classification (phase 1)
│   ├── clean-drive.ps1                 # Approval-gated cleanup + quarantine (phase 3)
│   ├── verify-report.ps1               # Verify + summary report (phase 4)
│   └── lib/
│       └── rubbish-core.ps1            # Safety function library (classify/quarantine/report)
├── references/
│   ├── junk-taxonomy.md                # Junk file taxonomy
│   ├── per-app-path-map.md             # Common app cache/temp path map
│   └── safety-rules.md                 # Safety rules & exclusion list
└── tests/
    ├── run-tests.ps1                   # Dual-mode test entry point
    ├── unit/
    │   ├── scan.Tests.ps1              # Pester 5 unit tests (scan classification)
    │   ├── clean.Tests.ps1             # Pester 5 unit tests (safe delete + quarantine)
    │   ├── core.Tests.ps1              # Pester 5 unit tests (core library)
    │   └── report.Tests.ps1            # Pester 5 unit tests (report)
    └── sandbox/
        └── run-sandbox-tests.ps1       # Zero-dependency fallback harness (no Pester)
```

## Testing

The test suite is **dual-mode** and conditionally picks its runner:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\rubbish_cleaning\tests\run-tests.ps1
```

- **Mode 0 (always, first):** parse-checks every `.ps1` under `scripts/` and `tests/` via `[System.Management.Automation.Language.Parser]::ParseFile`; exits 1 before any branch if a parse error is found.
- **Mode 1 (branch):**
  - **Pester 5.x installed** → prints `BRANCH: PESTER` and runs the four `tests/unit/*.Tests.ps1` suites via `Invoke-Pester -PassThru`; exits 0 only if all pass.
  - **Pester 5.x not installed** → prints `BRANCH: SANDBOX` and delegates to the zero-dependency harness `tests/sandbox/run-sandbox-tests.ps1` (plain PowerShell asserts, same four suites, builds/cleans its own temp tree under `$env:TEMP\rubbish-cleaner-tests\<pid>`), propagating its exit code.

Either way the same four behavior suites (scan classification, safe delete + quarantine, empty-dir detection, report fixture) are covered. Exit code 0 means all assertions passed.

## Limitations & Roadmap

### Current limitations

- Windows-only: built on PowerShell 5.1; no pwsh 7 (PowerShell Core) or Linux/macOS support yet
- Pester branch of the dual-mode tests: without Pester 5.x on the machine, only the syntax parse-check runs (the sandbox harness is the main execution path)
- `-Drive D:` is hardcoded in the sandbox test fixtures (ReportFixture and the clean-gating suites); machines without a fixed D: drive need it parameterized
- The verify-report "quarantine copy exists" assertion (report section 7) is skipped in sandbox tests (needs a real quarantine directory)
- Junk detection relies on a static path map (per-app-path-map.md); cache paths need manual maintenance when apps update, and no registry uninstall-entry auto-discovery exists
- Duplicate-archive detection only looks at the drive root (same-level same-name archive + extracted-dir pairs), does not recurse into subdirectories; no hash-level duplicate detection
- No scheduled-task integration (Task Scheduler / cron); cleanup is manual or agent-triggered
- No TTL/auto-purge for the quarantine directory (safety-first design; quarantined files are handled manually)
- Fixed thresholds (e.g. the 7-day freshness rule); the CLI exposes no filter params like `-MinSizeMB` / `-MaxAgeDays`
- Single-threaded PowerShell enumeration can be slow on large drives; no scan progress persistence or resume

### Roadmap / next iterations

- PowerShell 7 (pwsh) compatibility + cross-platform cache path support (Linux/macOS)
- GitHub Actions CI (with Pester 5.x preinstalled) so the Pester branch actually runs in CI
- Parameterize test fixtures (remove the hardcoded `-Drive D:`)
- Config-driven taxonomy: user-editable JSON (categories, paths, age/size thresholds, per-user overrides)
- CLI filter params: `-MinSizeMB` / `-MaxAgeDays` / dry-run report diff
- Recursive duplicate detection + hash-level dedup suggestions
- App path auto-discovery (read uninstall registry keys → derive per-app cache paths)
- Task Scheduler integration: scheduled scans + policy profiles (safe/aggressive) + space-freed notifications
- Quarantine management subcommands: list / restore / purge, or a TTL policy
- HTML report rendering of summary.md for manual auditing
- WSL awareness enhancements (extend the existing SKIP_WSL_REGISTERED handling to WSL distro temp-dir mounts)
- Multi-drive batch mode (`-Drives D:,E:`) with long-scan progress / resume

## License

MIT — see [LICENSE](LICENSE).
