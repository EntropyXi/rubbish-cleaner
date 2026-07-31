---
slug: c-drive-cleanup
status: reviewed-approved
intent: clear
review_required: true
plan_path: .omo/plans/c-drive-cleanup.md
plan_sha256: EB52FABF46560438AB95784A8DC38776019B876D87BF9B0972DA5F38F12793CF
review_round_id: rr-cleanup-20260731-002
round_status: complete
pending-action: execute plan in worker session (e.g. /start-work)
review:
  momus:
    status: approved
    workspace_root: C:\Users\entropy\Desktop
    runtime_home: null
    target: .omo/plans/c-drive-cleanup.md
    round_id: rr-cleanup-20260731-002
    plan_sha256: EB52FABF46560438AB95784A8DC38776019B876D87BF9B0972DA5F38F12793CF
    launch_id: la-momus-20260731-002
    session: ses_04866607fffeOCKnyC3d7zWuhp
    result: MOMUS VERDICT: APPROVED (all 6 round-1 fixes verified; all 6 review dimensions pass; no blockers)
  independent:
    status: approved
    workspace_root: C:\Users\entropy\Desktop
    runtime_home: null
    target: .omo/plans/c-drive-cleanup.md
    round_id: rr-cleanup-20260731-002
    plan_sha256: EB52FABF46560438AB95784A8DC38776019B876D87BF9B0972DA5F38F12793CF
    launch_id: la-oracle-20260731-002
    session: ses_048664ef5ffeKIOAelSLUWJNQE
    result: INDEPENDENT VERDICT: APPROVED (all 5 previously-cited blockers/majors resolved; 4 minor non-blocking implementation notes)
approach: Windows C: drive junk/temp/useless-file cleanup plan - scan-based inventory (done), user-approved cleanup batches, pre/post free-space verification
---

# Draft: c-drive-cleanup

## Components (topology ledger)
| id | outcome (one line) | status | evidence path |
| --- | --- | --- | --- |
| user-temp | %LOCALAPPDATA%\Temp cleared (node/dll pairs, wct*.tmp, installer temps, logs) | active | C:\Users\entropy\AppData\Local\Temp (scanned) |
| crash-dumps | CrashDumps (~318MB) + browser/Steam/JetBrains crash reports cleared | active | C:\Users\entropy\AppData\Local\CrashDumps, ...\Code\Crashpad, ...\Chrome\User Data\Crashpad, C:\Program Files (x86)\Steam\dumps |
| thumbnail-cache | Explorer thumbcache/iconcache (~498MB) cleared via API | active | C:\Users\entropy\AppData\Local\Microsoft\Windows\Explorer\thumbcache_*.db |
| browser-cache | Chrome Cache+Code Cache (~680MB), Edge Cache+Code Cache (~263MB) cleared | active | C:\Users\entropy\AppData\Local\Google\Chrome\User Data\Default\{Cache,Code Cache} |
| gpu-shader-cache | D3DSCache 533MB (single 512MB dxcache) + NVIDIA DXCache 22.9GB cleared | active | C:\Users\entropy\AppData\Local\D3DSCache, ...\NVIDIA\DXCache |
| dev-caches | pip 13.8GB + npm 7.3GB + .cache 2.7GB (torch/huggingface/opencode) cleared | active | C:\Users\entropy\AppData\Local\pip\cache, npm-cache, C:\Users\entropy\.cache |
| ide-caches | JetBrains caches (~600MB of ~2.3GB), Zotero cache2/startupCache, Jedi pkls | active | C:\Users\entropy\AppData\Local\JetBrains, AppData\Local\Zotero, AppData\Local\Jedi |
| system-admin | Windows\Temp + Prefetch + SoftwareDistribution\Download + DISM/WinSxS cleanup (needs elevation) | deferred-pending-decision | C:\Windows\Temp (access denied), C:\Windows\Prefetch (access denied), C:\Windows\SoftwareDistribution (52.9MB) |
| recycle-bin | ~30 deleted docx/doc files in $Recycle.Bin; empty only on explicit approval | pending-decision | C:\$Recycle.Bin\S-1-5-21-2873851764-1546416733-3274480772-1001 |
| hiberfil | hiberfil.sys 12.69GB + pagefile 7.75GB - report-only unless user opts in (powercfg /h off) | pending-decision | C:\hiberfil.sys, C:\pagefile.sys (root scan) |
| misc-logs | WeGame/yxq/EA/LGHUB logs, WER reports, ipynb_checkpoints, root junk C:\appverifUI.dll | active | C:\ProgramData\EA Desktop\Logs, ProgramData\Logishrd, C:\appverifUI.dll |

