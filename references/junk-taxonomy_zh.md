# rubbish-cleaner 分类法（Junk Taxonomy）

本文件定义 rubbish-cleaner 扫描的全部 15 个垃圾分类：每个分类的匹配规则、风险等级、
Action 映射与 MUST-NOT 边界。**内容与 `scripts/scan-drive.ps1` 的实现一一对应**，
任何修改必须以脚本实现为准，禁止凭想象扩充。

**源流（lineage）**：本分类法源自两个已经实际执行并验证过的清理计划——
`.omo/plans/c-drive-cleanup.md`（C 盘，2026-07-31 执行，释放 52.06 GB）与
`.omo/plans/d-drive-cleanup.md`（D 盘，2026-07-31 执行，释放 17.22 GiB）。
scan-drive.ps1 将这些人工验证过的目标泛化为可复用的、驱动器无关的分类规则。

## 风险 → Action 固定映射（对所有行恒定不变）

| 风险 | Action | 含义 |
|------|--------|------|
| SAFE | `delete` | 可再生缓存/临时文件，批准后直接删除 |
| CAUTION | `quarantine` | 移动（Move-Item）到隔离目录，**永不删除** |
| ASK | `ask` | 必须用户显式批准（`-Yes`）才处理，否则整类跳过 |
| ELEVATED | `report-only` | 只报告；清理只能由 UAC 提升的批处理执行，拒绝即跳过 |

## 分类总表

| id | 风险 | 触发位置 | Action |
|----|------|----------|--------|
| root-temps | SAFE | 驱动器根目录 `Temp`/`tmp`/`temp` 下的顶层文件 | delete |
| root-logs | SAFE | 驱动器根目录的日志/临时文件 | delete |
| duplicate-archives | ASK | 驱动器根目录的压缩包（有同名解压目录） | ask |
| empty-dirs | SAFE | 驱动器根目录的顶层空目录 | delete |
| recycle-bin | ASK | 驱动器根目录 `$RECYCLE.BIN` | ask |
| root-suspicious | CAUTION | 驱动器根目录的 `.dll`/`.exe`（basename 排除规则） | quarantine |
| app-caches | SAFE | 驱动器上的应用缓存路径（见 [per-app-path-map.md](per-app-path-map.md)） | delete |
| browser-caches | SAFE | 用户配置文件中的 Chrome/Edge 缓存（仅用户盘） | delete |
| gpu-shader | SAFE | NVIDIA DXCache/GLCache、D3DSCache（仅用户盘） | delete |
| dev-caches | SAFE | pip/npm/`.cache` 开发者缓存（仅用户盘） | delete |
| ide-caches | SAFE | JetBrains/Zotero/Jedi 缓存（仅用户盘） | delete |
| crash-dumps | SAFE | CrashDumps + 顶层 Crashpad 目录（仅用户盘） | delete |
| thumbnail-cache | SAFE | Explorer thumbcache/iconcache（仅用户盘） | delete |
| user-temp | SAFE | `%LOCALAPPDATA%\Temp` 顶层文件（仅用户盘） | delete |
| elevated-system | ELEVATED | Windows 系统目录（需 `-IncludeElevated`） | report-only |

---

## root-temps（SAFE → delete）

- **include patterns**：`<X>:\Temp`、`<X>:\tmp`、`<X>:\temp`（三者按 NTFS 大小写不敏感
  解析后去重）下的**顶层文件**，且 `LastWriteTime` 早于 7 天前。
- **MUST-NOT**：绝不递归到子目录；绝不删 7 天以内的文件；绝不删除 `Temp`/`tmp`/`temp`
  目录本身。
- **notes**：7 天规则在 clean-drive.ps1 删除前会再次校验（`SKIP_TOO_RECENT`）。

## root-logs（SAFE → delete）

- **include patterns**：驱动器根目录（不递归）下的 `<X>\*.log`、`<X>\*.tmp`、
  `*_install*.log` 文件。
- **MUST-NOT**：不递归子目录；不删子目录内的日志；不删根目录的其他类型文件。
- **notes**：`*_install*.log` 是安装残留日志（D 盘先例：mapinfo_install.log）。

## duplicate-archives（ASK → ask）

- **include patterns**：驱动器根目录下的 `<X>\*.zip|*.rar|*.7z`，且**存在同名解压目录**
  紧邻其旁（仅删压缩包，永不删解压目录）。
- **MUST-NOT**：没有同名解压目录的压缩包不列入；绝不删除解压出的文件夹；绝不删除
  安装包/程序文件。
- **notes**：D 盘先例删除了 6 个多余压缩包（~646 MB）并保留了全部解压目录。
  该分类是 ASK：不带 `-Yes` 时整类跳过。

