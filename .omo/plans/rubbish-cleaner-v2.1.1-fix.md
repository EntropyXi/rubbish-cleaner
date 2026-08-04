# rubbish-cleaner-v2.1.1-fix - Work Plan

## TL;DR (For humans)

**What you'll get:** v2.1.1 patch 修复——C 盘实测发现的 2 个分类缺陷（36/39 候选合法，2 个缺陷无数据丢失风险但需修复）：
1. **root-logs 系统所有者排除**：`C:\DumpStack.log` 这类 SYSTEM 所有者的 Windows 系统文件不再被标为 SAFE/delete（当前仅靠清理时锁探测兜底）
2. **user-temp 安装器/卸载器豁免**：`英雄联盟卸载.exe`、`antigravity-ide-download.exe` 这类安装/卸载程序不再被标为可删（当前 7 天门控不保护它们）

**What it will NOT do:** 不改架构、不动其他类别、不加新依赖、不碰用户文档。2 个函数 + 2 条回归测试 + 文档，最小 patch。

**Effort:** 低 — 3 todos、2 波次 + CI 门禁。**Risk:** 低。

---

> TL;DR (machine): Low effort, Low risk — 3 todos: (1) root-logs SYSTEM-owner exclusion (Windows stat owner check → skip system-owned root files; POSIX uid 0 skip); (2) user-temp installer/uninstaller exemption (.exe/.msi + setup/install/unins name patterns skip); (3) regression tests + docs + git finalize + CI gate (9-job + stress green). v2.1.1 tag + release.

## Scope
### Must have
Fix the 2 classification defects found by the C-drive real-scan validation (2026-08-04, run C-20260804-190536-482080):

