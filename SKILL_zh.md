---
name: rubbish-cleaner
description: >
  扫描并安全清理驱动器上的垃圾文件（临时文件、缓存、日志、空目录、崩溃转储），
  需用户批准后才删除。适用于 Claude Code 和 Codex。Use when 用户要垃圾清理、
  清理垃圾、磁盘清理、盘符清理、清缓存，或提到 junk cleanup、drive cleanup、
  cache cleanup、clean temp files。
---

# rubbish-cleaner

## 卷兼容性

- Windows 使用固定本地盘符，例如 `C:`；Linux/macOS 使用根目录 `/`。
- 容量信息通过 .NET `DriveInfo` 获取，不依赖 Windows 专属的 `Get-Volume`。
- Windows 的证据与隔离目录仍位于桌面 `.omo` 下；Linux/macOS 使用 `$HOME/.omo/`。

![test](https://github.com/EntropyXi/rubbish_cleaning_skill/workflows/test/badge.svg)

[English](SKILL.md) | [简体中文](SKILL_zh.md)

扫描指定驱动器，对垃圾文件（临时文件、缓存、日志、空目录、重复压缩包、根目录可疑
dll/exe 等）分类列出，经用户批准后**安全**清理，并产出验证报告。清理规则全部来自
已实际执行验证过的 C 盘 / D 盘清理计划（详见 [junk-taxonomy.md](references/junk-taxonomy.md) 的源流）。

## 1. 多平台兼容说明

本 skill 面向三平台分发，安装位置不同但内容一致：

- **Claude Code**：`~/.claude/skills/rubbish-cleaner/`
- **Codex**：`~/.codex/skills/rubbish-cleaner/` —— 通过 YAML frontmatter、
  相对路径脚本调用和 `agents/openai.yaml`（允许隐式调用 `$rubbish-cleaner`）获得支持
- **opencode**：`~/.config/opencode/skills/automation/rubbish-cleaner/`

**可移植调用规则**：脚本内部通过 `$PSScriptRoot` 定位 `scripts\lib\rubbish-core.ps1`，
因此**从 skill 根目录运行，或使用绝对路径运行**均可，与当前工作目录无关：

```powershell
cd <skill-root>
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\scan-drive.ps1 -Drive X:
```

## 平台支持

- **Windows**：PowerShell 5.1（系统自带）与 pwsh 7 均支持。
- **Linux / macOS**：需要 pwsh 7（PowerShell Core）——脚本不依赖 Windows 专属 cmdlet。
- **PS 5.1 专属特性仅限 Windows**：`elevated-system`（UAC 提升清理，`-Yes` + 系统盘）
  依赖 Windows UAC，在 Linux/macOS 上不弹提升框，直接记 `SKIP_ELEVATION_DENIED`
  并**静默跳过**，其余清理照常进行。

## 2. 触发条件

用户要求 垃圾清理 / 清理垃圾 / 磁盘清理 / 盘符清理 / 清缓存，或提到 junk cleanup、
drive cleanup、cache cleanup、clean temp files 时触发。扫描为只读（不删除任何东西），
可安全运行。

## 3. 铁律（5 条，完整护栏见 [safety-rules.md](references/safety-rules.md)）

1. **只删扫描结果**：clean-drive.ps1 只处理 candidates.csv 列出的行，CSV 之外一律不碰。
2. **-LiteralPath + junction 感知**：绝不用裸 `-Path`/裸 `-Recurse`（PS 5.1 的
   `-Recurse` 会跟随 NTFS junction）。
3. **锁定即跳过**：被占用的文件记 `SKIP_LOCKED`，绝不强删、绝不强杀进程。
4. **7 天规则**：临时文件只删 7 天以前的；删除前立即重验。
5. **隔离即移动**：CAUTION 项（根目录 dll/exe）Move-Item 到隔离目录，永不删除。

## 4. 工作流

预检 → 扫描 → 展示分类列表 → 用户批准 → 清理 → 验证 → 交付报告。

```powershell
# 1) 预检（可选）：确认目标盘为固定本地卷、确认占用进程
# 2) 扫描（只读，绝不删除）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\scan-drive.ps1 -Drive X:
# 3) 向用户展示分类列表（数量 + 总字节数），逐项征得批准
# 4) 用户批准后清理（-Yes = 批准 ASK 分类 + 不逐类询问）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\clean-drive.ps1 -Drive X: -Yes
#    或按分类交互式清理：不带 -Yes，脚本逐类 Read-Host 询问 y/n
# 5) 生成验证报告
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-report.ps1 -Drive X: -RunDir <run>
# 6) 把 <run>\summary.md 交给用户
```

要点（全部以 `<run>` = `<OutDir>\<盘符>-<时间戳>` 为运行目录，例如 `X-20260731-210209`）：

- **首次扫描后先展示，再清理**：把 scan 输出的分类列表（大小/数量）呈现给用户，
  获得批准后才带 `-Yes` 运行 clean-drive.ps1。绝不未经批准就 `-Yes`。
- 不传 `-RunDir` 时 verify-report.ps1 自动取该盘最新的运行目录。
- `-Categories root-temps,root-logs` 可过滤分类；`-Categories` 接受逗号分隔字符串。
- `-SkipElevated`：只把 elevated.ps1 写入运行目录，**不弹 UAC**（测试/CI 安全）。
- elevated-system 仅当用户盘 + `-Yes` 且在**系统盘**上才真正弹 UAC；拒绝即跳过并继续。
- 未带 `-Yes` 时 ASK 分类（duplicate-archives、recycle-bin）整类跳过。
- **多盘批量**：`-Drives C:,D:` 一次扫描/清理多个盘，各盘独立运行目录，
  **顺序执行**（`-Parallel` 为安全考虑被忽略）。
- **断点续扫/续清**：`-Resume` 从该盘运行目录里的 checkpoint 继续，已完成分类
  不会重复处理（clean 侧跳过已清理行，scan 侧保留已有候选）。
- **定时清理**：`scripts\schedule.ps1` 提供任务计划程序集成
  （`-Action Register -Drive C: -Policy safe -Time 02:00`、`-Action List`、
  `-Action Unregister`，支持安全/激进策略档）。

## 5. 分类与风险

| 风险 | Action | 说明 |
|------|--------|------|
| SAFE | delete | 可再生缓存/临时文件，批准后删除 |
| CAUTION | quarantine | 移动到隔离目录（永不删除） |
| ASK | ask | 必须用户显式批准（`-Yes`）才处理 |
| ELEVATED | report-only | 只报告；清理走 UAC 提升批处理，拒绝即跳过 |

15 个分类的精确匹配规则与逐类 MUST-NOT 见 [junk-taxonomy.md](references/junk-taxonomy.md)；
应用路径模板见 [per-app-path-map.md](references/per-app-path-map.md)。

## 6. 证据与报告位置

- 运行目录：`$env:USERPROFILE\Desktop\.omo\evidence\rubbish-cleaner\<盘符>-<时间戳>\`
  - `preflight.txt`（基线空闲字节）、`candidates.csv`（候选）、`scan-report.json`（分类报告）
  - `cleanup-errors.csv`（清理处置 CSV）、`summary.md`（验证报告，8 个 `##` 节）
  - elevated 运行时：`elevated.ps1` + `elevated-result.txt`
- 隔离目录：`$env:USERPROFILE\Desktop\.omo\quarantine\<盘符>\`（可恢复）

## 7. MUST NOT 清单（红线）

完整清单见 [safety-rules.md](references/safety-rules.md) 第 10、11 节；此处仅列核心红线：

- 不删用户文档、已安装程序、WinSxS / Installer / DriverStore、游戏安装与存档、
  聊天数据（WeChat/QQ msg/file/contact）、`.claude` / `.codex` / `.gitconfig`。
- pagefile.sys / swapfile.sys / hiberfil.sys 只报告，不删。
- 不用 cleanmgr；不用 DISM `/ResetBase`；不强杀进程；不清空回收站。

## 8. 测试

双模式入口（详见 `tests/`）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tests\run-tests.ps1
```

- 系统存在 **Pester 5.x** → 运行 Pester 套件（`tests\unit\`）。
- 否则（本机 PS 5.1 自带 Pester 3.4）→ 回退到**零依赖 sandbox 套件**
  （`tests\sandbox\`，纯 PowerShell 断言）。
- 两套测试都在 `$env:TEMP` 下构建 fake tree，只触碰测试自己的临时根目录，
  **绝不接触真实数据**；exit 0 = 全部通过。
