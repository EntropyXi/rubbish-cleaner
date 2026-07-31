# rubbish-cleaner

跨 Agent 的磁盘垃圾清理技能

[English](README.md) | [简体中文](README_zh.md)

面向磁盘范围的垃圾清理技能：扫描磁盘上的垃圾文件（临时文件、缓存、日志、空目录），并在显式批准后安全清理。这是一个轻量级技能，可从 Claude Code、Codex 和 opencode 调用，无 Python 运行时依赖，基于 Windows PowerShell 5.1 构建。

工作流是 **扫描 → 批准 → 清理 → 校验 → 报告**：每一次删除都会被隔离（移动到备份目录）而不是直接销毁，同时生成一份校验报告，方便你审计到底改动了什么。清理按磁盘、按运行批次隔离作用域，绝不会触碰你指定的目录之外的内容。

## 安装

克隆或复制本仓库，然后运行安装脚本（无需管理员权限）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\rubbish_cleaning\scripts\install.ps1
```

安装脚本会把整个技能（SKILL.md、scripts/、references/、agents/、tests/、README.md、LICENSE、requirements.txt，除了 `.git`、`.omo`、`.codegraph` 之外的所有内容）复制到目标平台目录。脚本是幂等的：重复运行会覆盖已有副本。`-Target` 用于选择平台（`all` | `claude` | `codex` | `opencode`，默认 `all`）。

### Claude Code

安装到：

```
%USERPROFILE%\.claude\skills\rubbish-cleaner\
```

### Codex

安装到：

```
%USERPROFILE%\.codex\skills\rubbish-cleaner\
```

### opencode

安装到：

```
%USERPROFILE%\.config\opencode\skills\automation\rubbish-cleaner\
```

## 使用

按顺序执行四个阶段，把 `X:` 替换为目标磁盘，把 `<run>` 替换为扫描输出的运行 ID（一个时间戳，例如 `20260731-153000`）：

```powershell
# 1. 扫描（只读）：把垃圾候选清单写入 candidates.csv + scan-report.json
powershell -NoProfile -ExecutionPolicy Bypass -File D:\rubbish_cleaning\scripts\scan-drive.ps1 -Drive X:

# 2. 批准前先人工复核候选清单
#    （打开 candidates.csv / scan-report.json，逐一过目列出的条目）

# 3. 清理（受批准门控）：把获批的候选隔离到备份目录
powershell -NoProfile -ExecutionPolicy Bypass -File D:\rubbish_cleaning\scripts\clean-drive.ps1 -Drive X: -Yes

# 4. 校验 + 报告：确认磁盘状态并写入 verify-report
powershell -NoProfile -ExecutionPolicy Bypass -File D:\rubbish_cleaning\scripts\verify-report.ps1 -Drive X: -RunDir <run>
```

第 1 步不会删除任何内容，扫描只做清单盘点。第 3 步需要显式的 `-Yes` 参数，并且是隔离而非删除，所以任何清理后悔的东西都可以从备份目录中恢复。

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

测试套件是**双模式**的，会按条件选择执行器：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\rubbish_cleaning\tests\run-tests.ps1
```

- **模式 0（总是先执行）：** 通过 `[System.Management.Automation.Language.Parser]::ParseFile` 对 `scripts/` 和 `tests/` 下所有 `.ps1` 做语法解析检查；只要发现解析错误，就会在任何分支执行前以退出码 1 退出。
- **模式 1（分支）：**
  - **已安装 Pester 5.x** → 打印 `BRANCH: PESTER`，并通过 `Invoke-Pester -PassThru` 运行四个 `tests/unit/*.Tests.ps1` 套件；只有全部通过才以 0 退出。
  - **未安装 Pester 5.x** → 打印 `BRANCH: SANDBOX`，转交给零依赖 harness `tests/sandbox/run-sandbox-tests.ps1`（纯 PowerShell 断言，同样的四个套件，在 `$env:TEMP\rubbish-cleaner-tests\<pid>` 下自建并清理自己的临时目录树），透传其退出码。

无论走哪条分支，四个行为套件（扫描分类、安全删除+隔离、空目录检测、报告夹具）都会被覆盖。退出码 0 表示所有断言通过。

## 当前不足

- Windows-only：基于 PowerShell 5.1，尚无 pwsh 7（PowerShell Core）与 Linux/macOS 支持
- 双模式测试的 Pester 分支：本机无 Pester 5.x 时只能做语法解析校验（sandbox harness 是主执行路径）
- 沙盒测试夹具中硬编码了 `-Drive D:`（ReportFixture 与清理门控套件），无固定 D 盘的机器需参数化
- verify-report 的"隔离副本存在"断言（报告第 7 节）在沙盒测试中被跳过（需真实隔离目录）
- 垃圾识别基于静态路径映射（per-app-path-map.md），应用更新缓存路径后需人工维护；未做注册表卸载项自动发现
- 重复压缩包检测仅限盘根（同层同名压缩包+解压目录对），不递归子目录；无哈希级重复文件检测
- 无定时任务集成（任务计划程序/cron）；清理为手动或 agent 触发
- 隔离目录无 TTL/自动清理（安全优先的设计，隔离文件需手动处理）
- 阈值固定（如 7 天新鲜度规则），CLI 未暴露 `-MinSizeMB` / `-MaxAgeDays` 之类过滤参数
- 大磁盘单线程 PowerShell 枚举可能较慢，扫描无进度持久化/断点续扫

## 下一步迭代方向

- PowerShell 7（pwsh）兼容 + 跨平台缓存路径支持（Linux/macOS）
- GitHub Actions CI（预装 Pester 5.x）让 Pester 分支真正在 CI 中执行
- 测试夹具参数化（去掉硬编码 `-Drive D:`）
- 配置驱动分类法：用户可编辑 JSON（分类、路径、年龄/大小阈值、每用户覆盖）
- CLI 过滤参数：`-MinSizeMB` / `-MaxAgeDays` / dry-run 报告对比
- 递归重复检测 + 哈希级文件去重建议
- 应用路径自动发现（读卸载注册表键 → 推导各应用缓存路径）
- 任务计划程序集成：定时扫描 + 策略档（安全/激进）+ 释放空间通知
- 隔离区管理子命令：list / restore / purge，或 TTL 策略
- summary.md 的 HTML 报告渲染，便于人工审计
- WSL 感知增强（已有 SKIP_WSL_REGISTERED 处置，扩展到 WSL 发行版临时目录挂载）
- 多盘批量模式（`-Drives D:,E:`）与长扫描进度/断点续扫

## License

MIT，见 [LICENSE](LICENSE)。
