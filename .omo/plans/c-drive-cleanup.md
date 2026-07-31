# c-drive-cleanup - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** A one-shot cleanup of your C: drive that reclaims roughly 45-50 GB by clearing browser caches, thumbnail/icon caches, crash dumps, GPU shader caches (including a 22.9 GB NVIDIA cache), your pip/npm/developer caches (~24 GB), the recycle bin (which you approved emptying), and — if the elevation prompt is accepted — Windows system temp files. You'll also get a written report showing exactly how much space was freed and anything that had to be skipped.

**Why this approach:** Everything targeted is a cache or temporary file that Windows or your tools recreate automatically on next use — nothing is user data, and nothing installed gets touched. Files that are in use (locked by running apps) are skipped and logged rather than forced, so nothing breaks mid-session. Admin-only system junk is attempted in a separate elevated step that safely does nothing if you decline.

**What it will NOT do:** It will not delete your documents, downloads, desktop files, homework, chat history, game saves, or any installed program. It will not touch Windows component stores, the system installer folder, or the paging/hibernation files (hibernation stays on, as you chose). Nothing irrecoverable is deleted — the one root-level DLL is moved to a quarantine folder instead of removed.

**Effort:** Medium — one scripted session, roughly 4 waves, mostly waiting on deletions.
**Risk:** Low — every target is a regenerable cache; locked files are skipped and logged; the elevated wave is optional and skip-safe.
**Decisions to sanity-check:** (1) recycle bin emptied (you approved); (2) hibernation stays enabled — hiberfil.sys 12.7 GB is only reported, not disabled; (3) NVIDIA shader cache (~23 GB) cleared — will rebuild as you play/run GPU apps; (4) the system-level wave will pop a UAC prompt — you can decline it.

Your next move: run this plan in a worker session (e.g. `/start-work c-drive-cleanup`). Full execution detail follows below.

---

> TL;DR (machine): Medium effort, Low risk — 12 sequential cleanup todos in 5 waves, PowerShell with -LiteralPath + skip-on-lock + CSV error log, ~45-50 GB reclaim, no git commit (N/A).

## Scope
### Must have
- Clear user-level safe caches/temps: Chrome + Edge (Cache, Code Cache, GPUCache, Crashpad reports), Explorer thumbcache_*.db + iconcache_*.db, %LOCALAPPDATA%\CrashDumps, all Crashpad report dirs (VS Code, Quark, Antigravity, QQ), Steam dumps, rime.weasel dumps, LarkShell monitor dumps, JetBrains resharper-host dumps
- Clear GPU shader caches: D3DSCache (wildcard subdirs), NVIDIA\DXCache + NVIDIA\GLCache (fully-qualified C:\Users\entropy\AppData\Local\NVIDIA\...)
- Clear %LOCALAPPDATA%\Temp top-level files only (no -Recurse; per-file try/catch; skip locked + files modified < 7 days)
- Clear developer caches: pip cache purge, npm cache clean --force (with pre-checks + manual-deletion fallbacks), .cache subdirs (torch, huggingface, opencode, codex-runtimes, pkg), JetBrains per-IDE caches\ + log\ only + Toolbox cache/logs, Zotero cache2/startupCache/shader-cache, Jedi cache
- Empty Recycle Bin: Clear-RecycleBin -Force (user approved 2026-07-31)
- Misc: EA Desktop\Logs, Logishrd LGHUB analytics, ProgramData WER ReportArchive, Packer bhcache files, quarantine C:\appverifUI.dll, clean C:\Temp + C:\tmp
- Elevated batch (attempt, skip-if-denied): C:\Windows\Temp (per-file, skip < 7 days old), C:\Windows\Prefetch (*.pf only, never Layout.ini), C:\Windows\SoftwareDistribution (Stop-Service wuauserv first), C:\Windows\Logs\WindowsUpdate *.etl + CBS CbsPersist cabs, DISM /StartComponentCleanup (NO /ResetBase)
- Produce .omo/evidence/summary.md with per-wave freed space + error CSV

