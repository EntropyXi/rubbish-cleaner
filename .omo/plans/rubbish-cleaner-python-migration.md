# rubbish-cleaner-python-migration - Work Plan

## TL;DR (For humans)

**What you'll get:** 将 `rubbish-cleaner` 技能从 PowerShell（5,548 行、14 个 .ps1 文件）完整迁移到 **Python 3.10+**——架构不变（scan → approve → clean → verify → report），只换实现语言。v2.0.0 发布。

**Why:** PS 是当前最大技术债务——pwsh 7 vs 5.1 行为分裂、BOM-less 编码随 locale 漂移、`$env:TEMP` 在 Linux 是 null、Pester 版本碎片化、Task Scheduler 三套平台注册逻辑。Python 一份 `sys.platform` + `os` + `tempfile` + `pathlib` + `psutil`（仅跨平台）根治全部。

**Key decisions already made within:** ① **psutil 作为唯一跨平台依赖**（驱动枚举 + 进程列表）；② **pywin32 仅 Windows 下 `try: import` 守卫**（schtasks 注册 + UAC 提权）；③ **pytest 作为 CI 测试框架**（本地无 pytest 时 runner 回落纯 assert——保留双模式设计）；④ **Python 3.10+ 下行兼容**（不用 match / `X | Y` union / walrus）；⑤ **扫描的并行全部用 `concurrent.futures.ThreadPoolExecutor`**（替代 PS 的 Start-Job / ForEach-Object -Parallel 三路分支）；⑥ **清除全部 .ps1 文件**（`git rm` 14 个，install 脚本过滤 `.ps1` 不复制）；⑦ **所有安全护栏逐行移植**（隔离不删 / 跳过锁定 / junction 感知 / 7 天规则 / ±500MB 容差）。

**What it will NOT do:** 不换架构、不加新功能（这是 v2.0.0 语言迁移，不是功能迭代）；不保留 PS 兼容层（完整替换，不是并存）。

**Effort:** 高 — 14 个 todo、8 个波次、4 个最终验证。核心工作是 scanner.py（1,296 行 PS → ~800 行 Python）+ 6 个 pytest 套件。
**Risk:** 中 — 最大的单点风险是 Windows 专有 API（junction 检测 / UAC / schtasks）需 `ctypes`/`pywin32` 守卫；CI 在三系统矩阵上首次跑 Python 可能有路径/编码小坑（但比 PS 时代的坑少得多——UTF-8 默认、`sys.platform` 不带歧义）。

---

> TL;DR (machine): High effort, Medium risk — 14 todos in 8 waves: deps+branch, platform+core (series), scanner+cleaner+report (parallel), install+schedule+runner (parallel), CI+docs (parallel), 6 pytest suites, delete .ps1, git finalize; F1-F4 final wave. Python 3.10+ only; psutil (cross-platform) + pywin32 (Windows, optional guarded import); concurrent.futures.ThreadPoolExecutor replaces parallel (intentional simplification — I/O-bound workload, thread crash isolation not needed); pytest-or-fallback dual-mode; all 9 v1.1.0 features ported; zero .ps1 files tracked after migration.

## Scope
### Must have
Port the ENTIRE rubbish-cleaner skill from PowerShell (5,548 lines across 14 .ps1 files) to Python 3.10+. Architecture unchanged (scan → approve → clean → verify → report); implementation language changes only.

**Files to create (9 total .py — 7 logic + 2 __init__ bootstrap):**
1. `scripts/lib/platform.py` — cross-platform detection
2. `scripts/lib/core.py` — safety primitives
3. `scripts/scanner.py` — 15-category classifier
4. `scripts/cleaner.py` — approval-gated safe cleanup
5. `scripts/report.py` — 8-section verification report
6. `scripts/install.py` — copy skill to 3 agent dirs
7. `scripts/schedule.py` — Task Scheduler / cron / launchd
8. `scripts/__init__.py` and `scripts/lib/__init__.py` (empty, for package resolution)

**Files to create (8 new test .py — 7 logic + 1 init):**
9. `tests/test_runner.py` — dual-mode: compileall gate + pytest-or-plain fallback
10. `tests/test_core.py`, `test_scanner.py`, `test_cleaner.py`, `test_report.py`
11. `tests/test_optimization.py` (parallel/checkpoint/platform/schedule/multidrive)
12. `tests/test_integration.py` (end-to-end scanner→cleaner→report on fake tree)
13. `tests/__init__.py` (empty)

**Files to modify:**
11. `requirements.txt` — from comment-only to actual Python deps
12. `.github/workflows/test.yml` — Python matrix (compileall + pytest + lint gate)
13. `SKILL.md` + `SKILL_zh.md` + `README.md` + `README_zh.md` + `CHANGELOG.md` + `CHANGELOG_zh.md` — update invocation examples, requirements note, changelog entry

**Files to delete:**
14. All 14 .ps1 files (scripts/ + tests/) + remove them via `git rm`

