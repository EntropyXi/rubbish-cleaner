# rubbish-cleaner-v2.1.2-fix - Work Plan

## TL;DR (For humans)

**What you'll get:** v2.1.2 补丁——修复 CD 盘实测发现的 empty-dirs 分类缺陷：`_scan_empty_dirs` 会把**保留/系统目录**标为候选（`D:\Program Files`、`D:\Program Files (x86)`、`C:\inetpub`、`C:\XboxGames`）。虽然清理时 `remove_if_empty` + `os.rmdir` fail-safe（SKIP_LOCKED，不会真删），但把用户安装根目录/系统保留目录标成"垃圾"是错误信号，必须修复。

**What it will NOT do:** 不改其他类别；不动 remove_if_empty 的 fail-safe 机制；不加新依赖；不改用户文档（仅 CHANGELOG）。

**Effort:** 极低 — 2 todos + CI 门禁 + 发布。**Risk:** 极低。

---

> TL;DR (machine): Trivial patch — extend `_scan_empty_dirs` reserved-dir exclusion set (casefold): `program files`, `program files (x86)`, `inetpub`, `xboxgames`, `windows`, `$recycle.bin` (already), `system volume information` (already), `.claude` (already). New set: {"$recycle.bin", "system volume information", ".claude", "program files", "program files (x86)", "inetpub", "xboxgames", "windows"}. Plus 1 regression test + CHANGELOG v2.1.2 (EN+ZH) + git finalize + CI gate (9-job matrix) + tag/release.

## Scope
### Must have
1. **`_scan_empty_dirs` reserved-dir exclusion** (`scripts/scanner.py` L500): extend the existing `skipped` set from `{"$recycle.bin", "system volume information", ".claude"}` to also include (all casefolded): `program files`, `program files (x86)`, `inetpub`, `xboxgames`, `windows`. Rationale (from CD-drive validation, verifier report 2026-08-04): these are OS-reserved / default-install / app-root directories — flagging them as junk is user-hostile even though `remove_if_empty` is fail-safe. Case-insensitive matching (path.name.casefold() already used at L502).
2. **Regression test** `test_fm16_empty_dirs_skips_reserved_dirs` in `tests/test_safety_fm.py`: fake drive root with dirs `Program Files`, `Program Files (x86)`, `inetpub`, `XboxGames`, `Windows` (all empty) + a control genuinely-empty dir `junkdir` → scan → only `junkdir` is a candidate; the 5 reserved dirs are NOT. Must FAIL pre-fix, PASS post-fix.
3. **CHANGELOG.md + CHANGELOG_zh.md**: add `## [v2.1.2] - 2026-08-04` entry (empty-dirs skips reserved/system dirs: Program Files*, inetpub, XboxGames, Windows).
4. **Git finalize + CI gate + tag/release**: feature branch → push → merge --no-ff → 9-job matrix green (stress job best-effort, informational) → `git tag -a v2.1.2` + `gh release create` → tag points at the CI-green commit (the fix merge + any follow-up — same practice as v2.1.1 retag).

