# 事故 RCA —— 跨卷隔离失败（v2.1.0）

本文记录一次清理事故的事后分析：隔离项目被移动到与源**不同卷**的位置，跨设备 `EXDEV`
导致移动失败，而失败以 `MOVE_FAILED` 静默记录 —— 文件既未被删除，也无法在预期位置恢复。
审查共识别出**九个故障模式（FM1–FM9）**，全部在 v2.1.0 修复。本次修复是一次安全模型转变：
从"信任分类"到"每次删除前验证 + 进程感知 + 保守默认 + 真实预览确认"。

## 根因与修复

| # | 故障模式 | 根因 | 修复 |
|---|----------|------|------|
| FM1 | POSIX flock 缺口 | POSIX advisory `flock` 无法可靠检测文件被打开；删除路径探测了其他进程可能不遵守的锁，然后 unlink。 | POSIX unlink **默认跳过**：`_process_row` 直接返回 `SKIP_POSIX_UNSAFE`，除非显式传入 `--allow-posix-unlink` 才调用探测。 |
| FM2 | 隔离锁绕过 | 隔离分支 `return core.quarantine(...)` 提前返回，**绕过了**删除所用的锁探测，被锁文件可能被从属主进程下移走。 | 隔离与删除走**相同的锁探测**（Windows `CreateFileW`；POSIX 按 FM1 默认跳过）。被锁 → `SKIP_LOCKED` / `SKIP_POSIX_UNSAFE`，绝不移动。 |
| FM3 | 提升批次强制删除 | UAC 提升批次不考虑时效直接删除裸通配目标，且结束后不恢复 `wuauserv` 服务。 | 批次**由候选驱动**（仅已批准行），每行删除都带 `if exist …` 门，`forfiles` 时效门跳过近期文件，最后重启 `wuauserv`。 |
| FM4 | 缺少进程感知 | 清理在其属主应用（Chrome、Steam、微信等）运行时删除缓存文件。 | 清理时的进程快照对每个分类设门：属主在运行 → 整类**跳过**并明确提示 —— **绝不结束进程**。`--close-apps` 改为提示用户自行关闭。 |
| FM5 | 目录与文件动作不匹配 | 缓存分类删除了目录本身；"空目录"清理删除了非空目录。 | **双动作**执行：缓存分类用 `clean_contents`（删除目录内文件、保留目录）；`empty-dirs` 用 `remove_if_empty`（仅删除验证为空的目录）。 |
| FM6 | 分类重叠 | 多个分类匹配同一路径，同一项目可能被重复处理且动作不同。 | **分类互斥**：分类不再重叠；例如 `root-logs` 去掉 `*.tmp`，把 `.tmp` 留给 `root-temps`。 |
| FM7 | 过期路径映射 | 静态应用路径映射在目录内容变成数据文件后仍断言其为缓存，存在数据丢失风险。 | **路径语义校验**：抽样前 20 个条目（上限保证速度）；若发现数据签名后缀（`.db`、`.sqlite`、`.index` 等）则升级为 `CAUTION` 并**隔离**，绝不 `clean_contents`。 |
| FM8 | 可移动盘 | 扫描/清理可能以可移动、CD/DVD 或网络卷为目标。 | **固定盘过滤**：`get_fixed_drives` 只返回固定本地盘；可移动/CD-ROM/网络盘一律排除。 |
| FM9 | 跨卷 EXDEV | 默认隔离目录位于桌面（用户卷），而源可能在另一盘（`D:` → `C:`），`os.rename` 以 `EXDEV` 失败，静默表现为 `MOVE_FAILED`。 | **同卷隔离**：默认解析到源卷 `<目标盘根>\.rubbish-quarantine\run-<时间戳>\`（POSIX 回退到旧位置下的按运行子目录）。`report.py` 采用相同的解析逻辑。 |

## 配套加固（FM0、FM13）

- **FM0 — 保守默认**：未指定 `-Categories` 时只处理按时效门限的临时文件、日志与验证过的空目录；
  应用缓存与崩溃转储改为显式开启（[`references/policies/`](references/policies/) 下的 `safe`/`aggressive` 配置档）。
- **FM13 — dry-run 预览**：scanner 与 cleaner 的 `--dry-run` 在执行前逐文件打印预览并升级确认，
  用户始终能看到将要删除的内容。

## 验证

每个修复都由 [`tests/test_safety_fm.py`](../tests/test_safety_fm.py) 中的回归测试锁定
（`test_fm{N}_*`，全部套件共 57 条断言）。每个测试在修复前失败、在修复后通过；测试只在临时目录
上构建 fake tree，绝不接触真实磁盘。
