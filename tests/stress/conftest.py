"""Shared fixtures for the rubbish-cleaner stress-test suite.

Every stress test runs STRICTLY inside a dedicated root directory:

- ``RUBBISH_STRESS_ROOT`` env override when set (MANDATORY on CI — GitHub
  Actions Windows runners have no ``D:`` drive);
- ``D:\\_rubbish_cleaner_stress`` on a local Windows dev machine;
- ``tempfile.gettempdir()/rubbish-stress`` on POSIX.

The root is created LAZILY by the ``stress_root`` fixture — never at import
time.  ``assert_no_escape`` snapshots the root's OWN subtree (relative paths +
SHA-256 + sizes, including the ``__sentinel/guard.txt`` file) before each test
and asserts it is byte-identical afterwards; it NEVER snapshots the parent of
the stress root (e.g. ``D:\\``), so unrelated user-file churn on the drive
cannot cause false positives.

Note for stress-test authors: tests MUST clean up any files they create under
the stress root before the test function returns (in the test body or in a
fixture torn down before ``assert_no_escape``), otherwise the after-snapshot
differs and the test fails.
"""

import hashlib
import os
import tempfile
from pathlib import Path

import pytest

# Fixed sentinel content of the guard file.  Any mutation of this file (or any
# new/changed file under the stress root) while a stress test runs proves the
# test escaped its sandbox.
GUARD_CONTENT = "rubbish-cleaner-stress-sentinel-do-not-delete\n"

# Sub-directories created under the stress root.
_SUBDIRS = ("unit", "integration", "fuzz", "__sentinel")


def _resolve_stress_root() -> Path:
    """Resolve the stress root: env override > Windows local default > POSIX tmp."""
    env_root = os.environ.get("RUBBISH_STRESS_ROOT")
    if env_root:
        return Path(env_root)
    if os.name == "nt":
        return Path(r"D:\_rubbish_cleaner_stress")
    return Path(tempfile.gettempdir()) / "rubbish-stress"


STRESS_ROOT = _resolve_stress_root()


@pytest.fixture(scope="session")
def stress_root():
    """Create (once per session) the stress root + subdirs + sentinel guard."""
    root = STRESS_ROOT
    root.mkdir(parents=True, exist_ok=True)
    for sub in _SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    guard = root / "__sentinel" / "guard.txt"
    guard.write_text(GUARD_CONTENT, encoding="utf-8")
    return root


def _snapshot(root: Path) -> str:
    """Record the stress-root subtree: relative path | sha256 | size, sorted."""
    lines = []
    guard = root / "__sentinel" / "guard.txt"
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            size = path.stat().st_size
            lines.append(f"{path.relative_to(root)}|{digest}|{size}")
    # Always report the guard file explicitly even if it vanished mid-test.
    if not guard.exists():
        lines.append("__sentinel/guard.txt|MISSING|0")
    return "\n".join(lines)


@pytest.fixture(autouse=True)
def assert_no_escape(stress_root):
    """Assert nothing under the stress-root subtree changed during a test.

    Snapshots BEFORE the test, asserts identical AFTER.  Scope is the stress
    root subtree ONLY — the parent directory is never snapshotted.
    """
    before = _snapshot(stress_root)
    yield
    after = _snapshot(stress_root)
    assert after == before, (
        "stress test escaped its sandbox: the stress-root subtree changed.\n"
        f"root={stress_root}\n"
        f"--- before ---\n{before}\n--- after ---\n{after}"
    )