### Must NOT have (guardrails)
- NEVER remove the existing exclusions ($recycle.bin, System Volume Information, .claude) or the casefold matching
- NEVER change remove_if_empty / is_dir_empty / the rmdir fail-safe (it's correct defense-in-depth)
- NEVER exclude user dirs (a genuinely empty `D:\junk` must still be flagged)
- NEVER touch other categories or other scanner functions
- NO new deps; NO user-facing doc changes beyond CHANGELOG
- CI gate: 9-job matrix green before declaring complete (stress job best-effort)

## Verification strategy
> Zero human intervention — all verification agent-executed.
- Test strategy: regression test fm16 (Oracle Finding A — the plan's "test-first" wording is reconciled: the fix lands in Wave 0 and the test in Wave 1, so restate as **"regression test (design-correct; the test guards the fix from future regression)"** rather than literal test-first)
- Full suite: `python tests/test_runner.py` → exit 0 (59 + 1 new = 60)
- C: re-scan sanity: `python scripts/scanner.py -Drive C: --dry-run` → inetpub/XboxGames no longer candidates (**vacuity note, Oracle Finding B: if a dir does NOT exist on the target drive, skip that assertion — it would vacuously pass; assert absence ONLY when the dir exists pre-scan**); D: re-scan → D:\Program Files no longer a candidate (same vacuity note)
- CI gate: 9-job matrix green on the merge commit (stress best-effort)
- Final verification wave F1-F4

## Execution strategy
### Parallel execution waves
- Wave 0 (todo 1): scanner.py reserved-dir exclusion — single
- Wave 1 (todo 2): regression test + CHANGELOG + git finalize + CI gate + tag/release — single
- Final Verification Wave (F1-F4): all 4 in PARALLEL after todo 2

### Dependency matrix
| Todo | Depends on | Blocks |
|---|---|---|
| 1 (empty-dirs reserved exclusion) | none | 2 |
| 2 (tests + docs + finalize + release) | 1 | F1-F4 |

## Todos
<!-- APPEND TASK BATCHES BELOW THIS LINE. -->
- [x] 1. scanner.py: `_scan_empty_dirs` reserved-dir exclusion
  What to do / Must NOT do: Modify `scripts/scanner.py::_scan_empty_dirs` (Metis Finding 1 — the plan's L500/L502 citations are STALE; the function now starts ~L594 with the casefold check at ~L598 after v2.1.1 insertions — **use symbol-based navigation (grep `def _scan_empty_dirs` / `skipped =`), NOT line numbers**): change the `skipped` set from `{"$recycle.bin", "system volume information", ".claude"}` to `{"$recycle.bin", "system volume information", ".claude", "program files", "program files (x86)", "inetpub", "xboxgames", "windows"}` (all lowercase — the existing `path.name.casefold() in skipped` check handles case-insensitivity; `"Program Files (x86)".casefold()` → `"program files (x86)"` ✓). NOTE (Metis Finding 2, accepted): `"windows"` is slightly generic — on a non-system drive a user's empty `D:\windows` folder would be suppressed; risk negligible (conservative posture, root-level empty `windows` is near-certainly stale). MUST NOT change anything else in the function or the remove_if_empty/is_dir_empty mechanics; MUST NOT exclude generic user dirs (control `junkdir` must stay flagged). VERIFY: (i) `python -m compileall scripts/` → 0; (ii) `python tests/test_runner.py` → exit 0 (59, no regression); (iii) live D: re-scan `python scripts/scanner.py -Drive D: --dry-run` → `D:\Program Files` ABSENT from candidates, `D:\tmp`/`D:\ModelingOS` still present; (iv) live C: re-scan → `C:\inetpub` + `C:\XboxGames` ABSENT. Evidence: `.omo/evidence/rubbish-cleaner-v2.1.2/task-1.txt`.
  Parallelization: Wave 0 | Blocked by: none | Blocks: 2
  Acceptance: Program Files*/inetpub/XboxGames/Windows no longer empty-dirs candidates (live C: + D: re-scans); genuinely empty dirs still flagged; 59 tests pass
  QA: happy — reserved dirs excluded; failure — a user's empty dir wrongly excluded → adjust set (must NOT include generic names like "temp"); Evidence task-1.txt
  Commit: `fix(scanner): v2.1.2 empty-dirs skips reserved/system dirs (Program Files*, inetpub, XboxGames, Windows)`

- [x] 2. Regression test + CHANGELOG + git finalize + CI gate + v2.1.2 tag/release
  What to do / Must NOT do: (a) Add `test_fm16_empty_dirs_skips_reserved_dirs` to `tests/test_safety_fm.py` (fm16 confirmed free — fm15 is the current max, Metis Finding 4): fake drive root (tmp_path) with empty dirs `Program Files`, `Program Files (x86)`, `inetpub`, `XboxGames`, `Windows`, `junkdir` → run the empty-dirs scan logic → assert ONLY `junkdir` is a candidate. MUST FAIL pre-fix (reserved dirs appear), PASS post-fix. Run: `python -m pytest tests/test_safety_fm.py::test_fm16_empty_dirs_skips_reserved_dirs -q` → 1 passed. (b) CHANGELOG.md + CHANGELOG_zh.md: `## [v2.1.2] - 2026-08-04` entry at the top (empty-dirs skips reserved/system dirs). (c) Full suite `python tests/test_runner.py` → exit 0 (**60 = 59 + fm16 — verified accurate, Metis Finding 6**). (d) Git: commit test+docs → push feature → merge --no-ff main → push main. (e) CI gate: poll `gh run view` until completed → **9-job matrix green** (stress best-effort, informational — do NOT block on it); on failure `--log-failed` → fix → re-push. (f) Tag + release: `git tag -a v2.1.2 <merge-sha> -m "v2.1.2: empty-dirs skips reserved/system dirs"`; push tag; `gh release create v2.1.2 --notes-file` (from the CHANGELOG entry). **Practice from v2.1.1: after CI, if the tag commit's own run is red for any reason (e.g. the informational stress job), move the tag to the CI-green HEAD commit** (Metis Finding 5 — belt-and-suspenders; if the order merge→CI-green→tag is followed, no retag is needed). MUST NOT force-push branches; MUST NOT squash; MUST NOT touch other files.
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: F1-F4
  Acceptance: fm16 passes; CHANGELOG v2.1.2 (EN+ZH); C:/D: re-scans show reserved dirs gone; CI 9-job matrix green; v2.1.2 tag + release on GitHub pointing at a green commit
  QA: happy — all green; failure — CI red → fix → re-push; Evidence task-2.txt
  Commit: `test+docs: v2.1.2 regression (fm16) + CHANGELOG` then the merge commit

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE.
- [x] F1. Plan compliance audit — verifier: todos 1-2 `- [x]`; the reserved-dir set present in `_scan_empty_dirs` (grep the 5 new names); fm16 test present; CHANGELOG v2.1.2 (EN+ZH); v2.1.2 tag + release on GitHub pointing at a green commit; git convention satisfied; worktree clean
- [x] F2. Code quality review — verifier: set uses casefold matching (no case-sensitivity bug); existing exclusions intact; no over-exclusion (control junkdir still flagged in the test); remove_if_empty/is_dir_empty untouched; no new deps; compileall clean; 3.10-compatible
- [x] F3. Agent-executed end-to-end QA — verifier: test_runner exit 0 (60); fm16 passes; live C: re-scan → inetpub/XboxGames absent; live D: re-scan → Program Files absent + tmp/ModelingOS present; CI 9-job matrix green on merge; evidence files present
- [x] F4. Scope fidelity — verifier: git diff shows ONLY scanner.py (the skipped set) + test_safety_fm.py (fm16) + CHANGELOG*; no other categories/functions changed; no user docs beyond CHANGELOG; no new deps; release points at a green commit

## Commit strategy
- Todo 1 commit on `feature/v2.1.2-fix`; todo 2 adds tests/docs then merges --no-ff to main + pushes
- Push network: probe proxy 127.0.0.1:7897; if UP default, if DOWN HTTP/1.1 bypass; retry 5x/20s
- CI gate: 9-job matrix green before declaring complete (stress best-effort)

## Success criteria
- C:/D: re-scans: Program Files*, inetpub, XboxGames, Windows no longer empty-dirs candidates; genuinely empty user dirs still flagged
- `python tests/test_runner.py` exit 0 (60 = 59 + fm16); CI 9-job matrix green
- CHANGELOG v2.1.2 (EN+ZH); tag v2.1.2 + GitHub release on a green commit
- Dual high-accuracy plan review passed; F1-F4 all APPROVE
