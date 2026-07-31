# rubbish-cleaner

Drive-scoped junk cleanup skill: scan a drive for junk files (temp files, caches, logs, empty directories) and clean them safely with explicit approval. A lightweight skill callable from Claude Code, Codex, and opencode — no Python runtime dependencies, built on Windows PowerShell 5.1.

The workflow is **scan → approve → clean → verify → report**: every deletion is quarantined (moved to a backup folder) instead of destroyed, and a verification report is generated so you can audit exactly what changed. Cleanup is per-drive and per-run-scoped, so it never touches anything outside the directory you point it at.

## 安装

Clone or copy the repo, then run the installer (no admin rights needed):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\rubbish_cleaning\scripts\install.ps1
```

The installer copies the whole skill (SKILL.md, scripts/, references/, agents/, tests/, README.md, LICENSE, requirements.txt — everything except `.git`, `.omo`, `.codegraph`) to the target platform directory. It is idempotent: re-running overwrites existing copies. `-Target` selects the platform (`all` | `claude` | `codex` | `opencode`, default `all`).

### Claude Code

Installed to:

```
%USERPROFILE%\.claude\skills\rubbish-cleaner\
```

### Codex

Installed to:

```
%USERPROFILE%\.codex\skills\rubbish-cleaner\
```

### opencode

Installed to:

```
%USERPROFILE%\.config\opencode\skills\automation\rubbish-cleaner\
```

## 使用

Run the four phases in order, replacing `X:` with the target drive and `<run>` with the run ID printed by the scan (a timestamp, e.g. `20260731-153000`):

```powershell
# 1. Scan (READ-ONLY): inventories junk candidates into candidates.csv + scan-report.json
powershell -NoProfile -ExecutionPolicy Bypass -File D:\rubbish_cleaning\scripts\scan-drive.ps1 -Drive X:

# 2. Review the candidates before approving anything
#    (open candidates.csv / scan-report.json and eyeball the listed items)

# 3. Clean (approval-gated): quarantines approved candidates to a backup dir
powershell -NoProfile -ExecutionPolicy Bypass -File D:\rubbish_cleaning\scripts\clean-drive.ps1 -Drive X: -Yes

# 4. Verify + report: confirms the drive state and writes verify-report
powershell -NoProfile -ExecutionPolicy Bypass -File D:\rubbish_cleaning\scripts\verify-report.ps1 -Drive X: -RunDir <run>
```

Nothing is deleted on step 1 — scanning is inventory-only. Step 3 requires the explicit `-Yes` flag and quarantines rather than deletes, so anything you regret can be restored from the backup directory.

## 文件结构

```
rubbish-cleaner/
├── SKILL.md                            # Skill 核心（渐进式披露）
├── README.md                           # 本文件
├── LICENSE                             # MIT
├── requirements.txt                    # 依赖说明（无第三方运行时依赖）
├── agents/
│   └── openai.yaml                     # Codex UI 元数据，Claude Code 可忽略
├── scripts/
│   ├── install.ps1                     # 一键安装到三平台 skill 目录
│   ├── scan-drive.ps1                  # 只读扫描 + 分类（阶段 1）
│   ├── clean-drive.ps1                 # 审批门控清理 + 隔离（阶段 3）
│   ├── verify-report.ps1               # 校验 + 汇总报告（阶段 4）
│   └── lib/
│       └── rubbish-core.ps1            # 安全函数库（分类/隔离/报告）
├── references/
│   ├── junk-taxonomy.md                # 垃圾文件分类法
│   ├── per-app-path-map.md             # 常见应用缓存/临时路径映射
│   └── safety-rules.md                 # 安全规则与排除清单
└── tests/
    ├── run-tests.ps1                   # 双模式测试入口
    ├── unit/
    │   ├── scan.Tests.ps1              # Pester 5 单元测试（扫描分类）
    │   ├── clean.Tests.ps1             # Pester 5 单元测试（安全删除+隔离）
    │   ├── core.Tests.ps1              # Pester 5 单元测试（核心库）
    │   └── report.Tests.ps1            # Pester 5 单元测试（报告）
    └── sandbox/
        └── run-sandbox-tests.ps1       # 零依赖回退 harness（无 Pester 时）
```

## 测试

The test suite is **dual-mode** and conditionally picks its runner:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\rubbish_cleaning\tests\run-tests.ps1
```

- **Mode 0 (always, first):** parse-checks every `.ps1` under `scripts/` and `tests/` via `[System.Management.Automation.Language.Parser]::ParseFile`; exits 1 before any branch if a parse error is found.
- **Mode 1 (branch):**
  - **Pester 5.x installed** → prints `BRANCH: PESTER` and runs the four `tests/unit/*.Tests.ps1` suites via `Invoke-Pester -PassThru`; exits 0 only if all pass.
  - **Pester 5.x not installed** → prints `BRANCH: SANDBOX` and delegates to the zero-dependency harness `tests/sandbox/run-sandbox-tests.ps1` (plain PowerShell asserts, same four suites, builds/cleans its own temp tree under `$env:TEMP\rubbish-cleaner-tests\<pid>`), propagating its exit code.

Either way the same four behavior suites (scan classification, safe delete + quarantine, empty-dir detection, report fixture) are covered. Exit code 0 means all assertions passed.

## License

MIT — see [LICENSE](LICENSE).
