# rubbish-cleaner

![test](https://github.com/EntropyXi/rubbish_cleaning_skill/workflows/test/badge.svg)

[English](README.md) | [Simplified Chinese](README_zh.md)

An agent-callable drive junk-cleanup skill for Claude Code, Codex and opencode. Release history: [CHANGELOG](CHANGELOG.md) ([Simplified Chinese](CHANGELOG_zh.md)).

## Documentation

The core user-facing documents listed below are maintained in English and Simplified Chinese pairs:

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

The agent reads [SKILL.md](SKILL.md) and deploys the skill itself, running `python scripts/install.py` if it is not yet installed (no manual steps needed). It then follows the built-in scan → approve → clean → verify → report flow, shows you the candidate list, and asks for your confirmation before deleting anything.

**Way 2: manual install (optional).** One command, no admin needed, idempotent:

```bash
python -m pip install -r requirements.txt
python scripts/install.py --target all
```

`--target all|claude|codex|opencode` selects the platform (default `all`). It installs to the corresponding Claude Code, Codex, and opencode skill directories under the current user's home directory.

## Use it with your agent

Invocation: type `/rubbish-cleaner` in opencode or Claude Code (a slash command); Codex triggers the skill via its display name. Append your requirement in plain language. The skill's trigger words (junk cleanup / drive cleanup / cache cleanup / clean temp files) also auto-activate it.

What you can specify in your prompt (the skill maps these to its scan/clean parameters):

- Target drive: `D:` on Windows, or `/` on Linux/macOS
- Categories to include or exclude (for example: keep installers, keep the recycle bin)
- Paths or folders to never touch
- Whether to only scan (dry-run) or also clean

Prompt examples:

1. `/rubbish-cleaner Scan drive C: and show me what can be freed. Do not delete anything yet.`
2. `/rubbish-cleaner Clean drive D: - remove temporary files and app caches selected by the built-in rules, but skip anything in D:\Downloads and D:\Games.`
3. `/rubbish-cleaner What junk is on E:? Only list browser caches and logs.`

The flow the agent follows: scan (read-only inventory) → shows you the categorized candidates with sizes → waits for your approval → cleans safely (quarantine = move to a backup folder, never permanent delete; files that Windows reports as locked are skipped, while POSIX defaults to skip files that cannot be safely unlinked, opt-in only via `--allow-posix-unlink`) → verifies and writes a summary report (`.omo\evidence\rubbish-cleaner\` run folder, `summary.md`). Windows stores this under the Desktop as before; Linux/macOS use `$HOME/.omo/`.

Safety: everything is per-drive and per-run scoped; nothing is permanently deleted (quarantined); each deletion is re-verified right before it happens; junction-aware; UAC-elevated system cleanup is optional and skip-if-denied.

## What it cleans

The full taxonomy lives in [junk-taxonomy.md](references/junk-taxonomy.md) and the per-app path map in [per-app-path-map.md](references/per-app-path-map.md). In short: root temp files and logs, duplicate archives, empty directories, the recycle bin (with approval), and per-app caches (anaconda, WeGame, WeChat, Steam leftovers, ...). On the system drive it also covers browser/GPU/pip/npm/IDE caches, crash dumps and thumbnails, plus an optional elevated system batch (Windows\Temp, Prefetch, SoftwareDistribution, CBS, DISM /StartComponentCleanup). It never touches user documents, installed programs, or system component stores.

## Project structure

```
rubbish-cleaner/
├── SKILL.md                            # Skill core (progressive disclosure)
├── README.md / README_zh.md            # EN/ZH docs (this file)
├── LICENSE                             # MIT
├── requirements.txt                    # psutil plus Windows-only pywin32
├── agents/
│   └── openai.yaml                     # Codex UI metadata; ignored by Claude Code
├── scripts/
│   ├── install.py                      # Installer to all three platform skill dirs
│   ├── scanner.py                      # Read-only scan + classification (phase 1)
│   ├── cleaner.py                      # Approval-gated cleanup + quarantine (phase 3)
│   ├── report.py                       # Verify + summary report (phase 4)
│   ├── schedule.py                     # Policy-based platform scheduler integration
│   └── lib/
│       ├── platform.py                 # Platform paths and fixed-drive helpers
│       └── core.py                     # Safety function library
├── references/
│   ├── junk-taxonomy.md                # Junk file taxonomy
│   ├── per-app-path-map.md             # Common app cache/temp path map
│   └── safety-rules.md                 # Safety rules & exclusion list
└── tests/
    ├── test_runner.py                 # Compileall + pytest/fallback entry point
    └── test_*.py                       # Six Python behavior suites
```

## Testing

The local test entry point is **dual-mode** and conditionally picks its runner:

```bash
python -m pip install -r requirements.txt
python tests/test_runner.py
```

- **Mode 0 (always, first):** compiles every Python module under `scripts/` and `tests/`; exits 1 before any test branch if a syntax error is found.
- **Mode 1 (branch):** uses pytest when installed (`python -m pytest tests/ -x --tb=short`), otherwise imports and runs `test_` functions with the same exit-code semantics.
- The six suites build fake trees under temporary directories and never touch real drives. `psutil` is required; `pywin32` is installed only on Windows.

GitHub Actions runs compileall, the Python 3.10 compatibility gate, pytest, and a read-only scanner smoke test on Windows, Ubuntu, and macOS for Python 3.10–3.12. Exit code 0 means all selected assertions passed.

## Current status

### Shipped capabilities

- Cross-platform fixed-drive support: `C:` on Windows and `/` on Linux/macOS.
- Python 3.10+ implementation with psutil; optional pywin32 is used only for Windows UAC and Task Scheduler integration.
- Multi-drive processing and checkpoint-based `-Resume` support.
- Policy scheduling for Task Scheduler, cron, and launchd.
- Approval-gated, quarantine-first cleanup; link-safe traversal; and native Windows/POSIX lock semantics.
- Three-platform CI across Windows, Ubuntu, and macOS.

### Active limitations

- Junk detection uses a static cache taxonomy, so application path changes need maintenance.
- Duplicate-archive detection is root-only and name-based; it does not recurse or use hashes.
- There are no quarantine management subcommands or TTL policy.
- The freshness rule is fixed at seven days; the CLI does not provide `-MinSizeMB` or `-MaxAgeDays`.
- The report section 7 real-quarantine integration assertion is not exercised by the sandbox harness.
- WSL-specific awareness is limited.
- GitHub Actions still emits a non-blocking `actions/checkout@v4` Node runtime deprecation warning.

### Prioritized next iterations

1. **Reliability and maintenance:** add real-quarantine integration coverage, update `actions/checkout`, and expand platform scheduling integration coverage.
2. **User control and recovery:** add a configuration-driven taxonomy, CLI thresholds and dry-run report diffs, and quarantine list/restore/purge/TTL management.
3. **Detection and reporting:** add recursive/hash duplicate suggestions, application path discovery, an HTML audit report, and WSL enhancements.

## License

MIT — see [LICENSE](LICENSE).