## Open assumptions (announced defaults)
| assumption | adopted default | rationale | reversible? |
| --- | --- | --- | --- |
| Recycle bin contains possibly-wanted documents | Do NOT auto-empty; list contents in report, let user decide | ~30 .docx/.doc files - destructive + irreversible | N/A (default protects) |
| hiberfil.sys / pagefile.sys | Report only, never delete; only `powercfg /h off` if user opts in | system features, disabling changes behavior | yes (re-enable) |
| Admin-required locations (Windows\Temp, Prefetch, SoftwareDistribution) | Included as an elevated batch that is SKIPPED if not run as Administrator; user-level batches run regardless | current session lacks elevation (access denied observed) | yes |
| Windows\Installer, WinSxS, DriverStore, ProgramData\Package Cache | Never delete manually; only DISM/cleanup-tool paths | breaks uninstall/repair | N/A |
| User files (Downloads 14.5GB, Desktop, Documents, .claude, .codex worktrees, .gemini) | Never touched; only duplicates/reports if user opts in | user data | N/A |
| VPN/game caches (yxq_nethelper, .GamingRoot, Riot, EA) | Program folders never deleted; only their log/cache subfolders | installed software | N/A |

## Findings (cited - path:lines)
- %LOCALAPPDATA%\Temp: hundreds of files - .fee7*.node/.bdef*.dll pairs (game anti-cheat temps), wct*.tmp, ~DF*.TMP (Office temps), is-*.tmp (Inno Setup), gfw-httpget, codex-index-*, WegameLauncher logs, yxqxylog, hsperfdata, jetbrainsd + jb.station.sock (JetBrains)
- CrashDumps: 10 dumps ~318MB - hnlshmoa.exe x7 (~33MB each), wallpaper64.exe x3 (~36MB), QQ.exe 9.2MB; plus Crashpad dirs for VS Code, Chrome, Edge, Quark, Antigravity, QQ, Steam dumps, JetBrains resharper-host dumps, rime.weasel dumps, LarkShell monitor dumps
- Explorer thumbcache: thumbcache_2560.db 176MB, iconcache_48.db 98MB, thumbcache_256.db 91MB, thumbcache_1280.db 64MB, total ~498MB
- Chrome: Cache 304MB + Code Cache 374MB; Edge: Cache 61MB + Code Cache 202MB
- D3DSCache 533MB (single file 9aab62e0918f9190\...dxcache = 512.3MB); NVIDIA\DXCache ~22.9GB (439 .nvph files, auto-regenerates)
- pip\cache 13.8GB (http-v2 bodies up to 3.3GB); npm-cache 7.3GB (26,409 files); .cache 2.7GB (opencode 960MB, codex-runtimes 1GB, torch 528MB, huggingface 161MB incl. 160MB .incomplete)
- JetBrains: CLion2025.3 560MB + CLion2026.1 448MB + PyCharm2025.3 1.3GB (caches ~600MB); Toolbox logs+cache; Zotero profile cache2/startupCache/shader-cache; Jedi CPython-314-33 .pkl cache
- System: C:\Windows\Temp + Prefetch EXIST but access denied (need admin); SoftwareDistribution 52.9MB (DataStore.edb 21MB + .old 20.75MB); Windows\Logs 61.5MB (WindowsUpdate 20MB, CBS 15.9MB); Panther 3.1MB; WER ~0.4MB; no Windows.old; MEMORY.DMP none; Minidump empty
- Root: hiberfil.sys 12.69GB, pagefile.sys 7.75GB, swapfile.sys 16MB, C:\appverifUI.dll (orphan Application Verifier DLL at root - junk), C:\Temp + C:\tmp exist
- Recycle bin (-1001 SID): 61 files ~0.8MB incl. ~30 .docx/.doc (possibly student documents), .bib, .lnk, .exe, .url
- User data (never touch): Downloads 14.5GB (48,761 files; .minecraft 1.6GB, PCL 1GB, ygopro-database, mingw64, installers, homework), Desktop 419MB, Documents (QQ nt_data, game saves, Bcut drafts), .claude transcripts/telemetry, .codex worktrees (yugioh-workflow-rag project), .gemini, Python314, Program Files (Zotero, Git, VSCode, JetBrains IDEs), Program Files (x86) (Steam, yxq_nethelper, WindowsPowerShell)

## Decisions (with rationale)
- Scope: 3-tier cleanup - (T1) user-level safe caches/temps, (T2) developer caches, (T3) admin-required system cleanup - T1+T2 always, T3 gated on elevation and user choice
- Safety: every target is a cache/temp/log that regenerates; no user documents, no installed programs, no system component stores
- Verification: measure free space on C: before/after per batch; assert specific largest files gone (512MB dxcache, thumbcache_2560.db, top pip bodies); system check via `sfc /verifyonly`? No - keep read-only smoke: Windows still boots/Explorer works is user-observable; agent verifies file-level assertions + free-space delta
- Test strategy: none (system cleanup, not code) - but agent-executed QA = exact file-existence assertions + Get-PSDrive free space before/after + error capture per deletion

