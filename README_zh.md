# rubbish-cleaner

## 卷兼容性

- Windows 使用固定本地盘符，例如 `C:`；Linux/macOS 使用根目录 `/`。
- 容量信息通过 .NET `DriveInfo` 获取，不依赖 Windows 专属的 `Get-Volume`。
- Windows 的证据与隔离目录仍位于桌面 `.omo` 下；Linux/macOS 使用 `$HOME/.omo/`。

![test](https://github.com/EntropyXi/rubbish_cleaning_skill/workflows/test/badge.svg)

[English](README.md) | [简体中文](README_zh.md)

一个可被 Claude Code / Codex / opencode 调用的磁盘垃圾清理技能。更新历史：[CHANGELOG.md](CHANGELOG.md)（简体中文版：[CHANGELOG_zh.md](CHANGELOG_zh.md)）。

## 文档

以下列出的核心用户文档以中英文成对维护：

- [README.md](README.md) ↔ [README_zh.md](README_zh.md)（本概览）
- [SKILL.md](SKILL.md) ↔ [SKILL_zh.md](SKILL_zh.md)（agent 技能核心）
- [CHANGELOG.md](CHANGELOG.md) ↔ [CHANGELOG_zh.md](CHANGELOG_zh.md)（更新历史）
- [references/junk-taxonomy.md](references/junk-taxonomy.md) ↔ [references/junk-taxonomy_zh.md](references/junk-taxonomy_zh.md)
- [references/per-app-path-map.md](references/per-app-path-map.md) ↔ [references/per-app-path-map_zh.md](references/per-app-path-map_zh.md)
- [references/safety-rules.md](references/safety-rules.md) ↔ [references/safety-rules_zh.md](references/safety-rules_zh.md)

## 快速开始

这是给 LLM agent 使用的技能，不是传统 CLI 工具。你用大白话描述要清理什么，agent 负责干活。有两种开始方式：

**方式 1（推荐）：让 agent 帮你部署。** 把仓库克隆（或下载并解压）到任意位置，然后打开你的 agent，直接在对话里输入你的请求，例如：

```
/rubbish-cleaner 清理 D 盘的临时文件和缓存，不要动我的安装包和游戏存档
```

agent 会读取 [SKILL.md](SKILL.md) 并自行部署技能（如果还没安装，它会自己运行 `scripts\install.ps1`，无需任何手动步骤），然后按内置的 扫描 → 批准 → 清理 → 校验 → 报告 流程执行，向你展示候选清单，并在删除任何东西之前征求你的确认。

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
- 只扫描（dry-run）还是也执行清理

提示词示例：

1. `/rubbish-cleaner 扫描 C 盘，列出可以释放空间的项目，先不要删除任何东西。`
2. `/rubbish-cleaner 清理 D 盘：清理内置规则选出的临时文件和应用缓存，但跳过 D:\Downloads 和 D:\Games 里的任何内容。`
3. `/rubbish-cleaner 看看 E 盘有什么垃圾？只要列出浏览器缓存和日志就行。`

agent 遵循的流程：扫描（只读盘点）→ 向你展示带大小的分类候选清单 → 等待你批准 → 安全清理（隔离 = 移动到备份目录，绝不永久删除；Windows 报告为锁定的文件会跳过，Linux/macOS 则遵循 POSIX 语义，打开中的文件仍可能被 unlink）→ 校验并写入汇总报告（`.omo\evidence\rubbish-cleaner\` 运行目录下的 `summary.md`）。

安全要点：一切按盘符、按运行批次隔离作用域；没有任何东西被永久删除（全部隔离）；每次删除前都会重新校验；支持 junction 感知；UAC 提升的系统清理是可选的，拒绝即跳过。

## 清理范围

完整分类见 [junk-taxonomy.md](references/junk-taxonomy.md)，各应用路径见 [per-app-path-map.md](references/per-app-path-map.md)。简单来说：盘根临时文件与日志、重复压缩包、空目录、回收站（需批准）、各应用缓存（anaconda、WeGame、微信、Steam 残留等）；在系统盘上还包括浏览器/GPU/pip/npm/IDE 缓存、崩溃转储、缩略图，以及可选的高权限系统批次（Windows\Temp、Prefetch、SoftwareDistribution、CBS、DISM /StartComponentCleanup）。绝不触碰用户文档、已安装程序、系统组件存储。

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
│   ├── schedule.ps1                    # 基于策略的平台定时任务集成
│   └── lib/
│       ├── platform.ps1                # 平台路径、固定盘和宿主程序辅助函数
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
    │   ├── report.Tests.ps1            # Pester 5 单元测试（报告）
    │   └── optimization.Tests.ps1      # Pester 5 单元测试（批量和断点续扫）
    └── sandbox/
        └── run-sandbox-tests.ps1       # 零依赖回退 harness（九个套件）
```

## 测试

本地测试入口是**双模式**的，会按条件选择执行器：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\rubbish_cleaning\tests\run-tests.ps1
```

- **模式 0（总是先执行）：** 通过 `[System.Management.Automation.Language.Parser]::ParseFile` 对 `scripts/` 和 `tests/` 下所有 `.ps1` 做语法解析检查；只要发现解析错误，就会在任何分支执行前以退出码 1 退出。
- **模式 1（分支）：**
  - **已安装 Pester 5.x** → 打印 `BRANCH: PESTER`，并通过 `Invoke-Pester -PassThru` 运行五个 `tests/unit/*.Tests.ps1` 文件中的 55 个测试；只有全部通过才以 0 退出。
  - **未安装 Pester 5.x** → 打印 `BRANCH: SANDBOX`，转交给零依赖 `tests/sandbox/run-sandbox-tests.ps1` harness（九个纯 PowerShell 套件，在 `$env:TEMP\rubbish-cleaner-tests\<pid>` 下自建并清理自己的临时目录树），透传其退出码。

GitHub Actions 在 Windows、Ubuntu 和 macOS 上运行语法解析检查、Pester 和 sandbox harness；Windows 还会通过 Windows PowerShell 5.1 运行完整测试入口。退出码 0 表示所选断言全部通过。

## 当前状态

### 已交付能力

- 跨平台固定盘支持：Windows 使用 `C:`，Linux/macOS 使用 `/`。
- 平台特定的默认路径和 PowerShell 宿主程序选择。
- 多盘处理和基于检查点的 `-Resume` 断点续扫/续清。
- 面向任务计划程序、cron 和 launchd 的策略定时。
- 审批门控、隔离优先的清理；链接安全遍历；以及原生 Windows/POSIX 锁语义。
- 覆盖 Windows、Ubuntu 和 macOS 的三平台 CI。

### 当前限制

- 垃圾识别使用静态缓存分类法，应用路径变化时需要维护。
- 重复压缩包检测仅限盘根且依赖名称；不递归，也不使用哈希。
- 没有隔离区管理子命令或 TTL 策略。
- 时效规则固定为七天；CLI 未提供 `-MinSizeMB` 或 `-MaxAgeDays`。
- sandbox harness 未覆盖报告第 7 节的真实隔离区集成断言。
- WSL 专项感知有限。
- GitHub Actions 仍会产生非阻塞的 `actions/checkout@v4` Node 运行时弃用警告。

### 优先级迭代方向

1. **可靠性和维护：** 增加真实隔离区集成覆盖，更新 `actions/checkout`，并扩展平台定时任务集成覆盖。
2. **用户控制和恢复：** 增加配置驱动分类法、CLI 阈值和 dry-run 报告差异，以及隔离区 list/restore/purge/TTL 管理。
3. **检测和报告：** 增加递归/哈希重复建议、应用路径发现、HTML 审计报告和 WSL 增强。

## License

MIT，见 [LICENSE](LICENSE)。