### Must NOT have (guardrails)
- NEVER change the 5-phase workflow architecture (scan/approve/clean/verify/report) — only the language
- NEVER require Python 3.12+ features — 3.10+ compatibility (match statement avoidance unless guarded; `X | Y` union syntax avoided)
- NEVER add heavy deps beyond `psutil` (drive/process enumeration) and `pywin32` (Windows-only: schtasks/COM/UAC); `psutil` is cross-platform and well-maintained; `pywin32` is imported only on Windows with a `try: import` guard
- NEVER remove the dual-mode test design (pytest when installed, plain-assert fallback when not — same exit-code semantics)
- NEVER remove the quarantine-not-delete, junction-aware, skip-locked, 7-day rule, ±500MB tolerance safety invariants
- NEVER change git convention: feature branch → push → merge --no-ff main → push
- NEVER break existing agent invocation flow (`/rubbish-cleaner` slash command, SKILL.md triggers, agents/openai.yaml — only update internal command examples)

## Verification strategy
> Zero human intervention — all verification agent-executed.
- Test strategy: tests-after, pytest suites mirroring the PS behavior matrix (same 9 suites + 5 Pester Describes → 6 pytest files)
- Each ported script is acceptance-tested against its PS equivalent: same fake-tree inputs, same CSV/JSON/report outputs (byte-level comparison where deterministic)
- `compileall` + `pytest` + ruff lint in CI on all 3 OS
- Dual high-accuracy plan review (momus + Oracle) as REQUIRED baseline gate — review triggers BEFORE execution per user instruction

## Execution strategy
### Parallel execution waves
- Wave 0 (todo 1): `requirements.txt` + project structure + feature branch — BLOCKING
- Wave 1 (todos 2, 3): `platform.py` + `core.py` — SERIES (core imports platform)
- Wave 2 (todos 4, 5, 6): scanner + cleaner + report — PARALLEL (all import core; no mutual deps)
- Wave 3 (todos 7, 8, 9): install + schedule + test runner — PARALLEL (independent; runner tests existing scripts)
- Wave 4 (todos 10, 11): CI + docs update — PARALLEL (independent)
- Wave 5 (todo 12): pytest test suites (6 files) — single (tests the completed port)
- Wave 6 (todo 13): delete .ps1 files + .gitignore cleanup — single (after all tests green)
- Wave 7 (todo 14): full QA + push feature + merge --no-ff main + push main + install re-sync
- Final Verification Wave (F1-F4): all 4 in PARALLEL after todo 14

### Dependency matrix
| Todo | Depends on | Blocks |
|---|---|---|
| 1 (requirements + branch) | none | 2-14 |
| 2 (platform.py) | 1 | 3-14 |
| 3 (core.py) | 2 | 4-14 |
| 4 (scanner.py) | 3 | 11, 12, 13, 14 |
| 5 (cleaner.py) | 3 | 11, 12, 13, 14 |
| 6 (report.py) | 3 | 11, 12, 13, 14 |
| 7 (install.py) | 1 | 14 |
| 8 (schedule.py) | 2 | 12, 14 |
| 9 (test_runner.py) | 1 | 12, 14 |
| 10 (CI update) | 1 | 12, 14 |
| 11 (docs update) | 4, 5, 6 | 13, 14 |
| 12 (pytest suites) | 4, 5, 6, 7, 8, 9, 10 | 13, 14 |
| 13 (delete .ps1 files) | 11, 12 | 14 |
| 14 (git finalize) | 7, 8, 9, 10, 11, 12, 13 | F1-F4 |

