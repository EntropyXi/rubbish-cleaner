# Development Guide — rubbish_cleaning_skill

> Developer/agent-facing conventions for contributing to this repository.
> Quick agent-facing reference: [`../AGENTS.md`](../AGENTS.md).
> 中文镜像： [`DEVELOPMENT_zh.md`](DEVELOPMENT_zh.md).

This guide is the human-readable expansion of the conventions summarized in
[`AGENTS.md`](../AGENTS.md) — the single source of truth for developer conventions. If anything
disagrees, `AGENTS.md` wins.

## Table of contents

1. [Project overview](#1-project-overview)
2. [The CI success gate](#2-the-ci-success-gate)
3. [Git workflow](#3-git-workflow)
4. [Quality gates](#4-quality-gates)
5. [Docs convention](#5-docs-convention)
6. [Done checklist](#6-done-checklist)

---

## 1. Project overview

**rubbish-cleaner** is an agent-callable drive junk-cleanup **skill** for LLM agents (Claude Code,
Codex, opencode). Users invoke it with `/rubbish-cleaner` or trigger words (*junk cleanup / drive
cleanup / cache cleanup / clean temp files*) plus a plain-language request. The agent reads
[`SKILL.md`](../SKILL.md), deploys via [`scripts/install.py`](../scripts/install.py), and runs the
built-in flow:

```
scan (read-only inventory) → approve (user confirmation) → clean (quarantine-first)
→ verify → report (summary.md in the run evidence folder)
```

- **Language:** Python 3.10+ (a CI lint rejects 3.11+ syntax and imports).
- **Dependencies:** psutil (required); `pywin32` optional and Windows-only (UAC elevation, Task
  Scheduler integration).
- **Tests:** [`tests/test_runner.py`](../tests/test_runner.py) dual-mode runner + 6 behavior suites
  that build fake trees under temp dirs and never touch real drives.
- **CI:** GitHub Actions — 3 OS × 3 Python versions (see below).

## 2. The CI success gate

**This is the single most important convention in the repository.**

> A change is NOT complete while GitHub Actions CI is not fully green.
> "CI non-green = not complete" is a hard precondition for declaring any work finished — no
> exception, regardless of local test results.

### 2.1 The 9-job matrix

Defined in [`.github/workflows/test.yml`](../.github/workflows/test.yml):

| OS | Python | Job |
|----|--------|-----|
| ubuntu-latest | 3.10 / 3.11 / 3.12 | 3 jobs |
| windows-latest | 3.10 / 3.11 / 3.12 | 3 jobs |
| macos-latest | 3.10 / 3.11 / 3.12 | 3 jobs |

All 9 must pass. A single red job blocks completion — the change must not be declared done, merged,
or handed off.

### 2.2 Mandated CI-closing step in every plan

Every work plan (planning phase) MUST include an explicit step that closes the gate:

1. after pushing, run `gh run list --repo EntropyXi/rubbish_cleaning_skill --limit 5`
   to find the run for the pushed commit;
2. poll `gh run view <id>` every ~20s until the run completes;
3. on failure, `gh run view <id> --log-failed` → fix → re-push → re-poll.

If the plan has no such step, add it before any coding begins.

### 2.3 Final verification wave (F-level)

The final verification wave of any task MUST include a CI-status check. Do not hand off, merge, or
declare complete a piece of work with a pending or failing run.

### 2.4 Test discipline — the v2.1.0 CI-fix lesson (commit `bd998ce`)

The v2.1.0 CI fixes taught us one hard rule: **tests must not encode OS-specific assumptions.**
Tests run on all three OSes, so a test that assumes Windows-only or POSIX-only behavior will
randomly break the matrix.

- **FM1 lesson:** `cleaner.clean()` tests on POSIX that exercise deletion MUST pass
  `allow_posix_unlink=True`. POSIX unlink is **default-skip** (`SKIP_POSIX_UNSAFE`) — the
  conservative default — so a deletion-asserting test that does not opt in will report a skip
  instead of a deletion and fail its assertion.
- **Windows-only behavior** must be gated — `-Skip:(-not ...)` in PowerShell CI steps,
  `IS_WINDOWS` checks in code — never assumed present on POSIX hosts.

Full post-mortem of the v2.1.0 incident (nine failure modes FM1–FM9):
[`references/incident-rca.md`](../references/incident-rca.md).

## 3. Git workflow

User convention: **feature branch → commit per todo → push feature → `merge --no-ff` to main →
push main**. Feature branches are kept (never deleted).

### 3.1 Feature-branch flow

1. Branch from `main`: `git checkout -b feature/<name>`.
2. Commit in atomic units — one commit per todo/step, semantic messages
   (`feat:` / `fix:` / `test:` / `docs:` / `ci:` / `docs(plan):`).
3. Push the feature branch and open/update the PR.
4. On `main`, merge with `--no-ff` and a descriptive message:
   `git merge --no-ff feature/<name> -m "Merge feature/<name>: <summary>"`.
5. Push `main`. Feature branches are kept.

### 3.2 Rules

- **Never force-push**, never rewrite pushed history.
- **Never commit directly to main** — the only exception is plan-docs commits
  (`.omo/plans`, `.omo/drafts`, `docs(plan): ...`), which follow the established convention.
- Rebase/squash only on un-pushed local branches; verify against CI afterwards.
- Do not delete feature branches.

### 3.3 Proxy handling

- Push/pull through `127.0.0.1:7897` (configured as `http.proxy` / `https.proxy`).
- If the proxy is DOWN, retry without it and force HTTP/1.1:
  `git -c http.proxy= -c https.proxy= -c http.version=HTTP/1.1 push` (same for pull).
- Retry up to 5 times with ~20s waits before giving up.

## 4. Quality gates

Before merging to `main`, ALL of the following MUST hold:

- `python tests/test_runner.py` exits **0** — dual-mode runner: `compileall` first, then pytest
  when installed, else a fallback that imports and runs every `test_*` function with the same
  exit-code semantics.
- `compileall` clean: `python -m compileall -q scripts tests`.
- No `eval` / `exec` / dynamic code execution on user data.
- No new dependencies without justification in the commit/PR.
- Safety invariants (from [`references/safety-rules.md`](../references/safety-rules.md) and the
  v2.1.0 RCA):
  - **no auto-kill** — process-awareness gate skips a category whose owner app is running;
  - **quarantine-not-delete** — nothing is permanently deleted; quarantine stays on the same volume;
  - **POSIX default-skip** — `--allow-posix-unlink` is required to unlink on POSIX;
  - **conservative default** — app-owned caches and crash dumps are opt-in via `-Categories`.

## 5. Docs convention

- All user-facing Markdown docs have **EN + ZH pairs** (`*.md` ↔ `*_zh.md`): README, SKILL,
  CHANGELOG, taxonomy, path map, safety rules, incident RCA, DEVELOPMENT.
- Filenames are hyperlinked on first mention (relative path).
- `CHANGELOG.md` / `CHANGELOG_zh.md` are updated per release.
- `AGENTS.md` is the single source of truth for developer conventions; this document is the
  human-readable expansion.
- `AGENTS.md` / `docs/DEVELOPMENT*` are developer/agent-facing — do NOT fold the CI gate or other
  dev conventions into user-facing `SKILL.md` / `README.md` / `references/safety-rules.md`.

## 6. Done checklist

- [ ] `python tests/test_runner.py` → exit 0
- [ ] `compileall` clean
- [ ] CI: 9/9 matrix jobs green (`gh run view <id>`)
- [ ] CI-closing step present in plan; F-wave included the CI-status check
- [ ] no user-facing docs touched by dev-convention changes
- [ ] feature branch merged with `--no-ff`, main pushed