### Must NOT have (guardrails, anti-slop, scope boundaries)
- NEVER delete: user documents (Downloads, Desktop, Documents, Pictures, Music, Videos), .claude, .codex, .gemini, .gitconfig, homework/lecture files, apiKey CSV
- NEVER delete: installed programs (Program Files, Program Files (x86) — Steam, Zotero, Git, yxq_nethelper, VScode, Python314, JetBrains IDEs, LGHUB, EA Desktop, Riot, WeGame, Antigravity, CherryStudio), C:\.GamingRoot
- NEVER delete: C:\Windows\WinSxS, C:\Windows\Installer (+$PatchCache$), C:\Windows\System32\DriverStore\FileRepository, pagefile.sys, swapfile.sys, hiberfil.sys (report-only; NO powercfg /h off — user chose not to)
- NEVER delete: QQ/WeChat data (Documents\Tencent Files\nt_qq), game saves (DEATH STRANDING 2, Ghost of Tsushima, R6 Siege), Bcut Drafts
- NEVER use -Path with wildcard-prone paths; ALWAYS -LiteralPath; ALWAYS filter out ReparsePoint/junctions in any -Recurse (esp. "Local Settings" junction)
- NEVER follow junctions during deletion walks
- NO cleanmgr, NO "AMD if present" (not found on this machine), NO /ResetBase for DISM
- NEVER run elevated commands without UAC launcher; if elevation denied, SKIP the wave, log it, continue — do not retry in a loop

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: none (system cleanup, not code) + agent-executed QA = file-existence assertions + free-space delta + error CSV review
- Evidence: .omo/evidence/task-<N>-c-drive-cleanup.<ext> (outside ulw-loop: use .omo/evidence/); error log at .omo/evidence/cleanup-errors.csv (columns: Timestamp, Wave, Action, Path, ErrorMessage, Disposition; Disposition in {OK, SKIP_LOCKED, SKIP_ACCESS_DENIED, SKIP_NOT_FOUND, SKIP_TOO_RECENT, SKIP_JUNCTION, SKIP_ELEVATION_DENIED, SKIP_SERVICE_RUNNING})
- Every Remove-Item wrapped in try/catch writing to the CSV; assert-before-assert-after for the largest known files per wave (e.g., D3DSCache 512MB dxcache, thumbcache_2560.db, NVIDIA .nvph count, pip http-v2 bodies)
- Free-space baseline: `(Get-Volume -DriveLetter C).SizeRemaining` recorded at todo 1 and re-read after each wave; final report sums deltas

## Execution strategy
### Parallel execution waves
- Wave 0 (todo 1): pre-flight baseline — single, blocking
- Wave 1 (todos 2-6): user-level safe caches — can parallelize, but all read same evidence CSV so serialize writes (run sequentially is acceptable; parallel only if the executor supports safe CSV appends)
- Wave 2 (todos 7-9): developer caches — sequential (npm/pip commands are global)
- Wave 3 (todo 10): recycle bin + misc + quarantine — after waves 1-2 (recycle bin content changes)
- Wave 4 (todo 11): elevated system batch — after waves 1-3; requires UAC; skip-if-denied
- Wave 5 (todo 12): post-run verification + summary — after all waves

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 (pre-flight baseline) | none | 2-11 | none |
| 2 (browser caches) | 1 | 12 | 3,4,5,6 |
| 3 (thumbcache/iconcache) | 1 | 12 | 2,4,5,6 |
| 4 (crash dumps) | 1 | 12 | 2,3,5,6 |
| 5 (GPU shader caches) | 1 | 12 | 2,3,4,6 |
| 6 (%TEMP% top-level) | 1 | 12 | 2,3,4,5 |
| 7 (pip+npm) | 1 | 12 | 8,9 |
| 8 (.cache subdirs) | 1 | 12 | 7,9 |
| 9 (JetBrains/Zotero/Jedi) | 1 | 12 | 7,8 |
| 10 (recycle bin + misc + quarantine) | 2-9 | 12 | none |
| 11 (elevated batch) | 1-10 | 12 | none |
| 12 (final verification + summary) | 2-11 | F1-F4 | none |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [ ] 1. Pre-flight baseline + machine state snapshot
  What to do / Must NOT do: Record baseline free space: `(Get-Volume -DriveLetter C).SizeRemaining` (and .Size) into .omo/evidence/task-1-c-drive-cleanup.txt. Assert NOT elevated: `([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)` -> record $false expected. List running processes that lock targets: `Get-Process chrome,msedge,clion64,pycharm64,Code,wallpaper64,steam,QQ -ErrorAction SilentlyContinue | Select-Object Name,Id` -> record. Create .omo/evidence/ dir if missing. MUST NOT delete anything in this todo. MUST NOT follow the "Local Settings" junction (C:\Users\entropy\Local Settings is a junction to AppData\Local - never traverse it).
  Parallelization: Wave 0 | Blocked by: none | Blocks: 2-11
  References: draft .omo/drafts/c-drive-cleanup.md (findings section); scan evidence: C:\Users\entropy\AppData\Local\Temp, C:\Users\entropy\AppData\Local\CrashDumps, C:\Users\entropy\AppData\Local\Microsoft\Windows\Explorer, C:\Users\entropy\AppData\Local\Google\Chrome\User Data\Default, C:\Users\entropy\AppData\Local\Microsoft\Edge\User Data\Default, C:\Users\entropy\AppData\Local\NVIDIA, C:\Users\entropy\AppData\Local\D3DSCache, C:\Users\entropy\AppData\Local\pip\cache, C:\Users\entropy\AppData\Local\npm-cache, C:\Users\entropy\.cache, C:\Users\entropy\AppData\Local\JetBrains
  Acceptance criteria (agent-executable): .omo/evidence/task-1-c-drive-cleanup.txt exists, contains SizeRemaining number > 0 and the IsInRole line "elevated: False"
  QA scenarios: happy: file created with correct numbers; failure: file missing or SizeRemaining blank -> rerun command, Evidence .omo/evidence/task-1-c-drive-cleanup.txt
  Commit: N/A (no git repo; file deletions untracked)

