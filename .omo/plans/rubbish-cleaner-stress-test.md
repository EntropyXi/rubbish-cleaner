# rubbish-cleaner-stress-test - Work Plan

## TL;DR (For humans)

**What you'll get:** 一套完整的压力测试套件，验证 rubbish-cleaner v2.1.0 的安全模型在极限负载下不失效。全部在专用目录 `D:\_rubbish_cleaner_stress\` 内进行——绝不触碰真实盘。

**四个测试级别：**
- **L1 单元压力**：10 万文件扫描性能基准、1000 层深嵌套不爆栈、长路径（>260/1024 字符）、4 盘并发清理无竞态、损坏符号链接/junction 循环不卡死、磁盘满优雅失败
- **L2 长跑泄漏**：10 轮扫描+清理，psutil 追踪 RSS/句柄——断言增长 ≤15%（无泄漏，含 2 轮预热）；每轮都验证 FM4 进程门控在重复下有效
- **L4 安全模糊测试（核心）**：确定性种子生成 500 个随机"世界"（随机文件树 + 随机操作序列），每个世界后断言 6 条安全不变量：非候选文件零丢失、隔离可恢复、进程持有的文件零删除、无越界（哨兵目录）、clean_contents 不删目录、FM7 数据文件不误删。任何违反 → 输出种子+操作轨迹可复现
- **L3 CI 压力 job**：独立 job（不阻塞主矩阵），ubuntu+windows，fuzz 20 轮 + 15 分钟超时

**What it will NOT do:** 不优化扫描器性能（本计划只产出基线，优化是后续独立计划）；不改用户文档；不加新依赖；不触碰真实盘；不进默认测试运行（`test_runner.py` 排除 tests/stress/，避免拖慢日常）。

