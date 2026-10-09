"""Discover embedded tests from standalone files whose names begin with digits."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


def load_tests(
    loader: unittest.TestLoader,
    standard_tests: unittest.TestSuite,
    pattern: str | None,
) -> unittest.TestSuite:
    """Load each numbered pattern module by path and collect its test cases."""
    del pattern
    suite = unittest.TestSuite(standard_tests)
    directory = Path(__file__).parent
    for source_path in sorted(directory.glob("[0-9][0-9]_*.py")):
        module_name = f"pattern_{source_path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, source_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Unable to load test module at {source_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        suite.addTests(loader.loadTestsFromModule(module))
    return suite


if __name__ == "__main__":
    unittest.main()