## Todos
<!-- APPEND TASK BATCHES BELOW THIS LINE. -->
- [x] 1. requirements.txt + .gitignore + feature branch
  What to do / Must NOT do: Rewrite `requirements.txt` from comment-only to actual Python deps. Content EXACTLY: `# rubbish-cleaner — Python 3.10+` / `# Required:` / `psutil>=5.9` / `# Optional (Windows-only):` / `pywin32>=306  # Task Scheduler, UAC, event log (imported only on Windows)` / `# Dev:` / `pytest>=8`. Create `scripts/__init__.py` and `scripts/lib/__init__.py` (empty, for package resolution). Update `.gitignore`: add `__pycache__/` and `*.pyc` if not present. Create `feature/python-migration` branch from main (currently @ bc3401d). Commit: `chore: Python migration prerequisites`. MUST NOT remove .ps1 files yet; MUST NOT add psutil to install.ps1 (it's a source dep, not a runtime install dep — users install it via `pip install -r requirements.txt` or the CI auto-installs it).
  Parallelization: Wave 0 | Blocked by: none | Blocks: 2-14
  References: current requirements.txt (4 comment lines); .gitignore
  Acceptance: `python -c "import psutil"` succeeds (psutil installed on this machine? IF NOT, run `pip install psutil` to validate the entry); `Select-String 'psutil' requirements.txt` → 1 match; branch created; `git status --porcelain` only the two files changed; commit `chore: Python migration prerequisites`
  QA: happy — deps file + branch ready; failure — psutil not importable → note it for CI, Evidence `.omo/evidence/python-migration/task-1.txt`
  Commit: `chore: Python migration prerequisites`

- [x] 2. scripts/lib/platform.py — cross-platform detection layer
  What to do / Must NOT do: Write `scripts/lib/platform.py` (pure Python 3.10+, no Windows-only imports at top level). Module-level constants (set at import): `IS_WINDOWS = sys.platform == 'win32'`; `IS_LINUX = sys.platform == 'linux'`; `IS_MACOS = sys.platform == 'darwin'`. Functions: `get_fixed_drives()` → `['C:\\','D:\\']` on Windows via `psutil.disk_partitions()` `fstype != ''` + drive letter regex (Python: `drive + ':\\'` for A: to Z: that exist and `psutil.disk_usage(drive)` doesn't throw); on POSIX → `['/']`. `get_user_cache_dir()` → Windows `os.environ.get('LOCALAPPDATA')`; macOS `Path.home() / 'Library/Caches'`; Linux `os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache'`. `get_system_temp_dir()` → `tempfile.gettempdir()`. `get_user_documents_dir()` → Windows `Path.home() / 'Documents'`; POSIX `Path.home()`. `resolve_fixed_drive(drive: str) → dict with 'Root','FreeBytes','TotalBytes'` (Windows: `psutil.disk_usage(drive)`; POSIX: `psutil.disk_usage('/')`; validate drive exists). MUST NOT import `pywin32` or `msvcrt`; MUST NOT write to disk; MUST NOT delete anything.
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 3-14
  References: PS platform.ps1 (184 lines); `psutil`/`sys`/`tempfile`/`pathlib` docs
  Acceptance: `python -c "from scripts.lib.platform import *; assert IS_WINDOWS; d=get_fixed_drives(); assert len(d)>0; print(d[0]); assert get_user_cache_dir(); assert get_system_temp_dir()"` → prints a valid drive + no assertion errors; parse-check (compileall) passes; commit = exactly this file
  QA: happy — all functions return non-empty on this Windows machine; failure — psutil not importable → install pip dep, Evidence `.omo/evidence/python-migration/task-2.txt`
  Commit: `feat(python): platform detection layer`

- [x] 3. scripts/lib/core.py — safety primitives (delete, quarantine, CSV, junction walk, parallel)
  What to do / Must NOT do: Write `scripts/lib/core.py`. Import `platform` (`from scripts.lib.platform import IS_WINDOWS, IS_LINUX, IS_MACOS, get_fixed_drives`). Functions: `is_dir_empty(path: str) → bool` — iterative stack walk via `os.scandir`, `entry.is_symlink()` → skip without descending; on Windows ALSO check `entry.is_junction()` if available (Python ≥3.12), else use `ctypes` fallback (`GetFileAttributesW` for `FILE_ATTRIBUTE_REPARSE_POINT`; if reparse point, treat as link and skip). Returns True only when no non-link entries found. `is_junction(path: str) → bool`: Windows → try `os.path.isjunction(path)` (Python 3.12+), except `AttributeError` → use `ctypes` fallback (`GetFileAttributesW`, check `FILE_ATTRIBUTE_REPARSE_POINT`); POSIX → False (no junction concept). NOTE: `os.path.isjunction()` is 3.12+, not 3.8+; the original plan's claim was wrong — MUST use the ctypes fallback for 3.10/3.11 compatibility. `safe_remove(path: str, phase: str, csv_path: str) → str` — try `os.remove`/`os.rmdir`, map exceptions: `PermissionError`→SKIP_ACCESS_DENIED; `OSError` winerror 32/ERROR_SHARING_VIOLATION→SKIP_LOCKED; `FileNotFoundError`→SKIP_NOT_FOUND; success→OK. Append to pipe-delimited CSV. `quarantine(path: str, quarantine_dir: str, phase: str, csv_path: str) → str` — `shutil.move` with `New-Item -Force` logic (os.makedirs quarantine_dir exist_ok=True); success→QUARANTINED; OSError→MOVE_FAILED. `write_cleanup_csv(csv_path: str, row: dict)`: append `Timestamp|Phase|Action|Path|ErrorMessage|Disposition`, header once. `JUNK_DISPOSITIONS` = the exact 12-element list. `test_file_locked(path: str) → bool`: **CRITICAL — must match PS `FileShare.None` semantics.** The PS original opens the file with `FileShare.None` (exclusive, zero sharing) — if it fails, someone else holds the file. The `msvcrt.locking()` API ACQUIRES locks (opposite direction) and is incorrect for this use case. Correct approach: on Windows, use `ctypes.windll.kernel32.CreateFileW(path, GENERIC_READ, 0, None, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None)` with `dwShareMode=0` (exclusive open). If `CreateFileW` returns `INVALID_HANDLE_VALUE` → locked (True); else `CloseHandle` and return False. On non-Windows: `os.open(path, os.O_RDONLY)` + `fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)` (advisory lock probe). `parallel_for_each(items: list, func: callable, throttle: int = 4, args: tuple = ()) → list`: `concurrent.futures.ThreadPoolExecutor(max_workers=throttle)` with `as_completed`; throttle≤1 or len≤1 → sequential fallback. MUST NOT delete anything outside `safe_remove` called paths; MUST NOT import pywin32 at top level.
  Parallelization: Wave 1 | Blocked by: 2 | Blocks: 4-14
  References: PS rubbish-core.ps1 (329 lines); `os`/`shutil`/`csv`/`concurrent.futures`/`pathlib` docs
  Acceptance: `python -c "from scripts.lib.core import *; assert 'SKIP_LOCKED' in JUNK_DISPOSITIONS; print(len(JUNK_DISPOSITIONS)); print(is_dir_empty(tempfile.mkdtemp()))"` → prints 12 + True; parse-check passes; functional smoke (temp tree): create file → safe_remove → gone + CSV OK; locked file → SKIP_LOCKED; quarantine → moved; parallel_for_each 20 items throttle 2 → all processed + order preserved; `is_dir_empty` empty/with-file/only-junction → True/False/True (on Windows; junction creation may need admin — skip if fails)
  QA: happy — all primitives work; failure — any function returns wrong disposition, Evidence `.omo/evidence/python-migration/task-3.txt`
  Commit: `feat(python): safety primitives (delete, quarantine, CSV, junction walk, parallel)`

- [x] 4. scripts/scanner.py — 15-category read-only classifier + checkpoint/resume
  What to do / Must NOT do: Write `scripts/scanner.py`. Import `core` + `platform`. Define `CATEGORY_LIST` (15 categories with `id`, `risk`, `action`), `CATEGORY_RISK_MAP`, `RISK_ACTION_MAP` as Python dicts (data, ported from PS `$script:CategoryRiskMap`). Function `scan(drive: str, **kwargs) → dict` — CLI entry: validates drive, `is_user_drive = (Path.home().drive == drive.upper())` on Windows; `is_user_drive = (drive == '/')` on POSIX. Preflight: `preflight.txt` with `BASELINE_FREE_BYTES=`, `TOTAL_BYTES=`, `PROCESSES=` (psutil.process_iter). Classification loop over 15 categories (platform-gated: Windows-only cats guarded by `if IS_WINDOWS`; POSIX cats by `if not IS_WINDOWS`). Per-category: iterate candidates, apply 7-day age rule (`getmtime` vs `datetime.now() - timedelta(days=7)`), junction-safe dir walk (core's `is_dir_empty` for empty-dirs), root-suspicious logic (basename without extension NOT in top-level dirs + Program Files subdirs). Outputs: `candidates.csv` (pipe-delimited 6-col header), `scan-report.json` (per-category summary), `scan-checkpoint.json` (completedCategories/currentCategory/lastPath/totalBytesSoFar — updated per category + every 500 files). Support `-Resume` (read checkpoint, skip completed, ordinal-ignorecase skip before lastPath). Support `-Drives` (multi-drive via subprocess: `subprocess.run([sys.executable, __file__, '-Drive', d])`). Support `-Parallel` (ThreadPoolExecutor for per-drive subprocesses). MUST NOT delete anything (read-only). MUST NOT import pywin32.
  Parallelization: Wave 2 | Blocked by: 3 | Blocks: 11, 12, 13, 14 | Can parallelize with: 5, 6
  References: PS scan-drive.ps1 (1,296 lines); references/junk-taxonomy.md; references/per-app-path-map.md
  Acceptance: `python scripts/scanner.py -Drive D: -Categories root-temps,root-logs` → exit 0, `candidates.csv` with 6-col header, `scan-report.json` parses. Fake-tree functional test: small tree with 5 candidate types → expected categories found, user-file NOT classified. `-Resume` branch: checkpoint → re-run → skip. `-Drives D:` → subprocess per-drive run dir. parse-check + compileall pass.
  QA: happy — classification matches PS behavior on same fake tree (compare candidates.csv rows from both — count + categories + action mapping must match per risk); failure — missing category or wrong action, Evidence `.omo/evidence/python-migration/task-4.txt`
  Commit: `feat(python): 15-category scanner + checkpoint/resume + multi-drive`

- [x] 5. scripts/cleaner.py — approval-gated safe cleanup
  What to do / Must NOT do: Write `scripts/cleaner.py`. Import `core` + `platform`. Function `clean(drive: str, **kwargs) → dict`. Reads `candidates.csv` (pipe-delimited, headers validated). Groups by Category. Risk dispatch: ASK→requires `-Yes` else skip; ELEVATED→only on system drive + `-Yes` or `-SkipElevated`; `-SkipElevated` writes `elevated.bat` (Windows: `subprocess.run(['schtasks.exe',...]`? NO — elevated batch is inline admin commands: `net stop wuauserv`, `dism.exe /online /cleanup-image /startcomponentcleanup`, `del /f /q Windows\Temp\*`, `del /f /q Windows\Prefetch\*.pf` BUT never Layout.ini) + launch via `ctypes.windll.shell32.ShellExecuteW(None, 'runas', ...)`; POSIX→SKIP_ELEVATION_DENIED. SAFE/CAUTION rows: per-item `safe_remove` or `quarantine`; re-verify before delete (empty-dir: `is_dir_empty` gate; temp files: 7-day age re-check). `-Drives` mode: sequential subprocess per drive (NEVER parallel — safety hard constraint, prints "parallel clean is disabled for safety"). `-Resume`: reads `clean-checkpoint.json`, skips rows before `lastCleanedRowIndex`. Approval: without `-Yes` → per-category `input("Clean category X? (y/n)")`. Outputs: `cleanup-errors.csv`, `clean-checkpoint.json`, `elevated.bat` (if -SkipElevated). MUST NOT parallelize deletion; MUST NOT delete without `-Yes` or explicit approval; MUST NOT follow symlinks/junctions in deletion walk.
  Parallelization: Wave 2 | Blocked by: 3 | Blocks: 11, 12, 13, 14 | Can parallelize with: 4, 6
  References: PS clean-drive.ps1 (627 lines); elevated.ps1 generated script
  Acceptance: `python scripts/cleaner.py -Drive D: -CandidatesCsv <fixture> -Yes -QuarantineDir <temp>` on fake-tree candidates → exit 0; delete-target gone + CSV OK; locked file survives + SKIP_LOCKED; quarantine-target moved; non-empty dir survives + SKIP_NOT_EMPTY; ASK without -Yes → skipped; ELEVATED with -SkipElevated → elevated.bat written + SKIP_ELEVATION_DENIED (UAC NOT launched — test-safe); `-Drives` with -Parallel → sequentially cleaned (strict timestamps); `-Resume` → rows before index skipped
  QA: happy — all 7 gate assertions pass on fake trees; failure — safety violation (deleted without -Yes or parallel clean), Evidence `.omo/evidence/python-migration/task-5.txt`
  Commit: `feat(python): approval-gated safe cleanup + elevated batch`

- [x] 6. scripts/report.py — 8-section summary verification report
  What to do / Must NOT do: Write `scripts/report.py`. Import `core` + `platform`. Function `verify_report(drive: str, **kwargs) → dict`. Parses `preflight.txt` (regex `^([A-Za-z_]+)=(.*)$`). Reads live free space. Reads `cleanup-errors.csv` (disposition counts; `scan_only` if absent). Joins `candidates.csv` to disposition by Path. Computes per-category freed = sum SizeBytes where OK/QUARANTINED. Live assertions: QUARANTINED originals absent + copy present in quarantine dir; SKIP_NOT_EMPTY dirs survived; SKIP_LOCKED survived; OK gone. Writes `summary.md` with exactly 8 `##` sections + `±500 MB tolerance` NOTE + 8-field per-category table (Category/Risk/Action/Candidates/Files/Freed(Safe)/Freed(Quarantined)/Skipped). Multi-drive: per-drive invoke + combined `multidrive-summary.md`. MUST NOT delete anything.
  Parallelization: Wave 2 | Blocked by: 3 | Blocks: 11, 12, 13, 14 | Can parallelize with: 4, 5
  References: PS verify-report.ps1 (498 lines); .omo/evidence/d-summary.md report precedent
  Acceptance: `python scripts/report.py -Drive D: -RunDir <fixture>` with crafted preflight.txt + errors.csv + candidates.csv → exit 0; summary.md has all 8 `## ` sections; per-category table has correct freed totals + disposition counts; ±500MB note present
  QA: happy — 8-section report generated; failure — missing section or reconciliation error, Evidence `.omo/evidence/python-migration/task-6.txt`
  Commit: `feat(python): 8-section summary verification report`

- [x] 7. scripts/install.py — copy skill to 3 agent dirs
  What to do / Must NOT do: Write `scripts/install.py`. Argparse: `--target` (choices: all|claude|codex|opencode, default all). Targets dict: `Path.home() / '.claude/skills/rubbish-cleaner'`, `... / '.codex/skills/rubbish-cleaner'`, `... / '.config/opencode/skills/automation/rubbish-cleaner'`. Copy everything except `.git`, `.omo`, `.codegraph`, `__pycache__`, `.pyc`, `.ps1` files (use `shutil.copytree` with `dirs_exist_ok=True` + `ignore=shutil.ignore_patterns('.git','.omo','.codegraph','__pycache__','*.pyc','*.ps1')`). MUST NOT copy .ps1 files (they are being deprecated). MUST NOT delete existing copies (idempotent overwrite).
  Parallelization: Wave 3 | Blocked by: 1 | Blocks: 12, 14 | Can parallelize with: 8, 9
  References: PS install.ps1 (81 lines); shutil docs
  Acceptance: `python scripts/install.py` → exit 0, prints "COPIED: ..." ×3, `INSTALL: PASS`; `Test-Path` for `platform.py` + `scanner.py` in all 3 agent dirs; `Test-Path` for `.ps1` files → False (not copied — .ps1 excluded by ignore_patterns)
  QA: happy — 3 dirs populated with .py files only; failure — .ps1 leaked, Evidence `.omo/evidence/python-migration/task-7.txt`
  Commit: `feat(python): install script (copy to 3 agent dirs, exclude .ps1)`

- [x] 8. scripts/schedule.py — Task Scheduler / cron / launchd
  What to do / Must NOT do: Write `scripts/schedule.py`. Argparse subcommands: `register`, `unregister`, `list`. Each takes `--drive`, `--policy` (default safe), `--time` (default 02:00). `register`: load policy JSON from `references/policies/<policy>.json`. Build scan + clean command: `python <repo>/scripts/scanner.py -Drive X: -Categories <list> && python <repo>/scripts/cleaner.py -Drive X: -Yes`. Windows: admin check via `ctypes.windll.shell32.IsUserAnAdmin()` (in a `try: import ctypes` guarded block); register via `subprocess.run(['schtasks.exe','/Create','/SC','DAILY','/ST',time,'/TN',f'rubbish-cleaner-{drive}','/TR',command])`; event log via pywin32 `win32evtlog` (best-effort, skip on failure). Linux: `register` under root → write to `/etc/cron.d/rubbish-cleaner` (preferred, survives user deletion); `register` under non-root → append to `crontab -l` output via `subprocess` with `crontab` command. `unregister` reverses the same path (remove cron.d file if root; filter+rewrite crontab if user). macOS: write plist XML to `~/Library/LaunchAgents/com.rubbish-cleaner.plist` + `launchctl load`. `unregister`/`list`: per-platform. MUST NOT auto-register; MUST NOT require admin (exit 1 with message if non-admin on register attempt).
  Parallelization: Wave 3 | Blocked by: 2 | Blocks: 12, 14 | Can parallelize with: 7, 9
  References: PS schedule.ps1 (376 lines); schtasks/crontab/launchctl docs
  Acceptance: `python scripts/schedule.py list` → exit 0; `python scripts/schedule.py register --drive C: --policy safe` → exit 1 "Administrator privileges required" (non-admin Windows); policy JSONs parse; `python scripts/schedule.py unregister` → exit 0 (no-op ok)
  QA: happy — admin gate + no side effects; failure — schtasks syntax error on register, Evidence `.omo/evidence/python-migration/task-8.txt`
  Commit: `feat(python): Task Scheduler / cron / launchd registration`

- [x] 9. tests/test_runner.py — dual-mode test runner (compileall + pytest-or-fallback)
  What to do / Must NOT do: Write `tests/test_runner.py`. Mode 0 (always first): `compileall.compile_dir('scripts', force=True, quiet=1)` + `compileall.compile_dir('tests', force=True, quiet=1)` → exit 1 on any `SyntaxError`. NOTE: `force=True` writes `.pyc` files to `__pycache__/` — these are gitignored. Mode 1: try `import pytest; sys.exit(pytest.main(['tests','-x','--tb=short']))` except ImportError → fallback: **discover** by scanning each `tests/test_*.py` with `ast.parse` → extract top-level `def test_*` functions → **execute** each function individually via `importlib` (import the module, then call each function in a try/except; `AssertionError` = FAIL; any other exception = ERROR; print per-function `PASS/FAIL/ERROR` with trace summary; per-file summary `PASS (N passed, F failed, E errors)`; exit 0 only if ALL pass). Module-level code in test files (fixtures, imports, `setUp`) executes normally on import. Print `BRANCH: PYTEST` or `BRANCH: FALLBACK`, exit 0 only if all pass.
  Parallelization: Wave 3 | Blocked by: 1 | Blocks: 12, 14 | Can parallelize with: 7, 8
  References: PS run-tests.ps1 (78 lines); pytest API
  Acceptance: On this machine: `python tests/test_runner.py` → exit 0; prints `BRANCH: FALLBACK` (pytest likely not installed; install it in CI); compileall step prints no errors (0 .py files yet — this gate works once .py files exist); check structure (functions present)
  QA: happy — dual-mode logic structured correctly; failure — import error, Evidence `.omo/evidence/python-migration/task-9.txt`
  Commit: `test(python): dual-mode runner (compileall gate + pytest/fallback)`

- [x] 10. .github/workflows/test.yml — Python CI matrix
  What to do / Must NOT do: Rewrite `.github/workflows/test.yml` completely for Python. Matrix: `os: [ubuntu-latest, windows-latest, macos-latest]`, `python-version: ['3.10', '3.11', '3.12']`, `fail-fast: false`. Steps: checkout@v4 → setup Python via `actions/setup-python@v5` with the matrix version → `pip install -r requirements.txt pytest` → **compileall** (`python -m compileall scripts/ tests/`; exit 1 if errors) → **compatibility lint** (ruff or raw grep: forbid `match` statement, `X | Y` type union, `:=` walrus, any `3.11+`/`3.12+` specific stdlib imports) → `pytest tests/ -x --tb=short` → **integration smoke** (`python scripts/scanner.py -Drive D: -Categories root-temps,root-logs` on Windows; equivalent with `/` on POSIX). The 3.10 job validates the ctypes fallback for `isjunction()` and `CreateFileW` lock detection. MUST NOT use Install-Module or any PS command.
  Parallelization: Wave 4 | Blocked by: 1 | Blocks: 12, 14 | Can parallelize with: 11
  References: current test.yml (52 lines); pytest docs; actions/setup-python
  Acceptance: YAML parses; steps reference Python 3.11 + pip + pytest; no PowerShell steps remain; commit = exactly this file
  QA: happy — CI YAML ready for push; failure — syntax error in YAML, Evidence `.omo/evidence/python-migration/task-10.txt`
  Commit: `ci(python): Python CI matrix (compileall + pytest + lint)`

- [x] 11. SKILL.md + SKILL_zh.md + README.md + README_zh.md + CHANGELOG.md + CHANGELOG_zh.md — docs update
  What to do / Must NOT do: Update ALL 6 docs for Python migration. (a) SKILL.md + SKILL_zh.md: replace all `powershell -File scripts\*.ps1` invocation examples with `python scripts/*.py`; update platform section: "Python 3.10+" replaces "PowerShell 5.1 + pwsh 7"; note that `psutil` is required (install once via pip); elevated system UAC still Windows-only (unchanged). (b) README.md + README_zh.md: update Quick Start usage examples (`python scripts/scanner.py -Drive X:`), install instructions (`pip install -r requirements.txt` + `python scripts/install.py`), Project Structure tree (`.ps1` → `.py`). Update Limitations & Roadmap: mark "Windows-only / PowerShell 5.1" as RESOLVED (now Python + cross-platform). (c) CHANGELOG.md + CHANGELOG_zh.md: add `## [v2.0.0] - 2026-08-01` entry describing the Python migration (language port, psutil dep, pytest suite, cross-platform unified). MUST NOT change language switchers, CI badge, trigger words, or frontmatter.
  Parallelization: Wave 4 | Blocked by: 4, 5, 6 | Blocks: 13, 14 | Can parallelize with: 10 (independent — docs reference .py scripts, not test files)
  References: current SKILL.md (142 lines); README.md
  Acceptance: grep `powershell -File` → 0 in SKILL/README; grep `python scripts` → ≥5 matches across SKILL/README; `python scripts/scanner.py` example present; `## [v2.0.0]` in CHANGELOG; SKILL.md size still < 40KB; all links resolve
  QA: happy — docs reflect Python reality; failure — stale PS reference, Evidence `.omo/evidence/python-migration/task-11.txt`
  Commit: `docs(python): update docs for Python migration (v2.0.0)`

- [x] 12. tests/ — 6 pytest test files mirroring PS behavior matrix
  What to do / Must NOT do: Write 6 pytest files porting the PS test suites. Each uses `tmp_path` fixture (built-in pytest). (a) `tests/test_core.py` — `is_dir_empty`, `is_junction`, `safe_remove`, `quarantine`, `write_cleanup_csv`, `JUNK_DISPOSITIONS`, `parallel_for_each`. (b) `tests/test_scanner.py` — fake tree classification (same tree structure as PS scan.Tests.ps1): `Temp/a.tmp` >7d, `tmp/b.log`, `empty1/`, `MyApp/cache/` with files, `archive.zip` + `archive/` pair, `root-suspicious.dll`, `keep/userfile.txt`. Assert: categories present (root-temps, root-logs, duplicate-archives, empty-dirs, root-suspicious), `keep` NOT classified, action per risk mapping correct. (c) `tests/test_cleaner.py` — safe-delete gate: delete → gone + OK; locked → survives + SKIP_LOCKED; quarantine → moved; empty/non-empty dir gate; ASK without -Yes → skip. (d) `tests/test_report.py` — crafted RunDir → `summary.md` 8 sections, reconciled totals. (e) `tests/test_optimization.py` — parallel_for_each (TL2 all 10, TL1 match), checkpoint/resume, platform detection, schedule list/register gates, multi-drive (sequential + parallel). (f) `tests/test_integration.py` — end-to-end: scanner.py subprocess → cleaner.py subprocess → report.py subprocess on a small fake tree; compare outputs with expected. MUST NOT require pytest at runtime (test_runner.py's fallback path handles pytest absence); MUST NOT modify real drives (all fixtures in tmp_path).
  Parallelization: Wave 5 | Blocked by: 4, 5, 6, 7, 8, 9, 10 | Blocks: 13, 14
  References: PS unit tests (core/scan/clean/report/optimization .Tests.ps1, 1,190 lines total); sandbox harness (889 lines)
  Acceptance: Full run `python tests/test_runner.py` → exit 0; pytest (if installed) → all 6 files pass, zero failures; compileall → all .py files clean; the test file count = 6 new files (plus the runner from todo 9)
  QA: happy — all suites green; failure — any test fails → fix the port, rerun; Evidence `.omo/evidence/python-migration/task-12.txt`
  Commit: `test(python): 6 pytest suites mirroring PS behavior matrix`

- [x] 13. Delete deprecated .ps1 files + .gitignore cleanup
  What to do / Must NOT do: Run `git rm` on ALL 14 .ps1 files: `scripts/lib/rubbish-core.ps1`, `scripts/lib/platform.ps1`, `scripts/scan-drive.ps1`, `scripts/clean-drive.ps1`, `scripts/verify-report.ps1`, `scripts/install.ps1`, `scripts/schedule.ps1`, `tests/run-tests.ps1`, `tests/sandbox/run-sandbox-tests.ps1`, `tests/unit/core.Tests.ps1`, `tests/unit/scan.Tests.ps1`, `tests/unit/clean.Tests.ps1`, `tests/unit/report.Tests.ps1`, `tests/unit/optimization.Tests.ps1`. Update `references/policies/*.json` if they reference `.ps1` paths (check and fix). MUST NOT delete non-.ps1 files; MUST NOT delete `.gitignore` or any config. PRECONDITION: before deletion, run a behavioral comparison on a deterministic fake tree on **Windows only** (where PS 5.1 is native and all 14 .ps1 files are available). PS scanner vs Python scanner → diff `candidates.csv` and `scan-report.json` rows (byte-level match required); PS clean + report vs Python equivalents on same fixture → diff `cleanup-errors.csv` + `summary.md` sections. If any divergence, STOP (do not delete .ps1). On Linux/macOS, skip the PS-vs-Python comparison (PS runtime not guaranteed), and rely on the pytest suites (todo 12) + F3 end-to-end smoke for validation.
  Parallelization: Wave 6 | Blocked by: 11, 12 | Blocks: 14
  References: the current .ps1 file list; .gitignore
  Acceptance: `git ls-files '*.ps1'` → empty (0 tracked .ps1 files remain); `Test-Path` for any of the 14 file paths → False (deleted from worktree); commit `chore: remove deprecated PowerShell scripts` with exactly these 14 deletions
  QA: happy — no .ps1 files tracked; failure — stray .ps1 left behind, Evidence `.omo/evidence/python-migration/task-13.txt`
  Commit: `chore: remove deprecated PowerShell scripts`

- [x] 14. Git finalize — push feature + merge --no-ff main + push main + install re-sync
  What to do / Must NOT do: All 13 previous todos land on `feature/python-migration`. (a) Full test: `python tests/test_runner.py` → exit 0. (b) Smoke: `python scripts/scanner.py -Drive D: -Categories root-temps,root-logs` (read-only) → exit 0. (c) `git status --porcelain` clean except untracked .omo. (d) Push: `git push -u origin feature/python-migration`. Network: probe proxy `127.0.0.1:7897`; if UP default, if DOWN `-c http.proxy= -c https.proxy= -c http.version=HTTP/1.1`; retry 5x/20s. (e) Checkout main, `git merge --no-ff feature/python-migration -m "Merge feature/python-migration: Python v2.0.0 rewrite"`. (f) Push main. (g) Verify: local HEAD == remote main sha; feature branch kept; `INSTALL: PASS` via `python scripts/install.py` (re-sync 3 agent dirs — now with .py files only, no .ps1). MUST NOT force-push; MUST NOT squash; MUST NOT delete feature branch.
  Parallelization: Wave 7 | Blocked by: 7, 8, 9, 10, 11, 12, 13 | Blocks: F1-F4
  References: user git convention
  Acceptance: branch = main; merge commit on top; `git ls-remote origin main` sha == local HEAD; `python scripts/install.py` → INSTALL: PASS; `Test-Path` for .py files in 3 agent dirs; `Test-Path` for .ps1 files in agent dirs → False
  QA: happy — merge + push + install all green; failure — push rejected → pull --rebase, re-merge, replay, Evidence `.omo/evidence/python-migration/task-14.txt`
  Commit: the merge commit itself

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE.
- [x] F1. Plan compliance audit — verifier diffs executed repo against plan: all 14 todos `- [x]`; every .py file exists (platform, core, scanner, cleaner, report, install, schedule, test_runner, 6 test_*.py); zero .ps1 tracked; CI updated; docs updated; CHANGELOG has v2.0.0; git convention satisfied; worktree clean
- [x] F2. Code quality review — verifier checks: all .py files compile (`compileall` 0 errors); Python 3.10+ compatible (no `match` statement, no `X | Y` union, no walrus `:=` outside tests); `-LiteralPath`/junction safety invariants ported to Python equivalents (`is_symlink` + `is_junction` skips); `safe_remove`/`quarantine` per-item try/catch with correct disposition mapping; `parallel_for_each` uses ThreadPoolExecutor; no `eval`/`exec` on user data; `requirements.txt` has psutil + pywin32 (optional); no new heavy deps beyond those two; SKILL.md < 40KB
- [x] F3. Agent-executed end-to-end QA — verifier runs: `python tests/test_runner.py` (exit 0, all suites pass, BRANCH marker); `python scripts/install.py` (3 agent dirs with .py only); read-only smoke `python scripts/scanner.py -Drive D: -Categories root-temps,root-logs` (exit 0, outputs, delta <500MB); `python scripts/schedule.py list` (exit 0); sandbox clean-run safety verified (nothing outside tmp_path deleted); all evidence files present
- [x] F4. Scope fidelity — verifier checks: 9 new .py files + 7 test .py + 6 updated docs + 2 updated configs; 14 .ps1 removed; the 9 deduplicated requirements from v1.1.0 are all ported (cross-platform, test-drive, multithreading, progress/resume, CI, schedule, multi-drive, CHANGELOG, bilingual/hyperlinks); no scope creep (no new features beyond the language port); PS 5.1 retired; constraints honored

## Commit strategy
- Commit per todo on `feature/python-migration`; never commit directly to main
- All 13 feature commits land on the branch; todo 14 merges `--no-ff` into main + pushes both
- Push network: probe proxy `127.0.0.1:7897`; if UP use default, if DOWN `-c http.proxy= -c https.proxy= -c http.version=HTTP/1.1`
- User git identity: EntropyXi; repo remote: `github.com/EntropyXi/rubbish_cleaning_skill`

## Success criteria
- `github.com/EntropyXi/rubbish_cleaning_skill` main contains ONLY Python sources (zero .ps1 files tracked)
- `python tests/test_runner.py` exits 0 on all 3 OS via CI; dual-mode (pytest/fallback) works
- All 9 v1.1.0 features ported (classifier, parallel, checkpoint, CI, schedule, multi-drive, CHANGELOG, bilingual, cross-platform)
- `pip install -r requirements.txt && python scripts/install.py` one-command install; SKILL.md agent invocation updated to `python scripts/*.py`
- Dual high-accuracy plan review passed; F1-F4 all APPROVE after execution