## empty-dirs（SAFE → delete）

- **include patterns**：驱动器根目录的顶层目录，且通过 junction 感知的
  `Test-DirEmpty`（整个子树除 reparse-point 子项外无文件、无非 junction 子目录）。
- **MUST-NOT**：跳过 `$RECYCLE.BIN`、`SYSTEM VOLUME INFORMATION`、`.CLAUDE`；
  跳过 junction（`Test-IsJunction`）；绝不使用裸 `-Recurse`；非空目录绝不删除
  （删除前会立即重验，`SKIP_NOT_EMPTY`）。
- **notes**：Steam `steamapps\common` 下的空目录走的是 app-caches 分类，规则相同。

## recycle-bin（ASK → ask）

- **include patterns**：`<X>:\$RECYCLE.BIN` 整目录的字节数 + 文件数（只统计，
  junction 安全遍历）。
- **MUST-NOT**：绝不自动清空；即使 `-Yes` 批准，目录也会因 Test-DirEmpty 重验失败而
  `SKIP_NOT_EMPTY`——本流水线**不会清空回收站**，只报告。
- **notes**：C 盘先例由用户单独批准用 `Clear-RecycleBin -Force` 清空，那是人工操作，
  不是本 skill 的行为。

## root-suspicious（CAUTION → quarantine）

- **include patterns**：驱动器根目录的 `<X>\*.dll` 与 `<X>\*.exe`，其 **basename
  （不含扩展名）** 既不匹配任何顶层目录名，也不匹配 `<X>\Program Files` 与
  `<X>\Program Files (x86)` 下任一子目录名。
- **MUST-NOT**：匹配到排除名单的 dll/exe 绝不列入；**只隔离（Move-Item），绝不删除**；
  隔离目录内的文件永不自动删除。
- **notes**：basename 排除规则用于放过"与已安装程序同名的合法文件"。D 盘先例：
  `dinput8.dll`、`sdhdship.exe`（游戏破解补丁，被隔离而非删除，随时可移回）。

## app-caches（SAFE → delete）

- **include patterns**：驱动器上的应用缓存路径，逐条 existence 检查后统计整目录。
  精确模板见 [per-app-path-map.md](per-app-path-map.md)：anaconda3 `pkgs\cache`、WeGame
  `*\tiny_cache` + `*\cache`、WeChat `xwechat_files\**\cache`（junction 安全递归查找）、
  Steam `steamapps\common` 空目录、Ubisoft `Ubisoft Game Launcher\cache`。
- **MUST-NOT**：绝不跟随 junction（`Find-DirsNamed` 跳过 reparse point）；绝不删
  WeChat 的 `msg/file/contact` 用户数据；绝不删 Steam 非空游戏目录与 appmanifest 文件；
  绝不删缓存目录本身（应用会重建）。
- **notes**：WeChat 缓存查找是"按名字找目录"，找到后不再下钻（其内容由该目录自己的
  行覆盖），避免重复统计。

## browser-caches（SAFE → delete；仅用户盘）

- **include patterns**（解析自 `$env:LOCALAPPDATA`）：Chrome 与 Edge 的
  `...\User Data\Default\{Cache, Code Cache, GPUCache}` 以及
  `...\User Data\Crashpad\reports`（4 个路径 × 2 个浏览器）。
- **MUST-NOT**：绝不删浏览器配置档（`Default\Cookies`、`Local Storage`、`Login Data`、
  `Preferences` 等）；绝不删这 4 个子目录以外的任何文件；绝不跟随 junction。
- **notes**：仅当用户配置文件位于被扫描盘上（`$IsUserDrive`）时才会评估。

## gpu-shader（SAFE → delete；仅用户盘）

- **include patterns**（`$env:LOCALAPPDATA`）：`NVIDIA\DXCache`、`NVIDIA\GLCache`、
  `D3DSCache`。
- **MUST-NOT**：绝不删 NVIDIA 目录本身或显卡配置；游戏运行中锁定的着色器文件
  记 `SKIP_LOCKED`，绝不强删。
- **notes**：C 盘先例中 NVIDIA DXCache 达 22.9 GB，是最大的单项回收来源之一。

## dev-caches（SAFE → delete；仅用户盘）

- **include patterns**：`$env:LOCALAPPDATA\pip\cache`、`$env:LOCALAPPDATA\npm-cache`、
  `~\.cache\{torch, huggingface, opencode, codex-runtimes, pkg}`（5 个具名子目录）。