1. **root-logs SYSTEM-owner exclusion** (`scripts/scanner.py::_scan_root_logs`, L477-486): root-level `.log`/`*_install.log` files whose OWNER is the SYSTEM account (Windows) / uid 0 (POSIX) must NOT be added as candidates. These are OS-owned files (DumpStack.log etc.) — even though the cleaner's lock probe usually SKIP_LOCKED them in non-elevated runs, elevated runs would delete them. Add an ownership check that skips system-owned files. Windows: `GetFileAttributesW` doesn't give owner — use `os.stat` + `ctypes.windll.advapi32.GetFileSecurityW` OR simpler: `Path.stat()` + check `st_uid` (Python on Windows maps owner to st_uid via pywin32? NO — plain Python os.stat on Windows gives st_uid=0 for all). PRAGMATIC approach: on Windows, skip files with the Hidden+System attribute set (`os.stat().st_file_attributes & FILE_ATTRIBUTE_SYSTEM`) — DumpStack.log has System+Hidden attributes; on POSIX skip `st_uid == 0`. Implement `_is_system_owned(path)` helper in scanner.py: Windows → `st.st_file_attributes & 0x4 (FILE_ATTRIBUTE_SYSTEM)`; POSIX → `st.st_uid == 0`. MUST NOT over-exclude (a user's own hidden .log must still be flagged).
2. **user-temp installer/uninstaller exemption** (`scripts/scanner.py::_scan_user_temp`, L676-683): files whose name matches installer/uninstaller patterns must NOT be added as candidates even if >7 days: suffixes `{.exe, .msi, .msu, .msp, .cab}` OR name contains (case-insensitive) `setup`, `install`, `unins`, `uninstall`, `updater`. This protects `英雄联盟卸载.exe` (uninstaller), `antigravity-ide-download.exe` (installer), `vscode-inno-updater-*.log` (updater — note: .log files with "updater" in name are ALSO covered by the name-pattern check even though they're logs). MUST NOT exempt generic temp junk (`.tmp`, `.dll`, `.node`, `.bat`, `.json` stay eligible).
3. **Regression tests + docs**: add to `tests/test_safety_fm.py` (naming: `test_fm14_root_logs_system_owned_skipped`, `test_fm15_user_temp_installer_exempt` — or follow the existing test_fm{N} convention; use the next available numbers — check what's already used, fm1-fm13 + fm0 exist; use `test_fm14_*` and `test_fm15_*`): (a) mock a root-level .log with System attribute (Windows) / uid 0 (POSIX via monkeypatch of `_is_system_owned`) → NOT in candidates; (b) user-temp with `unins000.exe`/`setup-x64.exe`/`antigravity-ide-download.exe` >7 days → NOT in candidates; (c) `wctCDFA.tmp`-like temp >7 days → STILL in candidates (control). Update `CHANGELOG.md` + `CHANGELOG_zh.md` with `## [v2.1.1] - 2026-08-04` entry.

### Must NOT have (guardrails)
- NEVER change other categories (root-temps/empty-dirs/app-caches/etc. untouched)
- NEVER remove the FM6 root-logs `*.tmp` exclusion or the 7-day user-temp gate
- NEVER exempt generic temp junk from user-temp (only installer/uninstaller patterns)
- NEVER break the FM7 data-signature / FM4 process-gate / FM5 dual-action behaviors
- NO new dependencies; NO architecture changes; NO user-facing doc changes beyond CHANGELOG
- CI success gate (AGENTS.md) applies: 9-job matrix + stress job green before declaring complete

## Verification strategy
> Zero human intervention — all verification agent-executed.
- Test strategy: regression tests-first (prove the defect on old code → fix → test passes) + the C-drive real-scan revalidation after fix (re-run the scan on C: → DumpStack.log + 卸载.exe must NOT appear)
- Each todo: happy + failure QA with exact commands + evidence under `.omo/evidence/rubbish-cleaner-v2.1.1/`
- Full suite: `python tests/test_runner.py` → exit 0 (57 + new)
- CI gate: 9-job matrix + stress job green on the merge commit
- Final verification wave F1-F4 after all todos

## Execution strategy
### Parallel execution waves
- Wave 0 (todo 1): root-logs system-owner exclusion — single (scanner.py)
- Wave 1 (todo 2): user-temp installer exemption — single (scanner.py, same file — MUST serialize after todo 1; or merge both into ONE worker)
- Wave 2 (todo 3): regression tests + docs + git finalize + CI gate + tag/release — single
- Final Verification Wave (F1-F4): all 4 in PARALLEL after todo 3
- FILE-LOCK NOTE: todos 1+2 both edit `scripts/scanner.py` — dispatch as ONE merged worker (or strictly serialized)

### Dependency matrix
| Todo | Depends on | Blocks |
|---|---|---|
| 1 (root-logs system-owner) | none | 3 |
| 2 (user-temp installer) | none (same-file — merged with 1) | 3 |
| 3 (tests + docs + finalize + release) | 1, 2 | F1-F4 |

## Todos
<!-- APPEND TASK BATCHES BELOW THIS LINE. -->
- [x] 1. scanner.py: root-logs system-owner exclusion (merged worker with todo 2 — same file)
  What to do / Must NOT do: Modify `scripts/scanner.py` (merged with todo 2 in ONE worker — both edit scanner.py). Add `_is_system_owned(path) -> bool` helper near the other `_is_*` helpers with a **TWO-TIER Windows strategy (Metis Finding 1 — CRITICAL: `C:\DumpStack.log` has Attributes=Archive (0x20), NOT System (0x4); attribute-only check FAILS for the primary target)**: Tier 1 → `(os.stat(path, follow_symlinks=False).st_file_attributes & 0x4) != 0` (FILE_ATTRIBUTE_SYSTEM — catches DumpStack.log.tmp-style files with the System flag); Tier 2 (only if Tier 1 False) → `ctypes.windll.advapi32.GetNamedSecurityInfoW` requesting `OWNER_SECURITY_INFORMATION` — if it returns `ERROR_ACCESS_DENIED (5)` → treat as system-owned (the actual signal for DumpStack.log; live-verified: GetNamedSecurityInfoW on it → ERROR_ACCESS_DENIED). If it SUCCEEDS, optionally check the owner SID == NT AUTHORITY\SYSTEM (S-1-5-18) — but ERROR_ACCESS_DENIED alone is the reliable conservative signal. POSIX → `os.stat(path, follow_symlinks=False).st_uid == 0` (uid-0 = root-owned system file; note: sudo-created user files also uid 0 — acceptable conservative over-exclusion, Metis Finding 2). Handle ALL stat/ctypes errors (return False on any unexpected error — never crash the scan). `_scan_root_logs` (L477-486): after the `.log`/`*_install.log` match, add `if _is_system_owned(path): continue` — system-owned root logs skipped. MUST NOT over-exclude: a Hidden-only (0x2) attribute file with a normal ACL must still be flagged (Metis Finding 10 — add a regression fixture with st_file_attributes=0x2 + successful ACL). VERIFY: (i) `python -m compileall scripts/` → 0; (ii) `python tests/test_runner.py` → exit 0 (57, no regression); (iii) live: `python -c "import sys; sys.path.insert(0,'scripts'); from scanner import _is_system_owned; print(_is_system_owned(r'C:\DumpStack.log'))"` → **True via the ACL tier** (Metis Finding 2 — this exact check must pass); (iv) `C:\DumpStack.log` absent from a fresh dry-run candidates list; (v) live control: create `C:\rubbish_v211_user_control.log` (user-owned) → `_is_system_owned` → False AND it appears as a candidate (Metis Finding 3), then delete the control file. Evidence: `.omo/evidence/rubbish-cleaner-v2.1.1/task-1.txt`.
  Parallelization: Wave 0 (merged with todo 2) | Blocked by: none | Blocks: 3
  Acceptance: DumpStack.log no longer a candidate (live re-scan C: shows 0 root-logs candidates); **the ACL-tier path is exercised and returns True for DumpStack.log**; control user-owned `C:\rubbish_v211_user_control.log` still flagged; Hidden-only (0x2) file still flagged; 57 tests pass
  QA: happy — system-owned root log excluded via Tier-2 ACL; failure — Tier-2 returns False for DumpStack.log (ctypes signature error?) → fix the ctypes call; over-exclusion of user logs → adjust; Evidence task-1.txt
  Commit: (with todo 2, one commit) `fix(scanner): v2.1.1 root-logs system-owner exclusion + user-temp installer exemption`

- [x] 2. scanner.py: user-temp installer/uninstaller exemption (merged worker with todo 1 — same file)
  What to do / Must NOT do: Modify `scripts/scanner.py` (merged with todo 1 in ONE worker). `_scan_user_temp` (L676-683): after `_is_older_than(path, context["cutoff"])`, add an installer/uninstaller exemption: `_INSTALLER_PATTERNS = {".exe", ".msi", ".msu", ".msp", ".cab"}` suffixes OR casefolded name containing any of `{"setup", "install", "unins", "uninstall", "updater"}` → skip. **DECISION on substring over-exemption (Metis Finding 5 + Oracle Finding A — RESOLVED): use whole-word regex `\b(setup|install|unins|uninstall|updater)\b` on the casefolded full name, and ACCEPT that `install.log` IS exempted** (verified: `\binstall\b` matches `install.log` because the `.` creates a word boundary — this is CONSISTENT with the design note "installer/updater whole-word in the name ARE exempted by design"; an `install.log` in user temp is an installer artifact, not generic junk). The suffix set stays exact-match (`.exe`/`.msi`/`.msu`/`.msp`/`.cab`). `installer_data.tmp` is NOT exempted (no boundary after "install" in "installer") and `setup_x64.exe` is exempted via suffix only. Document the intent: `.log`-suffixed files with an installer/updater whole-word in the name ARE exempted by design (Metis Finding 6 — intended, updater logs). MUST NOT exempt `.tmp`/`.dll`/`.node`/`.bat`/`.json` (unless a whole-word installer keyword is in the name — `setup` alone in `setup.bat`? NO — `.bat` is not in the suffix set but the name contains a whole-word keyword → exempted. Accept this: conservative direction, consistent with the design). MUST NOT remove the 7-day gate; MUST NOT change other functions. VERIFY: (i) compileall → 0; (ii) test_runner → exit 0 (57); (iii) live: `英雄联盟卸载.exe` + `antigravity-ide-download.exe` absent from a fresh dry-run candidates; control `wctCDFA.tmp` still present; `install.log` (created in a fake temp scan) IS exempted by design (Oracle Finding A resolution — update the acceptance accordingly). Evidence: `.omo/evidence/rubbish-cleaner-v2.1.1/task-2.txt`.
  Parallelization: Wave 0 (merged with todo 1) | Blocked by: none | Blocks: 3
  Acceptance: uninstaller/installer-named files (whole-word) no longer candidates; generic .tmp still flagged; install.log exempted by design; 57 tests pass
  QA: happy — installer files exempt, junk still flagged; failure — exemption over-broad (e.g. `installer_data.tmp` wrongly exempted) → tighten regex; Evidence task-2.txt
  Commit: (with todo 1, one commit) `fix(scanner): v2.1.1 root-logs system-owner exclusion + user-temp installer exemption`

- [x] 3. Regression tests + CHANGELOG + git finalize + CI gate + v2.1.1 tag/release
  What to do / Must NOT do: (a) Add regression tests to `tests/test_safety_fm.py` — `test_fm14_root_logs_system_owned_skipped`: monkeypatch `scanner._is_system_owned` → True on a fake root .log → 0 candidates; False → 1 candidate; PLUS a fixture with `st_file_attributes=0x2` (Hidden-only, no System) + successful ACL → NOT excluded (Metis Finding 10 — the over-exclusion edge); `test_fm15_user_temp_installer_exempt`: fake Temp dir with `unins000.exe` + `setup-x64.exe` + `wctCDFA.tmp` + `install.log` all >7 days → only `wctCDFA.tmp` is a candidate; `unins000.exe`/`setup-x64.exe`/`install.log` are ALL exempted (Oracle Finding A — `install.log` exemption is intended by design; whole-word regex matches it). Each must FAIL pre-fix, PASS post-fix. (b) CHANGELOG.md + CHANGELOG_zh.md: add `## [v2.1.1] - 2026-08-04` (fix root-logs system-owner exclusion via attribute+ACL two-tier; fix user-temp installer/uninstaller exemption via whole-word patterns; refs C-drive validation run C-20260804-190536-482080). (c) Full suite: `python tests/test_runner.py` → exit 0. (d) Re-run the C: real scan (read-only dry-run): `python scripts/scanner.py -Drive C: --dry-run` → **per-category verification (Metis Finding 9 — do NOT conflate):** root-logs: `DumpStack.log` NOT a candidate; user-temp: `英雄联盟卸载.exe` + `antigravity-ide-download.exe` NOT candidates; user-temp control: `wctCDFA.tmp` (or any remaining .tmp) STILL a candidate. (e) Git: push feature branch (probe proxy; retry 5x/20s) → merge `--no-ff` main → push main. (f) CI gate: poll `gh run view` until completed → **9-job matrix green (stress job best-effort, informational only — Oracle Finding B: the stress job is declared non-gating in test.yml, do NOT block on it for a minimal patch)**; on matrix failure `--log-failed` → fix → re-push. (g) Tag + release: `git tag -a v2.1.1 <merge-sha> -m "v2.1.1: root-logs system-owner exclusion + user-temp installer exemption"`; push tag; `gh release create v2.1.1 --notes-file` (from the CHANGELOG entry). MUST NOT force-push; MUST NOT squash; MUST NOT touch other files.
  Parallelization: Wave 2 | Blocked by: 1+2 | Blocks: F1-F4
  Acceptance: 2 new regression tests pass; CHANGELOG v2.1.1 present (EN+ZH); C: re-scan shows the 2 defects gone; CI 9-job + stress green; v2.1.1 tag + release on GitHub
  QA: happy — all green; failure — CI red → fix → re-push; Evidence task-3.txt
  Commit: the merge + docs commits

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE.
- [x] F1. Plan compliance audit — verifier: todos 1-3 `- [x]`; `_is_system_owned` + `_INSTALLER_PATTERNS` present in scanner.py; regression tests present; CHANGELOG v2.1.1 (EN+ZH); v2.1.1 tag + release on GitHub; git convention satisfied; worktree clean
- [x] F2. Code quality review — verifier: `_is_system_owned` correct on Windows (attribute 0x4) + POSIX (uid 0) with OSError guard; `_INSTALLER_PATTERNS` covers exe/msi/msu/msp/cab + setup/install/unins/uninstall/updater names without over-exempting .tmp/.dll/.node/.bat/.json; FM6 .tmp exclusion + 7-day gate intact; no new deps; 3.10-compatible (no X|Y unions in new code); compileall clean
- [x] F3. Agent-executed end-to-end QA — verifier: test_runner exit 0 (57+2); the 2 new regression tests pass; C: read-only re-scan → DumpStack.log + uninstaller/installer absent from candidates, control .tmp present; CI 9-job + stress green on merge; evidence files present
- [x] F4. Scope fidelity — verifier: ONLY scanner.py's two functions + tests + CHANGELOG changed (git diff check); no other categories touched; no user-facing docs changed beyond CHANGELOG; no new deps; CI gate honored; v2.1.1 release correct

## Commit strategy
- Todo 1+2 commit on `feature/v2.1.1-fix`; todo 3 adds tests/docs then merges --no-ff to main + pushes
- Push network: probe proxy 127.0.0.1:7897; if UP default, if DOWN HTTP/1.1 bypass; retry 5x/20s
- CI gate: 9-job matrix + stress job green before declaring complete

## Success criteria
- C: real-scan validation: DumpStack.log + 英雄联盟卸载.exe + antigravity-ide-download.exe NO LONGER candidates; generic temp junk still flagged
- `python tests/test_runner.py` exit 0 (57 + 2 new regression tests); CI 9-job + stress green
- CHANGELOG v2.1.1 (EN+ZH); tag v2.1.1 + GitHub release on the merge commit
- Dual high-accuracy plan review passed; F1-F4 all APPROVE
