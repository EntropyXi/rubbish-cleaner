# rubbish-cleaner 安全规则（Safety Rules）

本文件是 rubbish-cleaner 的**完整护栏清单**。这些规则由 `scripts/lib/rubbish-core.ps1`
（函数库）与 `scripts/scan-drive.ps1` / `scripts/clean-drive.ps1` / `scripts/verify-report.ps1`
共同实施。[SKILL.md](../SKILL.md) 只引用本文件，不重复全文。

## 1. 只处理扫描结果（candidates.csv）

clean-drive.ps1 只消费 scan-drive.ps1 产出的 `candidates.csv`
（表头 `Category|Risk|Path|SizeBytes|FileCount|Action`，管道分隔），逐行处理。
**任何不在该 CSV 中的路径绝不会被触碰**。elevated-system 例外：其清理目标由
UAC 提升批处理（`elevated.ps1`）内的绝对路径显式列出，且仅系统盘 + `-Yes` 可达。

## 2. -LiteralPath 优先

- 所有文件操作（Test-Path / Get-Item / Get-ChildItem / Remove-Item / Move-Item）一律使用
  `-LiteralPath`，**绝不使用 `-Path`**——路径不会被当作通配符解释。
- 清理脚本内任何裸通配符路径（如 `{X}\Wegame\*\cache`）都只在扫描侧通过显式
  `Get-ChildItem` 展开成具体路径后写入 CSV；删除侧永远拿到的是字面路径。

## 3. junction 感知递归 + PowerShell 5.1 注意事项

- **PS 5.1 的裸 `-Recurse` 会跟随 NTFS junction**（把 junction 当作真实子目录钻进去）。
  因此本流水线**从不使用裸 `-Recurse`**。
- 递归全部手动实现（迭代栈或 `Test-DirEmpty` 递归函数），每一步检查
  `Attributes -band [System.IO.FileAttributes]::ReparsePoint`，**reparse-point
  子项一律跳过、绝不下钻**。
- `Test-DirEmpty` 是 junction 感知的：只有"整棵树（排除 reparse-point 子项）无文件、
  无非 junction 子目录"才返回 $true。
- 该模式被 D 盘计划正式采用（d-drive-cleanup.md Task 2e/4a），防止误删通过
  junction 挂载的其他树。

## 4. 跳过锁定文件（skip-locked）

- 逐项删除，每项包在 try/catch 里（lib 的 `Invoke-SafeRemove`）。单个项目失败**绝不
  中断整次运行**（`$ErrorActionPreference = 'Continue'`）。
- IOException → `SKIP_LOCKED`；UnauthorizedAccessException → `SKIP_ACCESS_DENIED`；
  ItemNotFound → `SKIP_NOT_FOUND`（按异常链顺序判定，ItemNotFound 优先）。
- Windows 报告为锁定的文件会记入 cleanup CSV 并留给下次运行；POSIX 允许 unlink
  打开中的文件，此时按成功删除记录 `OK`。
- **绝不强杀进程、绝不重试循环**。

## 5. 7 天规则

- 临时文件分类（root-temps、user-temp、elevated-system 的 Windows\Temp）只列入
  `LastWriteTime` 早于 7 天的文件。
- 删除前**立即重验**：clean-drive.ps1 对 temp 文件重查年龄（现在太新 →
  `SKIP_TOO_RECENT`）；对目录重跑 junction 感知 `Test-DirEmpty`（现在非空 →
  `SKIP_NOT_EMPTY`）。

## 6. 隔离政策（quarantine = 移动，永不删除）

- CAUTION 分类（root-suspicious 等）→ `Invoke-Quarantine`：`Move-Item` 到
  `$env:USERPROFILE\Desktop\.omo\quarantine\<盘符>\`。**移动 ≠ 删除**，原始文件
  始终可移回。
- 隔离目录内容永不自动删除；verify-report.ps1 会现场断言"原文件消失 + 隔离副本存在"。
- 移动失败记 `MOVE_FAILED` + 错误消息，不中断运行。

## 7. 提升模式（elevation pattern：skip-if-denied）

- `elevated-system` 只在 `-IncludeElevated` 时被扫描；清理只在
  `$IsUserDrive`（用户盘）且（`-Yes` 或 `-SkipElevated`）时进入该分支。
- `-SkipElevated`（测试/CI 安全）：只把 `elevated.ps1` 写入运行目录，
  **绝不弹 UAC**（`Start-Process -Verb RunAs` 只在 `-Yes` + 系统盘时可达），并记
  `SKIP_ELEVATION_DENIED` 行后继续。
- `-Yes` + 非系统盘：拒绝运行，记 `SKIP_ELEVATION_DENIED` 继续。
- UAC 被拒 / 提升后未产出 `elevated-result.txt`：记 `SKIP_ELEVATION_DENIED`，整次
  运行正常结束（exit 0）。**不重试、不循环**。
- 提升批处理内 SoftwareDistribution 有独立护栏：仅当 `wuauserv` 服务确认 Stopped
  才清 `Download\*` 与 `DataStore.edb.old`/`DataStore.jfm.old`，之后重启服务；
  停服务失败 → `SKIP_SERVICE_RUNNING`，跳过该步。

## 8. 处置枚举（12 种 Disposition，见 lib `Get-JunkDispositions`）

| 枚举 | 含义 |
|------|------|
| `OK` | 删除成功 |
| `SKIP_LOCKED` | Windows 等平台报告文件被占用（IOException），跳过 |
| `SKIP_ACCESS_DENIED` | 无权限（UnauthorizedAccessException），跳过 |
| `SKIP_NOT_FOUND` | 项目不存在（已被删/移动），跳过 |
| `SKIP_NOT_EMPTY` | 目录重验非空，跳过（绝不强删） |
| `SKIP_JUNCTION` | 目标是 junction / reparse point，跳过 |
| `SKIP_TOO_RECENT` | 7 天规则重验失败，跳过 |
| `SKIP_WSL_REGISTERED` | WSL 注册中的发行版目录，跳过（D 盘先例） |
| `SKIP_ELEVATION_DENIED` | UAC 拒绝 / `-SkipElevated`，未执行 |
| `SKIP_SERVICE_RUNNING` | 相关服务运行中（wuauserv），跳过 |
| `QUARANTINED` | 已移动入隔离目录（非删除） |
| `MOVE_FAILED` | 隔离移动失败，记录错误消息 |

verify-report.ps1 的 `## 5. Skipped Items Table` 按此枚举计数；任何 SKIP 都不是失败，
只有"该删没删 + 没记录"才是问题。

