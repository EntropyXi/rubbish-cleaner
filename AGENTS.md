# AGENTS.md — rubbish_cleaning_skill 开发规范

> Agent 开发约定（AI 编码代理必读）。本文件是开发约定的**唯一事实来源**（single source of truth）。
> 面向人类的展开版： [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)（中文镜像：[`docs/DEVELOPMENT_zh.md`](docs/DEVELOPMENT_zh.md)）。

## 项目概述 / Project overview

This repository is **rubbish-cleaner**, an agent-callable drive junk-cleanup **skill** for LLM agents
(Claude Code, Codex, opencode). The user invokes it via `/rubbish-cleaner` (or trigger words:
*junk cleanup / drive cleanup / cache cleanup / clean temp files*) with a plain-language request; the
agent reads [`SKILL.md`](SKILL.md), deploys via `scripts/install.py`, and executes the built-in flow.

**Flow:** `scan` (read-only inventory) → `approve` (user confirmation) → `clean` (quarantine-first,
never permanent delete) → `verify` → `report` (`summary.md` written to the run evidence folder).

**Tech stack:** Python 3.10+ · psutil · pytest · GitHub Actions (3 OS × 3 Python matrix).
`pywin32` (Windows-only, optional) is used for UAC elevation and Task Scheduler integration.

**Layout:** `scripts/` (scanner, cleaner, report, schedule, `lib/core.py` safety library) ·
`references/` (taxonomy, per-app path map, safety rules, incident RCA) ·
`tests/` (`test_runner.py` + 6 behavior suites) · `agents/` (Codex metadata) ·
`.github/workflows/test.yml` (CI).

## 核心开发约定：CI 成功门禁 / Core dev convention: CI success gate

**A change is NOT complete while GitHub Actions CI is not fully green.**
"CI non-green = not complete" is a **hard precondition** for declaring any work finished —
no exception, regardless of local test results.

- **The CI matrix is 9 jobs**: `ubuntu-latest` / `windows-latest` / `macos-latest`
  × Python `3.10` / `3.11` / `3.12` (defined in `.github/workflows/test.yml`).
  All 9 must pass. A single red job blocks completion.

- **Every work plan (planning phase) MUST include an explicit CI-closing step:**
  1. after pushing, run `gh run list --repo EntropyXi/rubbish_cleaning_skill --limit 5`
     to find the run for the pushed commit;
  2. poll `gh run view <id>` (every ~20s) until the run completes;
  3. on failure, `gh run view <id> --log-failed` → fix → re-push → re-poll.
  If the plan has no such step, add it before any coding begins.

- **The final verification wave (F-level checks) MUST include a CI-status check.**
  Do not hand off, merge, or declare complete a piece of work with a pending or failing run.

- **Test discipline — the v2.1.0 CI-fix lesson (commit `bd998ce`):**
  Tests must NOT encode OS-specific assumptions.
  - FM1 lesson: `cleaner.clean()` POSIX tests that exercise deletion MUST pass
    `allow_posix_unlink=True` — POSIX unlink is **default-skip** (`SKIP_POSIX_UNSAFE`).
  - Windows-only behavior MUST be gated (`-Skip:(-not ...)` in PowerShell CI steps /
    `IS_WINDOWS` checks in code), never assumed present on POSIX hosts.
  - Full post-mortem: [`references/incident-rca.md`](references/incident-rca.md) (FM1–FM9).

## Git 流程 / Git workflow

User convention: **feature branch → commit per todo → push feature → `merge --no-ff` to main →
push main**. Feature branches are kept (never deleted).

1. Branch from `main`: `git checkout -b feature/<name>`.
2. Commit in atomic units — one commit per todo/step, semantic messages
   (`feat:` / `fix:` / `test:` / `docs:` / `ci:` / `docs(plan):`).
3. Push the feature branch (with PR), then:
4. `git merge --no-ff feature/<name>` on `main` (merge commit, e.g.
   `Merge feature/<name>: <summary>`), then push `main`.

Rules:

- **Never force-push**, never rewrite pushed history.
- **Never commit directly to main** — the only exception is plan-docs commits
  (`.omo/plans`, `.omo/drafts`, `docs(plan): ...`), which follow the established convention.
- **Proxy**: push/pull through `127.0.0.1:7897`. If the proxy is DOWN, retry with
  `git -c http.proxy= -c https.proxy= -c http.version=HTTP/1.1 push` (same for pull).
- Rebase/squash only on un-pushed local branches; verify against CI afterwards.

## 质量门禁 / Quality gates

Before merging to `main`, ALL of the following MUST hold:

- `python tests/test_runner.py` exits **0** — dual-mode runner: compileall first, then pytest
  when installed, else a fallback that imports and runs every `test_*` function with the same
  exit-code semantics.
- `compileall` clean: `python -m compileall -q scripts tests`.
- No `eval` / `exec` / dynamic code execution on user data.
- No new dependencies without justification in the commit/PR.
- Safety invariants (from [`references/safety-rules.md`](references/safety-rules.md) and the
  v2.1.0 RCA):
  - **no auto-kill** — process-awareness gate skips a category whose owner app is running;
  - **quarantine-not-delete** — nothing is permanently deleted; quarantine stays on the same volume;
  - **POSIX default-skip** — `--allow-posix-unlink` is required to unlink on POSIX;
  - **conservative default** — app-owned caches and crash dumps are opt-in via `-Categories`.

## 文档约定 / Docs convention

- All user-facing Markdown docs have **EN + ZH pairs** (`*.md` ↔ `*_zh.md`): README, SKILL,
  CHANGELOG, taxonomy, path map, safety rules, incident RCA, DEVELOPMENT.
- Filenames are hyperlinked on first mention (relative path).
- `CHANGELOG.md` / `CHANGELOG_zh.md` are updated per release.
- `AGENTS.md` is the single source of truth for developer conventions; the human-readable expanded
  version lives in `docs/DEVELOPMENT.md` (+ `docs/DEVELOPMENT_zh.md` mirror).
- `AGENTS.md` / `docs/DEVELOPMENT*` are developer/agent-facing — do NOT fold the CI gate or other
  dev conventions into user-facing `SKILL.md` / `README.md` / `references/safety-rules.md`.

## 完成前自查 / Done checklist

- [ ] `python tests/test_runner.py` → exit 0
- [ ] `compileall` clean
- [ ] CI: 9/9 matrix jobs green (`gh run view <id>`)
- [ ] CI-closing step present in plan; F-wave included the CI-status check
- [ ] no user-facing docs touched by dev-convention changes
- [ ] feature branch merged with `--no-ff`, main pushed
