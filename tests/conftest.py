"""Root test configuration for the rubbish-cleaner test suite.

- PINS the long-running stress suite out of default collection via pytest's
  native per-directory ``collect_ignore``.  No pytest.ini is created, so
  ``tests/test_runner.py`` and the 9-job CI matrix keep running only the fast
  suites without any change.
- Registers the ``stress`` marker used to tag every test under
  ``tests/stress/`` (run explicitly via ``-m stress`` or the CI stress job).
"""

collect_ignore = ["stress"]


def pytest_configure(config):
    """Register the ``stress`` marker so ``-m stress`` is always selectable."""
    config.addinivalue_line(
        "markers",
        "stress: long-running stress tests (run explicitly or via CI stress job)",
    )
