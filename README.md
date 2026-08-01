# rubbish-cleaner

![test](https://github.com/EntropyXi/rubbish_cleaning_skill/workflows/test/badge.svg)

[English](README.md) | [简体中文](README_zh.md)

An agent-callable drive junk-cleanup skill for Claude Code, Codex and opencode.

## Quick Start

This is a skill for LLM agents, not a traditional CLI tool. You describe what to clean in plain language; the agent does the rest. Two ways to get started:

**Way 1 (recommended): let the agent deploy it.** Clone (or download and unzip) the repo anywhere, then open your agent and simply type your request in the chat, for example:

```
/rubbish-cleaner Clean up drive D: - temp files and caches only, do NOT touch my installers or game saves
```

The agent reads [SKILL.md](SKILL.md) and deploys the skill itself, running `scripts\install.ps1` if it is not yet installed (no manual steps needed). It then follows the built-in scan → approve → clean → verify → report flow, shows you the candidate list, and asks for your confirmation before deleting anything.

**Way 2: manual install (optional).** One command, no admin needed, idempotent:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File <repo>\scripts\install.ps1
```

`-Target all|claude|codex|opencode` selects the platform (default `all`). It installs to `%USERPROFILE%\.claude\skills\rubbish-cleaner\`, `%USERPROFILE%\.codex\skills\rubbish-cleaner\` and `%USERPROFILE%\.config\opencode\skills\automation\rubbish-cleaner\`.

## Use it with your agent

Invocation: type `/rubbish-cleaner` in opencode or Claude Code (a slash command); Codex triggers the skill via its display name. Append your requirement in plain language. The skill's trigger words (junk cleanup / drive cleanup / cache cleanup / clean temp files) also auto-activate it.

What you can specify in your prompt (the skill maps these to its scan/clean parameters):

- Target drive, e.g. `D:`
- Categories to include or exclude (for example: keep installers, keep the recycle bin)
- Paths or folders to never touch
- Recency threshold in days (default: 7-day rule)
- Whether to only scan (dry-run) or also clean

Prompt examples:

1. `/rubbish-cleaner Scan drive C: and show me what can be freed. Do not delete anything yet.`
2. `/rubbish-cleaner Clean drive D: - remove temp files and app caches older than 30 days, but skip anything in D:\Downloads and D:\Games.`
3. `/rubbish-cleaner What junk is on E:? Only list browser caches and logs.`

The flow the agent follows: scan (read-only inventory) → shows you the categorized candidates with sizes → waits for your approval → cleans safely (quarantine = move to a backup folder, never permanent delete; locked files are skipped) → verifies and writes a summary report (`.omo\evidence\rubbish-cleaner\` run folder, `summary.md`).

Safety: everything is per-drive and per-run scoped; nothing is permanently deleted (quarantined); each deletion is re-verified right before it happens; junction-aware; UAC-elevated system cleanup is optional and skip-if-denied.

## What it cleans

The full taxonomy lives in [junk-taxonomy.md](references/junk-taxonomy.md) and the per-app path map in [per-app-path-map.md](references/per-app-path-map.md). In short: root temp files and logs, duplicate archives, empty directories, the recycle bin (with approval), and per-app caches (anaconda, WeGame, WeChat, Steam leftovers, ...). On the system drive it also covers browser/GPU/pip/npm/IDE caches, crash dumps and thumbnails, plus an optional elevated system batch (Windows\Temp, Prefetch, SoftwareDistribution, CBS, DISM /StartComponentCleanup). It never touches user documents, installed programs, or system component stores.

## Project structure

```
rubbish-cleaner/
├── SKILL.md                            # Skill core (progressive disclosure)
├── README.md / README_zh.md            # EN/ZH docs (this file)
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

- ✅ ~~Windows-only: built on PowerShell 5.1; no pwsh 7 (PowerShell Core) or Linux/macOS support yet~~ — now runs on Windows (PS 5.1 + pwsh 7) and Linux/macOS (pwsh 7); see [SKILL.md](SKILL.md) `平台支持`
- ✅ ~~Pester branch of the dual-mode tests: without Pester 5.x on the machine, only the syntax parse-check runs (the sandbox harness is the main execution path)~~ — GitHub Actions CI now runs the Pester branch with Pester 5.x preinstalled
- ✅ ~~`-Drive D:` is hardcoded in the sandbox test fixtures (ReportFixture and the clean-gating suites); machines without a fixed D: drive need it parameterized~~ — fixtures now auto-detect a test drive (first fixed drive on Windows, `/` elsewhere)
- The verify-report "quarantine copy exists" assertion (report section 7) is skipped in sandbox tests (needs a real quarantine directory)
- Junk detection relies on a static path map ([per-app-path-map.md](references/per-app-path-map.md)); cache paths need manual maintenance when apps update, and no registry uninstall-entry auto-discovery exists
- Duplicate-archive detection only looks at the drive root (same-level same-name archive + extracted-dir pairs), does not recurse into subdirectories; no hash-level duplicate detection
- No scheduled-task integration (Task Scheduler / cron); cleanup is manual or agent-triggered
- No TTL/auto-purge for the quarantine directory (safety-first design; quarantined files are handled manually)
- Fixed thresholds (e.g. the 7-day freshness rule); the CLI exposes no filter params like `-MinSizeMB` / `-MaxAgeDays`
- ✅ ~~Single-threaded PowerShell enumeration can be slow on large drives; no scan progress persistence or resume~~ — multi-drive batch (`-Drives D:,E:`) plus `-Resume` checkpoint/resume is now built in

### Roadmap / next iterations

- ✅ ~~PowerShell 7 (pwsh) compatibility + cross-platform cache path support (Linux/macOS)~~ — delivered via `scripts/lib/platform.ps1`
- ✅ ~~GitHub Actions CI (with Pester 5.x preinstalled) so the Pester branch actually runs in CI~~ — delivered via `.github/workflows/test.yml`
- ✅ ~~Parameterize test fixtures (remove the hardcoded `-Drive D:`)~~ — delivered (fixtures auto-detect a test drive)
- Config-driven taxonomy: user-editable JSON (categories, paths, age/size thresholds, per-user overrides)
- CLI filter params: `-MinSizeMB` / `-MaxAgeDays` / dry-run report diff
- Recursive duplicate detection + hash-level dedup suggestions
- App path auto-discovery (read uninstall registry keys → derive per-app cache paths)
- Task Scheduler integration: scheduled scans + policy profiles (safe/aggressive) + space-freed notifications
- Quarantine management subcommands: list / restore / purge, or a TTL policy
- HTML report rendering of summary.md for manual auditing
- WSL awareness enhancements (extend the existing SKIP_WSL_REGISTERED handling to WSL distro temp-dir mounts)
- ✅ ~~Multi-drive batch mode (`-Drives D:,E:`) with long-scan progress / resume~~ — delivered (`-Drives C:,D:` + `-Resume`)

## License

MIT — see [LICENSE](LICENSE).