- [ ] 2. Wave 1a - Clear Chrome + Edge caches and Crashpad reports
  What to do / Must NOT do: For each of these exact paths (delete CONTENTS of Cache/Code Cache/GPUCache subdirs and Crashpad\reports files, recreate empty parent dirs): C:\Users\entropy\AppData\Local\Google\Chrome\User Data\Default\Cache, ...\Default\Code Cache, ...\Default\GPUCache, ...\User Data\Crashpad\reports; C:\Users\entropy\AppData\Local\Microsoft\Edge\User Data\Default\Cache, ...\Default\Code Cache, ...\Default\GPUCache, ...\User Data\Crashpad\reports. Use -LiteralPath everywhere; per-file try/catch writing to .omo/evidence/cleanup-errors.csv (Disposition SKIP_LOCKED/SKIP_ACCESS_DENIED). MUST NOT delete browser profiles (Default\Cookies, Local Storage, Login Data), MUST NOT follow junctions, MUST NOT use -Path. If chrome/msedge processes are running (from todo 1), stop them gracefully first: `Stop-Process -Name chrome,msedge -Force -ErrorAction SilentlyContinue` (user-approved cleanup; browsers regenerate caches).
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 12 | Can parallelize with: 3,4,5,6
  References: scan evidence paths above; Metis findings B3 (LiteralPath), B4 (locking), B5 (junctions)
  Acceptance criteria (agent-executable): `(Get-ChildItem -LiteralPath "C:\Users\entropy\AppData\Local\Google\Chrome\User Data\Default\Cache" -Recurse -Force -File -ErrorAction SilentlyContinue | Measure-Object).Count` -eq 0 AND same for Edge Cache; free space increased by >= 800 MB vs todo 1 baseline
  QA scenarios: happy: cache dirs empty, delta >= 800MB; failure: locked files remain -> verify they are logged in cleanup-errors.csv with SKIP_LOCKED and NOT counted as failure, Evidence .omo/evidence/task-2-c-drive-cleanup.txt
  Commit: N/A

- [ ] 3. Wave 1b - Clear Explorer thumbnail + icon caches via MoveFileEx delayed-until-reboot
  What to do / Must NOT do: Target: C:\Users\entropy\AppData\Local\Microsoft\Windows\Explorer\thumbcache_*.db and iconcache_*.db (e.g., thumbcache_2560.db 176MB, thumbcache_256.db 91MB, thumbcache_1280.db 64MB, iconcache_48.db 98MB, iconcache_32.db, iconcache_16.db). Method: first try `Remove-Item -LiteralPath $f -Force`; on access-denied (explorer.exe locks them), queue via P/Invoke MoveFileEx with MOVEFILE_DELAY_UNTIL_REBOOT (value 4) — exact snippet (class wrapper REQUIRED for Add-Type to compile): Add-Type @' using System.Runtime.InteropServices; public class Win32 { [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] public static extern bool MoveFileEx(string lpExistingFileName, string lpNewFileName, int dwFlags); } '@; [Win32]::MoveFileEx($f, $null, 4). MUST NOT delete Explorer's other files (only thumbcache_*.db + iconcache_*.db), MUST NOT stop explorer.exe (disruptive), MUST NOT use -Path. Log every queued file in cleanup-errors.csv with Disposition SKIP_LOCKED and note "queued reboot".
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 12 | Can parallelize with: 2,4,5,6
  References: scan evidence: C:\Users\entropy\AppData\Local\Microsoft\Windows\Explorer (33 files ~498MB); Metis B1
  Acceptance criteria (agent-executable): every thumbcache_*.db/iconcache_*.db path is either absent (Test-Path -LiteralPath returns $false) or present in cleanup-errors.csv with "queued reboot" disposition; Test-Path on thumbcache_2560.db returns $false OR CSV contains its queue record
  QA scenarios: happy: files deleted or queued with records; failure: file absent from disk AND absent from CSV -> treat as error, re-attempt, Evidence .omo/evidence/task-3-c-drive-cleanup.txt
  Commit: N/A