**Effort:** 中 — 6 todos、4 波次 + CI 门禁。
**Risk:** 低 — 全部测试限定在专用目录 + 哨兵验证无越界；唯一真实盘操作是创建/清理 `D:\_rubbish_cleaner_stress\`。

---

> TL;DR (machine): Medium effort, Low risk — 6 todos in 4 waves: scaffold+marker (blocking), L1/L2/L4 stress suites (parallel), CI stress job, git-finalize+CI gate; F1-F4. All tests strictly in D:\_rubbish_cleaner_stress\ (sentinel assert_no_escape); L4 fuzz = 500 deterministic worlds × 6 safety post-conditions (the definitive FM1-FM9 stress proof); L2 leak ≤ 15% RSS/stable handles over 10 rounds (2 warm-up); L1 baselines (100k files/deep/long/symlink-cycle/disk-full/concurrent); CI stress job separate from the 9-job matrix. No perf optimization, no user-doc changes, no new deps.

## Scope
### Must have
A comprehensive stress-test suite for the rubbish-cleaner skill (v2.1.0, Python 3.10+, safety-hardened). All stress tests run STRICTLY inside a dedicated test root: `D:\_rubbish_cleaner_stress\` (created by the tests, never touched in real drives). The suite validates the skill's safety invariants (no auto-kill, quarantine-not-delete, POSIX default-skip, conservative default, process-awareness gate, clean_contents-keeps-dir, same-volume quarantine) under adversarial load — this is the definitive proof that the v2.1.0 FM1-FM9 fixes hold under stress.

**Directory layout** (all created by tests, all under `D:\_rubbish_cleaner_stress\`):
```
D:\_rubbish_cleaner_stress\
├── unit\        # L1: pytest stress tests (fast, deterministic, tmp under this root)
├── integration\ # L2: long-run resource-leak tests (multi-round scan+clean, psutil tracking)
└── fuzz\        # L4: safety-fuzz harness — random trees + random ops + post-conditions
```

**Test levels (all four):**
1. **L1 — Unit stress** (`tests/stress/test_stress_unit.py`, run by pytest): 100k-file tree scan performance + memory; 1000-level deep nesting no stack overflow; >260-char (Win) / >1024-char (POSIX) long paths; concurrent 4-drive scan via ThreadPoolExecutor — no races; broken symlink / junction cycle → no hang; disk-full simulation (via a monkeypatched statvfs) → graceful failure.
2. **L2 — Integration long-run** (`tests/stress/test_stress_longrun.py`, pytest, `@pytest.mark.stress` tag): 10 sequential scan+clean rounds on a growing fake tree; assert RSS + open-handle count stable (psutil) — no leak; process-running cleanup (mock Chrome) → 0 deletions + skip message every round.
3. **L4 — Safety fuzz** (`tests/stress/test_stress_fuzz.py`): THE core. N random file trees + random scan/clean operations, then post-conditions assert: (a) every non-candidate file byte-identical after clean; (b) every quarantined file recoverable (moved intact); (c) files owned by a "running" (mocked) process are never deleted; (d) no path outside the stress root is ever touched (a sentinel dir at `D:\_rubbish_cleaner_stress\__sentinel\` must remain byte-identical); (e) `clean_contents` never removes its target dir. Deterministic seed for reproducibility. Runs ~500 fuzz iterations (configurable).
4. **L3 — CI stress job** (`.github/workflows/test.yml` addition): a SEPARATE job `stress` (not in the main matrix, so it never blocks routine CI) on ubuntu-latest + windows-latest, running `python -m pytest tests/stress/ -m stress -x --tb=short` with a timeout. Uses the runner's temp dir as the stress root (not D:\ — CI has no D:).

### Must NOT have (guardrails)
- NEVER run any stress test against real drives (C:/D: user data) — the ONLY drive touched is `D:\_rubbish_cleaner_stress\` (L1/L2/L4) or the CI runner temp dir (L3); every test asserts a sentinel outside the root is untouched
- NEVER let the fuzz harness produce an unbounded tree (size cap: total files ≤ 50k, depth ≤ 20, filename length ≤ 200 — bounded so a bug can't OOM the runner)
- NEVER parallelize the fuzz harness itself (deterministic seed + single-threaded random walk for reproducibility)
- NEVER touch SKILL.md/README.md/safety-rules.md (user docs) — stress tests are dev artifacts under `tests/stress/`
- NO new runtime dependencies (psutil already present; pytest already a dev dep)
- NO scope creep: stress tests ONLY — do NOT optimize the scanner's performance in this plan (a baseline benchmark is produced; optimization is a SEPARATE follow-up plan if the baseline reveals a bottleneck)
- The stress suite must NOT be part of the default `tests/test_runner.py` run (it's slow — minutes not seconds); it runs via an explicit `python -m pytest tests/stress/ -m stress` or the CI stress job
- CI success gate (AGENTS.md convention) applies: 9-job matrix must stay green after this change

## Verification strategy
> Zero human intervention — all verification agent-executed.
- Test strategy: the stress suite IS the deliverable; each level has its own acceptance thresholds (time bounds, memory bounds, leak tolerance, fuzz pass criteria)
- Local execution (L1/L2/L4): `python -m pytest tests/stress/ -m stress -x --tb=short` → all pass; fuzz runs ≥500 iterations deterministically
- L2 leak assertion: RSS + handle-count growth over 10 rounds ≤ 15% (leak tolerance, with 2 warm-up rounds)
- L1 performance: 100k-file scan completes (baseline recorded, not a pass/fail gate unless > 10 min)
- CI: `stress` job green on ubuntu + windows (main matrix untouched, still 9 jobs green)
- Post-run cleanup: every test removes its `D:\_rubbish_cleaner_stress\<sub>` content in teardown; the root itself may remain empty

## Execution strategy
### Parallel execution waves
- Wave 0 (todo 1): `D:\_rubbish_cleaner_stress\` scaffold + `tests/stress/__init__.py` + stress pytest marker (`tests/conftest.py` `addopts`/marker registration) — BLOCKING (everything depends on the root + marker)
- Wave 1 (todos 2, 3, 4): L1 unit stress + L2 long-run + L4 fuzz harness — PARALLEL (independent files)
- Wave 2 (todo 5): CI stress job in test.yml — single
- Wave 3 (todo 6): git finalize — push feature + merge --no-ff main + push + CI gate (9-job matrix green) + stress job green
- Final Verification Wave (F1-F4): all 4 in PARALLEL after todo 6

### Dependency matrix
| Todo | Depends on | Blocks |
|---|---|---|
| 1 (scaffold + marker) | none | 2, 3, 4, 5, 6 |
| 2 (L1 unit stress) | 1 | 6 |
| 3 (L2 long-run) | 1 | 6 |
| 4 (L4 fuzz) | 1 | 6 |
| 5 (CI stress job) | 1 | 6 |
| 6 (git finalize) | 2, 3, 4, 5 | F1-F4 |

## Todos
<!-- APPEND TASK BATCHES BELOW THIS LINE. -->
- [x] 1. Stress-test scaffold: `D:\_rubbish_cleaner_stress\` + `tests/stress/` + pytest marker + EXCLUSION
  What to do / Must NOT do: (a) Create the stress root directory `D:\_rubbish_cleaner_stress\` (and subdirs `unit\`, `integration\`, `fuzz\`, `__sentinel\`) — via a fixture (env `RUBBISH_STRESS_ROOT` override; on Windows CI the env override MUST be set — GitHub Actions Windows runners have NO D: drive; default `D:\_rubbish_cleaner_stress\` only applies to the local dev machine). (b) Create `tests/stress/__init__.py` (empty). (c) Register the `stress` pytest marker AND — CRITICAL (Metis Finding 1) — **prevent pytest/CI from auto-discovering the slow stress tests**: pytest auto-recurses `tests/`, so the main 9-job CI matrix (`test.yml:84` `pytest tests/`) and `test_runner.py:104` (pytest branch) WILL collect `tests/stress/test_stress_*.py` unless excluded. **PINNED MECHANISM (Oracle Finding 2 — do NOT leave the choice open): create a NEW `tests/conftest.py` (confirmed it does not exist yet) with `collect_ignore = ["stress"]`** (pytest's native per-directory mechanism — auto-discovered, NO change needed to `test_runner.py`; the marker registers in the same file via `pytest_configure(config)`). Do NOT use pytest.ini (would require redundant test_runner.py edits). (d) `tests/stress/conftest.py`: `STRESS_ROOT` + `stress_root()` fixture resolving to env `RUBBISH_STRESS_ROOT` else `D:\_rubbish_cleaner_stress\` (Windows local) else `tempfile.gettempdir()/rubbish-stress`; creates the root + `__sentinel\guard.txt`. (e) `assert_no_escape()` fixture: snapshots `__sentinel\guard.txt` content + the stress-root's own subtree listing BEFORE each test, asserts identical AFTER. MUST NOT snapshot `D:\_rubbish_cleaner_stress\`'s PARENT (the real `D:\`) — user file churn on D: would false-positive; the sentinel covers the root subtree + guard file only. MUST NOT create the root at import time; MUST NOT touch any real drive path.
  Parallelization: Wave 0 | Blocked by: none | Blocks: 2-6
  Acceptance: `python -c "from tests.stress.conftest import STRESS_ROOT; print(STRESS_ROOT)"` resolves correctly; `pytest --collect-only tests/ -q` does NOT collect any tests/stress test (exclusion works); `python -m pytest tests/stress/ -m stress --collect-only` collects them (marker works); fixture imports cleanly: `python -c "from tests.stress.conftest import assert_no_escape; print('FIXTURE_OK')"` → prints FIXTURE_OK (Oracle Finding 4 — NOT a "sentinel test passes" dead criterion; todo 1 creates no test files yet)
  QA: happy — scaffold + exclusion + marker + fixture-import work; failure — `pytest tests/` collects stress tests → exclusion broken → fix; Evidence `D:\rubbish_cleaning_skill\.omo\evidence\rubbish-cleaner-stress-test\task-1.txt`
  Commit: `test(stress): scaffold stress root + marker + exclusion (conftest collect_ignore)`

- [x] 2. L1 — unit stress suite (`tests/stress/test_stress_unit.py`)
  What to do / Must NOT do: Write `tests/stress/test_stress_unit.py` with `@pytest.mark.stress` on every test, all using the `stress_root` fixture under `D:\_rubbish_cleaner_stress\unit\`:
  (a) `test_scan_100k_files` — generate a tree with files (DEFAULT 50,000 — env `STRESS_100K_COUNT` overrides to 100,000 for a one-off full benchmark; 2KB each via `os.write`; ~100MB at 50k) with a **generation-time guard**: `time.monotonic()` start; if generation exceeds 240s, abort the test with a clear "generation too slow on this machine — run with STRESS_100K_COUNT=50000 or a faster disk" message (Metis Finding 3: file generation is unbounded without a cap). Then run the REAL `scanner.scan()` on it with `-Categories root-temps`, assert it completes; record wall-time (scan baseline only, not a pass/fail gate unless > 10 min). Teardown removes the tree (with its own time budget).
  (b) `test_scan_deep_nesting` — 1000-level deep directory chain (path length will exceed POSIX PATH_MAX — on POSIX the deep chain is capped at the OS limit and the test asserts the scanner does NOT crash/loop, it gracefully stops; on Windows 1000 levels works), run scanner, assert no RecursionError / hang.
  (c) `test_scan_long_paths` — filenames/dirs crafted to ~270 chars on Windows / ~1050 on POSIX (just under limits), scanner handles them (no crash, correct candidates).
  (d) `test_clean_concurrent_drives` — two fake "drives" (subdirs under the stress root) with identical junk, run `cleaner.clean()` via two threads (ThreadPoolExecutor) concurrently, assert both complete with correct dispositions and NO cross-talk (each drive's files cleaned independently, no shared-state corruption).
  (e) `test_broken_symlink_cycle` — on POSIX create a symlink loop (a→b→a); on Windows create a junction cycle if possible (else skip with reason); run scanner's junction-aware walk, assert it terminates (no infinite loop) and the cycle is not followed.
  (f) `test_disk_full_graceful` — monkeypatch `os.statvfs` (or the free-space check the scanner uses) to return 0 free → run scanner, assert it fails GRACEFULLY (error message, exit 1, no crash, no partial corrupt output).
  MUST NOT create files outside `D:\_rubbish_cleaner_stress\unit\`; MUST clean up the 100k tree in teardown; MUST use the real scanner/cleaner entry points (not mocks) for all but the disk-full case.
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 6 | Can parallelize with: 3, 4
  Acceptance: `python -m pytest tests/stress/test_stress_unit.py -m stress -x --tb=short` → all pass (each test asserts its specific condition; the 100k test records baseline); sentinel `assert_no_escape` passes
  QA: happy — all 6 unit-stress tests pass within bounds; failure — hang on symlink cycle → fix walk; RecursionError on deep → fix; Evidence `D:\rubbish_cleaning_skill\.omo\evidence\rubbish-cleaner-stress-test\task-2.txt`
  Commit: `test(stress): L1 unit stress suite (100k files, deep nesting, long paths, concurrency, symlink cycles, disk-full)`

- [x] 3. L2 — integration long-run resource-leak suite (`tests/stress/test_stress_longrun.py`)
  What to do / Must NOT do: Write `tests/stress/test_stress_longrun.py` with `@pytest.mark.stress`:
  (a) `test_ten_rounds_no_leak` — create a growing fake tree (start 5k files, +500 files per round) under `D:\_rubbish_cleaner_stress\integration\`; **2 WARM-UP ROUNDS FIRST** (run scan+clean without measuring — lets Python GC/import caches settle; Oracle Finding 3), then for each of 10 measured rounds: snapshot `psutil.Process().memory_info().rss` + open-handle count via **`psutil.Process().num_fds()`** (cross-platform Linux/macOS/Windows — Metis Finding 7) → run REAL scanner.scan + cleaner.clean on it → snapshot again; assert growth over 10 rounds **≤ 15% of baseline** (relaxed from 5% — psutil RSS sampling noise + GC timing makes 5% flaky in CI; Oracle Finding 3) and handle count stable (≤ 50 fds delta).
  (b) `test_rounds_with_running_app` — each round: mock `psutil.process_iter` to return a chrome-like process, run cleaner with the browser-caches category → assert 0 files deleted + skip message EVERY round (the FM4 process gate holds under repetition).
  MUST NOT create files outside the integration subdir; MUST close all file handles opened during the scan/clean (assert no handle leak as part of (a)); MUST use the real scanner/cleaner entry points.
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 6 | Can parallelize with: 2, 4
  Acceptance: `python -m pytest tests/stress/test_stress_longrun.py -m stress -x --tb=short` → all pass; RSS growth ≤ 15%; handles stable
  QA: happy — no leak over 10 rounds; failure — RSS grows > 15% → leak in scanner/cleaner → fix + re-run; Evidence `D:\rubbish_cleaning_skill\.omo\evidence\rubbish-cleaner-stress-test\task-3.txt`
  Commit: `test(stress): L2 long-run resource-leak suite (10 rounds, process-gate repeatability)`

- [x] 4. L4 — safety-fuzz harness (`tests/stress/test_stress_fuzz.py`) — THE CORE
  What to do / Must NOT do: Write `tests/stress/test_stress_fuzz.py` with `@pytest.mark.stress`. THE definitive safety proof. Design:
  - A deterministic PRNG (seeded, default `seed=42`, overridable via env `FUZZ_SEED`) generating random "worlds": N=500 random file trees under `D:\_rubbish_cleaner_stress\fuzz\<iter>\` with: 50-300 files/dirs, random names (incl. unicode + spaces + dots + long names ≤ 200 chars), random depths (≤ 20), random content (deterministic bytes from the seed), plus a subset flagged as "cache-like" (dirs named Cache/cache/temp/Temp with small files) and a subset flagged as "data-like" (containing .db/.sqlite/.index files — must trigger FM7 CAUTION).
  - **Wall-clock guard (Metis Finding 4):** a `time.monotonic()` budget per iteration (default 30s) and a total budget for the run (default 25 min at 500 iters); exceeding either aborts with a clear message + the seed so it's reproducible. Document expected runtime: "~5-20 min at default 500 iterations" (local); CI runs FUZZ_ITERS=20.
  - **Process-ownership mapping (Oracle Finding 1 — the fuzz CANNOT predict categories from dir names alone):** the fuzz MUST run the REAL scanner on each world FIRST, inspect the returned candidate list (`context["rows"]` — each row has `category`, `path`, `risk`), identify candidates whose `category` field has entries in `_CATEGORY_OWNER_PROCESSES`, then randomly select a subset of THOSE candidates to mark as "process-owned" — mocking `psutil.process_iter` to return the corresponding REAL owner-process stems (e.g. browser-caches→chrome/msedge; app-caches→wechat/weixin; dev-caches→pip/npm/python/node). Do NOT mark a dir as process-owned by its generated name alone (a `Cache` dir at the root may be `root-temps` which has NO owners — the gate would never fire and the invariant would vacuously pass).
  - **Operation tracking (Oracle Finding 5):** the fuzz MUST track the operation type (scan/clean/clean_contents/remove_if_empty) AND its target for each iteration, so post-condition (e) is only asserted when `clean_contents` was actually called in that iteration.
  - **FM7 risk-field access (Oracle Finding 6):** post-condition (f) MUST read the scanner's returned candidate list (`context["rows"]` `risk` field) and verify any candidate whose `path` is data-like (contains `.db`/`.sqlite`/`.index`) has `risk == "CAUTION"`, never `"SAFE"`.
  - Random operation sequence per world (10-30 ops, PRNG-chosen): scan with random -Categories, clean with random approved categories (+ optional `allow_posix_unlink`), quarantine random candidates, etc. — all through the REAL scanner/cleaner entry points with mocked `psutil.process_iter` where a random subset of the tree's dirs is treated as "owned by a running process".
  - POST-CONDITIONS (the invariants, asserted after EVERY world):
    (a) every file NOT in the candidate set is byte-identical to its pre-run state (snapshot before);
    (b) every QUARANTINED file exists intact in the quarantine dir (recoverable);
    (c) files under a "running-process-owned" dir are NEVER deleted;
    (d) NOTHING outside `D:\_rubbish_cleaner_stress\fuzz\` changed (sentinel `assert_no_escape`);
    (e) `clean_contents` never removed its target dir (dir still exists after its files were cleaned);
    (f) FM7: any dir containing a data-like file is CAUTION/quarantined, never SAFE-deleted.
  - On ANY violation: report the seed + iteration + the operation trace that caused it (reproducible).
  - Bounds (MUST): total files per world ≤ 300, total bytes per world ≤ 5MB, depth ≤ 20, filename ≤ 200 chars, 500 iterations default (env `FUZZ_ITERS` to shrink for CI).
  - Cleanup per iteration (always, even on failure — use try/finally).
  MUST NOT use mocks for the scanner/cleaner core (only psutil.process_iter is mocked); MUST be deterministic (same seed → same result); MUST NOT unbounded the tree.
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 6 | Can parallelize with: 2, 3
  Acceptance: `python -m pytest tests/stress/test_stress_fuzz.py -m stress -x --tb=short` (with FUZZ_ITERS default 500) → all 500 iterations pass with all 6 post-conditions; also run with `FUZZ_ITERS=20` for a quick CI sanity
  QA: happy — 500/500 worlds satisfy all invariants; failure — any invariant broken → reproduce with the reported seed → fix the violating code (scanner/cleaner) → re-run; Evidence `D:\rubbish_cleaning_skill\.omo\evidence\rubbish-cleaner-stress-test\task-4.txt`
  Commit: `test(stress): L4 safety-fuzz harness (deterministic random worlds + 6 post-conditions)`

- [x] 5. CI stress job in `.github/workflows/test.yml`
  What to do / Must NOT do: ADD a SEPARATE job `stress` to `test.yml` (NOT part of the main matrix — it must never block routine CI): `runs-on: [ubuntu-latest, windows-latest]` (2 OS; mac excluded to keep cost down), steps: checkout → setup-python 3.11 → pip install deps → **set `env: RUBBISH_STRESS_ROOT: ${{ runner.temp }}\rubbish-stress` for the whole job** (Metis Finding 2 — Windows CI runners have NO D: drive; the fixture defaults to `D:\_rubbish_cleaner_stress\` locally only, the env override redirects CI to `C:\Users\...\Temp`) → `python -m pytest tests/stress/ -m stress -x --tb=short` with env `FUZZ_ITERS=20` and a job-level `timeout-minutes: 15`. MUST NOT add the stress job to the main matrix; MUST NOT make it a required check (informational for perf/leak trends); MUST keep the main 9-job matrix green (the todo-1 exclusion ensures main CI never runs stress tests).
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 6
  Acceptance: test.yml parses; `stress` job present with 2-OS runs-on + `RUBBISH_STRESS_ROOT: ${{ runner.temp }}\rubbish-stress` env + FUZZ_ITERS=20 + timeout-minutes 15; main matrix untouched (9 jobs still listed, still `pytest tests/` — stress excluded via `tests/conftest.py` `collect_ignore` from todo 1)
  QA: happy — YAML valid; failure — YAML breaks main workflow → fix, Evidence `D:\rubbish_cleaning_skill\.omo\evidence\rubbish-cleaner-stress-test\task-5.txt`
  Commit: `ci(stress): separate stress job (2 OS, runner.temp stress root, fuzz 20 iters, 15-min timeout)`

- [x] 6. Git finalize + CI gate
  What to do / Must NOT do: (a) All todos 1-5 on `feature/stress-test`. (b) Local verification: `python tests/test_runner.py` → exit 0 (57 existing — stress files excluded by glob); `python -m pytest tests/stress/ -m stress -x --tb=short` with FUZZ_ITERS=20 → all pass. (c) `git status --porcelain` clean. (d) Push feature: probe proxy `127.0.0.1:7897`; if UP default, if DOWN `-c http.proxy= -c https.proxy= -c http.version=HTTP/1.1`; retry 5x/20s. (e) Merge `--no-ff feature/stress-test -m "Merge feature/stress-test: stress test suite (L1/L2/L4 + CI job)"`; push main. (f) **CI GATE (AGENTS.md mandatory):** `gh run list` → find the push's run; poll `gh run view <id>` until completed; the 9-job matrix MUST be green AND the `stress` job must be green (or `success` — it's informational, but should not be red). If the stress job fails: `gh run view <id> --log-failed` → fix → re-push. MUST NOT force-push; MUST NOT squash; MUST NOT delete feature branch; MUST NOT commit .omo docs.
  Parallelization: Wave 3 | Blocked by: 2, 3, 4, 5 | Blocks: F1-F4
  Acceptance: main merged + pushed; 9-job matrix green; stress job green; worktree clean; feature kept
  QA: happy — all green; failure — stress job red → fix → re-push, Evidence `D:\rubbish_cleaning_skill\.omo\evidence\rubbish-cleaner-stress-test\task-6.txt`
  Commit: the merge commit itself

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE.
- [x] F1. Plan compliance audit — verifier diffs repo against plan: all 6 todos `- [x]`; tests/stress/ has test_stress_unit.py + test_stress_longrun.py + test_stress_fuzz.py + conftest.py + __init__.py; stress marker registered; test.yml has the `stress` job; the stress root is NEVER a real drive path (grep — only `D:\_rubbish_cleaner_stress\` + env override + tempfile); commit chain + git convention satisfied; worktree clean
- [x] F2. Code quality review — verifier checks: stress tests compile; no real-drive paths hardcoded except the sanctioned stress root; fuzz is deterministic (single seed, no time-based randomness); bounds enforced (file/depth/byte caps); sentinel assert_no_escape present in every test module; no new deps; test_runner.py glob still excludes tests/stress/
- [x] F3. Agent-executed end-to-end QA — verifier runs: `python tests/test_runner.py` (exit 0, 57, stress excluded); `python -m pytest tests/stress/test_stress_unit.py -m stress` (all pass); `python -m pytest tests/stress/test_stress_longrun.py -m stress` (all pass, leak ≤ 15%); `python -m pytest tests/stress/test_stress_fuzz.py -m stress` with FUZZ_ITERS=20 (20/20 worlds pass); confirm `D:\_rubbish_cleaner_stress\` sentinel intact + no files escaped; CI stress job green
- [x] F4. Scope fidelity — verifier checks: all 4 stress levels delivered (L1/L2/L4 local + L3 CI); no scope creep (no scanner performance optimization, no user-doc changes, no new deps); all files under tests/stress/ or the CI job; safety invariants are what's being tested (not weakened); CI gate convention honored

## Commit strategy
- Commit per todo on `feature/stress-test` with exact messages listed; never commit directly to main
- Todo 6 merges `--no-ff` into main + pushes both
- Push network: probe proxy 127.0.0.1:7897; if UP default, if DOWN HTTP/1.1 bypass; retry 5x/20s
- CI gate (AGENTS.md): 9-job matrix must be green before declaring complete; stress job green too

## Success criteria
- Stress suite delivered: tests/stress/{test_stress_unit,test_stress_longrun,test_stress_fuzz}.py + conftest + marker + CI stress job
- All stress tests pass locally (L1/L2/L4, fuzz 500 iters deterministic) and in CI (stress job, fuzz 20 iters, 15-min timeout)
- Fuzz harness proves the 6 safety invariants hold under 500 random adversarial worlds (no auto-kill violation, quarantine recoverable, process-gate holds, no escape, clean_contents keeps dir, FM7 data-signature honored)
- L2 proves no resource leak over 10 rounds (RSS ≤ 15% growth, handles stable)
- L1 establishes performance/robustness baselines (100k files, deep nesting, long paths, symlink cycles, disk-full, concurrent drives)
- 9-job CI matrix stays green; stress job green; git per convention; worktree clean
- Dual high-accuracy plan review passed; F1-F4 all APPROVE
