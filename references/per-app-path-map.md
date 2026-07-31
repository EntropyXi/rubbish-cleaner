# per-app-path-map：应用 → 路径模板 → 分类

驱动 rubbish-cleaner 各分类匹配的**路径模板**。所有模板都是**驱动器相对**或
**用户配置相对**的占位形式——**盘符与用户名在运行时解析，绝不硬编码**
（扫描侧：`$Drive` + `$env:USERPROFILE` / `$env:LOCALAPPDATA` / `$env:APPDATA`；
删除侧：只处理 candidates.csv 中已解析成字面路径的行）。

**占位符约定**：
- `{X}` = 被扫描的驱动器盘符（如 `D:`）；扫描时替换为 `$Drive`。
- `%LOCALAPPDATA%` / `%APPDATA%` / `~` = 运行用户的配置目录；仅当用户配置位于被
  扫描盘（`$IsUserDrive`）时才评估（标注"仅用户盘"的分类）。
- `*` / `**` = 扫描侧用 junction 安全的显式 `Get-ChildItem` 展开，**绝不裸 `-Recurse`**。
- 每条模板都先 `Test-Path` 存在性检查，不存在即跳过，绝不创建。

## 主表

| App | {X}-相对路径模板 | 分类 | 备注 |
|-----|------------------|------|------|
| anaconda3 | `{X}\anaconda3\pkgs\cache` | app-caches | 只清 cache 子目录；`envs` 与 `Lib` 永不碰 |
| WeGame | `{X}\Wegame\*\tiny_cache` | app-caches | 每个子目录下的小缓存 |
| WeGame | `{X}\Wegame\*\cache` | app-caches | 与 tiny_cache 分开统计 |
| WeChat | `{X}\WeiXin\xwechat_files\**\cache` | app-caches | junction 安全按名递归查找；只清 cache，msg/file/contact 不动 |
| Steam | `{X}\SteamLibrary\steamapps\common\*`（仅空目录） | app-caches | 删除前重验 Test-DirEmpty；非空游戏目录 = SKIP_NOT_EMPTY；appmanifest 永不碰 |
| Ubisoft | `{X}\Ubisoft Game Launcher\cache` | app-caches | 启动器缓存，删后自动重建 |
| Chrome（仅用户盘） | `%LOCALAPPDATA%\Google\Chrome\User Data\Default\{Cache, Code Cache, GPUCache}` | browser-caches | 配置档（Cookies/Local Storage/Login Data）永不碰 |
| Chrome Crashpad（仅用户盘） | `%LOCALAPPDATA%\Google\Chrome\User Data\Crashpad\reports` | browser-caches | 崩溃报告，非用户数据 |
| Edge（仅用户盘） | `%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\{Cache, Code Cache, GPUCache}` | browser-caches | 同上 |
| Edge Crashpad（仅用户盘） | `%LOCALAPPDATA%\Microsoft\Edge\User Data\Crashpad\reports` | browser-caches | 同上 |
| NVIDIA（仅用户盘） | `%LOCALAPPDATA%\NVIDIA\DXCache` | gpu-shader | C 盘先例 22.9 GB；游戏运行时可能 SKIP_LOCKED |
| NVIDIA（仅用户盘） | `%LOCALAPPDATA%\NVIDIA\GLCache` | gpu-shader | 着色器 GL 缓存 |
| D3DSCache（仅用户盘） | `%LOCALAPPDATA%\D3DSCache` | gpu-shader | 系统级着色器缓存 |
| pip（仅用户盘） | `%LOCALAPPDATA%\pip\cache` | dev-caches | `pip.ini` 配置永不碰；目录本身保留 |
| npm（仅用户盘） | `%LOCALAPPDATA%\npm-cache` | dev-caches | `.npmrc` 配置永不碰；目录本身保留 |
| PyTorch（仅用户盘） | `~\.cache\torch` | dev-caches | 模型缓存（hub/checkpoints） |
| HuggingFace（仅用户盘） | `~\.cache\huggingface` | dev-caches | hub 缓存与 .incomplete blob |
| opencode（仅用户盘） | `~\.cache\opencode` | dev-caches | C 盘先例 960 MB |
| Codex（仅用户盘） | `~\.cache\codex-runtimes` | dev-caches | C 盘先例 1 GB |
| pkg（仅用户盘） | `~\.cache\pkg` | dev-caches | sqlite .node 二进制缓存 |
| JetBrains IDE（仅用户盘） | `%LOCALAPPDATA%\JetBrains\<IDE>\caches` | ide-caches | 每个 IDE 各自一行；system/config/plugins 永不碰 |
| JetBrains IDE（仅用户盘） | `%LOCALAPPDATA%\JetBrains\<IDE>\log` | ide-caches | 日志目录内容 |
| JetBrains Toolbox（仅用户盘） | `%LOCALAPPDATA%\JetBrains\Toolbox\{cache, logs}`（Toolbox-Dev 同） | ide-caches | scripts/.appState.json/.settings.json 保留 |
| Zotero（仅用户盘） | `%APPDATA%\Zotero\Zotero\Profiles\*\cache2` | ide-caches | 每个配置档分别统计 |
| Zotero（仅用户盘） | `%APPDATA%\Zotero\Zotero\Profiles\*\startupCache` | ide-caches | 启动缓存 |
| Zotero（仅用户盘） | `%APPDATA%\Zotero\Zotero\Profiles\*\shader-cache` | ide-caches | 配置档 .sqlite 永不碰 |
| Jedi（仅用户盘） | `%LOCALAPPDATA%\Jedi\Jedi\*\*.pkl` | ide-caches | 只删 *.pkl 补全缓存 |
| CrashDumps（仅用户盘） | `%LOCALAPPDATA%\CrashDumps` | crash-dumps | 整目录统计；C 盘先例 10 个文件 ~318 MB |
| Crashpad 顶层（仅用户盘） | `%LOCALAPPDATA%` 下名为 `Crashpad` 的非 junction 目录 | crash-dumps | 顶层应用崩溃目录（VS Code、Quark、QQ 等） |
| Explorer 缩略图（仅用户盘） | `%LOCALAPPDATA%\Microsoft\Windows\Explorer\thumbcache_*.db` | thumbnail-cache | explorer 占用时 SKIP_LOCKED/ACCESS_DENIED，不强杀 |
| Explorer 图标（仅用户盘） | `%LOCALAPPDATA%\Microsoft\Windows\Explorer\iconcache_*.db` | thumbnail-cache | 同上 |
| 用户临时目录（仅用户盘） | `%LOCALAPPDATA%\Temp` 顶层文件（>7 天） | user-temp | 只删顶层文件；存活子目录（yxqxylog、hsperfdata_*、codex-index-*）不动 |
| 根临时目录 | `{X}\Temp`、`{X}\tmp`、`{X}\temp` 顶层文件（>7 天） | root-temps | 三者按实际解析路径去重 |
| 根日志 | `{X}\*.log`、`{X}\*.tmp`、`{X}\*_install*.log` | root-logs | 仅根目录，不递归 |
| 重复压缩包 | `{X}\*.zip`、`{X}\*.rar`、`{X}\*.7z`（存在同名解压目录） | duplicate-archives | ASK 分类；只删压缩包，解压目录保留 |
| 回收站 | `{X}\$RECYCLE.BIN` | recycle-bin | ASK 分类；只报告，本流水线不清空 |
| 根可疑文件 | `{X}\*.dll`、`{X}\*.exe`（basename 排除规则） | root-suspicious | CAUTION → 隔离（Move-Item），永不删 |
| 根空目录 | `{X}\*` 顶层空目录（junction 感知） | empty-dirs | 跳过 $RECYCLE.BIN / SYSTEM VOLUME INFORMATION / .CLAUDE |
| Windows Temp | `{X}\Windows\Temp` 顶层文件（>7 天） | elevated-system | 仅报告；清理走 UAC 批处理 |
| Prefetch | `{X}\Windows\Prefetch\*.pf` | elevated-system | 永不删 Layout.ini |
| SoftwareDistribution | `{X}\Windows\SoftwareDistribution` | elevated-system | 护栏：仅 wuauserv Stopped 时清 Download + .old 文件 |
| WindowsUpdate 日志 | `{X}\Windows\Logs\WindowsUpdate\*.etl`（>7 天） | elevated-system | 仅报告 |
| CBS 日志 | `{X}\Windows\Logs\CBS\CbsPersist_*.cab` | elevated-system | 仅报告 |
| DISM 标记 | `{X}\Windows`（DISM StartComponentCleanup，无 /ResetBase） | elevated-system | 报告用标记行；绝不 /ResetBase |