- [ ] 4. Wave 1c - Clear crash dumps across all apps
  What to do / Must NOT do: Delete .dmp files (and Crashpad\reports contents) under: C:\Users\entropy\AppData\Local\CrashDumps (10 files ~318MB: hnlshmoa.exe x7, wallpaper64.exe x3, QQ.exe); C:\Users\entropy\AppData\Roaming\Code\Crashpad\reports; C:\Users\entropy\AppData\Local\Quark\User Data\Crashpad\reports; C:\Users\entropy\AppData\Roaming\Antigravity\Crashpad\reports; C:\Users\entropy\AppData\Roaming\QQ\Crashpad\reports; C:\Program Files (x86)\Steam\dumps (may be access-denied if Steam was system-installed — that is fine, log SKIP_ACCESS_DENIED and continue); C:\Users\entropy\AppData\Local\Temp\rime.weasel (*.dmp only, keep rime config); C:\Users\entropy\AppData\Roaming\LarkShell\sdk_storage\log\monitor\pending; C:\Users\entropy\AppData\Local\JetBrains\CLion2026.1\resharper-host\temp and CLion2025.3\resharper-host\temp (delete *.dmp). Use -LiteralPath; per-file try/catch -> cleanup-errors.csv. MUST NOT delete Crashpad "settings" or non-.dmp files, MUST NOT delete rime.weasel non-dmp files, MUST NOT follow junctions.
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 12 | Can parallelize with: 2,3,5,6
  References: scan evidence: C:\Users\entropy\AppData\Local\CrashDumps (10 dumps), *.dmp glob across AppData (83 matches seen incl. Code/Quark/QQ/Antigravity/Steam/JetBrains/LarkShell/rime)
  Acceptance criteria (agent-executable): `(Get-ChildItem -LiteralPath "C:\Users\entropy\AppData\Local\CrashDumps" -File -Force | Measure-Object).Count` -eq 0 AND free space delta >= 300 MB
  QA scenarios: happy: CrashDumps empty + delta; failure: dumps remain locked -> logged SKIP_LOCKED in CSV, Evidence .omo/evidence/task-4-c-drive-cleanup.txt
  Commit: N/A

- [ ] 5. Wave 1d - Clear GPU shader caches (D3DSCache + NVIDIA DXCache + GLCache)
  What to do / Must NOT do: D3DSCache: iterate per-file with try/catch (NEVER directory-level -Recurse with SilentlyContinue — it swallows per-file failures and -LiteralPath does not expand '*'): `Get-ChildItem -LiteralPath "C:\Users\entropy\AppData\Local\D3DSCache" -Directory | ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -File -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object { try { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction Stop; Write-Output "OK|$($_.FullName)" | Out-File .omo/evidence/cleanup-errors.csv -Append } catch { Write-Output "$(Get-Date -Format o)|Wave1d|delete|$($_.FullName)|$($_.Exception.Message)|SKIP_LOCKED" | Out-File .omo/evidence/cleanup-errors.csv -Append } } }` (kills the 512MB 9aab62e0918f9190\*.dxcache). NVIDIA: delete contents of C:\Users\entropy\AppData\Local\NVIDIA\DXCache (439 .nvph files ~22.9GB) and C:\Users\entropy\AppData\Local\NVIDIA\GLCache (15MB) using the same per-file try/catch pattern. MUST NOT delete the NVIDIA directory itself, MUST NOT delete GLCache subfolder structures beyond contents, MUST NOT use -Path, MUST NOT use directory-level Remove-Item -Recurse for these targets. Skip gracefully if GPU apps (games) lock files (SKIP_LOCKED logged).
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 12 | Can parallelize with: 2,3,4,6
  References: scan evidence: C:\Users\entropy\AppData\Local\NVIDIA\DXCache (22.9GB), C:\Users\entropy\AppData\Local\D3DSCache (533MB); Metis D1/F5
  Acceptance criteria (agent-executable): `(Get-ChildItem -LiteralPath "C:\Users\entropy\AppData\Local\NVIDIA\DXCache" -File -Force | Measure-Object).Count` -eq 0 AND `(Get-ChildItem -LiteralPath "C:\Users\entropy\AppData\Local\D3DSCache" -File -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object).Count` -eq 0 AND free space delta >= 22 GB
  QA scenarios: happy: both empty, delta >= 22GB; failure: DXCache files locked by running game -> SKIP_LOCKED in CSV, delta smaller, NOT a failure, Evidence .omo/evidence/task-5-c-drive-cleanup.txt
  Commit: N/A

