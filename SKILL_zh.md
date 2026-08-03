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

**可移植调用规则**：脚本通过 Python 模块路径定位自身资源，
因此**从 skill 根目录运行，或使用绝对路径运行**均可，与当前工作目录无关：

```bash
cd <skill-root>
python -m pip install -r requirements.txt
python scripts/scanner.py -Drive X: -Categories root-temps,root-logs
```

## 平台支持

- **三平台**：需要 Python 3.10+ 与 `psutil`。
- **Windows 专属依赖**：`pywin32` 仅在 Windows 安装，用于 Task Scheduler 和 UAC。
- **Windows 专属特性**：`elevated-system`（UAC 提升清理，`-Yes` + 系统盘）
  依赖 Windows UAC，在 Linux/macOS 上不弹提升框，直接记 `SKIP_ELEVATION_DENIED`
  并**静默跳过**，其余清理照常进行。

## 2. 触发条件

用户要求 垃圾清理 / 清理垃圾 / 磁盘清理 / 盘符清理 / 清缓存，或提到 junk cleanup、
drive cleanup、cache cleanup、clean temp files 时触发。扫描为只读（不删除任何东西），
可安全运行。

## 3. 铁律（完整护栏见 [safety-rules.md](references/safety-rules.md)）

1. **只删扫描结果**：cleaner.py 只处理 candidates.csv 列出的行，CSV 之外一律不碰。
2. **保守默认（FM0）**：不带 `-Categories` 时只处理按时效门限的临时文件、日志与验证过的空目录；
   应用缓存与崩溃转储为显式开启。
3. **-LiteralPath + junction 感知**：绝不用裸 `-Path`/裸 `-Recurse`（会跟随 NTFS junction）。
4. **遵循文件系统锁语义**：Windows 锁定的文件记 `SKIP_LOCKED`；POSIX **默认跳过**无法安全 unlink 的
   文件（`SKIP_POSIX_UNSAFE`），需 `--allow-posix-unlink` 显式放开。任何平台都不强杀进程。
5. **进程感知（FM4）**：属主应用正在运行的分类整类跳过并明确提示，**绝不自动结束进程**；
   `--close-apps` 改为提示用户自行关闭。
6. **7 天规则**：临时文件只删 7 天以前的；删除前立即重验。
7. **隔离即移动（FM9）**：CAUTION 项移动到**同卷**隔离目录（Windows 为
   `X:\.rubbish-quarantine\run-<时间戳>\`，POSIX 回退到旧位置下的按运行子目录），永不删除。
8. **先预览再执行（FM13）**：`--dry-run` 逐文件打印预览，不触碰任何文件；真实清理前先跑一次。

## 4. 工作流

预检 → 扫描 → 展示分类列表 → 用户批准 → 清理 → 验证 → 交付报告。

```bash
# 1) 预检（可选）：确认目标盘为固定本地卷、确认占用进程
# 2) 扫描（只读，绝不删除）
python scripts/scanner.py -Drive X: -Categories root-temps,root-logs
# 3) 向用户展示分类列表（数量 + 总字节数），逐项征得批准
# 4) 用户批准后清理（-Yes = 批准 ASK 分类 + 不逐类询问）
python scripts/cleaner.py -Drive X: -Yes
#    或按分类交互式清理：不带 -Yes，脚本逐类 Read-Host 询问 y/n
# 5) 生成验证报告
python scripts/report.py -Drive X: -RunDir <run>
# 6) 把 <run>\summary.md 交给用户
```

要点（全部以 `<run>` = `<OutDir>\<盘符>-<时间戳>` 为运行目录，例如 `X-20260731-210209`）：

- **首次扫描后先展示，再清理**：把 scan 输出的分类列表（大小/数量）呈现给用户，
  获得批准后才带 `-Yes` 运行 cleaner.py。绝不未经批准就 `-Yes`。
- 不传 `-RunDir` 时 report.py 自动取该盘最新的运行目录。
- `-Categories root-temps,root-logs` 可过滤分类；`-Categories` 接受逗号分隔字符串。
- `-SkipElevated`：只把 elevated 批处理写入运行目录，**不弹 UAC**（测试/CI 安全）。
- elevated-system 仅当用户盘 + `-Yes` 且在**系统盘**上才真正弹 UAC；拒绝即跳过并继续。
- 未带 `-Yes` 时 ASK 分类（duplicate-archives、recycle-bin）整类跳过。
- **多盘批量**：`-Drives C:,D:` 一次扫描/清理多个盘，各盘独立运行目录，
  **顺序执行**（`-Parallel` 为安全考虑被忽略）。
- **断点续扫/续清**：`-Resume` 从该盘运行目录里的 checkpoint 继续，已完成分类
  不会重复处理（clean 侧跳过已清理行，scan 侧保留已有候选）。
- **定时清理**：`python scripts/schedule.py` 提供任务计划程序集成
  （`register --drive C: --policy safe --time 02:00`、`list`、
  `unregister --drive C:`，支持安全/激进策略档）。

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

- 运行目录：Windows 为桌面 `.omo\evidence\rubbish-cleaner\<盘符>-<时间戳>\`，Linux/macOS 为 `$HOME/.omo/evidence/rubbish-cleaner/`
  - `preflight.txt`（基线空闲字节）、`candidates.csv`（候选）、`scan-report.json`（分类报告）
  - `cleanup-errors.csv`（清理处置 CSV）、`summary.md`（验证报告，8 个 `##` 节）
  - elevated 运行时：`elevated` 批处理 + `elevated-result.txt`
- 隔离目录：**同卷** `X:\.rubbish-quarantine\run-<时间戳>\`（Windows），POSIX 回退到旧位置下的按运行
  子目录；`-QuarantineDir` 可覆盖。内容可恢复，永不自动删除。

## 7. MUST NOT 清单（红线）

完整清单见 [safety-rules.md](references/safety-rules.md) 第 10、11 节；此处仅列核心红线：

- 不删用户文档、已安装程序、WinSxS / Installer / DriverStore、游戏安装与存档、
  聊天数据（WeChat/QQ msg/file/contact）、`.claude` / `.codex` / `.gitconfig`。
- pagefile.sys / swapfile.sys / hiberfil.sys 只报告，不删。
- 不用 cleanmgr；不用 DISM `/ResetBase`；不强杀进程；不清空回收站。

## 8. 测试

双模式入口（详见 `tests/`）：

```bash
python tests/test_runner.py
```

- 系统存在 **pytest** → 运行六个 Python 套件；否则回退到 `test_runner.py` 的零依赖断言模式。
- 测试在临时目录下构建 fake tree，只触碰测试自己的临时根目录，
  **绝不接触真实数据**；exit 0 = 全部通过。
