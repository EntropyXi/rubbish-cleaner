# Incident RCA — Cross-Volume Quarantine Failure (v2.1.0)

This document records the post-mortem of a cleanup incident in which quarantine
items were moved to a **different volume** than their source, the move failed
with a cross-device `EXDEV`, and the failure was recorded silently as
`MOVE_FAILED` — the file was neither deleted nor recoverable at the expected
location. The review surfaced **nine failure modes (FM1–FM9)**; all were fixed
in v2.1.0. The fix is a safety-model shift: from "classification-is-trust" to
"verify-before-every-deletion + process-awareness + conservative-defaults +
real-preview-confirmation".

## Root causes and fixes

| # | Failure mode | Root cause | Fix |
|---|--------------|-----------|-----|
| FM1 | POSIX flock gap | POSIX advisory `flock` cannot be trusted to detect an open file; the delete path probed a lock that other processes may not honor, then unlinked. | POSIX unlink is **default-skip**: `_process_row` returns `SKIP_POSIX_UNSAFE` without calling the probe unless `--allow-posix-unlink` is passed explicitly. |
| FM2 | Quarantine lock bypass | The quarantine branch `return core.quarantine(...)` returned early and **bypassed** the lock probe used by delete, so a locked file could be moved out from under its owner. | Quarantine runs the **same lock probe** as delete first (Windows `CreateFileW`; POSIX default-skip per FM1). Locked → `SKIP_LOCKED` / `SKIP_POSIX_UNSAFE`, never moved. |
| FM3 | Elevated batch force-delete | The UAC-elevated batch deleted bare wildcard targets regardless of age, and did not restore the `wuauserv` service afterwards. | The batch is **candidates-driven** (only approved rows), every deletion line is gated (`if exist …`), a `forfiles` age gate skips recent files, and `wuauserv` is restarted at the end. |
| FM4 | No process awareness | Cleanup deleted cache files while their owner application (Chrome, Steam, WeChat, …) was running. | A clean-time process snapshot gates each category: if an owner is running the whole category is **skipped** with a clear message — never killed. `--close-apps` prompts the user instead. |
| FM5 | Dir-vs-file mismatch | Cache categories deleted the directory itself, and "empty dir" cleanup deleted directories that were not empty. | **Dual-action** execution: cache categories use `clean_contents` (files inside deleted, directory kept); `empty-dirs` uses `remove_if_empty` (only verified-empty directories). |
| FM6 | Category overlap | Multiple taxonomy categories matched the same paths, so the same item could be processed more than once with different actions. | **Taxonomy mutual exclusion**: categories no longer overlap; e.g. `root-logs` drops `*.tmp` and leaves `.tmp` to `root-temps`. |
| FM7 | Stale path map | A static per-app path map claimed a directory was cache even after its content changed to data files, risking data loss. | **Path semantic validation**: the first 20 entries (cap for speed) are sampled; if any data-signature suffix (`.db`, `.sqlite`, `.index`, …) is found the path is escalated to `CAUTION` and **quarantined**, never `clean_contents`'d. |
| FM8 | Removable drives | Scan/clean could target removable, CD/DVD, or network volumes. | **Fixed-drive filter**: `get_fixed_drives` returns only fixed local drives; removable/CD-ROM/network drives are excluded. |
| FM9 | Cross-volume EXDEV | The default quarantine dir lived on the Desktop (user volume) while sources could be on another drive (`D:` → `C:`), so `os.rename` failed with `EXDEV`, surfaced as a silent `MOVE_FAILED`. | **Same-volume quarantine**: the default resolves to `<target_drive_root>\.rubbish-quarantine\run-<timestamp>\` on the source volume (POSIX falls back to a per-run subdir under the legacy location). `report.py` consumes the same resolution. |

## Supporting hardening (FM0, FM13)

- **FM0 — conservative default**: without `-Categories`, only age-gated temp
  files, logs, and verified-empty dirs are processed; app-owned caches and crash
  dumps are opt-in (`safe`/`aggressive` policy profiles in
  [`references/policies/`](references/policies/)).
- **FM13 — dry-run preview**: `--dry-run` on scanner and cleaner prints a
  per-file preview with upgraded confirmation before any action, so the user
  always sees exactly what will be deleted.

## Verification

Every fix is locked by a regression test in [`tests/test_safety_fm.py`](../tests/test_safety_fm.py)
(`test_fm{N}_*`, 57 total assertions across all suites). Each test fails on the
pre-fix code and passes on the fixed code; nothing is tested against real drives.
