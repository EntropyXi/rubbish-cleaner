# rubbish-cleaner-v2.1-fix - Work Plan

## TL;DR (For humans)

**What you'll get:** v2.1.0 安全加固——一次性修复事故 RCA 识别的全部 9 个误删失效模式（FM1–FM9）。核心不是打补丁，而是安全模型转变：**分类即信任 → 删除前逐文件验证 + 进程感知 + 保守默认 + 真实预览确认**。

**P0 止血（直接误删路径）：** 提权批处理强制 `del *` → 改为 candidates 驱动 + `forfiles /d +7` 年龄门控 + wuauserv 重启（FM3）；POSIX 锁探测默认跳过（flock 不可靠，FM1）；隔离补锁探测（FM2）；可移动盘过滤（FM8）。

**P1 根本（机制消除）：** 进程感知门（owner 在跑 → 整类跳过，绝不 kill，FM4）；双动作执行 `clean_contents`/`remove_if_empty`（清内容保目录，FM5）；保守默认姿态 + `--dry-run` 逐文件预览（FM0）。

**P2 深化：** 分类法互斥（FM6）、路径语义验证（含数据文件 → CAUTION，FM7）、同卷隔离（FM9）。

**P3 体验：** 全 FM 回归测试 + CI 真实应用清理集成测试 + 逐文件预览确认。

**What it will NOT do:** 不杀进程、不默认 POSIX 删除、不用裸 `del *`、不删任何含文件的目录（clean_contents 保目录）、不引入新依赖、不加未列 roadmap 项（TTL/去重/HTML/config-taxonomy/WSL 留待后续）、架构不变。

**Effort:** 高 — 14 todos、6 波次（文件锁约束下按合并 worker 执行）。P0 止血 ~150 行可先发；P1 安全模型重构 ~600 行。
**Risk:** 中 — 涉及删除语义变更，全部由回归测试 + 双高精度审查 + F1-F4 把关。

---

> TL;DR (machine): High effort, Medium risk — 14 todos in 6 waves (file-lock-constrained merged workers): Wave0 FM1+FM2+FM3 (cleaner.py), FM8 (platform.py), Wave2 FM4+FM5+FM0 (cleaner+scanner), FM9, Wave4 FM6+FM7 (scanner.py), Wave5 regression+CI+dry-run, Wave6 git-finalize+docs; F1-F4. Safety-model shift: process-aware gate (never kill), dual-action (clean_contents/remove_if_empty), POSIX default-skip (--allow-posix-unlink override), candidates-driven elevated batch with forfiles age gate + wuauserv restart, mutual-exclusion taxonomy, data-signature CAUTION, same-volume quarantine, conservative default + dry-run preview. No new deps; no scope creep beyond FM1-FM9.

## Scope
### Must have
Complete safety hardening of the rubbish-cleaner skill (v2.1), fixing all 9 identified failure modes (FM1-FM9) from the incident RCA. The fix is NOT 9 patches — it is a safety-model shift: from "classification-is-trust" to "verify-before-every-deletion + process-awareness + conservative-defaults + real-preview-confirmation". All changes are Python (v2.0.0 codebase, no .ps1 remains).

**P0 — 止血 (stop-the-bleeding, ~150 lines + regression tests):**
1. FM3: elevated batch — age-gated deletion (`forfiles /d +7`) + `net start wuauserv` restart + batch generated ONLY from user-approved candidates.csv rows (no independent `del *` path)
2. FM1: POSIX lock probe — default SKIP (never unlink on POSIX unless explicit `--allow-posix-unlink`); Windows CreateFileW probe retained
3. FM2: quarantine lock-probe — quarantine goes through the same lock-probe as delete (no early-return bypass)
4. FM8: `get_fixed_drives()` — filter `"fixed" in partition.opts` (no removable/CD/network drives)

**P1 — 根本 (root fix, ~600 lines + tests):**
5. FM4: process awareness — category→owner-process map (upgrade `_WATCHED_PROCESSES`); scan-time AND clean-time process snapshots; owner-running → category skipped with clear message; optional `--close-apps` prompt (never auto-kill)
6. FM5: dual-action execution — new action enum `clean_contents` (recursively delete files inside dir, KEEP dir) vs `remove_if_empty` (only delete truly empty dir); cache categories → `clean_contents`; empty-dirs → `remove_if_empty`
7. FM0: conservative default posture — default = only age-gated temp/logs + verified empty dirs; app-owned caches opt-in only; `safe.json` redefined conservative, `aggressive.json` = old safe content; `--dry-run` produces per-file preview diff

