# d-drive-cleanup - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->

**What you'll get:** 清理 D 盘约 15-20GB：删掉出厂驱动安装包（保留保修卡文件夹）、运行 conda 缓存清理、删掉 6 个解压后多余的压缩包、Steam 已卸载游戏的空文件夹和 WeGame 残留、微信缓存；`dinput8.dll` 和 `sdhdship.exe` 会被移到隔离文件夹（随时可恢复），绝不删除。

**Why this approach:** 所有目标都是"重复件、缓存、安装残留"——删除后不影响任何已装软件和你的数据。你已确认的边界（保留启动器、保留游戏、保留 conda 环境、保留微信聊天记录、保留保修卡）全部写死在计划里作为护栏，执行时逐项验证。

**What it will NOT do:** 不碰任何游戏（英雄联盟 43GB、Steam 游戏、Apex 97GB、MDPro3）、conda 环境、微信聊天记录、百度网盘下载、CRYSTALiA 压缩包、三个游戏启动器、学习资料和符号链接。D 盘回收站（0.1MB）也不动。

**Effort:** Short — 5 个任务，4 个并行清理 + 汇总报告
**Risk:** Low — 唯一"有风险"的两个文件（可能被游戏使用的破解补丁）是隔离而非删除；其余全是缓存/残留
**Decisions to sanity-check:** ① dinput8.dll/sdhdship.exe 隔离而非删除（我自作主张的保守处理，可随时移回）② Driver 删驱动部分保留保修卡 ③ conda clean 后新装包需重新下载

Your next move: 批准后执行（本会话直接跑，或 `/start-work d-drive-cleanup`）。Full execution detail follows below.

---

> TL;DR (machine): Short effort, Low risk — 6 sequential/parallel cleanup todos, PowerShell -LiteralPath + skip-on-lock + CSV error log, ~15-20GB reclaim on D:, no git commit (N/A)

## Scope
### Must have
- Delete D:\Driver contents EXCEPT D:\Driver\19_电子信息保修卡 (keep the warranty folder intact with all its files); delete Auto_Install_Driver.bat, Ver.txt, and all numbered driver subdirs (1_Chipset through 20_DouDou) — ~7GB
- Run `conda clean --all -y` via D:\anaconda3\Scripts\conda.exe (reclaims tarballs, unused extracted packages, index cache, logs from the 16.7GB pkgs store; existing envs in D:\anaconda3\envs untouched) — 5-10GB
- Delete the 6 root-level archive/extracted duplicates (archives ONLY, keep extracted folders): D:\MapInfo Professional 10.0 汉化破解版.zip, D:\clash.verge_64.0525.zip, D:\sakura-v0.9.8-windows-x64.zip, D:\EPA.zip, D:\UsbEAm Hosts Editor v3.63.zip, D:\2016-2025年高数AII期末试题.rar — ~646MB
- Delete verified-empty Steam leftover dirs under D:\SteamLibrary\steamapps\common (each must have 0 files and 0 subdirs before deletion; never touch installed games or appmanifest files) — ~0.8GB
- Delete WeGame residue: D:\Wegame\WeGameInstaller, D:\Wegame\英雄联盟(26)\tiny_cache, contents of D:\Wegame\英雄联盟(26)\Game\Logs, and the empty mojibake dir D:\Wegame\鑻遍泟鑱旂敾(26) — ~564MB; KEEP the League install (Game 34.2GB, LeagueClient, Cross, Riot Client, Launcher)
- Delete WeChat cache dirs only: every `cache` subdir recursively under D:\WeiXin\xwechat_files (found via Get-ChildItem -Recurse -Directory -Filter cache), NOT msg/file/contact data — ~346MB
- Quarantine (MOVE, never delete) D:\dinput8.dll and D:\sdhdship.exe to C:\Users\entropy\Desktop\.omo\quarantine\d\ (create dir first) — ~36MB
- Delete D:\mapinfo_install.log, D:\mapinfo_install2.log, and verified-empty top-level dirs: D:\新建文件夹 (2), D:\Tencent Games, D:\leidian, D:\WSL (if Ubuntu inside is empty), D:\GameVideos, D:\_original_doc_backup (each must be verified empty first; skip any that contain files)
- Produce .omo/evidence/d-summary.md with per-task freed space + error CSV