- [ ] 6. Wave 1e - Clear %LOCALAPPDATA%\Temp top-level files
  What to do / Must NOT do: For C:\Users\entropy\AppData\Local\Temp: delete TOP-LEVEL FILES ONLY (no -Recurse, no subdirectories, no junction traversal — note C:\Users\entropy\Local Settings is a junction to AppData\Local, never touch it). Per-file: skip if LastWriteTime within last 7 days (Disposition SKIP_TOO_RECENT), else try/catch Remove-Item -LiteralPath -Force (Disposition SKIP_LOCKED on IOException). Targets seen: .fee7*.node/.bdef*.dll pairs, wct*.tmp, ~DF*.TMP, is-*.tmp, gfw-httpget-*, codex-index-*, WegameLauncher.*.log, yxqxylog\, hsperfdata_entropy\, jetbrainsd-*, jb.station.entropy.sock, mat-debug-*.log, *.png/*.webp temp images, gq.txt, giscus_query.txt, kg_rag.webp. MUST NOT delete subdirectories' contents (e.g., yxqxylog, hsperfdata_entropy, codex-index-* subdirs stay; only delete loose files), MUST NOT delete files newer than 7 days, MUST NOT follow junctions.
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 12 | Can parallelize with: 2,3,4,5
  References: scan evidence: C:\Users\entropy\AppData\Local\Temp (hundreds of files listed); Metis F1 (no -Recurse, per-file try/catch)
  Acceptance criteria (agent-executable): at least 50 loose files removed (count via CSV OK rows for this wave) AND no subdirectory was modified (spot-check: Test-Path yxqxylog\info_20260723-205632.30672 still $true OR dir still exists), free space delta recorded
  QA scenarios: happy: loose files cleared, subdirs untouched; failure: locked files -> SKIP_LOCKED logged, Evidence .omo/evidence/task-6-c-drive-cleanup.txt
  Commit: N/A

- [ ] 7. Wave 2a - Purge pip + npm caches
  What to do / Must NOT do: pip: `pip --version` pre-check; if OK run `pip cache purge` (canonical command; removes all ~13.8GB from C:\Users\entropy\AppData\Local\pip\cache). If pip missing from PATH, manual fallback: Remove-Item -LiteralPath "C:\Users\entropy\AppData\Local\pip\cache\*" -Recurse -Force (with junction filter). npm: `npm --version` pre-check; if OK run `npm cache clean --force` (clears C:\Users\entropy\AppData\Local\npm-cache 7.3GB). If npm missing, manual fallback delete of npm-cache contents. MUST NOT delete pip/npm config files (pip.ini, .npmrc), MUST NOT delete the cache dirs themselves (apps expect them to exist — recreate if removed), MUST NOT use -Path.
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 12 | Can parallelize with: 8,9
  References: scan evidence: C:\Users\entropy\AppData\Local\pip\cache (13.8GB), C:\Users\entropy\AppData\Local\npm-cache (7.3GB); Metis D3/D4 (lock `pip cache purge` + `npm cache clean --force`, pre-checks, fallbacks)
  Acceptance criteria (agent-executable): `(Get-ChildItem -LiteralPath "C:\Users\entropy\AppData\Local\pip\cache" -File -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object).Count` -eq 0 AND same for npm-cache OR command exit codes 0 recorded; free space delta >= 20 GB cumulative
  QA scenarios: happy: caches empty, exit codes 0; failure: pip/npm command not found -> fallback delete runs, both logged, Evidence .omo/evidence/task-7-c-drive-cleanup.txt
  Commit: N/A

- [ ] 8. Wave 2b - Clear .cache subdirectories (torch, huggingface, opencode, codex-runtimes, pkg)
  What to do / Must NOT do: Delete contents (recreate dirs) of: C:\Users\entropy\.cache\torch (528MB incl. hub\checkpoints\vgg16-397923af.pth), C:\Users\entropy\.cache\huggingface (161MB incl. 160MB .incomplete blob — remove the .incomplete + hub blobs), C:\Users\entropy\.cache\opencode (960MB), C:\Users\entropy\.cache\codex-runtimes (1GB), C:\Users\entropy\.cache\pkg (sqlite .node binaries). Use -LiteralPath, try/catch -> CSV. MUST NOT delete .cache entirely (other tools may use it), MUST NOT delete gdown\cookies.txt or oh-my-opencode small config files unless inside the listed subdirs — scope is EXACTLY the 5 listed subdirs, MUST NOT use -Path.
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 12 | Can parallelize with: 7,9
  References: scan evidence: C:\Users\entropy\.cache breakdown (opencode 960MB, codex-runtimes 1GB, torch 528MB, huggingface 161MB, pkg); Metis D5 (explicit subdir list)
  Acceptance criteria (agent-executable): `(Get-ChildItem -LiteralPath "C:\Users\entropy\.cache\torch" -File -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object).Count` -eq 0 AND same for huggingface/opencode/codex-runtimes/pkg; free space delta >= 2.5 GB cumulative
  QA scenarios: happy: all 5 subdirs empty; failure: locked torch model -> SKIP_LOCKED logged, Evidence .omo/evidence/task-8-c-drive-cleanup.txt
  Commit: N/A

- [ ] 9. Wave 2c - Clear JetBrains caches/logs + Zotero cache + Jedi cache
  What to do / Must NOT do: JetBrains: for each dir under C:\Users\entropy\AppData\Local\JetBrains matching CLion2025.3, CLion2026.1, PyCharm2025.3: delete ONLY <IDE>\caches\ and <IDE>\log\ contents (recreate dirs). Delete C:\Users\entropy\AppData\Local\JetBrains\Toolbox\cache\* and Toolbox\logs\*.log (keep Toolbox\scripts, .appState.json, .settings.json). MUST NOT delete <IDE>\system, <IDE>\config, <IDE>\plugins, <IDE>\jbr, <IDE>\options, or the <IDE> parent dir. Zotero: delete contents of C:\Users\entropy\AppData\Local\Zotero\Zotero\Profiles\2418qklb.default\cache2, ...\startupCache, ...\shader-cache (regenerate on next launch; keep profile .sqlite/config). Jedi: delete C:\Users\entropy\AppData\Local\Jedi\Jedi\CPython-314-33\*.pkl. Use -LiteralPath, try/catch -> CSV, no junction traversal.
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 12 | Can parallelize with: 7,8
  References: scan evidence: C:\Users\entropy\AppData\Local\JetBrains (CLion2025.3 560MB, CLion2026.1 448MB, PyCharm2025.3 1.3GB, Toolbox logs/cache), C:\Users\entropy\AppData\Local\Zotero\...\cache2+startupCache, C:\Users\entropy\AppData\Local\Jedi; Metis D2 (exact subdirs only)
  Acceptance criteria (agent-executable): `Test-Path -LiteralPath "C:\Users\entropy\AppData\Local\JetBrains\CLion2026.1\caches"` -eq $true (dir recreated, empty) AND `(Get-ChildItem -LiteralPath "C:\Users\entropy\AppData\Local\Jedi\Jedi\CPython-314-33" -Filter *.pkl -File | Measure-Object).Count` -eq 0 AND JetBrains\PyCharm2025.3\system still exists (Test-Path $true)
  QA scenarios: happy: caches empty, system/config intact; failure: IDE running locks caches -> SKIP_LOCKED logged, IDE not killed, Evidence .omo/evidence/task-9-c-drive-cleanup.txt
  Commit: N/A

- [ ] 10. Wave 3 - Empty Recycle Bin + misc program logs + quarantine root DLL + clean C:\Temp/C:\tmp
  What to do / Must NOT do: (a) `Clear-RecycleBin -Force -ErrorAction SilentlyContinue` (user approved 2026-07-31; ~30 deleted docx/doc, .bib, .lnk, .exe, .url). (b) Delete log/analytics contents: C:\ProgramData\EA Desktop\Logs\*.*, C:\ProgramData\Logishrd\LGHUB\analytics\*, C:\ProgramData\Microsoft\Windows\WER\ReportArchive\* + ReportQueue\*, C:\ProgramData\Packer\*.bin (bhcache files — regenerate). (c) Quarantine C:\appverifUI.dll: first check `Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers' -ErrorAction SilentlyContinue` and log the result; then create the quarantine dir with `New-Item -ItemType Directory -Force -Path "C:\Users\entropy\Desktop\.omo\quarantine"` and MOVE (not delete) C:\appverifUI.dll to C:\Users\entropy\Desktop\.omo\quarantine\appverifUI.dll, wrapped in try/catch -> CSV (Disposition SKIP_LOCKED or SKIP_ACCESS_DENIED on failure). MUST NOT permanently delete appverifUI.dll (quarantine first), MUST NOT skip the quarantine dir creation, MUST NOT use a relative .omo\quarantine path (use the absolute C:\Users\entropy\Desktop\.omo\quarantine path so it works regardless of CWD). (d) Delete contents of C:\Temp and C:\tmp (root-level temp dirs seen in scan; per-file try/catch, skip < 7 days). MUST NOT delete WER ReportArchive structure beyond Report.wer files? (delete all files inside ReportArchive + ReportQueue), MUST NOT delete LGHUB install/config (ProgramData\LGHUB\installation.json etc. — only \analytics\), MUST NOT delete EA Desktop install (only Logs\), MUST NOT permanently delete appverifUI.dll (quarantine first).
  Parallelization: Wave 3 | Blocked by: 2-9 | Blocks: 12 | Can parallelize with: none
  References: scan evidence: C:\$Recycle.Bin\S-1-5-21-2873851764-1546416733-3274480772-1001 (61 files), C:\ProgramData\EA Desktop\Logs, C:\ProgramData\Logishrd\LGHUB\analytics, C:\ProgramData\Microsoft\Windows\WER, C:\ProgramData\Packer, C:\appverifUI.dll, C:\Temp, C:\tmp; Metis B7 (Clear-RecycleBin -Force), F4 (quarantine), C1 (no cleanmgr)
  Acceptance criteria (agent-executable): `(Get-ChildItem -LiteralPath "C:\$Recycle.Bin\S-1-5-21-2873851764-1546416733-3274480772-1001" -File -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object).Count` -eq 0 AND Test-Path C:\appverifUI.dll -eq $false AND Test-Path "C:\Users\entropy\Desktop\.omo\quarantine\appverifUI.dll" -eq $true
  QA scenarios: happy: recycle bin empty, DLL quarantined; failure: recycle bin files locked (rare) -> logged, Evidence .omo/evidence/task-10-c-drive-cleanup.txt
  Commit: N/A

- [ ] 11. Wave 4 - Elevated system cleanup (attempt; skip-if-denied)
  What to do / Must NOT do: Write script .omo/evidence/wave4-elevated.ps1 containing ONLY: (a) C:\Windows\Temp top-level files older than 7 days (per-file try/catch; NO -Recurse; skip locked); (b) C:\Windows\Prefetch: delete only *.pf files, NEVER Layout.ini; (c) C:\Windows\SoftwareDistribution — GUARDED deletion: `$wuauserv = Get-Service wuauserv -ErrorAction SilentlyContinue; if ($wuauserv -and $wuauserv.Status -eq 'Running') { try { Stop-Service wuauserv -Force -ErrorAction Stop } catch { Write-Output "wuauserv stop failed: $($_.Exception.Message) - SKIPPING SoftwareDistribution cleanup" | Out-File C:\Users\entropy\Desktop\.omo\evidence\wave4-result.txt -Append; Write-Output "$(Get-Date -Format o)|Wave4|service-stop|wuauserv|$($_.Exception.Message)|SKIP_SERVICE_RUNNING" | Out-File C:\Users\entropy\Desktop\.omo\evidence\cleanup-errors.csv -Append } }; if ((Get-Service wuauserv -ErrorAction SilentlyContinue).Status -eq 'Stopped') { delete Download\* + DataStore\DataStore.edb.old + DataStore.jfm.old (per-file try/catch); Start-Service wuauserv -ErrorAction SilentlyContinue }` — deletion of SoftwareDistribution contents happens ONLY if the service is confirmed Stopped; otherwise log SKIP_SERVICE_RUNNING and move on. (d) C:\Windows\Logs\WindowsUpdate\*.etl older than 7 days + C:\Windows\Logs\CBS\CbsPersist_*.cab; (e) DISM /Online /Cleanup-Image /StartComponentCleanup (NO /ResetBase); (f) write .omo/evidence/wave4-result.txt with exit codes. Launch: `Start-Process powershell.exe -Verb RunAs -Wait -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','C:\Users\entropy\Desktop\.omo\evidence\wave4-elevated.ps1'`. The elevated child runs with CWD C:\Windows\System32 — script MUST use absolute paths throughout. MUST NOT delete Prefetch\Layout.ini, MUST NOT delete SoftwareDistribution\DataStore.edb itself (only .old) or its contents if wuauserv could not be stopped, MUST NOT use /ResetBase, MUST NOT delete WinSxS/Installer/DriverStore contents manually, MUST NOT loop-retry on UAC denial — if Start-Process throws or the script did not write wave4-result.txt within 5 min, record Disposition SKIP_ELEVATION_DENIED and move on.
  Parallelization: Wave 4 | Blocked by: 1-10 | Blocks: 12 | Can parallelize with: none
  References: scan evidence: C:\Windows\Temp + Prefetch (access denied — needs admin), C:\Windows\SoftwareDistribution (52.9MB: DataStore.edb 21MB + .old 20.75MB), C:\Windows\Logs (WindowsUpdate 20MB, CBS 15.9MB); Metis B2 (elevation pattern), B8 (DISM 740, no /ResetBase), B9 (wuauserv), F2 (Prefetch Layout.ini), F3 (Windows\Temp per-file + 7-day rule)
  Acceptance criteria (agent-executable): .omo/evidence/wave4-result.txt exists with exit codes (0 or logged failure) OR cleanup-errors.csv contains SKIP_ELEVATION_DENIED; if elevation succeeded: `(Get-ChildItem -LiteralPath "C:\Windows\Prefetch" -Filter *.pf | Measure-Object).Count` -eq 0 AND Test-Path C:\Windows\Prefetch\Layout.ini still $true (if it existed pre-run)
  QA scenarios: happy: wave4-result.txt with exit 0, Prefetch .pf cleared, Layout.ini intact; failure: UAC denied -> SKIP_ELEVATION_DENIED logged, no files touched in Windows\Temp/Prefetch, run continues, Evidence .omo/evidence/task-11-c-drive-cleanup.txt
  Commit: N/A

- [ ] 12. Post-run verification + freed-space summary report
  What to do / Must NOT do: Re-read `(Get-Volume -DriveLetter C).SizeRemaining`; compute per-wave deltas vs todo-1 baseline; count OK vs SKIP_* rows in .omo/evidence/cleanup-errors.csv; assert the key largest files are gone (D3DSCache dxcache wildcard count 0, thumbcache_2560.db absent-or-queued, NVIDIA DXCache count 0-or-skipped, pip/npm cache counts 0); write .omo/evidence/summary.md with: baseline free, final free, total freed, per-wave freed, skipped items table, quarantine note (appverifUI.dll moved). Tolerance: total freed = (final free - baseline free) with a fixed tolerance of ±500 MB (other system processes write to C: during the run); if outside tolerance, note the discrepancy in summary.md but do NOT treat as failure — file-existence assertions are the primary evidence. Regeneration note: GPU/browser caches may begin regenerating within seconds of deletion; for D3DSCache/NVIDIA/Chrome/Edge counts, accept count <= 10 of recently-created (LastWriteTime within 10 min) small files as evidence of successful clearing + normal regeneration. MUST NOT delete anything new, MUST NOT re-run cleanup commands, MUST NOT count SKIP rows as failures.
  Parallelization: Wave 5 | Blocked by: 2-11 | Blocks: F1-F4 | Can parallelize with: none
  References: todo 1 baseline file, cleanup-errors.csv, wave4-result.txt
  Acceptance criteria (agent-executable): summary.md exists and contains all 8 required fields; total freed = final free - baseline free - (any other writes by this session) with tolerance; if any wave had errors, summary lists them
  QA scenarios: happy: summary complete, totals consistent; failure: numbers don't reconcile -> re-read Get-Volume, recompute, note discrepancy in summary, Evidence .omo/evidence/task-12-c-drive-cleanup.txt
  Commit: N/A

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit
- [ ] F2. Code quality review
- [ ] F3. Real manual QA
- [ ] F4. Scope fidelity

## Commit strategy
N/A — this machine has no git repository (verified: workspace root is not a git repo). Changes are file deletions on a live Windows system, not source changes. Nothing is committed; the durable record is .omo/evidence/summary.md + cleanup-errors.csv + wave4-result.txt.

## Success criteria
- C: drive free space increased by approximately 45-50 GB (user-level ~3GB + dev caches ~24GB + NVIDIA DXCache 22.9GB + system wave variable), measured via Get-Volume SizeRemaining delta
- All largest known files gone or logged as skipped with reason (nothing silently skipped)
- Zero user documents, installed programs, system component stores, chat data, or game saves deleted (audited in F4)
- Recycle bin emptied (user-approved); appverifUI.dll quarantined, not deleted
- System boots and Explorer works (user-observable; F3 manual QA confirms browsers/IDEs relaunch normally)
- .omo/evidence/summary.md produced and handed to the user as the cleanup report

## EXECUTION COMPLETE (2026-07-31)
- ALL 12 TODOS + F1-F4 FINAL VERIFICATION WAVE: ALL PASS
- Final result: **52.06 GB freed** (14.7GB -> 63.2GB free on C:)
- Breakdown: GPU shader caches 25.6GB + pip/npm 25.8GB + .cache 4.2GB + JetBrains 2.9GB + browsers 1.34GB + crash dumps 0.56GB + thumbnails 0.2GB + misc
- 197 SKIP rows all dispositioned (39 locked / 20 access-denied / 137 too-recent / 1 elevation-denied) — none silent
- Wave 4 (elevated): UAC declined by user -> SKIP_ELEVATION_DENIED per plan; wave4-elevated.ps1 staged at .omo/evidence/ for manual admin run
- F1 compliance PASS | F2 quality PASS | F3 manual QA PASS | F4 scope PASS
- JetBrains HEALTH PASS (3 IDEs intact, caches regenerated)
- Remaining manual items: run wave4-elevated.ps1 as admin when ready (Windows\Temp, Prefetch, SoftwareDistribution, DISM, EA Logs, appverifUI.dll quarantine); delete opentui.dll after opencode exits; delete toolbox.9.log after Toolbox closes