**P2 — 深化 (deepening, ~400 lines + tests):**
8. FM6: taxonomy mutual-exclusion — ownership table (each file belongs to exactly one category); root-logs drops `*.tmp` (leaves to root-temps age-gated path); scanner claims set
9. FM7: path semantic validation — every static-map path verified at scan time (exists + content signature: cache-like = many small files; if `.db`/`.index`/user-data present → CAUTION not SAFE); mid-term registry-based discovery note
10. FM9: same-volume quarantine — quarantine dir on target drive root (`X:\.rubbish-quarantine\`), no cross-volume EXDEV; quarantine truly recoverable

**P3 — 体验 (UX, ~300 lines + tests):**
11. Layer 8: pytest regression tests for EVERY failure mode (mock psutil processes running/stopped; POSIX no-flock → SKIP; quarantine lock-probe; batch content assertions; mutual-exclusion claim tests)
12. Layer 8: CI integration test — Windows runner installs a small real app with a Cache dir, runs scan→clean, verifies app still starts + dir skeleton intact
13. Dry-run per-file preview + per-category confirmation upgrade ("will delete N files / X MB" with pattern list, not just category name)

### Must NOT have (guardrails)
- NEVER auto-kill processes (no Stop-Process / taskkill equivalent); `--close-apps` only prompts the user
- NEVER delete on POSIX without explicit `--allow-posix-unlink` (P0; advisory flock is not trustworthy)
- NEVER let the elevated batch contain a bare `del /f /q <dir>\*` — every deletion in the batch must come from candidates.csv rows + `forfiles` age gate
- NEVER remove the Windows CreateFileW(dwShareMode=0) lock probe (it is correct); POSIX path is the only one made conservative
- NEVER delete a directory that contains ANY file (clean_contents deletes files but keeps the dir; remove_if_empty only deletes verified-empty dirs)
- NEVER break the 9 v2.0.0 features (cross-platform, multi-drive, checkpoint/resume, schedule, CI, bilingual docs, etc.)
- NO new runtime dependencies beyond psutil (already present); pywin32 remains Windows-only optional
- NO scope creep: only FM1-FM9 fixes + the conservative posture; do NOT add quarantine TTL, recursive dedup, HTML reports, config-driven taxonomy, WSL enhancements (those stay on the roadmap, not this plan)
- NEVER change the 5-phase architecture (scan → approve → clean → verify → report)

## Verification strategy
> Zero human intervention — all verification agent-executed.
- Test strategy: regression tests-first for each FM (write the failing test that proves the FM, then the fix, then the test passes) + tests-after for new features
- Each todo: happy + failure QA with exact commands + evidence under `.omo/evidence/rubbish-cleaner-v2.1-fix/`
- Full suite: `python tests/test_runner.py` → 24 existing + N new all pass
- CI: 3 OS × 3.10/3.11/3.12 matrix; new CI integration test on Windows runner (real-app clean test)
- Dual high-accuracy plan review (momus + Oracle) REQUIRED baseline gate
- F1-F4 final verification wave after all todos

## Execution strategy
### Parallel execution waves
- FILE-LEVEL LOCK CONSTRAINT (applies to all waves): no two todos editing the same `.py` file may run in parallel; each worker MUST `git pull` (or fetch+merge) the latest committed state of the branch before editing, and MUST `git commit` its changes before the next wave touching the same file starts. `cleaner.py` is touched by todos 1, 2, 3, 5, 6, 7, 10, 13 — these MUST be serialized or the workers MUST be merged. `scanner.py` is touched by todos 5, 7, 8, 9 — todos 8+9 merged into ONE worker; todo 5's scanner.py portion must serialize before or merge with the 8+9 worker. IMPLEMENTATION RULE (overrides wave labels): **Wave 0 = todos 1+2+3 as ONE worker** (all three edit cleaner.py: FM3 `_elevated_batch_text`, FM1+FM2 `_process_row`); **Wave 2 = todos 5+6+7 as ONE worker** (cleaner.py `clean()` + scanner.py defaults); **Wave 3 = todo 10 alone** (cleaner.py quarantine dir); **Wave 4 = todos 8+9 as ONE worker** (scanner.py mutual-exclusion + content-signature) — todo 10 (Wave 3) parallelizes across waves with 8+9 only if the executor runs the two waves concurrently (different files: cleaner.py vs scanner.py); **Wave 5 = todo 13 alone** (cleaner.py dry-run) serialized after Wave 3 cleaner.py commits; todos 11 (tests/) and 12 (workflow) parallel. The dependency matrix below is authoritative over wave labels.
- Wave 0 (todos 1+2+3, ONE worker): FM3 batch + FM1 POSIX skip + FM2 quarantine probe — all cleaner.py edits in one atomic worker
- Wave 1 (todo 4): FM8 fixed-drives (platform.py) — can parallelize with Wave 0 (different file)
- Wave 2 (todos 5+6+7, ONE worker): FM4 process awareness + FM5 dual-action + FM0 conservative posture
- Wave 3 (todo 10): FM9 same-volume quarantine (cleaner.py)
- Wave 4 (todos 8+9, ONE worker): FM6 mutual exclusion + FM7 path validation (both scanner.py)
- Wave 5 (todos 11, 12, 13): regression tests + CI integration test + dry-run preview (todo 13 cleaner.py serialized after Wave 3)
- Wave 6 (todo 14): git finalize — push feature + merge --no-ff main + push + install re-sync + docs
- Final Verification Wave (F1-F4): all 4 in PARALLEL after todo 14

### Dependency matrix
| Todo | Depends on | Blocks |
|---|---|---|
| 1 (FM3 batch) | none | 11, 12, 14 |
| 2 (FM1 POSIX skip) | 1 | 11, 12, 14 |
| 3 (FM2 quarantine probe) | 1 | 11, 14 |
| 4 (FM8 fixed drives) | none | 11, 14 |
| 5 (FM4 process awareness) | 2, 3 | 6, 7, 11, 12, 14 |
| 6 (FM5 dual-action) | 5 | 7, 11, 12, 14 |
| 7 (FM0 conservative posture) | 5, 6 | 11, 12, 14 |
| 8 (FM6 mutual exclusion) | none | 11, 14 |
| 9 (FM7 path validation) | 8 | 11, 14 |
| 10 (FM9 same-volume quarantine) | 3 | 11, 14 |
| 11 (regression tests per FM) | 1-10 | 12, 13, 14 |
| 12 (CI integration test) | 1-10 | 14 |
| 13 (dry-run preview + confirm upgrade) | 5, 6, 7, 10 | 14 |
| 14 (git finalize + docs) | 7, 11, 12, 13 | F1-F4 |
NOTE: dependency matrix reflects FILE-LOCK ORDERING (todos 2,3 after 1; 9 after 8; all same-file edits serialized). The wave labels are logical groupings; the executor MUST follow this matrix over wave labels.

## Todos
<!-- APPEND TASK BATCHES BELOW THIS LINE. -->
- [x] 1. FM3 — elevated batch safety (age gate + wuauserv restart + candidates-driven)
  What to do / Must NOT do: Modify `scripts/cleaner.py::_elevated_batch_text` and the elevated generation path. NEW requirements: (a) the batch must be generated from the APPROVED candidates.csv rows for the `elevated-system` category — only files the scanner listed (mtime >7d) and the user approved go into the batch commands, NOT a bare `del /f /q "<win>\Temp\*"`; (b) every `del` command in the batch must include a `forfiles /d +7` age gate as defense-in-depth: `forfiles /p "<win>\Temp" /d +7 /c "cmd /c del /f /q @path"`; (c) the batch must `net start wuauserv` after the DISM/cleanup steps (restore the service); (d) the batch must end with `exit /b 0` only if all steps completed — wrap in `if errorlevel 1 exit /b 1` semantics so failures propagate. MUST NOT contain any bare wildcard delete; MUST NOT skip the service restart; MUST NOT delete Prefetch Layout.ini (already excluded). Evidence: D:\rubbish_cleaning_skill\.omo\evidence\rubbish-cleaner-v2.1-fix\task-1.txt
  Parallelization: Wave 0 (merged worker with FM1+FM2 — all three edit cleaner.py) | Blocked by: none | Blocks: 11, 12, 14
  Acceptance: grep generated batch for `del /f /q "<win>\Temp\*"` → 0 (no bare wildcard); grep for `forfiles /d +7` → present; grep for `net start wuauserv` → present; batch generation unit test: mock candidates with a 5-day-old and 10-day-old file → batch only references the 10-day-old; batch must be generated AFTER user approval — unit test: mock 3 candidates, approve 2 via mapping → batch only contains the 2 approved rows; `python tests/test_runner.py` still 24 pass
  QA: happy — batch safe + service restored; failure — batch contains bare del → REJECT, Evidence task-1.txt
  Commit: `fix(safety): FM3 elevated batch age-gate + wuauserv restart + candidates-driven`

- [x] 2. FM1 — POSIX lock probe default-skip
  What to do / Must NOT do: Modify `scripts/cleaner.py::_process_row` (FM1+FM2 merged in ONE worker with FM3 — see file-lock constraint). FM1 (POSIX): `core.test_file_locked` KEEPS its `-> bool` contract (True=locked) — do NOT change its return type. Instead, in `_process_row`, before calling the lock probe: if `not IS_WINDOWS and not allow_posix_unlink` → return disposition `SKIP_POSIX_UNSAFE` directly (never call flock for a deletion decision). Only when `allow_posix_unlink=True` does POSIX proceed to the flock probe. ADD `--allow-posix-unlink` to `cleaner.py::_build_parser()` (store_true, default False) — this is the explicit user override. FM2 (quarantine): the `if action == "quarantine"` early-return must NOT bypass the lock probe — quarantine runs the SAME Windows lock probe first (CreateFileW; POSIX default-skip per FM1); only if not locked → proceed to `core.quarantine`. Add `SKIP_POSIX_UNSAFE` to `JUNK_DISPOSITIONS` in core.py (12→13) AND update the disposition table in `references/safety-rules.md` + `references/safety-rules_zh.md` (the `## 处置枚举` section — do NOT rely on a "§2.4" number, use the section title), plus `references/junk-taxonomy.md` + `_zh` disposition mentions, plus the `README.md`/`README_zh.md` "POSIX may unlink an open file" sentence (now: POSIX defaults to skip). SCHEDULED-TASK CONSEQUENCE (document in SKILL.md + README): POSIX cron/launchd tasks do NOT pass `--allow-posix-unlink` → scheduled cleanup is scan-only on Linux/macOS (zero deletions); document explicitly as intended. MUST NOT change Windows CreateFileW behavior; MUST NOT silently delete POSIX files by default; MUST NOT break `test_file_locked` bool callers.
  Parallelization: Wave 0 (merged worker with FM2+FM3) | Blocked by: none (Wave 0 merged worker) | Blocks: 11, 12, 14
  Acceptance: `python -c "from scripts.lib.core import JUNK_DISPOSITIONS; print(len(JUNK_DISPOSITIONS))"` → 13; on this Windows machine POSIX branch not exercised but parse-check + unit test with `IS_WINDOWS=False` mock → probe returns SKIP_POSIX_UNSAFE without allow flag, proceeds with allow flag
  QA: happy — POSIX default-skip works in mocked test; failure — Windows behavior changed → REJECT, Evidence task-2.txt
  Commit: `fix(safety): FM1 POSIX unlink default-skip (advisory flock unreliable)`

- [x] 3. FM2 — quarantine lock-probe
  What to do / Must NOT do: Modify `scripts/cleaner.py::_process_row` — the `if action == "quarantine": return core.quarantine(...)` early-return (currently bypasses lock probe) must instead run the SAME lock probe as delete first (FM2 is merged with FM1 in one worker — see FM1 todo for the combined implementation). Windows CreateFileW; POSIX default-skip per FM1. Only if not locked → proceed to `core.quarantine`. MUST NOT change quarantine's move-not-delete semantics; MUST NOT quarantine a locked file (returns SKIP_LOCKED / SKIP_POSIX_UNSAFE).
  Parallelization: Wave 0 (merged with FM1+FM3, same worker — same file cleaner.py) | Blocked by: none (Wave 0 merged worker) | Blocks: 11, 14
  Acceptance: unit test: lock a temp DLL (open handle) → quarantine attempt → file NOT moved + disposition SKIP_LOCKED; unlocked → moved + QUARANTINED; `python tests/test_runner.py` 24+ pass
  QA: happy — locked DLL not quarantined; failure — locked file moved → REJECT, Evidence task-3.txt
  Commit: `fix(safety): FM2 quarantine lock-probe (no early-return bypass)`

- [x] 4. FM8 — fixed-drive filter
  What to do / Must NOT do: Modify `scripts/lib/platform.py::get_fixed_drives` — add `and "fixed" in partition.opts` to the filter (also exclude `cdrom`). Verify `resolve_fixed_drive` error message still accurate. MUST NOT scan/return removable, CD, or network drives.
  Parallelization: Wave 1 | Blocked by: none | Blocks: 11, 14 | Can parallelize with: 2, 3
  Acceptance: unit test mocking `psutil.disk_partitions` with a removable drive → get_fixed_drives excludes it; on real machine → only C: and D: returned
  QA: happy — removable excluded; failure — USB appears in fixed list → REJECT, Evidence task-4.txt
  Commit: `fix(safety): FM8 fixed-drive filter (exclude removable/cdrom)`

- [x] 5. FM4 — process awareness gate
  What to do / Must NOT do: Upgrade `scripts/scanner.py::_WATCHED_PROCESSES` from decoration to a gate. Build `CATEGORY_OWNER_PROCESSES` dict (browser-caches→[chrome,msedge]; app-caches(WeChat)→[wechat,weixin]; gpu-shader→[game processes list]; dev-caches→[pip,npm,python,node]; ide-caches→[jetbrains*,zotero,code]; crash-dumps→[] etc.). At scan time AND clean time: snapshot `psutil.process_iter()` names. Cleaner gate: **FM4 gate operates at CATEGORY granularity** — if owner process of a category is running → category skipped entirely + clear message ("检测到 Chrome 运行中，浏览器缓存清理已跳过。关闭后重跑该类别即可。"). Add `--close-apps` CLI flag: prompts user to close listed apps (never auto-kill, `input()` confirm). NOTE (FM4×FM5 interplay): FM5's `clean_contents` per-file "process gate" is a defense-in-depth DOUBLE-CHECK for cases where the category gate was bypassed (e.g., app was stopped at scan time but started by clean time — the clean-time snapshot differs from scan-time); if FM4 already gated the category, no files reach clean_contents. This is intentional two-tier design. MUST NOT kill processes; MUST NOT skip silently (message required); MUST NOT gate elevated-system (wuauserv handled separately).
  Parallelization: Wave 2 (merged worker with FM5+FM0) | Blocked by: Wave 0 (cleaner.py serialization) | Blocks: 6, 7, 11, 12, 14
  Acceptance: unit test mock psutil: chrome running → browser-caches 0 files deleted + skip message; chrome stopped → cleaned normally; `--close-apps` with running chrome → prompts, doesn't kill
  QA: happy — running-app caches protected; failure — app running but cache deleted → REJECT, Evidence task-5.txt
  Commit: `feat(safety): FM4 process-awareness gate (owner-running → category skip)`

- [x] 6. FM5 — dual-action execution (clean_contents vs remove_if_empty)
  What to do / Must NOT do: Modify the action enum in `scanner.py` (category→action) and `cleaner.py` execution. NEW actions: `clean_contents` (recursively delete FILES inside a directory, KEEP the directory itself; each file goes through the full Layer-2 verification chain — process gate, lock probe, age re-check) and `remove_if_empty` (only delete a directory if verified-empty — replaces current behavior for empty-dirs category). Cache categories (app/browser/gpu/dev/ide/crash) → `clean_contents`; empty-dirs → `remove_if_empty`. MUST NOT delete the directory in clean_contents; MUST NOT delete non-empty dirs in remove_if_empty; MUST NOT break the empty-dirs category's junction awareness.
  Parallelization: Wave 2 | Blocked by: 5 | Blocks: 7, 11, 12, 14
  Acceptance: fake Chrome-cache tree (dir with 5 files) → clean_contents deletes 5 files + dir SURVIVES + OK rows; empty dir → remove_if_empty deletes it; non-empty dir → SKIP_NOT_EMPTY; `python tests/test_runner.py` 24+ pass
  QA: happy — cache freed without deleting dir; failure — dir deleted by clean_contents → REJECT, Evidence task-6.txt
  Commit: `feat(safety): FM5 dual-action (clean_contents keeps dir, remove_if_empty only-empty)`

- [x] 7. FM0 — conservative default posture
  What to do / Must NOT do: Change default scan/clean behavior WITHOUT adding a new `--policy` CLI flag (it exists only in schedule.py; scanner.py/cleaner.py do NOT have it — do NOT invent one). Instead: (a) change `scanner.py::_applicable_categories()` default set to conservative: `{root-temps, root-logs, empty-dirs, user-temp}` (age-gated temp/logs + verified empty dirs; NO app-owned caches, NO crash-dumps — crash-dumps stays aggressive-only); app-owned caches (browser/app/gpu/dev/ide) require explicit `--categories <list>`. (b) Redefine `references/policies/safe.json` as conservative with the same 4 categories and `references/policies/aggressive.json` as the old safe content (adds browser/app/gpu/dev/ide/crash-dumps(contents)); schedule.py still passes policy files unchanged. (c) Add `--dry-run` to scanner.py + cleaner.py: produces per-file preview diff (path + size + category + decision reason) and exits without deleting (cleaner.py `clean()` with dry_run kwarg skips all mutations). MUST NOT add --policy to scanner/cleaner parsers; MUST NOT silently change explicit `-Yes` full-category behavior (power users keep control); MUST NOT remove the 7-day rule; MUST NOT change schedule.py policy references.
  Parallelization: Wave 2 (merged worker with FM5+FM0 — all three todos 5/6/7 edit cleaner.py + scanner.py) | Blocked by: 5, 6 | Blocks: 11, 12, 14
  Acceptance: default scan → candidates.csv excludes browser-caches; `--categories browser-caches` → includes; `--dry-run` → preview printed + 0 deletions + exit 0; policy JSONs parse + safe.json has no app-owned cats
  QA: happy — conservative by default, dry-run previews; failure — default includes app caches → REJECT, Evidence task-7.txt
  Commit: `feat(safety): FM0 conservative default + safe/aggressive policy redefinition + dry-run`

- [x] 8. FM6 — taxonomy mutual exclusion
  What to do / Must NOT do: Modify `scanner.py` classification (FM6 merged with FM7 in one worker): (a) root-logs drops `*.tmp` matching (leaves .tmp to root-temps which has age gate + delete-time recheck); (b) add a claims set — after a path is claimed by a category, later categories skip it. Enforce single-ownership. MUST NOT create new categories; MUST NOT change risk levels; MUST NOT break root-temps age gating.
  Parallelization: Wave 4 (merged with FM7, same worker — same file scanner.py) | Blocked by: none | Blocks: 11, 14
  Acceptance: fake tree with a root `.tmp` file → appears in EXACTLY one category (root-temps, not root-logs); `python tests/test_runner.py` 24+ pass
  QA: happy — no double-ownership; failure — file in 2 categories → REJECT, Evidence task-8.txt
  Commit: `fix(safety): FM6 taxonomy mutual-exclusion (root-logs drops .tmp, claims set)`

- [x] 9. FM7 — path semantic validation
  What to do / Must NOT do: Modify `scanner.py` per-app-path-map handling (FM6+FM7 merged in ONE worker — both edit scanner.py; see file-lock constraint): before classifying a static-map path, verify (a) path exists; (b) content signature — sample the FIRST 20 entries via `os.scandir` order (if total ≤ 20, check all); if ANY entry's suffix is in the data-file set `{.db, .sqlite, .sqlite3, .sqlitedb, .db-shm, .db-wal, .index, .dat}` (extensions matched case-insensitively) → upgrade that path to CAUTION (quarantine) with message "路径语义可疑，请人工确认"; `.json`/`.xml`/`.ini`/`.conf`/`.bak` do NOT count as data-like (only the enumerated set). MUST NOT delete a path whose content signature is data-like (CAUTION→quarantine, never delete); MUST NOT slow the scan unreasonably (sample cap 20, no full traversal); MUST NOT remove existing SAFE classifications that pass the signature.
  Parallelization: Wave 4 (merged with FM6, same worker — same file scanner.py) | Blocked by: none | Blocks: 11, 14
  Acceptance: fake "cache" dir containing a `.sqlite` file → classified CAUTION not SAFE; clean dir of small files → SAFE; `python tests/test_runner.py` 24+ pass
  QA: happy — data-like dirs protected; failure — .sqlite dir deleted as SAFE → REJECT, Evidence task-9.txt
  Commit: `feat(safety): FM7 path semantic validation (data-signature → CAUTION)`

- [x] 10. FM9 — same-volume quarantine
  What to do / Must NOT do: Modify `scripts/cleaner.py` quarantine dir logic: default quarantine dir = `<target_drive_root>\.rubbish-quarantine\` (same volume as the source), falling back to a per-run subdir. Avoids cross-volume EXDEV (which caused MOVE_FAILED silent failure). MUST NOT delete quarantine contents; MUST NOT place quarantine on a different volume from the source; MUST NOT break the quarantine recovery story (document the new location in SKILL.md + report).
  Parallelization: Wave 3 | Blocked by: 3 | Blocks: 11, 14 | Can parallelize with: 8, 9 (cross-file: cleaner.py vs scanner.py — only if the executor runs Wave 3 and Wave 4 concurrently)
  Acceptance: on a drive with a D: source, quarantine target resolves to `D:\.rubbish-quarantine\...` (Test-Path); move succeeds (no EXDEV); unit test mocking cross-device → uses same-volume path
  QA: happy — quarantine same-volume + recoverable; failure — EXDEV/MOVE_FAILED → REJECT, Evidence task-10.txt
  Commit: `fix(safety): FM9 same-volume quarantine (no EXDEV silent failure)`

- [x] 11. Regression tests for every failure mode
  What to do / Must NOT do: Extend `tests/` with regression tests covering FM1-FM9 (mock psutil processes running/stopped; POSIX no-flock → SKIP_POSIX_UNSAFE; quarantine lock-probe; batch content assertions — no bare del, forfiles present, net start present; mutual-exclusion claims; data-signature CAUTION; same-volume quarantine; fixed-drive filter). New test file(s): `tests/test_safety_fm.py` (or extend existing). TEST NAMING CONVENTION (mandatory for mechanical F1 verification): every regression test function named `test_fm{N}_{short_description}` (e.g., `test_fm4_process_gate_skips_running_app`, `test_fm5_clean_contents_keeps_directory`) — F1 greps `def test_fm` and counts unique FM IDs. Requirement: N ≥ 12 NEW TEST FUNCTIONS (countable via `pytest --collect-only`), each FM1-FM9 having ≥1 dedicated test + 3 edge tests (FM4 process-started-mid-clean, FM1 --allow-posix-unlink explicit path, FM5 clean_contents on junction dir). Each test must FAIL on the old code and PASS on the fixed code. MUST NOT test against real drives (tmp_path only); MUST NOT weaken existing assertions.
  Parallelization: Wave 5 | Blocked by: Wave 0-4 (all FM fixes committed) | Blocks: 12, 13, 14
  Acceptance: `python tests/test_runner.py` → ALL pass (24 existing + N new, N ≥ 12 regression asserts); each FM has ≥1 dedicated test
  QA: happy — all FM regressions covered + pass; failure — an FM without a test → REJECT, Evidence task-11.txt
  Commit: `test(safety): regression suite for FM1-FM9`

- [x] 12. CI integration test (real-app clean)
  What to do / Must NOT do: Add a CI integration test (Windows runner): create a small fake "app" with a Cache dir + a data file; run scanner → cleaner (clean_contents) → verify (a) cache files gone, (b) cache dir still exists, (c) the app's "data" file untouched, (d) app "still starts" (a marker script that reads the data file exits 0). Add to `.github/workflows/test.yml` as an extra step (or a separate job) on windows-latest only. MUST NOT use real apps; MUST NOT run elevated cleanup in CI; MUST NOT modify the fake app's data.
  Parallelization: Wave 5 | Blocked by: 1-10 | Blocks: 14 | Can parallelize with: 11, 13
  Acceptance: CI workflow includes the integration test; locally run the equivalent scenario → passes (cache freed, dir kept, data intact)
  QA: happy — integration test green; failure — data file deleted or dir gone → REJECT, Evidence task-12.txt
  Commit: `ci(safety): real-app clean integration test (cache freed, dir kept, data intact)`

- [x] 13. Dry-run per-file preview + confirmation upgrade
  What to do / Must NOT do: Upgrade the approval flow: (a) `--dry-run` (todo 7) output is a per-file preview diff; (b) per-category confirmation now shows "将删除 N 个文件 / X MB" plus the first K patterns/rows (not just the category name); (c) after clean, the report lists deleted + skipped + reasons. MUST NOT require confirmation for already-`-Yes` flows; MUST NOT leak full paths if the user asked for short output; MUST NOT change exit codes.
  Parallelization: Wave 5 | Blocked by: 5, 6, 7, 10 | Blocks: 14 | Can parallelize with: 11, 12
  Acceptance: dry-run on fake tree → prints N files / X MB / per-file list, 0 deletions; interactive confirm shows counts + patterns; report shows skip reasons
  QA: happy — user sees real preview before approving; failure — confirm without preview → REJECT, Evidence task-13.txt
  Commit: `feat(ux): FM13 dry-run per-file preview + per-category confirmation upgrade`

- [x] 14. Git finalize + docs
  What to do / Must NOT do: (a) All 13 todos on `feature/v2.1-safety-fix`. (b) Full test: `python tests/test_runner.py` → exit 0. (c) `git status --porcelain` clean. (d) Push feature: probe proxy `127.0.0.1:7897`; if UP default, if DOWN `-c http.proxy= -c https.proxy= -c http.version=HTTP/1.1`; retry 5x/20s. (e) Merge `--no-ff` to main + push main. (f) `python scripts/install.py` re-sync 3 agent dirs. (g) Docs: CHANGELOG.md + CHANGELOG_zh.md add `## [v2.1.0]` entry (safety hardening, FM1-FM9, conservative default, dual-action, process awareness, dry-run); SKILL.md + SKILL_zh.md + README + README_zh update safety notes (conservative default, --allow-posix-unlink, process-awareness skip behavior, same-volume quarantine location); add `references/incident-rca.md` + `_zh` (the 9-FM analysis + fixes). Commit docs separately: `docs: v2.1.0 safety hardening (FM1-FM9) + incident RCA`. MUST NOT force-push; MUST NOT squash; MUST NOT delete feature branch.
  Parallelization: Wave 6 | Blocked by: 7, 11, 12, 13 | Blocks: F1-F4
  Acceptance: main merged + pushed + remote sha match; install PASS; CHANGELOG v2.1.0 present; incident-rca.md present; SKILL safety notes updated
  QA: happy — all green; failure — push rejected → pull --rebase + re-merge, Evidence task-14.txt
  Commit: the merge + docs commits

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE.
- [x] F1. Plan compliance audit — verifier diffs repo against plan: all 14 todos `- [x]`; every FM has a fix in code (grep for forfiles/net-start-wuauserv/SKIP_POSIX_UNSAFE/clean_contents/owner-process-gate/data-signature/same-volume-quarantine/fixed-opts-filter); regression tests exist; CHANGELOG v2.1.0; incident-rca.md; git convention satisfied; worktree clean
- [x] F2. Code quality review — verifier checks: every .py compiles; 3.10+ compatible; FM1-FM9 fixes correctly implemented (POSIX default-skip logic, no bare del in batch, quarantine lock-probe not bypassed, clean_contents never deletes dir, process gate uses psutil not kill, data-signature samples not slow, same-volume quarantine path); no new deps; safety invariants intact
- [x] F3. Agent-executed end-to-end QA — verifier runs: `python tests/test_runner.py` (all pass incl. regression suite); dry-run on a fake tree (preview + 0 deletions); fake-app clean test (cache freed, dir kept, data intact); process-gate test (mock chrome running → browser-caches skipped); elevated batch generation inspection (no bare del, forfiles, net start); install re-sync; evidence files
- [x] F4. Scope fidelity — verifier checks: all 9 FM fixed (each artifact present); FM0 conservative default; no scope creep (no quarantine TTL/recursive dedup/HTML/config-taxonomy/WSL — grep absence); 5-phase architecture unchanged; no new deps; 9 v2.0.0 features intact; constraints honored

## Commit strategy
- Commit per todo on `feature/v2.1-safety-fix` with exact messages listed; never commit directly to main
- Todo 14 merges `--no-ff` into main + pushes both; docs committed before merge
- Push network: probe proxy; if UP default, if DOWN HTTP/1.1 bypass; retry 5x/20s
- User git identity: EntropyXi; repo: `github.com/EntropyXi/rubbish_cleaning_skill`

## Success criteria
- All 9 FM closed with code + regression tests; conservative default posture; process-awareness gate; dual-action execution (clean_contents/remove_if_empty)
- `python tests/test_runner.py` exits 0 (24 existing + N new ≥ 12 regression asserts); CI 3-OS green + new integration test
- main @ v2.1.0 merged + pushed; install re-synced; docs updated (CHANGELOG, SKILL, README, incident-rca bilingual)
- Dual high-accuracy plan review passed; F1-F4 all APPROVE
