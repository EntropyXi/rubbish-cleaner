---
slug: rubbish-cleaner-python-migration
status: review-complete
intent: clear
review_required: true
plan_path: .omo/plans/rubbish-cleaner-python-migration.md
plan_sha256_final: A5725981DFC2C833BAA7C1EB6EA32DD6B3015F802E37335D21B6CB111D676B7A
review_summary: round 1→momus APPROVED, oracle CHANGES_REQUESTED (3CRITICAL+4MOD). round 2→momus APPROVED, oracle CHANGES_REQUESTED (1: count 10→9). round 3→BOTH APPROVED. Total fixes: C1 isjunction ctypes fallback, C2 CreateFileW lock detection, C3 file count correction, M1 CI 3.10 matrix, M2 Todo7 Blocks, M3 cron root/non-root, M4 behavioral comparison Windows-only guard, plus Momus __init__.py.
pending-action: execute in worker session ($start-work rubbish-cleaner-python-migration)
