# 更新日志

本文件记录本项目所有重要变更。

格式基于 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)，并遵循 [语义化版本](https://semver.org/spec/v2.0.0.html)。

## [v2.1.0] - 2026-08-03

安全加固版本。一次跨卷隔离事故的事后分析识别出九个故障模式（FM1–FM9，完整分析见
[references/incident-rca_zh.md](references/incident-rca_zh.md)）；所有故障均已修复并配有回归测试。

### 新增

- 保守默认姿态（FM0）：未指定 `-Categories` 时，扫描/清理只处理按时效门限的临时文件、日志和验证过的空目录；
  应用缓存与崩溃转储改为显式开启。
- [`scripts/scanner.py`](scripts/scanner.py) 与 [`scripts/cleaner.py`](scripts/cleaner.py) 均支持
  `--dry-run` 预览：逐文件打印将要删除的内容，不触碰任何文件。
- 进程感知门（FM4）：若某分类的属主应用正在运行（Chrome、Steam、微信等），整类跳过并给出明确提示，
  **绝不自动结束进程**；`--close-apps` 改为提示用户自行关闭。
- 固定盘过滤（FM8）：只处理固定本地盘 — 可移动盘、CD/DVD 与网络盘一律排除。

### 变更

- POSIX unlink 默认跳过（FM1）：无法安全 unlink 的文件记为 `SKIP_POSIX_UNSAFE`，不再探测；
  需通过 `--allow-posix-unlink` 显式放开。
- 隔离与删除走相同的锁探测（FM2）：被锁定的文件不会被移动。
- 提升系统批次由已批准的候选行驱动，加入 `forfiles` 时效门，并在之后重启 `wuauserv` 服务（FM3）。
- 双动作执行（FM5）：缓存分类使用 `clean_contents`（删除目录内文件、保留目录）；
  `empty-dirs` 使用 `remove_if_empty`（仅删除验证过为空的目录）。
- 分类互斥（FM6）：分类之间不再在同一路径上重叠。
- 数据签名校验（FM7）：静态映射缓存目录的抽样内容与预期不符时升级为 `CAUTION` 并隔离，绝不原地
  `clean_contents`。
- 同卷隔离默认（FM9）：隔离文件移动到源卷 `X:\.rubbish-quarantine\run-<时间戳>\` 下 —
  消除了跨卷 `EXDEV` 导致的静默 `MOVE_FAILED`。[`scripts/report.py`](scripts/report.py) 也采用相同的解析
  逻辑，不再假定桌面隔离目录。

### 修复

- 修复 [事故 RCA](references/incident-rca_zh.md) 中的 FM1–FM9 安全发现：POSIX flock 缺口、隔离锁绕过、
  提升批次强制删除、缺少进程感知、目录与文件动作不匹配、分类重叠、过期路径映射、可移动盘、跨卷隔离 `EXDEV`。

### 测试

- 在 [`tests/test_safety_fm.py`](tests/test_safety_fm.py) 中新增 FM0–FM9 与 FM13 回归覆盖
  （全部套件共 57 条断言）。

## [v2.0.0] - 2026-08-01

### 变更

- 将扫描 → 批准 → 清理 → 校验 → 报告完整流程从 PowerShell 迁移到 Python 3.10+。
- 增加跨平台 `psutil` 运行时依赖；`pywin32` 仅在 Windows 安装，用于 UAC 和 Task Scheduler。
- 用六个 pytest 套件以及 compileall/零依赖回退测试入口替换 PowerShell 测试框架。
- 统一 Windows、Ubuntu 和 macOS 的 Python 3.10–3.12 CI，并加入兼容性检查和只读扫描 smoke 测试。
- 保留隔离优先清理、junction/符号链接安全、七天时效门、检查点续扫以及原生 Windows/POSIX 锁语义。

## [v1.1.0] - 2026-08-01

### 新增

- 为 [`scripts/scanner.py`](scripts/scanner.py)、[`scripts/cleaner.py`](scripts/cleaner.py) 和 [`scripts/report.py`](scripts/report.py) 增加了使用 `-Drives` 的多驱动器批量执行。可选的 `scanner.py -Parallel` 路径会并发扫描多个驱动器，扫描和清理会按类别发布进度；清理仍按顺序执行且必须获得批准。
- 增加了扫描和清理检查点及 `-Resume`，并通过 [`scripts/schedule.py`](scripts/schedule.py) 和 [`references/policies/`](references/policies/) 配置文件提供基于策略的计划任务。

### 变更

- 通过 [`scripts/lib/platform.py`](scripts/lib/platform.py) 增加固定驱动器解析和平台特定的默认路径，使受支持的 Windows、Linux 和 macOS 主机采用相应的驱动器与运行时语义。
- 更新了 [`scripts/lib/core.py`](scripts/lib/core.py)，以支持 POSIX 链接处理和被锁定项目的行为。

### 修复

- 防止重复评估根临时目录，并规范化系统临时目录根路径，同时保留重复的路径分隔符。
- 保持生产脚本可由 Windows PowerShell 5.1 解析。

### 测试

- 在 [`tests/test_runner.py`](tests/test_runner.py) 和 [`.github/workflows/test.yml`](.github/workflows/test.yml) 中明确 pytest/回退入口的通过/失败结果。
- 将 CI 扩展为 Windows、Ubuntu 和 macOS 矩阵，并包含 Windows PowerShell 5.1 测试入口。

## [v1.0.0] - 2026-07-31

### 新增

- 作为 `rubbish-cleaner` 技能完成仓库脚手架搭建，并通过 GitHub CLI（`gh`）创建；开发遵循 feature 分支 git 工作流。
- [SKILL.md](SKILL.md)：技能核心，采用渐进式披露。
- `scripts/lib/core.py`：安全函数库（分类、隔离、报告辅助函数）。
- 核心脚本：
  - `scripts/scanner.py`：只读磁盘扫描 + 垃圾分类。
  - `scripts/cleaner.py`：需批准的带隔离机制的安全清理。
  - `scripts/report.py`：清理后验证 + 摘要报告。
- 参考资料：[junk-taxonomy.md](references/junk-taxonomy.md)、[safety-rules.md](references/safety-rules.md)、[per-app-path-map.md](references/per-app-path-map.md)。
- 双模式测试套件：零依赖回退入口（`tests/test_runner.py`）与六个 pytest 测试文件。
- [install.py](scripts/install.py)：一键安装脚本；技能安装到 Claude Code、Codex 与 opencode 技能目录。

### 变更

- 以 agent-first 方式重写 [README.md](README.md)：通过 agent 快速上手、斜杠命令用法、弱化手动安装。
- 仅英文 README 清理：修复中英混合的标题与注释。
- 双语 README：新增 [README_zh.md](README_zh.md) 镜像与语言切换器。
- 在 README 中新增"限制与路线图"章节。
- 仓库更名为 `rubbish_cleaning_skill`。

关键文件：[SKILL.md](SKILL.md)、[README.md](README.md)、[install.py](scripts/install.py)、[CHANGELOG](CHANGELOG.md)。
