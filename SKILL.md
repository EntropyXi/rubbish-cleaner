---
name: rubbish-cleaner
description: >
  扫描并安全清理驱动器上的垃圾文件（临时文件、缓存、日志、空目录、崩溃转储），
  需用户批准后才删除。适用于 Claude Code 和 Codex。Use when 用户要垃圾清理、
  清理垃圾、磁盘清理、盘符清理、清缓存，或提到 junk cleanup、drive cleanup、
  cache cleanup、clean temp files。
---

# rubbish-cleaner

![test](https://github.com/EntropyXi/rubbish_cleaning_skill/workflows/test/badge.svg)

[English](SKILL.md) | [简体中文](SKILL_zh.md)

This skill scans a fixed local volume, presents categorized candidates, waits
for approval, performs quarantine-first cleanup, and writes a verification
report. The five phases remain **scan → approve → clean → verify → report**.

## Platform and installation

- Python 3.10 or newer is required.
- `psutil` is the cross-platform runtime dependency. `pywin32` is optional and
  installed only on Windows for UAC and Task Scheduler integration.
- Windows accepts a fixed local drive such as `C:`. Linux and macOS accept `/`.
- Windows keeps evidence and quarantine under the Desktop `.omo` directory;
  Linux and macOS use `$HOME/.omo/`.
- Install the skill from its repository root with:

```bash
python -m pip install -r requirements.txt
python scripts/install.py --target all
```

The installer supports `--target all|claude|codex|opencode` and is idempotent.

## Trigger and invocation rules

Trigger on requests such as 垃圾清理 / 清理垃圾 / 磁盘清理 / 盘符清理 / 清缓存,
junk cleanup, drive cleanup, cache cleanup, or clean temp files. Scanning is read-only. Run commands from the
skill root or pass absolute paths; never assume the caller's working directory.

```bash
# Windows
python scripts/scanner.py -Drive C: -Categories root-temps,root-logs
python scripts/cleaner.py -Drive C: -Yes
python scripts/report.py -Drive C:

# Linux/macOS
python scripts/scanner.py -Drive / -Categories root-temps,root-logs
python scripts/cleaner.py -Drive / -Yes
python scripts/report.py -Drive /
```

Always show the scan candidates and obtain explicit user approval before
running cleanup. `-Yes` is valid only after that approval; without it, ASK
categories remain interactive. Use `-RunDir` to connect clean and report to a
specific scan, or let report select the latest run. `-Resume` continues a
checkpoint without repeating completed categories. `-Drives` performs
multi-volume scans with independent run directories.

## Safety invariants

1. Cleanup processes only rows in the scan's `candidates.csv`; it never walks
   arbitrary paths supplied outside the scan output.
2. Every path is checked immediately before mutation. Junctions, Windows
   reparse points, and POSIX symbolic links are not traversed.
3. CAUTION items are moved to a per-drive quarantine; they are never permanently
   deleted. SAFE items are deleted only after the same approval gate.
4. Files newer than seven days are skipped. Windows-locked files are recorded
   as `SKIP_LOCKED`; POSIX may unlink an open file according to native filesystem
   semantics, and records the actual result.
5. User documents, installed programs, system component stores, pagefile and
   hibernation files, and protected application data are never cleanup targets.
6. Elevated system cleanup is Windows-only. On Linux/macOS it is skipped with
   `SKIP_ELEVATION_DENIED` and the normal cleanup continues.

## Categories and evidence

The 15-category taxonomy and exclusions are defined in
[references/junk-taxonomy.md](references/junk-taxonomy.md) and
[references/safety-rules.md](references/safety-rules.md). The per-application
path map is in [references/per-app-path-map.md](references/per-app-path-map.md).

Each run contains `preflight.txt`, `candidates.csv`, `scan-report.json`,
`cleanup-errors.csv`, and an eight-section `summary.md`. Windows stores runs in
`%USERPROFILE%\Desktop\.omo\evidence\rubbish-cleaner\`; Linux/macOS store them
under `$HOME/.omo/evidence/rubbish-cleaner/`. Quarantined items are kept in the
matching `.omo/quarantine/<drive-id>/` directory.

## Scheduling

`python scripts/schedule.py` provides policy-backed registration, listing, and
unregistration for Windows Task Scheduler, Linux cron, and macOS launchd. The
command is opt-in and never runs at import time. Use `--action list` to inspect
existing entries before changing a schedule.

## Testing

Run the dual-mode entry point:

```bash
python tests/test_runner.py
```

It first runs `compileall`, then uses pytest when available or a dependency-free
fallback that imports and invokes `test_` functions. All fixtures live below
temporary directories and never touch real drives. The GitHub Actions matrix
runs Python 3.10, 3.11, and 3.12 on Windows, Ubuntu, and macOS.