## 9. ±500MB 容差

- verify-report.ps1 计算 `Total Freed = Final Free − Baseline Free`，并与
  per-category freed 估算（cleanup CSV 中 OK/QUARANTINED 行的 SizeBytes 之和）对账。
- 偏差在 **±500 MB（500,000,000 bytes）** 内视为可接受；超出时在 summary.md 中作为
  **NOTE**（而非失败）记录——因为其他进程会并发写入磁盘、快照时机有差异。
- 文件级断言（Test-Path）才是主要证据，磁盘数字只是旁证。

## 10. 永不删除清单（never-delete list）

- **用户文档**：Downloads / Desktop / Documents / Pictures / Music / Videos，
  以及任何作业、讲义、apiKey CSV。
- **已安装程序**：Program Files / Program Files (x86)（Steam、Zotero、Git、VS Code、
  Python、JetBrains IDEs、LGHUB、EA Desktop、Riot、WeGame、Antigravity、CherryStudio
  等），以及 C 盘 `.GamingRoot`。
- **Windows 组件存储**：`WinSxS`、`Installer`（含 `$PatchCache$`）、
  `System32\DriverStore\FileRepository`。
- **pagefile.sys / swapfile.sys / hiberfil.sys**：只报告，绝不删；不执行
  `powercfg /h off`（用户决定保持休眠）。
- **游戏安装**：已安装游戏目录与存档（Steam 全部已装游戏、workshop、League、
  Apex、MDPro3、CRYSTALiA、MuMu 虚拟机、nx_device 等）；Steam `appmanifest` 文件。
- **聊天数据**：WeChat/QQ 的 `msg`、`file`、`contact` 目录（xwechat_files 内除
  `cache` 外的全部内容）、Tencent Files。
- **个人配置**：`.claude`、`.codex`、`.gitconfig`、`.gemini`、pip 的 `pip.ini`、
  npm 的 `.npmrc`、Zotero 的 `.sqlite`、JetBrains 的 `system/config/plugins`。
- **特殊保留**：`$RECYCLE.BIN` 内容（本流水线不清空回收站）、`SYSTEM VOLUME
  INFORMATION`、D 盘 `Driver\19_电子信息保修卡` 保修文件夹、DimensionToTsuLovers
  符号链接、conda 环境（`anaconda3\envs` 与 `Lib`）。

## 11. 禁用命令（never-run）

- **NO cleanmgr**——不使用磁盘清理向导；本 skill 是脚本式精确清理。
- **NO `/ResetBase`**——DISM 只用 `/StartComponentCleanup`，绝不重置组件库基线。
- **NO force-kill**——绝不 `Stop-Process -Force` 来解锁文件；Windows 锁定项跳过并记录，POSIX 遵循原生 unlink 语义。
- **NO `powercfg /h off`**、**NO 裸 `-Recurse`**、**NO `Clear-RecycleBin`**
  （回收站只报告，除非用户单独手动批准）。

## 12. 跨平台注意事项

- **Linux/macOS 无 UAC/提升**：`elevated-system` 的 UAC 提升（`Start-Process -Verb RunAs`）
  是 Windows 专属。在 Linux/macOS 上不弹提升框，直接记 `SKIP_ELEVATION_DENIED`
  并**静默跳过**，其余清理逻辑不受影响。
- **隔离目录位置**：Windows 用 `$env:USERPROFILE\Desktop`，其他平台改用
  `Get-UserDocumentsDir`（见 `scripts/lib/platform.ps1`）解析用户文档目录，
  保证隔离目录始终落在可恢复的用户目录下。
- **elevated-system 仅 Windows**：Windows 上仅系统盘 + `-Yes` 时真正执行提升批次；
  在其他平台该分支**永远不执行**（静默跳过），不产生 elevated.ps1。
