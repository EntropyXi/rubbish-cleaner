# rubbish-cleaner

[English](README.md) | [简体中文](README_zh.md)

一个可被 Claude Code / Codex / opencode 调用的磁盘垃圾清理技能。

## 快速开始

这是给 LLM agent 使用的技能，不是传统 CLI 工具。你用大白话描述要清理什么，agent 负责干活。有两种开始方式：

**方式 1（推荐）：让 agent 帮你部署。** 把仓库克隆（或下载并解压）到任意位置，然后打开你的 agent，直接在对话里输入你的请求，例如：

```
/rubbish-cleaner 清理 D 盘的临时文件和缓存，不要动我的安装包和游戏存档
```

agent 会读取 SKILL.md 并自行部署技能（如果还没安装，它会自己运行 `scripts\install.ps1`，无需任何手动步骤），然后按内置的 扫描 → 批准 → 清理 → 校验 → 报告 流程执行，向你展示候选清单，并在删除任何东西之前征求你的确认。

**方式 2：手动安装（可选）。** 一条命令，无需管理员权限，可重复执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File <repo>\scripts\install.ps1
```

`-Target all|claude|codex|opencode` 用于选择平台（默认 `all`）。会安装到 `%USERPROFILE%\.claude\skills\rubbish-cleaner\`、`%USERPROFILE%\.codex\skills\rubbish-cleaner\` 和 `%USERPROFILE%\.config\opencode\skills\automation\rubbish-cleaner\`。

## 在 agent 中使用

调用方式：在 opencode 或 Claude Code 中输入 `/rubbish-cleaner`（斜杠命令）；Codex 通过它的技能显示名称触发。然后用大白话附上你的需求。技能的触发词（垃圾清理 / 清理垃圾 / 磁盘清理 / 盘符清理 / junk cleanup / drive cleanup / cache cleanup / clean temp files）也会自动激活它。

你可以在提示词里指定这些内容（技能会映射到扫描/清理参数）：

- 目标盘符，例如 `D:`
- 要包含或排除的类别（例如：不删安装包、不删回收站）
- 绝不触碰的路径或文件夹（不删哪些）
- 按天数指定的时效阈值（指定天数，默认 7 天规则）
- 只扫描（dry-run）还是也执行清理

提示词示例：

1. `/rubbish-cleaner 扫描 C 盘，列出可以释放空间的项目，先不要删除任何东西。`
2. `/rubbish-cleaner 清理 D 盘：删除超过 30 天的临时文件和缓存，但跳过 D:\Downloads 和 D:\Games 里的任何内容。`
3. `/rubbish-cleaner 看看 E 盘有什么垃圾？只要列出浏览器缓存和日志就行。`

agent 遵循的流程：扫描（只读盘点）→ 向你展示带大小的分类候选清单 → 等待你批准 → 安全清理（隔离 = 移动到备份目录，绝不永久删除；被占用的文件会跳过）→ 校验并写入汇总报告（`.omo\evidence\rubbish-cleaner\` 运行目录下的 `summary.md`）。

安全要点：一切按盘符、按运行批次隔离作用域；没有任何东西被永久删除（全部隔离）；每次删除前都会重新校验；支持 junction 感知；UAC 提升的系统清理是可选的，拒绝即跳过。

## 清理范围

完整分类见 `references/junk-taxonomy.md`，各应用路径见 `references/per-app-path-map.md`。简单来说：盘根临时文件与日志、重复压缩包、空目录、回收站（需批准）、各应用缓存（anaconda、WeGame、微信、Steam 残留等）；在系统盘上还包括浏览器/GPU/pip/npm/IDE 缓存、崩溃转储、缩略图，以及可选的高权限系统批次（Windows\Temp、Prefetch、SoftwareDistribution、CBS、DISM /StartComponentCleanup）。绝不触碰用户文档、已安装程序、系统组件存储。

## 文件结构

```
rubbish-cleaner/
├── SKILL.md                            # Skill 核心（渐进式披露）
├── README.md / README_zh.md            # 中英文文档（本文件）
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
