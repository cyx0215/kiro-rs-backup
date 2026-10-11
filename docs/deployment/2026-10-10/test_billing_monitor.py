"""Compatibility entry point for the relocated regression tests."""

import pathlib
import runpy
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
TARGET = ROOT / "monitoring" / "billing" / "tests" / "test_billing_monitor.py"


def load_tests(loader, tests, pattern):
    namespace = runpy.run_path(str(TARGET))
    return unittest.TestSuite(
        loader.loadTestsFromTestCase(value)
        for value in namespace.values()
        if isinstance(value, type) and issubclass(value, unittest.TestCase)
    )


if __name__ == "__main__":
    globals().update({key: value for key, value in runpy.run_path(str(TARGET)).items()
                      if isinstance(value, type) and issubclass(value, unittest.TestCase)})
    unittest.main()
