# 更新日志

本文件记录本项目所有重要变更。

格式基于 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)，并遵循 [语义化版本](https://semver.org/spec/v2.0.0.html)。

## [v1.0.0] - 2026-07-31

### 新增

- 作为 `rubbish-cleaner` 技能完成仓库脚手架搭建，并通过 GitHub CLI（`gh`）创建；开发遵循 feature 分支 git 工作流。
- [SKILL.md](SKILL.md)：技能核心，采用渐进式披露。
- `lib/rubbish-core.ps1`：安全函数库（分类、隔离、报告辅助函数）。
- 核心脚本：
  - `scripts/scan-drive.ps1`：只读磁盘扫描 + 垃圾分类。
  - `scripts/clean-drive.ps1`：需批准的带隔离机制的安全清理。
  - `scripts/verify-report.ps1`：清理后验证 + 摘要报告。
- 参考资料：`references/junk-taxonomy.md`、`references/safety-rules.md`、`references/per-app-path-map.md`。
- 双模式测试套件：零依赖沙箱测试（`tests/sandbox/run-sandbox-tests.ps1`）与 Pester 5 单元测试（`tests/unit/`）。
- [install.ps1](scripts/install.ps1)：一键安装脚本；技能安装到 Claude Code、Codex 与 opencode 技能目录，并更新 opencode 技能索引（外部配置）。

### 变更

- 以 agent-first 方式重写 [README.md](README.md)：通过 agent 快速上手、斜杠命令用法、弱化手动安装。
- 仅英文 README 清理：修复中英混合的标题与注释。
- 双语 README：新增 [README_zh.md](README_zh.md) 镜像与语言切换器。
- 在 README 中新增"限制与路线图"章节。
- 仓库更名为 `rubbish_cleaning_skill`。

关键文件：[SKILL.md](SKILL.md)、[README.md](README.md)、[install.ps1](scripts/install.ps1)、[CHANGELOG](CHANGELOG.md)。
