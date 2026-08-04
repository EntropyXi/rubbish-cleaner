---
slug: rubbish-cleaner-v2.1.1-fix
status: review-in-progress
intent: clear
review_required: true
plan_path: .omo/plans/rubbish-cleaner-v2.1.1-fix.md
metis: DONE - 10 findings. CRITICAL F1: DumpStack.log Attributes=Archive(0x20) NOT System(0x4) — attribute-only check fails; fixed with two-tier (Tier1 0x4 + Tier2 GetNamedSecurityInfoW ERROR_ACCESS_DENIED=system-owned). HIGH F2/F3: ACL-tier acceptance + live user-log control added. MEDIUM F5: whole-word name matching (\b(setup|install|unins|uninstall|updater)\b) so install.log/installer_data.tmp stay flagged. F6: .log-with-updater-name exemption documented as intended. F10: Hidden-only(0x2) regression fixture added. F9: per-category C: re-scan verification (not conflated). F7: fm14/fm15 numbering confirmed correct.
pending-action: dual high-accuracy review, then approval gate
