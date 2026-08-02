"""Compile-check and run the Python test suite with or without pytest."""

from __future__ import annotations

import ast
import compileall
import importlib.util
import sys
import tokenize
from pathlib import Path
from types import ModuleType


def _test_functions(test_file: Path) -> list[str]:
    with tokenize.open(test_file) as stream:
        tree = ast.parse(stream.read(), filename=str(test_file))
    return [
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    ]


def _import_test_module(test_file: Path, index: int) -> ModuleType:
    module_name = f"_fallback_test_{index}"
    spec = importlib.util.spec_from_file_location(module_name, test_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load {test_file}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _run_fallback() -> int:
    root = Path.cwd()
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)

    total_passed = 0
    total_failed = 0
    total_errors = 0

    for index, test_file in enumerate(sorted(Path("tests").glob("test_*.py"))):
        function_names = _test_functions(test_file)
        passed = 0
        failed = 0
        errors = 0
        try:
            module = _import_test_module(test_file, index)
        except Exception as error:
            errors = 1
            print(
                f"ERROR: {test_file.as_posix()}::<module>: "
                f"{type(error).__name__}: {error}"
            )
        else:
            for function_name in function_names:
                test_id = f"{test_file.as_posix()}::{function_name}"
                try:
                    getattr(module, function_name)()
                except AssertionError as error:
                    failed += 1
                    print(f"FAIL: {test_id}: {error}")
                except Exception as error:
                    errors += 1
                    print(f"ERROR: {test_id}: {type(error).__name__}: {error}")
                else:
                    passed += 1
                    print(f"PASS: {test_id}")

        total_passed += passed
        total_failed += failed
        total_errors += errors
        print(
            f"FILE SUMMARY: {test_file.as_posix()}: "
            f"{passed} passed, {failed} failed, {errors} errors"
        )

    print(
        f"SUMMARY: {total_passed} passed, "
        f"{total_failed} failed, {total_errors} errors"
    )
    return 0 if total_failed == 0 and total_errors == 0 else 1


def main() -> None:
    root_text = str(Path.cwd())
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    scripts_ok = compileall.compile_dir("scripts", force=True, quiet=1)
    tests_ok = compileall.compile_dir("tests", force=True, quiet=1)
    if not scripts_ok or not tests_ok:
        sys.exit(1)

    try:
        import pytest
    except ImportError:
        print("BRANCH: FALLBACK")
        sys.exit(_run_fallback())

    print("BRANCH: PYTEST")
    sys.exit(pytest.main(["tests", "-x", "--tb=short"]))


if __name__ == "__main__":
    main()