### Must NOT have (guardrails)
- NEVER delete: CRYSTALiA 7z (D:\Game\PC[CRYSTALiA]次元错位恋人{A25922C8-2EC5-4D92-9AE3-73A2C0D4665B}.7z — user kept), all 3 launchers (Epic Games, Battle.net, Ubisoft Game Launcher), BaiduNetdiskDownload, BaiduNetdisk app
- NEVER delete: installed games (League of Legends, all Steam installed games incl. workshop 2.7GB, Apex 97GB, MDPro3, Hypergryph Launcher, CRYSTALiA extracted folder, MuMu vms 33.9GB, nx_device)
- NEVER delete: D:\anaconda3\envs (19.4GB user environments) or D:\anaconda3\Lib; conda clean --all ONLY
- NEVER delete: WeChat user data (msg/, file/, contact/ dirs in xwechat_files), only `cache` dirs
- NEVER delete: D:\Driver\19_电子信息保修卡 (warranty) or anything outside D:\Driver\* listed targets
- NEVER delete: DimensionToTsuLovers symlink (points into CRYSTALiA game), System Volume Information, $RECYCLE.BIN, .claude, user docs/study files, D:\Game\PC[CRYSTALiA]... folder
- NEVER permanently delete quarantined DLL/exe (move only)
- NEVER use -Path with wildcards; ALWAYS -LiteralPath; NEVER follow junctions — Get-ChildItem -Recurse FOLLOWS NTFS junctions in PowerShell 5.1, so empty-dir verification MUST use a junction-aware function (see Task 2e/4a spec), never bare `-Recurse` for verification; NO cleanmgr; NO admin elevation needed (all targets are user-writable on D:) — if any target gives access-denied, log SKIP_ACCESS_DENIED and continue
- conda clean MUST be run with -y (non-interactive) and MUST NOT use --all including envs removal flags beyond default (plain `clean --all -y` only)

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: none (system cleanup) + agent-executed QA = file-existence assertions + free-space delta + error CSV
- Evidence: .omo/evidence/d-task-<N>-d-drive-cleanup.txt; error log .omo/evidence/d-cleanup-errors.csv (columns: Timestamp,Wave,Action,Path,ErrorMessage,Disposition; Disposition in {OK, SKIP_LOCKED, SKIP_ACCESS_DENIED, SKIP_NOT_FOUND, SKIP_NOT_EMPTY, SKIP_JUNCTION, SKIP_WSL_REGISTERED})
- Every Remove-Item/Move-Item wrapped in try/catch writing to the CSV; assert-before-assert-after per task
- Free-space baseline: `(Get-Volume -DriveLetter D).SizeRemaining` recorded at task 1 and re-read after each task; final report sums deltas; tolerance ±500MB

## Execution strategy
### Parallel execution waves
- Wave 0 (task 1): pre-flight baseline — single, blocking
- Wave 1 (tasks 2-5): four independent cleanup lanes, run in parallel (no shared targets)
- Wave 2 (task 6): post-run verification + summary — after all

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 (baseline) | none | 2-5 | none |
| 2 (driver+root misc) | 1 | 6 | 3,4,5 |
| 3 (conda clean) | 1 | 6 | 2,4,5 |
| 4 (Steam+WeGame) | 1 | 6 | 2,3,5 |
| 5 (WeChat cache) | 1 | 6 | 2,3,4 |
| 6 (summary) | 2-5 | F1-F4 | none |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE - never rewrite the headers above. -->
- [ ] 1. Pre-flight baseline D: + conda availability + installed-game inventory check
  What to do / Must NOT do: Record `(Get-Volume -DriveLetter D).SizeRemaining` and .Size into .omo/evidence/d-task-1-d-drive-cleanup.txt. Check conda: Test-Path D:\anaconda3\Scripts\conda.exe -> record True/False. Check WeChat/Steam/WeGame processes running: `Get-Process WeChat,WeChatApp,WeChatStore,Weixin,steam,WeGame -ErrorAction SilentlyContinue | Select Name,Id` -> record (locked caches expected -> SKIP_LOCKED). INVENTORY installed Steam games: `Get-ChildItem -LiteralPath "D:\SteamLibrary\steamapps\common" -Directory -Force | ForEach-Object { $c = (Get-ChildItem -LiteralPath $_.FullName -Force -Recurse -ErrorAction SilentlyContinue | Measure-Object).Count; "$($_.Name)|$c" }` -> write full name+count list to the evidence file (this removes ALL hardcoded game-name assumptions: Task 4 deletes only dirs whose count is 0; Task 6 spot-checks use this list). WSL registration check: `wsl --list --quiet 2>$null` -> record output (if "Ubuntu" appears, D:\WSL\Ubuntu deletion is FORBIDDEN and logged SKIP_WSL_REGISTERED instead). Create .omo/evidence/ dir if missing. MUST NOT delete anything in this todo, MUST NOT follow junctions.
  Parallelization: Wave 0 | Blocked by: none | Blocks: 2-5
  References: draft .omo/drafts/d-drive-cleanup.md findings section
  Acceptance criteria (agent-executable): file exists, contains SizeRemaining > 0, conda.exe True/False line, process list, Steam game inventory (each line "name|count"), wsl --list output line
  QA scenarios: happy: file with all 5 sections; failure: missing section -> re-run, Evidence .omo/evidence/d-task-1-d-drive-cleanup.txt
  Commit: N/A (no git repo)

