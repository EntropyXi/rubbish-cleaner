# python-ci-fix - Investigation & Fix Plan

## 根本问题（Root Cause，非表象）

Python v2.0.0 迁移时，`report.py` 引入的「无跟随写入 + Windows 句柄审计」安全层**缺少平台分派**：

- `report.py` 的 `_write_text_no_follow` 调用链（L70/L80/L116）在**任何平台**都先执行 `ctypes.WinDLL("kernel32")` → POSIX 上 `AttributeError: module 'ctypes' has no attribute 'WinDLL'`。POSIX 的 `O_NOFOLLOW` 分支（L60）存在但**永远到达不了**——WinDLL 导入在分支判断前就崩溃。
- 同一缺陷在 `core.py` 已正确修复（L41/59/85/122/276 均有 `IS_WINDOWS` 门控）——**report.py 是迁移时遗漏的门控点**。这是"迁移代码时安全硬化层的平台契约未在每个文件落实"的系统性问题。

### 根因链
```
表象: 9 CI job 红 (ubuntu/macos/windows × 3.10/3.11/3.12)
  └─ ubuntu×3: report.py WinDLL 无条件调用 → AttributeError
  └─ macos×3: core.py _assert_no_traversal_components 误判 /var→/private/var 符号链接 → Errno 62
  └─ win3.10/11: report.py 8.3 短路径 (runner~1) 与审计期望不匹配 → Errno 10062
  └─ win3.12: core.py is_junction 对真实 junction 假阴性 → AssertionError
直接原因: (1) report.py 平台门控缺失 (2) core.py traversal 守卫对系统符号链接过严 (3) 路径审计未折叠 8.3 短名 (4) is_junction 检测版本差异
最根本: 迁移引入的安全硬化层 (report.py Windows 句柄审计 + core.py traversal 守卫) 的**跨平台语义未定义且未在 CI 矩阵上验证**——首次在 3 OS 运行即暴露 4 类平台假设错误。
```

## 波及面（Blast Radius）
| 失败 | 涉及函数 | 调用链 | 影响的测试 |
|---|---|---|---|
| ubuntu WinDLL | report.py `_windows_close_handle`/`_windows_final_path`/`_windows_parent_guard`/`_windows_assert_handle_path` | `_write_text_no_follow` → `verify_report` → summary 写入 | test_integration |
| win 8.3 短路径 | report.py `_windows_assert_handle_path` (L96-97) | 同上 | test_integration |
| macos /var | core.py `_assert_no_traversal_components` (L94-109) | `_read_candidates` → `clean` | test_cleaner |
| win is_junction | core.py `is_junction` (L57-82) | `_is_windows_reparse_point` | test_core |

## 修复设计（每项一个 todo）
- T1: report.py 平台分派——`_write_text_no_follow` 在 Windows 用句柄审计路径，POSIX 用 `O_NOFOLLOW`（已有 L60 分支），用 `if platform.IS_WINDOWS:` 包裹 WinDLL 代码；所有 `_windows_*` 辅助函数顶部加 `if not platform.IS_WINDOWS: raise/return` 守卫（与 core.py 风格一致）
- T2: core.py `_assert_no_traversal_components`——区分「用户路径中的 traversal link」（拒绝）与「系统/标准库路径中的符号链接」（macOS `/tmp`→`/private/tmp`, `/var`→`/private/var` 允许）；用 `os.path.realpath` 规范化后仅对「落在审计根之外的 link」报错；或对 macOS 加入系统根豁免名单
- T3: core.py `is_junction` 8.3/版本兼容——windows 3.12 假阴性：改为 `os.path.isjunction()` 优先（3.12+），3.10/11 用 `_is_windows_reparse_point` 回退（已有）；确认 GetFileAttributesW 在 8.3 路径上的行为
- T4: report.py 8.3 短路径——`_windows_assert_handle_path` 的 expected/actual 比较改用 `GetFinalPathNameByHandle` 返回的实际扩展路径（`_windows_final_path` 已返回 `\\?\` 扩展）——只比较扩展后的真实路径，不依赖用户输入路径的字面形式

## 验证
- 本地: `python tests/test_runner.py` 全绿 (24)
- 推送 feature → 触发 CI → 9 job 全绿
- merge --no-ff → push main

## 提交策略
- 每个 T 一个 commit on `feature/ci-rootfix`；merge 到 main