- **MUST-NOT**：绝不删 pip 配置（`pip.ini`）或 npm 配置（`.npmrc`）；绝不删缓存目录
  本身；绝不删 `.cache` 整目录或其他未列出的子目录。
- **notes**：C 盘先例中 pip 13.8 GB + npm 7.3 GB + `.cache` ~2.6 GB。扫描只做目录
  统计，清理交给 clean-drive.ps1 的逐项安全删除。

## ide-caches（SAFE → delete；仅用户盘）

- **include patterns**：
  - JetBrains：`$env:LOCALAPPDATA\JetBrains\<IDE>\caches` 与 `<IDE>\log`；
    `Toolbox`/`Toolbox-Dev` 额外含 `cache` 与 `logs`。
  - Zotero：`$env:APPDATA\Zotero\Zotero\Profiles\*\cache2`、`startupCache`、
    `shader-cache`。
  - Jedi：`$env:LOCALAPPDATA\Jedi\Jedi\*\*.pkl`。
- **MUST-NOT**：绝不删 IDE 的 `system`、`config`、`plugins`、`jbr`、`options`；
  绝不删 Zotero 配置档的 `.sqlite`/配置；Jedi 只删 `*.pkl`；绝不删 IDE 父目录。
- **notes**：C 盘先例验证 JetBrains 3 个 IDE 清理后健康（caches 重建、system/config
  完好）。

## crash-dumps（SAFE → delete；仅用户盘）

- **include patterns**：`$env:LOCALAPPDATA\CrashDumps` 整目录；以及
  `$env:LOCALAPPDATA` 下名为 `Crashpad` 的非 junction 顶层目录。
- **MUST-NOT**：绝不跟随 junction 版本的 `Crashpad`；绝不删非 dump 的崩溃配置
  （`settings.json` 等属浏览器的 browser-caches 范畴，这里只删整目录统计项）。
- **notes**：C 盘先例删除了 10 个 ~318 MB 的转储文件及多个应用的 Crashpad 报告。

## thumbnail-cache（SAFE → delete；仅用户盘）

- **include patterns**：`$env:LOCALAPPDATA\Microsoft\Windows\Explorer` 下
  `thumbcache_*.db` 与 `iconcache_*.db`。
- **MUST-NOT**：绝不删 Explorer 目录里的其他文件；explorer.exe 锁定（访问被拒）时
  记 `SKIP_LOCKED` 或 `SKIP_ACCESS_DENIED`，绝不杀进程。
- **notes**：C 盘先例 thumbcache_2560.db 176 MB。锁定处理：文件被 explorer 占用时
  跳过并记录，留给下次运行。

## user-temp（SAFE → delete；仅用户盘）

- **include patterns**：`$env:LOCALAPPDATA\Temp` 下的**顶层文件**，`LastWriteTime`
  早于 7 天。
- **MUST-NOT**：绝不递归子目录（`yxqxylog`、`hsperfdata_*`、`codex-index-*` 等
  存活子目录一律不动）；绝不删 7 天以内的文件；绝不跟随 junction。
- **notes**：与 root-temps 同规则，但作用于用户临时目录。删除前同样重验 7 天规则。

## elevated-system（ELEVATED → report-only；需 `-IncludeElevated`）

- **include patterns**（仅统计；清理走 UAC 提升批处理 `elevated.ps1`）：
  - `<X>:\Windows\Temp` 顶层文件（>7 天）；
  - `<X>:\Windows\Prefetch\*.pf`（**永不删 Layout.ini**）；
  - `<X>:\Windows\SoftwareDistribution`（**护栏**：仅当 `wuauserv` 确认 Stopped 时
    才允许清 `Download\*` 与 `DataStore.edb.old`/`DataStore.jfm.old`）；
  - `<X>:\Windows\Logs\WindowsUpdate\*.etl`（>7 天）；
  - `<X>:\Windows\Logs\CBS\CbsPersist_*.cab`；
  - DISM `/Online /Cleanup-Image /StartComponentCleanup`（**无 `/ResetBase`**）标记行。
- **MUST-NOT**：绝不删 `Prefetch\Layout.ini`；绝不删 `SoftwareDistribution\DataStore.edb`
  （只删 `.old`）；绝不删 WinSxS / Installer / DriverStore；绝不使用 `/ResetBase`；
  不弹 UAC 只准备批处理（`-SkipElevated`）或拒绝（`SKIP_ELEVATION_DENIED`）时绝不执行。
- **notes**：该分类只在 `-IncludeElevated` 时扫描；清理仅当 `$IsUserDrive` 且
  （`-Yes` 或 `-SkipElevated`）时可达，且只在系统盘上用 `-Yes` 才真正弹 UAC。