- [ ] 2. Driver cleanup + root archives/logs/empty-dirs + quarantine DLLs
  What to do / Must NOT do: (a) D:\Driver: delete ALL contents EXCEPT D:\Driver\19_电子信息保修卡 (delete subdirs 1_Chipset..20_DouDou, Auto_Install_Driver.bat, Ver.txt; keep the warranty folder + all its files). (b) Delete the 6 root archives: D:\MapInfo Professional 10.0 汉化破解版.zip, D:\clash.verge_64.0525.zip, D:\sakura-v0.9.8-windows-x64.zip, D:\EPA.zip, D:\UsbEAm Hosts Editor v3.63.zip, D:\2016-2025年高数AII期末试题.rar (archives ONLY — extracted folders stay). (c) Delete D:\mapinfo_install.log + D:\mapinfo_install2.log. (d) Quarantine: New-Item -ItemType Directory -Force -Path "C:\Users\entropy\Desktop\.omo\quarantine\d"; Move-Item -LiteralPath D:\dinput8.dll -> quarantine\d\dinput8.dll; Move-Item -LiteralPath D:\sdhdship.exe -> quarantine\d\sdhdship.exe (MOVE only, never delete; if locked by a running process, log SKIP_LOCKED and continue — do NOT force or retry). (e) Delete verified-empty dirs using the JUNCTION-AWARE check (never bare -Recurse — PS 5.1 follows NTFS junctions into other trees): define and use
  `function Test-DirEmpty([string]$p) { $items = Get-ChildItem -LiteralPath $p -Force -ErrorAction SilentlyContinue; if (-not $items) { return $true }; foreach ($i in $items) { if ($i.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { continue }; if ($i.PSIsContainer) { if (-not (Test-DirEmpty $i.FullName)) { return $false } } else { return $false } }; return $true }`
  Candidates: D:\新建文件夹 (2), D:\Tencent Games, D:\leidian, D:\GameVideos, D:\_original_doc_backup, D:\WSL\Ubuntu (ONLY if task-1 wsl --list did NOT report Ubuntu; if registered -> log SKIP_WSL_REGISTERED and leave), then D:\WSL if now empty. Each must pass Test-DirEmpty first; if not empty, log SKIP_NOT_EMPTY and leave. MUST NOT delete extracted folders (MapInfo Professional 10.0 汉化破解版/, clash.verge_64.0525/, sakura-v0.9.8-windows-x64/, EPA/, UsbEAm Hosts Editor v3.63/, 2016-2025年高数AII期末试题/), MUST NOT delete 19_电子信息保修卡, MUST NOT delete DimensionToTsuLovers symlink, MUST NOT delete D:\Game\PC[CRYSTALiA] folder or .7z, MUST NOT use -Path, per-item try/catch -> CSV (SKIP_ACCESS_DENIED/SKIP_NOT_EMPTY/SKIP_LOCKED/SKIP_WSL_REGISTERED).
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 6 | Can parallelize with: 3,4,5
  References: draft findings (D:\Driver 25 entries 7GB; root archives ~646MB; empty dirs list)
  Acceptance criteria (agent-executable): Test-Path D:\Driver\19_电子信息保修卡 -eq $true AND (Get-ChildItem -LiteralPath "D:\Driver" -Force | Measure-Object).Count -eq 1; Test-Path D:\MapInfo Professional 10.0 汉化破解版.zip -eq $false AND Test-Path "D:\MapInfo Professional 10.0 汉化破解版" -eq $true (extracted folder SURVIVED); Test-Path D:\dinput8.dll -eq $false AND Test-Path "C:\Users\entropy\Desktop\.omo\quarantine\d\dinput8.dll" -eq $true; Test-Path "D:\新建文件夹 (2)" -eq $false; free-space delta recorded
  QA scenarios: happy: warranty kept (only 1 entry left), archives gone + extracted folders intact, DLLs quarantined; failure: warranty missing -> CRITICAL error (guard makes this impossible); locked/denied -> SKIP_* logged, Evidence .omo/evidence/d-task-2-d-drive-cleanup.txt
  Commit: N/A

