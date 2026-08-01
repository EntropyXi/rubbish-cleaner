# rubbish-cleaner-optimization - Work Plan (v2)

## TL;DR (For humans)
<!-- Fill this LAST. -->

## Scope
### Must have
The user's message listed "current limitations" (不足) and "iteration directions" (迭代方向) with heavy duplication: the same item appears once as a limitation and once as a direction. Re-analysis deduplicated the request to **9 distinct requirements**, each solved once:

1. **Cross-platform (req 1)**: pwsh 7 compatibility + Linux/macOS cache/temp path support → `scripts/lib/platform.ps1` (runtime detection) + scan-drive platform branch + `references/per-app-path-map.md` Linux/macOS section
2. **Test fixture drive parameterization (req 2)**: remove hardcoded `-Drive D:` from `tests/sandbox/run-sandbox-tests.ps1` → auto-detect via `Get-PSDrive` (fallback to current root drive)
3. **Multithreading (req 3)**: replace single-threaded enumeration → `Invoke-ParallelForEach` wrapper (pwsh 7 `ForEach-Object -Parallel`, PS 5.1 `Start-Job` fallback)
4. **Progress persistence + resume (req 4)**: `Write-Progress` + `<run>\scan-checkpoint.json` + `-Resume` on scan/clean
5. **Pester branch executable via CI (req 5)**: `.github/workflows/test.yml` with Pester 5.x preinstalled on a 3-OS matrix → Pester branch runs in CI
6. **Task Scheduler integration (req 6)**: `scripts/schedule.ps1` (`-Register/-Unregister/-List`, `-Policy safe|aggressive`, policy JSON, `Write-EventLog`/summary-file notification)
7. **Multi-drive batch (req 7)**: `-Drives` array param on scan/clean/verify-report; sequential clean (safety); `-Parallel` for scan via subprocesses
8. **CHANGELOG (req 8)**: `CHANGELOG.md` + `CHANGELOG_zh.md` bilingual project history
9. **Bilingual .md + hyperlinks (req 9)**: Chinese mirrors for SKILL.md + all references/*.md; hyperlink all file-name mentions in every tracked .md

### Must NOT have (guardrails)
- NEVER replace PS 5.1 as a supported runtime — pwsh 7 is additive (runtime feature-detection; `Start-Job` fallback retained)
- NEVER run destructive operations on real drives during development — all deletion logic tested against `$env:TEMP` fake trees only
- NEVER register a scheduled task without explicit user confirmation (`-Register` gated)
- NEVER parallelize deletion (clean-drive always sequential per drive; only scanning is parallelized)
- NEVER use `-Path` with wildcard-susceptible patterns (keep `-LiteralPath` discipline)
- NO new runtime dependencies (Pester 5 is CI-only; pwsh 7 is optional-additive)
- NO breaking changes to existing CLI signatures — new params (`-Drives`, `-Resume`, `-Parallel`) are additive with defaults matching current behavior; `-Drive`/`-Drives` resolved via `ParameterSetName` (default `SingleDrive` for backward compat)
- NO scope creep: only the 9 deduplicated requirements; do NOT touch unlisted roadmap items (quarantine TTL, recursive dedup, HTML reports, WSL enhancements, config-driven taxonomy, CLI filters)

## Verification strategy
> Zero human intervention — all verification is agent-executed.
- Test strategy: tests-after per module (sandbox harness + Pester 5 unit suites in todo 12)
- Dual high-accuracy review (momus + independent Oracle) is a REQUIRED gate per user request — the plan is reviewed before execution, and F1-F4 re-verify after
- Each todo: happy + failure QA with exact commands + evidence path under `.omo/evidence/rubbish-cleaner-optimization/`
- CI (todo 8) is the cross-platform verification harness — sandbox harness must pass on all 3 OS matrix entries
- Hyperlink system (todo 13): acceptance includes a script that extracts all `[text](path)` links, dedupes, and asserts every target exists (Test-Path)

## Execution strategy
### Parallel execution waves
- Wave 0 (todo 1): `lib/platform.ps1` — BLOCKING (all platform branches depend on detection)
- Wave 1 (todos 2, 5, 6): cross-platform paths + test-drive param + CI workflow — PARALLEL (depend on 1 only)
- Wave 2 (todo 3): `Invoke-ParallelForEach` — single (depends on 1)
- Wave 3 (todo 4): progress/checkpoint/resume — single (depends on 2 + 3)
- Wave 4 (todos 7, 8): Task Scheduler + multi-drive — PARALLEL (depend on 1 + 4)
- Wave 5 (todos 9, 10, 11, 12, 13): docs + CHANGELOG + bilingual/hyperlinks + tests — SERIES (9+10→11→13; 12 parallel to 13; all before 14)
- Wave 6 (todo 14): git finalize + install re-sync
- Final Verification Wave (F1-F4): all 4 in PARALLEL after todo 14

### Dependency matrix
| Todo | Depends on | Blocks |
|---|---|---|
| 1 (platform.ps1) | none | 2-14 |
| 2 (cross-platform paths) | 1 | 4, 11, 13 |
| 3 (Invoke-ParallelForEach) | 1 | 4 |
| 4 (progress/checkpoint/resume) | 2, 3 | 7, 9, 12 |
| 5 (test fixture -Drive) | 1 | 12, 14 |
| 6 (CI workflow) | 1 | 12 |
| 7 (Task Scheduler) | 1, 4 | 12 |
| 8 (multi-drive batch) | 1, 4 | 11, 12, 13 |
| 9 (docs update) | 2, 8 | 11, 13, 14 |
| 10 (CHANGELOG) | 8 | 13, 14 |
| 11 (bilingual + hyperlinks) | 9, 10 | 14 |
| 12 (tests) | 5, 6, 7, 8 | 14 |
| 13 (docs for new features) | 8, 11 | 14 |
| 14 (git finalize) | 9, 10, 11, 12, 13 | F1-F4 |

## Todos
<!-- APPEND TASK BATCHES BELOW THIS LINE - never rewrite the headers above. -->
- [x] 1. scripts/lib/platform.ps1 — cross-platform detection layer
  What to do / Must NOT do: Write `scripts/lib/platform.ps1` as a DOT-SOURCED library (no top-level execution) with EXACTLY these helper functions and module-scoped variables:
  - Platform detection on import (evaluate ONCE at dot-source time): `$script:IsWindows`, `$script:IsLinux`, `$script:IsMacOS`, `$script:IsCoreCLR`. Detection logic: if `$PSVersionTable.PSEdition -eq 'Core'` (pwsh 6+) → use built-in `$IsWindows/$IsLinux/$IsMacOS`. Else (PS 5.1 Desktop) → `[System.Environment]::OSVersion.Platform`: `PlatformID.Win32NT` → Windows; `PlatformID.Unix` → test via `uname` for Linux vs macOS. Set `$script:IsCoreCLR = ($PSVersionTable.PSEdition -eq 'Core')`.
  - `Get-FixedDriveLetters()`: Windows → `(Get-PSDrive -PSProvider FileSystem | Where-Object {$_.Free -gt 0} | ForEach-Object {$_.Root})` returned as `@('C:\','D:\')`. Linux/macOS → `@('/')`.
  - `Get-UserCacheDir()`: Windows → `$env:LOCALAPPDATA`; macOS → `$env:HOME + '/Library/Caches'`; Linux → `$env:XDG_CACHE_HOME ?? $env:HOME + '/.cache'`.
  - `Get-SystemTempDir()`: Windows → `$env:TEMP`; Linux/macOS → `'/tmp'`.
  - `Get-UserDocumentsDir()`: Windows → `[Environment]::GetFolderPath('MyDocuments')`; Linux/macOS → `$env:HOME`.
  - All function params use `-LiteralPath` where applicable. MUST NOT require pwsh 7 (gracefully degrade). MUST NOT write to disk at import time. MUST NOT delete anything.
  Parallelization: Wave 0 | Blocked by: none | Blocks: 2-14
  References: PS version table docs; per-app-path-map.md
  Acceptance criteria (agent-executable): parse-check 0 errors; dot-source `platform.ps1` then `Get-Command Get-FixedDriveLetters,Get-UserCacheDir,Get-SystemTempDir,Get-UserDocumentsDir | Measure-Object` count = 4; `$script:IsWindows -eq $true` on this machine; `$script:IsCoreCLR` correctly set; `Get-FixedDriveLetters` returns at least one drive on this machine
  QA scenarios: happy: all 4 functions + 4 booleans loaded; failure: parse error → fix, Evidence `.omo/evidence/rubbish-cleaner-optimization/task-1.txt`
  Commit: `feat(platform): cross-platform detection layer`

- [x] 2. references/per-app-path-map.md + scripts/scan-drive.ps1 — cross-platform cache/temp paths
  What to do / Must NOT do: (a) APPEND a new section `### Linux / macOS` to `references/per-app-path-map.md` with EXACTLY these entries (pipe table format matching existing Windows section): pip (`{cache}/pip`), npm (`{cache}/npm`), torch (`{cache}/torch`), huggingface (`{cache}/huggingface`), opencode (`{cache}/opencode`), codex-runtimes (`{cache}/codex-runtimes`), general .cache (`{cache}/*` temp files >7d), system temp (`{temp}/*` >7d), browser caches (Chrome `{cache}/google-chrome/Default/{Cache,Code Cache,GPUCache}`; Firefox `{cache}/mozilla/firefox/*/cache2/`; Edge `{cache}/microsoft-edge/Default/{Cache,Code Cache,GPUCache}`), IDE (JetBrains `{cache}/JetBrains/*/`; VSCode `~/.config/Code/{Cache,CachedData,logs}/`; Zotero `{cache}/zotero/{cache2,startupCache,shader-cache}`), crash dumps (`/var/crash/*`), thumbnails (`{cache}/thumbnails/`), recycle bin (`~/.local/share/Trash/`; ASK). Note: `{cache}` = `Get-UserCacheDir`, `{temp}` = `Get-SystemTempDir`. (b) Modify `scripts/scan-drive.ps1`: dot-source `platform.ps1`; add a single `if (-not $script:IsWindows) { ... }` block at the END of the `Get-JunkCandidates` function body, immediately before `return $result`. Inside, register Linux/macOS category equivalents with the SAME category ids as Windows, only path templates differ (resolved via `Get-UserCacheDir`/`Get-SystemTempDir`). $isUserDrive on Linux/macOS = `$Drive -eq '/'`. MUST NOT remove/break existing Windows classification.
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 4, 11, 13 | Can parallelize with: 5, 6
  References: existing per-app-path-map.md Windows section (46 rows); platform.ps1
  Acceptance criteria (agent-executable): parse-check 0 errors on scan-drive.ps1; per-app-path-map.md grep `### Linux / macOS` → 1 match; at least 15 Linux/macOS entries; `git show` diff confirms no Windows entries removed; scan-drive.ps1 grep `$script:IsWindows` → ≥1 match
  QA scenarios: happy: map updated + scanner branches; failure: broken categories → diff, Evidence `.omo/evidence/rubbish-cleaner-optimization/task-2.txt`
  Commit: `feat(scan): cross-platform cache paths (Linux/macOS)`
  NOTE: the current per-app-path-map.md has 46 pipe-table rows (not 44) — the Windows section row count in any doc references should say 46.

- [x] 3. scripts/lib/rubbish-core.ps1 — Invoke-ParallelForEach multithreading wrapper
  What to do / Must NOT do: ADD function `Invoke-ParallelForEach` to `scripts/lib/rubbish-core.ps1` (append after existing 8 functions, keep all untouched). Signature: `Invoke-ParallelForEach([object[]]$InputObject, [scriptblock]$ScriptBlock, [int]$ThrottleLimit = 4, [object[]]$ArgumentList = @())`. (pwsh 7 path — `$script:IsCoreCLR`): `$InputObject | ForEach-Object -Parallel $ScriptBlock -ThrottleLimit $ThrottleLimit -ArgumentList $ArgumentList`, collect results. (PS 5.1 fallback): `Start-Job` runs in a clean scope with NO access to caller variables — inner job scriptblock MUST be `{ param($items,$sb,$args_outer) foreach($i in $items){ & $sb -ArgumentList @($i)+$args_outer } }` passing `$args_outer` (caller's `-ArgumentList`) into each invocation so scriptblocks can reference external variables via `$args`. Batch `$InputObject` into chunks of `$ThrottleLimit`, each chunk spawns a `Start-Job`; `Wait-Job` all; `Receive-Job`; `Remove-Job`. If ThrottleLimit=1 or input count=1 → sequential fallback `$InputObject | ForEach-Object { & $ScriptBlock -ArgumentList @($_)+$ArgumentList }`. Error handling: per-item errors collected, aggregate `Write-Error` after all items. MUST NOT change existing function signatures. DOCUMENT: complex callers needing full scan context MUST use subprocess invocation, not in-process wrapper.
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 4 | Can parallelize with: none
  References: existing rubbish-core.ps1; ForEach-Object -Parallel / Start-Job docs
  Acceptance criteria (agent-executable): parse-check 0 errors; `Get-Command Invoke-ParallelForEach` found; functional test: create 20 temp dirs, invoke parallel creation, assert all 20 exist + order preserved + `Get-Job` count = 0 after
  QA scenarios: happy: parallel works, order preserved, jobs cleaned; failure: job leak → assert Get-Job == 0, Evidence `.omo/evidence/rubbish-cleaner-optimization/task-3.txt`
  Commit: `feat(lib): Invoke-ParallelForEach multithreading wrapper`

- [x] 4. scripts/scan-drive.ps1 + scripts/clean-drive.ps1 — Write-Progress + checkpoint + -Resume
  What to do / Must NOT do: (a) `Write-Progress -Activity "Scanning $CategoryName" -PercentComplete` before each category loop in scan-drive.ps1 (category-level granularity). (b) Checkpoint file `<run>\scan-checkpoint.json` written after each category + mid-category every 500 files: `{"drive":"X:","completedCategories":[...],"currentCategory":"X","lastPath":"X:\\...","totalBytesSoFar":123456,"timestamp":"..."}`. (c) `[switch]$Resume` on scan-drive.ps1: reads checkpoint, skips `completedCategories`, skips files whose FullName sorts STRICTLY BEFORE `lastPath` via `[System.StringComparer]::OrdinalIgnoreCase.Compare($f.FullName, $lastPath) -lt 0`; the file AT `lastPath` is RE-SCANNED (may have been partial). ALWAYS re-sort by FullName before comparing. KNOWN LIMITATION (code comment): files inserted between checkpoint and resume sorting before lastPath are silently missed — acceptable for cleanup tool. No checkpoint → exit 1 "No checkpoint found for resume". (d) `[switch]$Resume` on clean-drive.ps1: reads `<run>\clean-checkpoint.json` (`{"completedCategories":[...],"lastCleanedRowIndex":N}`), skips rows before lastCleanedRowIndex. (e) Write-Progress per category in clean-drive. MUST NOT change behavior when -Resume NOT set (checkpoint still written, unused). MUST NOT checkpoint more often than every 500 files. MUST NOT fail if checkpoint can't be written (catch + continue).
  Parallelization: Wave 3 | Blocked by: 2, 3 | Blocks: 7, 8, 12
  References: scan-drive.ps1 category loops; clean-drive.ps1 per-category processing
  Acceptance criteria (agent-executable): parse-check 0 errors; fake-tree scan → checkpoint produced; `-Resume` re-run → skips completed (exit 0); clean-drive -Resume → rows before lastCleanedRowIndex skipped; progress output visible
  QA scenarios: happy: checkpoint + skip + progress; failure: resume without checkpoint → exit 1; mid-category partial skip correct, Evidence `.omo/evidence/rubbish-cleaner-optimization/task-4.txt`
  Commit: `feat(scan,clean): Write-Progress + checkpoint + -Resume`

- [x] 5. tests/sandbox/run-sandbox-tests.ps1 — auto-detect test drive (remove hardcoded `-Drive D:`)
  What to do / Must NOT do: Replace ALL 3 hardcoded `'D:'` references in `tests/sandbox/run-sandbox-tests.ps1` (L338, L373, L408) with auto-detection. Dot-source platform.ps1 at top (`. (Join-Path $PSScriptRoot '../../scripts/lib/platform.ps1')`). If `$script:IsWindows`: `$script:TestDrive = ((Get-PSDrive -PSProvider FileSystem | Where-Object {$_.Used -gt 0 -and $_.Root -match '^[A-Z]:\\$'} | Select-Object -First 1).Root).TrimEnd('\')`. Else: `$script:TestDrive = '/'`. If null → fallback `'C:'`; if even C: unavailable → print `SKIP: no fixed drive available for test fixtures` and exit 0. Replace: L338 `-Drive 'D:'` → `-Drive $script:TestDrive`; L373 `Get-Volume -DriveLetter 'D'` → `Get-Volume -DriveLetter $script:TestDrive.TrimEnd(':')`; L408 same as L338. MUST NOT change any other line.
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 12, 14 | Can parallelize with: 2, 6
  References: sandbox harness lines 338, 373, 408
  Acceptance criteria (agent-executable): parse-check 0 errors; `Select-String -Pattern "'D:'"` on harness → 0 matches; harness runs exit 0 on auto-detected drive; `$script:TestDrive` printed in BRANCH header
  QA scenarios: happy: no hardcoded D:, harness green; failure: no fixed drives → skip exit 0, Evidence `.omo/evidence/rubbish-cleaner-optimization/task-5.txt`
  Commit: `fix(test): auto-detect test drive (remove hardcoded -Drive D:)`

- [x] 6. .github/workflows/test.yml — GitHub Actions CI with Pester 5
  What to do / Must NOT do: Create `.github/workflows/test.yml` (new) with EXACTLY:
```yaml
name: test
on: [push, pull_request]
jobs:
  test:
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
      fail-fast: false
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - name: Install Pester 5
        shell: pwsh
        run: Install-Module Pester -Force -MinimumVersion 5.0 -Scope CurrentUser -SkipPublisherCheck
      - name: Parse-check all scripts
        shell: pwsh
        run: |
          $fail = 0
          Get-ChildItem -Recurse -Path scripts,tests -Filter '*.ps1' | ForEach-Object {
            $tokens = $null; $errors = $null
            [System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$tokens, [ref]$errors)
            if ($errors.Count -gt 0) { $fail++; Write-Host "PARSE FAIL: $($_.FullName)" }
            else { Write-Host "PARSE PASS: $($_.FullName)" }
          }
          Write-Host "PARSE CHECK: $fail file(s) with errors"
          if ($fail -gt 0) { throw "$fail file(s) with parse errors" }
      - name: PS 5.1 compatibility grep (pwsh-7-only syntax)
        shell: pwsh
        run: |
          $forbidden = @('\?\?', '\|\|', '&&', '-AsHashtable', 'ForEach-Object\s+-Parallel')
          # scripts/lib/rubbish-core.ps1 is EXCLUDED: todo 3's Invoke-ParallelForEach
          # legitimately uses ForEach-Object -Parallel inside the pwsh-7 guard ($script:IsCoreCLR)
          $hits = Select-String -Path scripts\*.ps1 -Pattern ($forbidden -join '|') -AllMatches
          if ($hits) { Write-Host "FORBIDDEN SYNTAX FOUND:"; $hits | ForEach-Object { Write-Host "$_" }; throw "PS 5.1 incompatible syntax detected" }
          else { Write-Host "PS 5.1 COMPATIBILITY: PASS" }
      - name: Run Pester 5 unit tests
        shell: pwsh
        run: Invoke-Pester -Path tests/unit -PassThru -Output Detailed
      - name: Run zero-dependency sandbox harness
        shell: pwsh
        run: ./tests/sandbox/run-sandbox-tests.ps1
```
MUST NOT add extra steps (no codecov/notifications/deploy); all 3 checks must pass; `fail-fast: false`.
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 12 | Can parallelize with: 2, 5
  References: Pester 5 Install-Module; GitHub Actions matrix; run-tests.ps1 parse-check pattern
  Acceptance criteria (agent-executable): `Test-Path .github\workflows\test.yml`; YAML PARSE CHECK (must pass — use `powershell -NoProfile -Command "python -c \"import yaml,sys; yaml.safe_load(open(r'D:\rubbish_cleaning_skill\.github\workflows\test.yml'))\""` if python available, else `pwsh -Command "ConvertFrom-Yaml"` if the yaml module exists, else a structural indentation review of the 4-level nesting); then structural sanity (name/on/jobs/test/strategy/matrix/runs-on/steps nesting); commit = exactly this file
  QA scenarios: happy: file correct; failure: YAML indentation broken → fix, Evidence `.omo/evidence/rubbish-cleaner-optimization/task-6.txt`
  Commit: `ci: GitHub Actions matrix (ubuntu/windows/macos) with Pester 5`

- [x] 7. scripts/schedule.ps1 — Task Scheduler integration with policy profiles
  What to do / Must NOT do: Write `scripts/schedule.ps1` with params: `[Parameter(Mandatory)]$Action` (Register|Unregister|List), `[string]$Drive` (mandatory for Register), `[string]$Policy = 'safe'` (loads `references/policies/<Policy>.json`), `[string]$Interval = 'daily'`, `[string]$Time = '02:00'`. Register on Windows: (a) if NOT admin → `Write-Error "Scheduled task registration requires administrator privileges"`, exit 1. (b) Load policy JSON (fail if missing). (c) Build action: `powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "& '<repo>\scripts\scan-drive.ps1' -Drive $Drive -Categories $(($policy.Categories | ForEach-Object {$_}) -join ','); & '<repo>\scripts\clean-drive.ps1' -Drive $Drive -Yes"`, `<repo>` = `(Get-Item $PSScriptRoot).Parent.FullName`. (d) `Register-ScheduledTask -TaskName "rubbish-cleaner-$Drive" -Trigger (New-ScheduledTaskTrigger -Daily -At $Time) -Action (New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments) -Force`. Linux: cron line template — write to `/etc/cron.d/rubbish-cleaner` (sudo) or user `crontab -l` append: `<Minute> <Hour> * * * pwsh -NoProfile -File '<repo>/scripts/scan-drive.ps1' -Drive '<Drive>' -Categories '<comma-list>' && pwsh -NoProfile -File '<repo>/scripts/clean-drive.ps1' -Drive '<Drive>' -Yes` (Minute/Hour derived from `-Time` HH:MM). macOS: launchd plist at `~/Library/LaunchAgents/com.rubbish-cleaner.plist` — structure: `<plist><dict><key>Label</key><string>com.rubbish-cleaner</string><key>ProgramArguments</key><array><string>pwsh</string><string>-NoProfile</string><string>-File</string><string>&lt;repo&gt;/scripts/scan-drive.ps1</string>...</array><key>StartCalendarInterval</key><dict><key>Hour</key><integer>2</integer><key>Minute</key><integer>0</integer></dict></dict></plist>`, then `launchctl load`. Unregister: remove per platform. List: enumerate. Create `references/policies/safe.json` (categories: root-temps,root-logs,app-caches,empty-dirs,browser-caches,gpu-shader,dev-caches,ide-caches,thumbnail-cache; maxAgeDays:30; includeElevated:false; excludeCategories:recycle-bin,root-suspicious; notifyOnCompletion:true) and `references/policies/aggressive.json` (adds recycle-bin,root-suspicious,crash-dumps,user-temp,elevated-system; maxAgeDays:90; includeElevated:true). Notification: summary to `$HOME\.rubbish-cleaner\scheduled\<Drive>-<timestamp>\summary.md`; Windows also `Write-EventLog -LogName Application -Source 'rubbish-cleaner' -EventId 1000` (register source via `New-EventLog` if absent, or skip on failure). MUST NOT auto-register; MUST NOT register without policy/-Drive; MUST NOT fail if EventLog source absent (skip).
  Parallelization: Wave 4 | Blocked by: 1, 4 | Blocks: 12 | Can parallelize with: 8
  References: Register-ScheduledTask; crontab; launchd; scan-drive/clean-drive interfaces
  Acceptance criteria (agent-executable): parse-check 0 errors; `-Action List` → exit 0; `-Action Register -Drive C: -Policy safe` → exit 1 with admin error (not crash); policy JSONs parse; `-Action Unregister` → exit 0
  QA scenarios: happy: List/Register-admin-error/Unregister all clean; failure: malformed policy JSON → clear error, Evidence `.omo/evidence/rubbish-cleaner-optimization/task-7.txt`
  Commit: `feat(schedule): Task Scheduler integration with policy profiles`

- [x] 8. scripts/scan-drive.ps1 + scripts/clean-drive.ps1 + scripts/verify-report.ps1 — multi-drive batch mode
  What to do / Must NOT do: (a) Add parameter-set resolution to scan-drive.ps1: `ParameterSetName='SingleDrive'` for `-Drive` (Mandatory), `ParameterSetName='MultiDrive'` for `-Drives` (Mandatory), `[CmdletBinding(DefaultParameterSetName='SingleDrive')]`. When `-Drives`: iterate each drive (sequential default); per-drive scan via SUBPROCESS with a PLATFORM BRANCH: Windows → `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/scan-drive.ps1 -Drive $drive`; Linux/macOS → `pwsh -NoProfile -File scripts/scan-drive.ps1 -Drive $drive` (use platform.ps1 `$script:IsWindows` to select) — avoids in-process scope isolation AND works cross-platform. Combined summary `$OutDir\multidrive-<timestamp>\drives.csv`. `[switch]$Parallel` → launch per-drive subprocesses concurrently via `Start-Job` (PS 5.1) / `Start-Process -NoNewWindow` (pwsh 7). Edge: NEITHER `-Drive` NOR `-Drives` → exit 1 "Must specify either -Drive or -Drives" (implementation: make `-Drive` non-mandatory with `$null` default so the custom error fires before PowerShell's own binding error; `-Drives` likewise). (b) clean-drive.ps1: `-Drives` + `-Parallel` accepted; `-Parallel` IGNORED → print "parallel clean is disabled for safety — cleaning drives sequentially"; drives cleaned SEQUENTIALLY (one at a time). (c) verify-report.ps1: `-Drives` walks each drive's latest run dir, combined multi-drive summary. MUST NOT change single-drive behavior; MUST NOT parallelize deletion.
  Parallelization: Wave 4 | Blocked by: 1, 4 | Blocks: 11, 12, 13 | Can parallelize with: 7
  References: existing scan/clean/verify param blocks; Invoke-ParallelForEach; checkpoint format
  Acceptance criteria (agent-executable): parse-check 0 errors; `scan-drive.ps1 -Drives D: -Categories root-temps` → exit 0, per-drive run dir; `scan-drive.ps1` (no params) → exit 1 with "Must specify either -Drive or -Drives"; `-Drive D: -Drives C:` → exit 1 (both); RUNTIME PARALLEL-CLEAN SAFETY: `clean-drive.ps1 -Drives C:,D: -Parallel -Yes` with per-drive timestamps → assert warning string present + drive B start > drive A end (sequential)
  QA scenarios: happy: multi-drive scan works, mutual exclusion enforced, parallel clean rejected; failure: parallel clean actually concurrent → violation, Evidence `.omo/evidence/rubbish-cleaner-optimization/task-8.txt`
  Commit: `feat(scan,clean,report): multi-drive batch mode (-Drives D:,E:)`

- [x] 9. SKILL.md + README.md + README_zh.md + references/safety-rules.md — documentation update for all new features
  What to do / Must NOT do: (a) SKILL.md: add `## 平台支持` section (Windows PS 5.1 + pwsh 7; Linux/macOS pwsh 7; PS 5.1-only features like elevated system cleanup are Windows-only). Add multi-drive example `-Drives C:,D:` + `-Resume` note + `schedule.ps1` reference + CI badge `![test](https://github.com/EntropyXi/rubbish_cleaning_skill/workflows/test/badge.svg)`. (b) README.md: update Limitations & Roadmap — strike through (or ✅) the 4 items now addressed (Windows-only, Pester branch, -Drive D: hardcode, single-thread progress); keep unaddressed items; CI badge under title. (c) README_zh.md: same in Chinese. (d) safety-rules.md: add `## 跨平台注意事项` (no UAC/elevation on Linux/macOS; quarantine via Get-UserDocumentsDir; elevated-system Windows-only, silently skipped elsewhere). MUST NOT remove existing content; MUST NOT exceed SKILL.md 40KB (6KB base, safe); MUST NOT change language switcher lines.
  Parallelization: Wave 5 | Blocked by: 2, 8 | Blocks: 11, 13, 14
  References: current SKILL.md (6.1KB); README.md LIMITATIONS lines 100-128
  Acceptance criteria (agent-executable): SKILL.md has `## 平台支持`, `-Drives`, CI badge; README.md has CI badge + 4 strikethrough limitations; README_zh.md mirrors; safety-rules.md has `## 跨平台注意事项`
  QA scenarios: happy: all docs updated; failure: missing section/badge → fix, Evidence `.omo/evidence/rubbish-cleaner-optimization/task-9.txt`
  Commit: `docs: platform, multi-drive, CI, resume, schedule notes`

- [x] 10. CHANGELOG.md + CHANGELOG_zh.md — bilingual project changelog
  What to do / Must NOT do: Create `CHANGELOG.md` (EN) + `CHANGELOG_zh.md` (ZH) at repo root, keepachangelog.com format. `## [v1.0.0] - 2026-07-31`: original plan delivery (scaffold repo + gh create; feature-branch git workflow; lib/rubbish-core.ps1 safety library; scan-drive/clean-drive/verify-report scripts; references (taxonomy/safety-rules/per-app-path-map); SKILL.md; dual-mode tests (sandbox + Pester 5 unit); install.ps1 + install into 3 agent skill dirs + opencode skill index updated). Also post-plan: README i18n (agent-first rewrite, bilingual, language switcher), English-only README cleanup, Limitations & Roadmap section, repository renamed to rubbish_cleaning_skill. NOTE: CLASSIFICATION.md lives OUTSIDE the repo (in the user's opencode config) — do NOT list it as a repo artifact; phrase as "opencode skill index updated (external config)". Link key files (e.g., `[SKILL.md](SKILL.md)`). ZH faithful translation with links preserved. MUST NOT fabricate future entries; MUST NOT alter git history.
  Parallelization: Wave 5 | Blocked by: 8 | Blocks: 13, 14 | Can parallelize with: 9
  References: `git log --oneline` history; README.md LIMITATIONS
  Acceptance criteria (agent-executable): Test-Path both files True; `Select-String 'v1.0.0'` → 1 match each; EN section `## [v1.0.0]`; ZH equivalent; both link at least SKILL.md + README.md + install.ps1; all `](` link targets exist
  QA scenarios: happy: files exist, entry present, links resolve; failure: broken link → fix, Evidence `.omo/evidence/rubbish-cleaner-optimization/task-10.txt`
  Commit: `docs: bilingual CHANGELOG (v1.0.0 history)`

- [x] 11. SKILL_zh.md + references/*_zh.md — Chinese mirrors + hyperlink all file-name mentions
  What to do / Must NOT do: (a) Create `SKILL_zh.md` + `references/junk-taxonomy_zh.md` + `references/safety-rules_zh.md` + `references/per-app-path-map_zh.md` (faithful Chinese translations; code/commands/paths untranslated; identical section structure; frontmatter `name: rubbish-cleaner` + Chinese description). Do NOT touch README* (already paired) or CHANGELOG* (todo 10). (b) HYPERLINK SYSTEM: in EVERY tracked .md (README.md, README_zh.md, SKILL.md, SKILL_zh.md, CHANGELOG.md, CHANGELOG_zh.md, references/*.md, references/*_zh.md), scan for plain-text mentions of other tracked filenames and replace with relative markdown links `[filename.ext](relative/path)`. Rules: (1) link only FIRST occurrence per section; (2) relative paths from referencing file; (3) verify target exists before linking; (4) do NOT link inside code fences; (5) do NOT link self-references; (6) do NOT alter existing intentional links (switcher, CI badge, license). MUST NOT touch .omo internal artifacts (plans/drafts are not user-facing).
  Parallelization: Wave 5 | Blocked by: 9, 10 | Blocks: 14 | Can parallelize with: none (needs docs current)
  References: current SKILL.md; current references/*.md
  Acceptance criteria (agent-executable): Test-Path True for 4 new files; each _zh starts with equivalent heading; SKILL_zh.md has frontmatter; hyperlink check script (agent-executable, completeness-based): (1) extract all plain-text mentions of tracked .md filenames OUTSIDE code fences from all .md files; assert ≥90% have been converted to `[filename](path)` links; (2) extract all `[text](path)` links from all .md, dedupe, assert EVERY target exists via Test-Path (0 broken links); (3) lower-bound sanity: ≥15 new hyperlinks beyond pre-existing switcher/License/CI-badge links
  QA scenarios: happy: 4 files + links valid; failure: broken link → fix; link in code fence → revert, Evidence `.omo/evidence/rubbish-cleaner-optimization/task-11.txt`
  Commit: `docs: Chinese translations + hyperlink all tracked .md files`

- [x] 12. tests/sandbox/run-sandbox-tests.ps1 + tests/unit/ — tests for all new features
  What to do / Must NOT do: EXTEND `tests/sandbox/run-sandbox-tests.ps1` with 5 new suites (append after existing 4, keep intact). Pattern: temp tree under `$env:TEMP\rubbish-cleaner-tests\<pid>\`, plain if/throw, cleanup in finally, SUITE lines. (a) `InvokeParallelForEach`: 10 temp dirs, parallel rename ThrottleLimit 2 → all processed; ThrottleLimit 1 → results match; 0 leftover jobs. (b) `CheckpointResume`: fake tree 2 categories → checkpoint produced; -Resume → skip message; partial resume correct. (c) `PlatformDetection`: dot-source platform.ps1 → IsWindows true (this machine); Get-FixedDriveLetters ≥1; Get-UserCacheDir non-empty. (d) `ScheduleParams`: `-Action List` → 0; `-Action Register -Drive C: -Policy safe` → exit 1 admin error; policy JSONs parse. (e) `MultiDrive`: two fake drive trees → `-Drives` scan → two per-drive run dirs; `-Parallel` same result. Also CREATE `tests/unit/optimization.Tests.ps1` (Pester 5: BeforeAll/It/Should) covering same 5 (one Describe per suite). MUST NOT break existing 4-suite flow.
  Parallelization: Wave 5 | Blocked by: 5, 6, 7, 8 | Blocks: 14
  References: existing sandbox harness; Invoke-ParallelForEach; checkpoint format; schedule.ps1; multi-drive
  Acceptance criteria (agent-executable): parse-check 0 errors both files; `tests/run-tests.ps1` → exit 0 (sandbox branch: 4+5=9 suites PASS); `Select-String 'SUITE'` → 9 SUITE lines; `RESULT: PASS`
  QA scenarios: happy: 9/9 PASS; failure: suite FAIL → fix code, rerun; job leak → Get-Job 0, Evidence `.omo/evidence/rubbish-cleaner-optimization/task-12.txt`
  Commit: `test: 5 new sandbox + Pester suites (parallel, checkpoint, platform, schedule, multi-drive)`

- [x] 13. SKILL.md + README.md + README_zh.md — cross-reference links for new files
  What to do / Must NOT do: After todos 10-11 create new files (CHANGELOG*, SKILL_zh*, references/*_zh*), update SKILL.md/README.md/README_zh.md to reference them: add CHANGELOG link in README intro (`[CHANGELOG](CHANGELOG.md)` + zh), add language-switcher additions for SKILL.md ↔ SKILL_zh.md at top of both (e.g., `[English](SKILL.md) | [简体中文](SKILL_zh.md)`), add `## 文档` section listing all bilingual pairs. MUST NOT break existing links; MUST NOT exceed 40KB SKILL.md.
  Parallelization: Wave 5 | Blocked by: 8, 11 | Blocks: 14 | Can parallelize with: 12
  References: todo 11 outputs; existing switcher pattern
  Acceptance criteria (agent-executable): README.md contains `[CHANGELOG](CHANGELOG.md)`; SKILL.md + SKILL_zh.md both have switcher lines linking each other; README_zh.md mirrors; all new links resolve (Test-Path)
  QA scenarios: happy: cross-links present; failure: missing/broken link → fix, Evidence `.omo/evidence/rubbish-cleaner-optimization/task-13.txt`
  Commit: `docs: cross-reference new bilingual files`

- [x] 14. Git finalize — feature branch push + merge --no-ff main + install re-sync
  What to do / Must NOT do: (a) Create branch `feature/rubbish-cleaner-optimization` from main; all todos 1-13 land on it (each its own commit). (b) Full test: `tests/run-tests.ps1` → 0; sandbox → 0; smoke `scan-drive.ps1 -Drive D: -Categories root-temps,root-logs` (read-only) → 0 + outputs. (c) `git status --porcelain` clean except gitignored. (d) Push `git push -u origin feature/rubbish-cleaner-optimization`. Network: probe `127.0.0.1:7897`; use working route (proxy or direct `-c http.proxy= -c https.proxy= -c http.version=HTTP/1.1`); retry 5x/20s. (e) Checkout main, `git merge --no-ff feature/rubbish-cleaner-optimization -m "Merge feature/rubbish-cleaner-optimization: cross-platform, multithreading, CI, schedule, multi-drive, docs"`. (f) Push main. (g) Verify local HEAD == remote main sha; worktree clean; feature branch kept. (h) `install.ps1` re-sync 3 agent dirs (new files: platform.ps1, schedule.ps1, policies JSON, CHANGELOG*, SKILL_zh*, references/*_zh* — all copied by install; CI workflow NOT copied, repo-only). MUST NOT force-push/squash/delete branches/commit directly to main.
  Parallelization: Wave 6 | Blocked by: 9, 10, 11, 12, 13 | Blocks: F1-F4
  References: user git convention; previous finalize patterns
  Acceptance criteria (agent-executable): branch = main after merge; `git log --oneline -1` = merge commit; `git ls-remote origin main` sha == local HEAD (string match); feature branch on origin; `INSTALL: PASS`; Test-Path platform.ps1 + schedule.ps1 + SKILL_zh.md in 3 installed dirs
  QA scenarios: happy: merge + push + install; failure: push rejected → pull --rebase origin main, re-merge, re-push, Evidence `.omo/evidence/rubbish-cleaner-optimization/task-14.txt`
  Commit: the merge commit itself

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit — verifier diffs executed repo against plan: all 14 todos `- [x]`; every required file exists (platform.ps1, schedule.ps1, policies/*.json, .github/workflows/test.yml, CHANGELOG.md, CHANGELOG_zh.md, SKILL_zh.md, references/*_zh.md ×3, updated scan/clean/verify-report/rubbish-core, updated per-app-path-map/safety-rules/SKILL/README*); 9-suite harness green; commits match per-todo list; git state per convention; worktree clean; no unplanned files
- [ ] F2. Code quality review — verifier checks: every .ps1 parse-clean; PS 5.1-compatible syntax (grep `??`, ternary `\w+\s*\?\s*\w+\s*:`, `ForEach-Object -Parallel` outside guarded branch, `-AsHashtable`, `&&`, `||` → 0); `-LiteralPath` discipline; no hardcoded drive letters; platform detection consistent; Invoke-ParallelForEach cleans jobs; SKILL.md < 40KB; hyperlink targets all resolve
- [ ] F3. Agent-executed end-to-end QA — verifier runs: `tests\run-tests.ps1` (exit 0, 9 suites PASS); `tests\sandbox\run-sandbox-tests.ps1` (exit 0, no hardcoded D:); read-only smoke `scan-drive.ps1 -Drive D: -Categories root-temps,root-logs` (0, delta <500MB); `scan-drive.ps1 -Drives D: -Categories root-temps` (0, per-drive run dir); `schedule.ps1 -Action List` (0); hyperlink checker script (all targets resolve); evidence written
- [ ] F4. Scope fidelity — verifier checks: all 9 deduplicated requirements delivered (cross-platform, test-drive param, multithreading, progress/resume, CI-Pester, scheduler, multi-drive, CHANGELOG, bilingual+hyperlinks); no scope creep (only the 9; no unlisted roadmap items); constraints honored (no new deps, no breaking signatures, no destructive ops on real drives during development, PS 5.1 fallbacks intact)

## Commit strategy
- Commit per todo on `feature/rubbish-cleaner-optimization` with exact messages listed per todo; never commit directly to main
- All 13 feature commits land on the branch; todo 14 merges `--no-ff` into main + pushes both
- Push network: probe proxy `127.0.0.1:7897`; if UP use default, if DOWN use `-c http.proxy= -c https.proxy= -c http.version=HTTP/1.1`
- `.omo/evidence/`, `.omo/start-work/`, `.omo/notepads/`, `.codegraph/` gitignored; `.omo/plans/` + `.omo/drafts/` committed (existing convention)
- User git identity: EntropyXi / 25804170@qq.com; repo remote: `github.com/EntropyXi/rubbish_cleaning_skill`

## Success criteria
- All 9 deduplicated requirements delivered once each (no duplication between "limitations" and "directions")
- `-Drive D:` hardcodes zero in tests; platform.ps1 cross-platform; Invoke-ParallelForEach multithreading; checkpoint + resume; CI 3-OS matrix runs Pester 5 + sandbox; schedule.ps1 + 2 policies; multi-drive -Drives + -Parallel (subprocess); bilingual CHANGELOG + translations; hyperlinks resolve
- Both test suites (9 sandbox + 5 Pester Describe) pass; `tests/run-tests.ps1` dual-mode exit 0
- Git state: main merged + pushed; feature on origin; worktree clean; install synced to 3 agent dirs
- Dual high-accuracy review (momus + Oracle) passed before execution; F1-F4 all APPROVE after
