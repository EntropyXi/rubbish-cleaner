"""Install the rubbish-cleaner skill into supported agent directories."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Optional, Sequence


_TARGET_RELATIVE_PATHS = {
    "claude": Path(".claude") / "skills" / "rubbish-cleaner",
    "codex": Path(".codex") / "skills" / "rubbish-cleaner",
    "opencode": Path(".config")
    / "opencode"
    / "skills"
    / "automation"
    / "rubbish-cleaner",
}
_IGNORE = shutil.ignore_patterns(
    ".git",
    ".omo",
    ".codegraph",
    "__pycache__",
    "*.pyc",
    "*.ps1",
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        choices=("all", "claude", "codex", "opencode"),
        default="all",
        help="agent directory to install to (default: all)",
    )
    parser.add_argument(
        "--target-root",
        type=Path,
        default=Path.home(),
        help=argparse.SUPPRESS,
    )
    return parser


def install(target: str, target_root: Path) -> list[Path]:
    """Merge the repository into the selected installation directories."""
    source = Path(__file__).resolve().parents[1]
    selected = tuple(_TARGET_RELATIVE_PATHS) if target == "all" else (target,)
    copied: list[Path] = []
    for name in selected:
        destination = target_root / _TARGET_RELATIVE_PATHS[name]
        shutil.copytree(
            source,
            destination,
            dirs_exist_ok=True,
            ignore=_IGNORE,
        )
        copied.append(destination)
    return copied


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _build_parser().parse_args(argv)
    try:
        copied = install(arguments.target, arguments.target_root)
    except OSError as error:
        print(f"INSTALL FAILED: {error}", file=sys.stderr)
        return 1
    for destination in copied:
        print(f"COPIED: {os.fspath(destination)}")
    print("INSTALL: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