- [ ] 3. conda clean --all
  What to do / Must NOT do: FIRST record env baseline: `(Get-ChildItem -LiteralPath "D:\anaconda3\envs" -Force | Measure-Object).Count` -> note the number. Then run `& D:\anaconda3\Scripts\conda.exe clean --all -y` (non-interactive, default flags only). Capture exit code. If conda.exe missing -> log SKIP_NOT_FOUND to CSV and skip (no manual deletion of pkgs). If exit code != 0 -> log the error and continue (do not fall back to manual deletion). MUST NOT delete D:\anaconda3\envs, D:\anaconda3\Lib, or the pkgs dir itself; MUST NOT run conda with any flags beyond `clean --all -y`; MUST NOT use admin.
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 6 | Can parallelize with: 2,4,5
  References: draft finding (pkgs 16.7GB, envs 19.4GB KEEP)
  Acceptance criteria (agent-executable): conda exit code 0 recorded AND `(Get-ChildItem -LiteralPath "D:\anaconda3\envs" -Force | Measure-Object).Count` equals the pre-run baseline number; free-space delta >= 3GB OR exit-code-0-with-smaller-delta explained in evidence
  QA scenarios: happy: exit 0, envs intact, big delta; failure: conda missing -> SKIP_NOT_FOUND logged, no manual deletion, Evidence .omo/evidence/d-task-3-d-drive-cleanup.txt
  Commit: N/A

- [ ] 4. Steam empty leftover dirs + WeGame residue
  What to do / Must NOT do: (a) Steam: read the installed-game inventory from .omo/evidence/d-task-1-d-drive-cleanup.txt (lines "name|count"); for every dir under D:\SteamLibrary\steamapps\common whose inventory count is 0 -> Remove-Item -LiteralPath $d -Recurse -Force (re-verify with the junction-aware Test-DirEmpty function from Task 2e first; skip if it now has content); every dir with count > 0 is an INSTALLED GAME or content-bearing folder -> SKIP_NOT_EMPTY, NEVER delete. NEVER delete appmanifest files in D:\SteamLibrary\steamapps\*. (b) WeGame: delete D:\Wegame\WeGameInstaller (whole dir), D:\Wegame\英雄联盟(26)\tiny_cache (whole dir), CONTENTS of D:\Wegame\英雄联盟(26)\Game\Logs (recreate dir via New-Item -ItemType Directory -Force after), and D:\Wegame\鑻遍泟鑱旂敾(26) (mojibake empty dir — verify with Test-DirEmpty first, SKIP_NOT_EMPTY if not). KEEP D:\Wegame\英雄联盟(26)\Game, LeagueClient, Cross, Riot Client, Launcher. MUST NOT use -Path, per-item try/catch -> CSV.
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 6 | Can parallelize with: 2,3,5
  References: draft findings (14 empty dirs ~0.8GB; WeGame residue ~564MB)
  Acceptance criteria (agent-executable): Test-Path D:\Wegame\WeGameInstaller -eq $false AND Test-Path D:\Wegame\英雄联盟(26)\tiny_cache -eq $false AND Test-Path "D:\SteamLibrary\steamapps\common\BlackMythWukong" -eq $false AND every installed-game dir from the task-1 inventory (count > 0) still passes Test-Path -eq $true; free-space delta recorded
  QA scenarios: happy: empty dirs gone, installed games intact; failure: installed game dir flagged empty (never happens - has files) -> SKIP_NOT_EMPTY protects; locked WeGame files -> SKIP_LOCKED, Evidence .omo/evidence/d-task-4-d-drive-cleanup.txt
  Commit: N/A

