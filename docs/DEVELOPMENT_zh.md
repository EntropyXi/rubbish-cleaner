# 开发指南 — rubbish_cleaning_skill

> 面向开发者 / 编码代理的仓库开发约定。
> Agent 快速参考： [`../AGENTS.md`](../AGENTS.md)（唯一事实来源）。
> English mirror: [`DEVELOPMENT.md`](DEVELOPMENT.md)。

本文档是 [`AGENTS.md`](../AGENTS.md) 所载约定的面向人类展开版；如有出入，以 `AGENTS.md` 为准。

## 目录

1. [项目概述](#1-项目概述)
2. [CI 成功门禁](#2-ci-成功门禁)
3. [Git 流程](#3-git-流程)
4. [质量门禁](#4-质量门禁)
5. [文档约定](#5-文档约定)
6. [完成前自查](#6-完成前自查)

---

## 1. 项目概述

**rubbish-cleaner** 是一个面向 LLM 代理（Claude Code、Codex、opencode）的**可调用磁盘垃圾清理技能**。
用户通过 `/rubbish-cleaner` 或触发词（垃圾清理 / 磁盘清理 / 清缓存 / clean temp files）加自然语言请求调用；
代理阅读 [`SKILL.md`](../SKILL.md)，通过 [`scripts/install.py`](../scripts/install.py) 部署，并执行内置流程：

```
scan（只读清单）→ approve（用户确认）→ clean（先隔离，绝不永久删除）
→ verify（复核）→ report（summary.md 写入运行证据目录）
```

- **语言：** Python 3.10+（CI 有 3.11+ 语法与 import 的兼容性检查）。
- **依赖：** psutil（必需）；`pywin32` 仅 Windows 可选（UAC 提权、任务计划集成）。
- **测试：** [`tests/test_runner.py`](../tests/test_runner.py) 双模式运行器 + 6 个行为套件，
  在临时目录构建假数据树，绝不触碰真实磁盘。
- **CI：** GitHub Actions — 3 个操作系统 × 3 个 Python 版本（见下）。

## 2. CI 成功门禁

**这是本仓库最重要的约定。**

> 只要 GitHub Actions CI 未完全变绿，任何改动都**不得**声明完成。
> "CI 非绿 = 未完成" 是宣告工作完成的硬性前置条件——无论本地测试结果如何，概无例外。

### 2.1 9 任务矩阵

定义于 [`.github/workflows/test.yml`](../.github/workflows/test.yml)：

| 操作系统 | Python | 任务数 |
|----|--------|-----|
| ubuntu-latest | 3.10 / 3.11 / 3.12 | 3 |
| windows-latest | 3.10 / 3.11 / 3.12 | 3 |
| macos-latest | 3.10 / 3.11 / 3.12 | 3 |

9 个任务必须全部通过；任意一个任务变红都会阻塞完成——该改动不得被声明完成、合并或移交。

### 2.2 每个计划必须含"关闭 CI 门禁"步骤

任何工作计划（规划阶段）**必须**包含一个显式的收尾步骤：

1. 推送后执行 `gh run list --repo EntropyXi/rubbish_cleaning_skill --limit 5`
   找到对应本次推送的 run；
2. 每约 20 秒轮询 `gh run view <id>` 直到运行结束；
3. 失败时 `gh run view <id> --log-failed` → 修复 → 重新推送 → 重新轮询。

如果计划中没有该步骤，在编码开始前补上。

### 2.3 最终验证波（F 级检查）

任何任务的最终验证波**必须**包含 CI 状态检查。不得在 run 处于 pending 或失败状态时移交、合并或声明完成。

### 2.4 测试纪律 —— v2.1.0 CI 修复教训（commit `bd998ce`）

v2.1.0 的 CI 修复给我们一条铁律：**测试不得编码操作系统相关假设。**
测试会在三个 OS 上运行，任何假定仅 Windows 或仅 POSIX 行为的测试都会随机打挂矩阵。

- **FM1 教训：** 在 POSIX 上执行删除断言的 `cleaner.clean()` 测试**必须**传入
  `allow_posix_unlink=True`。POSIX 解除链接是**默认跳过**（`SKIP_POSIX_UNSAFE`，保守默认值）；
  未显式选入的删除断言测试会得到"跳过"而非"删除"，从而断言失败。
- **Windows 专属行为**必须加门控（PowerShell CI 步骤用 `-Skip:(-not ...)`，代码用
  `IS_WINDOWS` 判断），绝不能假定 POSIX 主机上一定存在。

v2.1.0 事故的完整复盘（九个失败模式 FM1–FM9）：
[`references/incident-rca.md`](../references/incident-rca.md)。

## 3. Git 流程

用户约定：**功能分支 → 按 todo 逐个提交 → 推送功能分支 → `merge --no-ff` 到 main → 推送 main**。
功能分支保留（不删除）。

### 3.1 功能分支流程

1. 从 `main` 切出：`git checkout -b feature/<name>`。
2. 原子化提交——每个 todo/步骤一个提交，语义化消息
   （`feat:` / `fix:` / `test:` / `docs:` / `ci:` / `docs(plan):`）。
3. 推送功能分支并打开/更新 PR。
4. 在 `main` 上用 `--no-ff` 合并并写描述性消息：
   `git merge --no-ff feature/<name> -m "Merge feature/<name>: <summary>"`。
5. 推送 `main`。功能分支保留。

### 3.2 规则

- **绝不 force-push**，绝不改写已推送的历史。
- **绝不直接提交到 main** —— 唯一例外是计划文档提交
  （`.omo/plans`、`.omo/drafts`、`docs(plan): ...`），遵循既有约定。
- 只在未推送的本地分支上 rebase/squash；之后用 CI 复核。
- 不删除功能分支。

### 3.3 代理处理

- push/pull 走 `127.0.0.1:7897`（已配置为 `http.proxy` / `https.proxy`）。
- 若代理 DOWN，去掉代理并强制 HTTP/1.1 重试：
  `git -c http.proxy= -c https.proxy= -c http.version=HTTP/1.1 push`（pull 同理）。
- 重试至多 5 次，间隔约 20 秒，仍失败才放弃。

## 4. 质量门禁

在合并到 `main` 之前，**全部**下列条件必须成立：

- `python tests/test_runner.py` 退出码为 **0**——双模式运行器：先 `compileall`，再在装有 pytest
  时走 pytest，否则回退为导入并运行每个 `test_*` 函数，退出码语义一致。
- `compileall` 通过：`python -m compileall -q scripts tests`。
- 不得对用户数据使用 `eval` / `exec` / 动态代码执行。
- 不得无理由新增依赖（提交/PR 中需说明）。
- 安全不变量（来自 [`references/safety-rules.md`](../references/safety-rules.md) 与 v2.1.0 RCA）：
  - **不自动杀进程**——进程感知门控跳过所属应用正在运行的类别；
  - **先隔离不删除**——绝不永久删除；隔离目录与被清理对象同卷；
  - **POSIX 默认跳过**——POSIX 上解除链接需显式 `--allow-posix-unlink`；
  - **保守默认**——应用缓存与崩溃转储需通过 `-Categories` 显式选入。

## 5. 文档约定

- 所有面向用户的 Markdown 文档均为 **EN + ZH 成对**（`*.md` ↔ `*_zh.md`）：README、SKILL、
  CHANGELOG、taxonomy、path map、safety rules、incident RCA、DEVELOPMENT。
- 文件名在首次提及时用相对路径超链接。
- `CHANGELOG.md` / `CHANGELOG_zh.md` 按版本更新。
- `AGENTS.md` 是开发约定的唯一事实来源；本文档是其面向人类展开版。
- `AGENTS.md` / `docs/DEVELOPMENT*` 面向开发者/代理——**不得**把 CI 门禁等开发约定塞进
  面向用户的 `SKILL.md` / `README.md` / `references/safety-rules.md`。

## 6. 完成前自查

- [ ] `python tests/test_runner.py` → 退出码 0
- [ ] `compileall` 通过
- [ ] CI：9/9 矩阵任务全绿（`gh run view <id>`）
- [ ] 计划中含 CI 收尾步骤；F 波包含 CI 状态检查
- [ ] 开发约定改动未触碰任何面向用户文档
- [ ] 功能分支已 `--no-ff` 合并，main 已推送