## Scope IN
- %LOCALAPPDATA%\Temp contents (with in-use skip), CrashDumps + all Crashpad report dirs + Steam\dumps + rime.weasel dumps + LarkShell monitor dumps
- Explorer thumbcache_*.db + iconcache_*.db, Chrome/Edge Cache + Code Cache + GPUCache + Crashpad
- D3DSCache, NVIDIA\DXCache (DXCache + GLCache), AMD if present
- pip cache, npm cache, .cache (torch/huggingface/opencode/codex-runtimes/pkg)
- JetBrains caches/logs (per-IDE caches + Toolbox cache/logs, NOT config/settings), Zotero cache dirs (NOT profile data), Jedi cache
- ProgramData logs: EA Desktop\Logs, Logishrd\LGHUB analytics, Packer cache (bhcache files - regenerate), Package Cache report-only
- System (elevated batch): Windows\Temp, Prefetch, SoftwareDistribution\Download + DataStore.old, Windows\Logs\WindowsUpdate ETL + CBS persist cabs, DISM /StartComponentCleanup, cleanmgr
- Recycle bin: only with explicit user approval (or list-and-leave)
- Root junk: C:\appverifUI.dll, C:\Temp, C:\tmp, DumpStack.log.tmp (report only)
- Report artifacts: .omo/evidence/ summary of freed space per category

## Scope OUT (Must NOT have)
- User documents/data: Downloads, Desktop, Documents, Pictures, Music, Videos, .claude, .codex, .gemini, .gitconfig, homework/lecture files, apiKey CSV
- Installed programs: Program Files, Program Files (x86) (Steam, Zotero, Git, yxq_nethelper, VScode, Python314, JetBrains IDEs, LGHUB, EA Desktop, Riot, WeGame, Antigravity, CherryStudio), C:\.GamingRoot
- System integrity: C:\Windows\WinSxS, C:\Windows\Installer (+ $PatchCache$), C:\Windows\System32\DriverStore\FileRepository, pagefile.sys, swapfile.sys, hiberfil.sys (unless user opts in via powercfg)
- QQ/WeChat user data (Documents\Tencent Files\nt_qq - contains chat DBs), game saves (Documents\DEATH STRANDING 2, Ghost of Tsushima, R6 Siege), Bcut Drafts
- Anything in the above list that a deletion would make irrecoverable; default = list-and-report, delete only on explicit approval

## Open questions
1. Recycle bin: ~30 deleted .docx/.doc files present. Empty, list-only, or list-then-empty?
   -> RESOLVED (2026-07-31): 清空回收站 (empty it)
2. Depth: user-level caches only (~2.5-3GB), + dev caches (~24GB: pip/npm/.cache), or + hiberfil via powercfg /h off (12.7GB)?
   -> RESOLVED (2026-07-31): 用户级 + 开发者缓存 (~26-27GB); hiberfil report-only, no powercfg /h off
3. Admin batch: attempt elevated system cleanup (Windows\Temp, SoftwareDistribution, DISM) or keep fully user-level?
   -> RESOLVED (2026-07-31): 尝试提权执行 - include admin batch, skip gracefully if elevation fails

## Approval gate
status: approved-by-user + dual-review-approved (rr-cleanup-20260731-002: momus APPROVED + oracle APPROVED)
<!-- When exploration is exhausted and unknowns are answered, set status: awaiting-approval. -->
<!-- That durable record is the loop guard: on a later turn read it and resume at the gate instead of re-running exploration. -->
Approach (for the brief): single PowerShell-driven cleanup plan executed by the worker session:
  - Wave 1: user-level safe caches/temps (browser caches, thumbcache/iconcache, CrashDumps + Crashpad, D3DSCache, NVIDIA DXCache, %LOCALAPPDATA%\Temp, misc logs)
  - Wave 2: developer caches (pip cache purge, npm cache clean, .cache subdirs torch/huggingface/opencode/codex-runtimes, JetBrains caches+logs, Zotero cache, Jedi cache)
  - Wave 3: recycle bin empty (user approved)
  - Wave 4 (elevated, skip-if-denied): Windows\Temp, Prefetch, SoftwareDistribution\Download, Windows\Logs ETL, DISM /StartComponentCleanup via an elevated launcher
  - Verification: free-space delta on C: before/after each wave, file-existence assertions for the largest known files, error capture log
Next action on approval: write .omo/plans/c-drive-cleanup.md, run mandatory Metis gap analysis, append todos, fill TL;DR last.