## 运行时解析规则（重要）

- 以上所有 `{X}` 路径在扫描时替换为被扫描盘符；`%LOCALAPPDATA%` / `%APPDATA%` / `~`
  在用户配置位于被扫描盘时才参与（`$IsUserDrive` 判定）。
- 通配（`*` / `**` / 花括号集合）只在扫描侧展开成真实路径后进入 candidates.csv；
  clean-drive.ps1 收到的永远是字面路径（-LiteralPath）。
- 任何模板目标不存在 → 静默跳过，绝不创建目录、绝不误伤相邻路径。
- 新增分类目标必须先在这里登记模板，再实现到 scan-drive.ps1（单一事实来源）。

### Linux / macOS

> **占位符约定（Linux/macOS）**：`{cache}` = `Get-UserCacheDir`（Linux `$XDG_CACHE_HOME`
> 或 `~/.cache`，macOS `~/Library/Caches`）；`{temp}` = `Get-SystemTempDir`（Linux/macOS
> 均为 `/tmp`）；`~` = `$env:HOME`。路径分隔统一用 `/`。
> 全部条目仅在用户盘（`/`）时评估（`$isUserDrive = $Drive -eq '/'`）；扫描侧由
> `scan-drive.ps1` 的 `if (-not $script:IsWindows)` 分支解析，**分类 id 与 Windows 完全相同**，
> 仅路径模板不同。

