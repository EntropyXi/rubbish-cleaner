---
slug: rubbish-cleaner
status: plan-written (2026-07-31)
intent: clear
review_required: false
plan_path: .omo/plans/rubbish-cleaner.md
metis: DONE - 10 findings all folded (1 critical: gh --source pushes main -> split create+remote add; 2 high: all 6 CLASSIFICATION stats lines + pre-flight verify; 3 medium: repo-exists failure mode, F3 spec, word-count->byte-limit; 4 low: -SkipElevated test-safe switch, wave 5-6 collapse, Test-FileLocked full spec confirmed present, draft report.ps1 rename)
pending-action: review .omo/plans/rubbish-cleaner.md (round 2 after fixes)
review:
  round 1 (rr-rubbish-cleaner-20260731-001):
    plan_sha256: 15191302531CED6B512BA4F36B8AAF67ABB69D7A86A4457F3317EE5286DA7AF6 (echoed identically by BOTH lanes - artifact verified)
    momus: APPROVED (ses_047ea73ccffe0xPmFb4OER7GXX, bg_be2f8091) - 1 advisory (wave numbering, fixed)
    independent: CHANGES_REQUESTED (ses_047ea59a1ffeo6LPNWVJVvLMSi, bg_ec54c977) - gh create determinism (CRITICAL, fixed: run from $env:TEMP + remote add), wave numbering (fixed), candidates.csv schema pin (fixed), junction-test SKIP-as-pass fallback (fixed), Pester 3.4 conflict -Force (fixed), preflight.txt format (fixed), sandbox cleanup parent dir (fixed); Test-FileLocked spec confirmed present in file (display-truncation artifact only, no fix needed)
  round 2 (rr-rubbish-cleaner-20260731-002):
    round_status: completed - BOTH APPROVED
    plan_sha256: CFB0BBA2E428C62794BA8BE78C0E65180A1BEF9588DB2892FF282C087AD0B0F0 (echoed identically by BOTH lanes - artifact verified)
    momus: APPROVED (ses_047dfd134ffeL9XVhmjJ8mP4f3, bg_a8ee5485) - 8/8 fixes verified, no new contradictions
    independent: APPROVED (ses_047dfb792ffejtxceTxWV9TOHt, bg_ceb796a0) - 7/7 fixes verified, zero contradictions; 3 non-blocking advisories (A1 F1-F4 post-push path, A2 root-suspicious matching rule, A3 percentage rounding) - ALL folded into plan, hence round 3 for final digest
  round 3 (rr-rubbish-cleaner-20260731-003):
    round_status: completed - BOTH APPROVED (FINAL)
    plan_sha256: 217B8870D2EC1D100ECE2DC9D8512ABC4F0A2C0468C3BBA6EC577CDEF2244D7C (echoed identically by BOTH lanes - final artifact verified)
    momus: APPROVED (ses_047d3fb68fferLHQAq9EIcml7q, bg_d97021d3) - A1/A2/A3 all PASS, no new contradictions
    independent: APPROVED (ses_047d3ea33ffegi34F43M6QJY3y, bg_96c1b310) - A1/A2/A3 all PASS, contradiction scan negative (6/6)
    fix/retry summary: round1 CHANGES_REQUESTED (7 fixes) -> round2 BOTH APPROVED (3 non-blocking advisories folded) -> round3 BOTH APPROVED (final digest 217B8870...D7C)
    live-plan validation: plan file not modified since round-3 dispatch (only draft updated); both lanes hashed the live file at first action with identical digests - PASS
    status: review-complete
    pending-action: execute plan in worker session (e.g. /start-work rubbish-cleaner)
