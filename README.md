# rubbish-cleaner

![test](https://github.com/EntropyXi/rubbish_cleaning_skill/workflows/test/badge.svg)

[English](README.md) | [Simplified Chinese](README_zh.md)

An agent-callable drive junk-cleanup skill for Claude Code, Codex and opencode. Release history: [CHANGELOG](CHANGELOG.md) ([Simplified Chinese](CHANGELOG_zh.md)).

## Documentation

Every document in this repository ships in English and Simplified Chinese, maintained in pairs:

- [README.md](README.md) ↔ [README_zh.md](README_zh.md) (overview)
- [SKILL.md](SKILL.md) ↔ [SKILL_zh.md](SKILL_zh.md) (agent-facing skill core)
- [CHANGELOG.md](CHANGELOG.md) ↔ [CHANGELOG_zh.md](CHANGELOG_zh.md) (release history)
- [references/junk-taxonomy.md](references/junk-taxonomy.md) ↔ [references/junk-taxonomy_zh.md](references/junk-taxonomy_zh.md)
- [references/per-app-path-map.md](references/per-app-path-map.md) ↔ [references/per-app-path-map_zh.md](references/per-app-path-map_zh.md)
- [references/safety-rules.md](references/safety-rules.md) ↔ [references/safety-rules_zh.md](references/safety-rules_zh.md)

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

- Target drive: `D:` on Windows, or `/` on Linux/macOS
- Categories to include or exclude (for example: keep installers, keep the recycle bin)
- Paths or folders to never touch
- Recency threshold in days (default: 7-day rule)
- Whether to only scan (dry-run) or also clean

Prompt examples:

1. `/rubbish-cleaner Scan drive C: and show me what can be freed. Do not delete anything yet.`
2. `/rubbish-cleaner Clean drive D: - remove temp files and app caches older than 30 days, but skip anything in D:\Downloads and D:\Games.`
3. `/rubbish-cleaner What junk is on E:? Only list browser caches and logs.`

The flow the agent follows: scan (read-only inventory) → shows you the categorized candidates with sizes → waits for your approval → cleans safely (quarantine = move to a backup folder, never permanent delete; files that Windows reports as locked are skipped, while POSIX may unlink an open file according to native filesystem semantics) → verifies and writes a summary report (`.omo\evidence\rubbish-cleaner\` run folder, `summary.md`). Windows stores this under the Desktop as before; Linux/macOS use `$HOME/.omo/`.

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
│   ├── schedule.ps1                    # Policy-based platform scheduler integration
│   └── lib/
│       ├── platform.ps1                # Platform paths, fixed-drive, and host helpers
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
    │   ├── report.Tests.ps1            # Pester 5 unit tests (report)
    │   └── optimization.Tests.ps1      # Pester 5 unit tests (batch and resume behavior)
    └── sandbox/
        └── run-sandbox-tests.ps1       # Zero-dependency fallback harness (nine suites)
```

## Testing

The local test entry point is **dual-mode** and conditionally picks its runner:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\rubbish_cleaning\tests\run-tests.ps1
```

- **Mode 0 (always, first):** parse-checks every `.ps1` under `scripts/` and `tests/` via `[System.Management.Automation.Language.Parser]::ParseFile`; exits 1 before any branch if a parse error is found.
- **Mode 1 (branch):**
  - **Pester 5.x installed** → prints `BRANCH: PESTER` and runs five `tests/unit/*.Tests.ps1` files containing 55 tests via `Invoke-Pester -PassThru`; exits 0 only if all pass.
  - **Pester 5.x not installed** → prints `BRANCH: SANDBOX` and delegates to the zero-dependency `tests/sandbox/run-sandbox-tests.ps1` harness, which runs nine plain-PowerShell suites and builds/cleans its own temp tree under `$env:TEMP\rubbish-cleaner-tests\<pid>`; its exit code is propagated.

GitHub Actions runs parser checks, Pester, and the sandbox harness on Windows, Ubuntu, and macOS. Windows also runs the full entry point under Windows PowerShell 5.1. Exit code 0 means all selected assertions passed.

## Current status

### Shipped capabilities

- Cross-platform fixed-drive support: `C:` on Windows and `/` on Linux/macOS.
- Platform-specific default paths and PowerShell host selection.
- Multi-drive processing and checkpoint-based `-Resume` support.
- Policy scheduling for Task Scheduler, cron, and launchd.
- Approval-gated, quarantine-first cleanup; link-safe traversal; and native Windows/POSIX lock semantics.
- Three-platform CI across Windows, Ubuntu, and macOS.

### Active limitations

- Junk detection uses a static cache taxonomy, so application path changes need maintenance.
- Duplicate-archive detection is root-only and name-based; it does not recurse or use hashes.
- There are no quarantine management subcommands or TTL policy.
- Thresholds are fixed; the CLI does not provide `-MinSizeMB` or `-MaxAgeDays`.
- The report section 7 real-quarantine integration assertion is not exercised by the sandbox harness.
- WSL-specific awareness is limited.
- GitHub Actions still emits a non-blocking `actions/checkout@v4` Node runtime deprecation warning.

### Prioritized next iterations

1. **Reliability and maintenance:** add real-quarantine integration coverage, update `actions/checkout`, and expand platform scheduling integration coverage.
2. **User control and recovery:** add a configuration-driven taxonomy, CLI thresholds and dry-run report diffs, and quarantine list/restore/purge/TTL management.
3. **Detection and reporting:** add recursive/hash duplicate suggestions, application path discovery, an HTML audit report, and WSL enhancements.

## License

MIT — see [LICENSE](LICENSE).