| App | 路径模板 | 分类 | 备注 |
|-----|----------|------|------|
| pip（仅用户盘） | `{cache}/pip` | dev-caches | 包下载缓存；目录本身保留 |
| npm（仅用户盘） | `{cache}/npm` | dev-caches | `_cacache` 内容 |
| PyTorch（仅用户盘） | `{cache}/torch` | dev-caches | 模型 hub/checkpoints 缓存 |
| HuggingFace（仅用户盘） | `{cache}/huggingface` | dev-caches | hub 缓存与 .incomplete blob |
| opencode（仅用户盘） | `{cache}/opencode` | dev-caches | 同 Windows `~\.cache\opencode` |
| Codex（仅用户盘） | `{cache}/codex-runtimes` | dev-caches | 运行时常量缓存 |
| 通用 .cache（仅用户盘） | `{cache}/*` 顶层文件（>7 天） | user-temp | 只删顶层文件，不递归子目录 |
| 系统临时目录 | `{temp}/*` 顶层文件（>7 天） | root-temps | `/tmp` 顶层文件，>7 天 |
| Chrome（仅用户盘） | `{cache}/google-chrome/Default/{Cache, Code Cache, GPUCache}` | browser-caches | 配置档永不碰 |
| Firefox（仅用户盘） | `{cache}/mozilla/firefox/*/cache2/` | browser-caches | 每个配置档 profile 下的 cache2 |
| Edge（仅用户盘） | `{cache}/microsoft-edge/Default/{Cache, Code Cache, GPUCache}` | browser-caches | 同上 |
| JetBrains IDE（仅用户盘） | `{cache}/JetBrains/*/`（caches/log） | ide-caches | 每个 IDE 各自一行 |
| VS Code（仅用户盘） | `~/.config/Code/{Cache, CachedData, logs}/` | ide-caches | 配置/扩展目录永不碰 |
| Zotero（仅用户盘） | `{cache}/zotero/{cache2, startupCache, shader-cache}` | ide-caches | 配置档 .sqlite 永不碰 |
| 崩溃转储 | `/var/crash/*` | crash-dumps | apport 崩溃报告 |
| 缩略图（仅用户盘） | `{cache}/thumbnails/` | thumbnail-cache | normal/large/fail 子目录 |
| 回收站（仅用户盘） | `~/.local/share/Trash/` | recycle-bin | ASK 分类；只报告，本流水线不清空 |