approach: Multi-agent-callable drive-junk-cleanup skill (PowerShell), generalized from the C/D-drive cleanup architecture (scan -> classify -> user approval -> safe delete with guardrails -> verify -> report), packaged as a git repo (github.com/EntropyXi/rubbish-cleaner) with main+feature branch workflow, installed to Claude Code / Codex / opencode skill dirs; tests: conditional branch (Pester 5.x if installed, else zero-dep sandbox harness); requirements.txt manifest at repo root
note: scaffold-plan.mjs present but no shell execution tool available; draft hand-built to the identical template (same precedent as d-drive-cleanup); naming resolved: rubbish-cleaner; test strategy resolved: dual-mode conditional (user's scope change 2026-07-31)
---

# Draft: rubbish-cleaner

## Components (topology ledger)
| id | outcome (one line) | status | evidence path |
| --- | --- | --- | --- |
| skill-core | SKILL.md with trigger conditions, safety rules, scan->approve->clean->verify->report workflow (cross-agent: Claude Code / Codex / opencode) | pending | previous plans .omo/plans/c-drive-cleanup.md, d-drive-cleanup.md |
| scan-module | scripts/scan-drive.ps1: drive inventory + junk categorization (generic patterns + per-app known paths), outputs categorized JSON/CSV | pending | .omo/evidence/d-task-1-d-drive-cleanup.txt (inventory precedent) |
| safety-layer | safe-delete / quarantine / skip-locked / junction-aware helpers + error CSV + free-space delta accounting | pending | .omo/evidence/cleanup-errors.csv, d-cleanup-errors.csv (CSV format precedent), wave4-elevated.ps1 (PS patterns) |
| report-module | scripts/verify-report.ps1: summary.md with baseline/final free, per-category freed, skipped table, quarantine note | pending | .omo/evidence/summary.md, d-summary.md (8-field report precedent) |
| test-harness | tests/run-tests.ps1 dual-mode: Pester 5.x detected -> Invoke-Pester on tests/unit; else -> zero-dep sandbox harness tests/sandbox/run-sandbox-tests.ps1 (fake dir tree, plain if/throw asserts, exit code); both suites cover the SAME behavior matrix | pending | aigc-reduce tests/test_aigc_scan.py (user's testing convention) |
| packaging | repo layout (README, LICENSE Apache-2.0, .gitignore, requirements.txt manifest, agents/openai.yaml for Codex) + install script to 3 agent dirs + opencode CLASSIFICATION.md index update | pending | aigc-reduce repo layout (multi-platform convention), CLASSIFICATION.md (index update rule) |
| git-lifecycle | gh repo create EntropyXi/<name>; feature branch dev -> run tests -> push -> merge main -> push (user's main/feature convention) | pending | D:\yugioh-workflow-rag\.git\config (main+feature on origin), ~/.gitconfig (proxy, user) |

## Open assumptions (announced defaults)
| assumption | adopted default | rationale | reversible? |
| --- | --- | --- | --- |
| Skill language | PowerShell 5.1 (Windows) only | previous architecture all PS; platform win32; PS 5.1 junction caveat documented | yes |
| Safety model | dry-run by default; scan->categorized list->user/agent approval->execute; quarantine (move, never delete) for uncertain files; skip-locked; 7-day recency rule for temps | proven in C/D plans (F1-F4 pass) | yes |
| Categories | user temps/caches, browser caches, crash dumps, GPU shader caches, dev caches (pip/npm/.cache), IDE caches, empty dirs, installer leftovers, duplicate archives, recycle bin (ask), elevated system batch (skip-if-denied) | C-drive plan taxonomy (12 todos, 5 waves) | yes |
| Install targets | all 3: ~/.claude/skills/, ~/.codex/skills/, ~/.config/opencode/skills/automation/ + CLASSIFICATION.md index | user said "各大agent"; user maintains aigc-reduce in all | yes |
| Repo host | github.com/EntropyXi/<name>.git, https push via global proxy 127.0.0.1:7897, gh CLI installed+authenticated | yugioh-workflow-rag precedent | yes |
| License | MIT | user's own skill convention (aigc-reduce README:153 "License: MIT"); opencode awesome-skills is Apache-2.0 but user's repo precedent wins | yes |
| Elevated system categories | optional module, skip-if-denied (UAC), never blocks | C-drive Wave 4 precedent (user declined UAC -> SKIP_ELEVATION_DENIED logged) | yes |
| Report location | $env:USERPROFILE\Desktop\.omo\evidence\ per run + per-drive scan cache | previous plans used Desktop\.omo | yes |

## Findings (cited - path:lines)
- Previous cleanup architecture executed successfully twice: C: 52.06GB freed (summary.md, all 12 todos + F1-F4 PASS), D: 17.22GB freed (d-summary.md, 9/9 assertions PASS) - the architecture to generalize
- PowerShell patterns established: -LiteralPath everywhere, per-item try/catch -> CSV with Disposition enum (OK/SKIP_LOCKED/SKIP_ACCESS_DENIED/SKIP_NOT_FOUND/SKIP_NOT_EMPTY/SKIP_JUNCTION/SKIP_TOO_RECENT/SKIP_WSL_REGISTERED/SKIP_ELEVATION_DENIED/SKIP_SERVICE_RUNNING), junction-aware Test-DirEmpty (PS 5.1 follows junctions!), quarantine via Move-Item, 7-day rule for temps, free-space via (Get-Volume).SizeRemaining with +/-500MB tolerance, wave4-elevated.ps1 pattern (Start-Process -Verb RunAs, absolute paths, result file)
- Skill format conventions: SKILL.md + YAML frontmatter (name, description, third-person triggers) + scripts/ + references/ + assets/; progressive disclosure (<5k words SKILL.md); imperative style (skill-creator SKILL.md:1-215)
- User's multi-platform skill precedent: aigc-reduce in BOTH ~/.claude/skills/ and ~/.codex/skills/ (Codex variant adds agents/openai.yaml, README.md, LICENSE, .gitignore, tests/; ~/.claude/skills/aigc-reduce:1-60 dual-platform notes)
- opencode skills dir has 5 categories; new skills go in the appropriate category dir + update CLASSIFICATION.md index (CLASSIFICATION.md:117-119); automation/ is the fitting category (file-organizer precedent)
- Git: user EntropyXi / 25804170@qq.com, https proxy 127.0.0.1:7897 in global config (~/.gitconfig:1-11); existing repo convention main+feature both pushed to origin (D:\yugioh-workflow-rag\.git\config:8-17); gh.exe installed (C:\Program Files\GitHub CLI\gh.exe) and authenticated (AppData\Roaming\GitHub CLI\hosts.yml)
- Drive inventory findings from previous scans: D: had Driver 7GB, conda pkgs 16.7GB, root archives 646MB, empty Steam dirs 0.8GB, WeGame residue 564MB, WeChat cache 346MB; C: had NVIDIA DXCache 22.9GB, pip 13.8GB, npm 7.3GB, .cache 2.7GB, thumbcache 498MB, crash dumps 318MB, hiberfil 12.7GB report-only - these feed the skill's junk taxonomy knowledge base (references/junk-taxonomy.md)

## Decisions (with rationale)
- Architecture: ADOPT previous architecture (user delegated judgment; it is dual-reviewed and proven). Generalize: drive letter becomes a parameter; paths become category rules (generic globs + per-app template paths); everything else (safety layer, evidence, report, QA) carries over
- Skill must work when invoked by ANY agent: SKILL.md must contain the FULL self-contained workflow (scan->approve->clean->verify->report) with exact script invocations, so the calling agent needs no prior context
- Test strategy (user scope change 2026-07-31): DUAL-MODE CONDITIONAL — tests/run-tests.ps1 first detects Pester 5.x via `Get-Module -ListAvailable -Name Pester | Where-Object { $_.Version -ge [version]'5.0.0' }`; if found -> `Invoke-Pester -Path tests/unit -PassThru -Output Detailed` (Pester 5 syntax, exit 1 on FailedCount>0); if NOT found -> run zero-dep sandbox harness tests/sandbox/run-sandbox-tests.ps1 (constructs fake junk tree in a temp dir, runs scan/classify/safe-delete/report against it, plain if/throw asserts, exit code). Both suites assert the SAME behavior matrix (category classification, quarantine=move-not-delete, skip-locked simulation, junction-aware empty-dir check, report 8 fields, free-space delta accounting). Rationale: PS 5.1 machine, no forced installs, portability across agent machines, matches aigc-reduce plain-test convention; Pester branch adds formal unit coverage when available
- requirements.txt (user scope change 2026-07-31): comment-only manifest at repo root declaring NO Python runtime dependencies (skill is PowerShell 5.1 built-in only) + optional Pester 5.x dev dependency install hint; if the user intended a Python component, they will correct at the brief
- Naming: rubbish-cleaner (repo + skill name)
- Git: create repo via gh (installed + authenticated); dev on feature/<name> branch; commit per component; push feature after tests pass; merge to main; push main

## Scope IN
- Skill repo: SKILL.md, scripts/ (scan, clean, verify, report, install, lib/), references/ (junk taxonomy, safety rules, per-app path map), agents/openai.yaml (Codex), README.md, LICENSE (Apache-2.0), .gitignore, requirements.txt (comment-only manifest), tests/ (run-tests.ps1 dual-mode + unit/ Pester 5-syntax suite + sandbox/ zero-dep harness)
- Generalization of all proven cleanup categories to per-drive rules
- Install to 3 agent skill dirs + opencode CLASSIFICATION.md index
- Git lifecycle per user convention (feature -> push -> merge -> main -> push)

## Scope OUT (Must NOT have)
- NOT the one-shot C/D cleanup execution (already done)
- NOT touching actual user drives during development (all testing in sandbox dirs)
- NO new cleanup categories beyond what previous architecture proved + per-drive generic rules
- NOT auto-executing destructive actions without approval (dry-run default)

## Open questions
ALL RESOLVED (2026-07-31):
1. Repo/skill name -> rubbish-cleaner (user picked recommended)
2. Test strategy -> user scope change: DUAL-MODE conditional (Pester 5.x if installed, else zero-dep sandbox harness); both suites assert the same behavior matrix
3. requirements.txt -> comment-only manifest (no Python runtime deps; optional Pester 5.x dev hint) — interpretation flagged in brief for user correction

## Approval gate
status: approved-by-user (2026-07-31, "可以")
Approach: 12-task plan (8 waves) as topology ledger: W0 repo scaffold+git+gh create, W1 feature branch, W2 lib, W3 scan, W4 clean+report parallel, W5 references+SKILL.md, W6 tests (dual-mode), W7 packaging+install+index+README, W8 feature QA+push, W9 merge main+push; final verification wave F1-F4
Next action: write .omo/plans/rubbish-cleaner.md (hand-built), mandatory Metis gap analysis, append todos, fill TL;DR last