- [ ] 5. WeChat cache dirs
  What to do / Must NOT do: Find cache dirs: `Get-ChildItem -LiteralPath "D:\WeiXin\xwechat_files" -Directory -Recurse -Filter cache -ErrorAction SilentlyContinue` (e.g. ...\<wxid>\<appid>\cache). For each cache dir, delete CONTENTS ONLY, keeping the dir itself (method: `Remove-Item -LiteralPath "$cacheDir\*" -Recurse -Force -ErrorAction SilentlyContinue` — do NOT delete+recreate the dir, preserving ACLs; verify the dir still exists after). Per-cache-dir try/catch -> CSV (SKIP_LOCKED if WeChat running — recorded in task 1; note WeChat process names include WeChat, WeChatApp, WeChatStore, Weixin). MUST NOT delete anything outside `cache` dirs (msg/, file/, contact/, temp/ subdirs of xwechat_files stay), MUST NOT delete the xwechat_files root or user wxid dirs, MUST NOT use -Path, MUST NOT follow junctions.
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 6 | Can parallelize with: 2,3,4
  References: draft finding (D:\WeiXin\xwechat_files cache 346MB, 4755 files)
  Acceptance criteria (agent-executable): every found cache dir's recursive file count -eq 0 (checked via `(Get-ChildItem -LiteralPath $cacheDir -Recurse -Force -File -ErrorAction SilentlyContinue | Measure-Object).Count -eq 0`) OR SKIP_LOCKED logged for it; non-cache user data survives — concrete check: `(Get-ChildItem -LiteralPath "D:\WeiXin\xwechat_files" -Directory -Recurse -Depth 1 -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne 'cache' } | Measure-Object).Count -gt 0`; free-space delta recorded
  QA scenarios: happy: all cache dirs empty, user data intact; failure: WeChat running -> SKIP_LOCKED logged (WeChat closed later by user frees it), Evidence .omo/evidence/d-task-5-d-drive-cleanup.txt
  Commit: N/A

- [ ] 6. Post-run verification + d-summary.md
  What to do / Must NOT do: Re-read `(Get-Volume -DriveLetter D).SizeRemaining`; compute per-task deltas vs task-1 baseline; count OK vs SKIP_* in .omo/evidence/d-cleanup-errors.csv; assert key targets using the task-1 inventory (warranty dir exists, 6 archives gone + extracted folders present, DLLs quarantined, League install intact per task-1 WeGame paths, envs count unchanged from task-3 baseline, all installed Steam games from inventory still Test-Path true); write .omo/evidence/d-summary.md with: baseline free, final free, total freed, per-task freed, skipped table, quarantine note. Tolerance ±500MB (other system writes); if out of tolerance, note discrepancy, do NOT fail. MUST NOT delete anything new, MUST NOT re-run cleanup commands.
  Parallelization: Wave 2 | Blocked by: 2-5 | Blocks: F1-F4 | Can parallelize with: none
  References: task-1 baseline, d-cleanup-errors.csv
  Acceptance criteria (agent-executable): d-summary.md exists with all 8 required fields; totals reconcile within tolerance
  QA scenarios: happy: summary complete, numbers consistent; failure: unreconciled numbers -> re-read Get-Volume, recompute, note in summary, Evidence .omo/evidence/d-task-6-d-drive-cleanup.txt
  Commit: N/A

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit
- [ ] F2. Code quality review
- [ ] F3. Real manual QA
- [ ] F4. Scope fidelity

## Commit strategy
N/A — no git repository on this machine. Durable record: .omo/evidence/d-summary.md + d-cleanup-errors.csv.

## Success criteria
- D: drive free space increased by approximately 15-20 GB (Driver ~7GB + conda 5-10GB + archives 0.65GB + Steam/WeGame 1.4GB + WeChat 0.35GB + misc 0.1GB), measured via Get-Volume SizeRemaining delta
- All targets deleted or logged as skipped with reason (nothing silent)
- Zero user data deleted: warranty folder, CRYSTALiA 7z, launchers, games, conda envs, WeChat chat data, BaiduNetdisk, symlink — audited in F4
- dinput8.dll + sdhdship.exe quarantined (recoverable), not deleted
- .omo/evidence/d-summary.md produced and handed to the user as the D-drive cleanup report
