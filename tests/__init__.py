# Tests must never open a browser, window or terminal on the desktop running them.
from orbit import runner as _runner


def _no_launch(self, argv):
    raise AssertionError(f"a test tried to open something for real: {argv}")


_runner.Runner.launch = _no_launch


# Tests that stand in for real programs with small shell scripts, or use a pseudo-terminal, need a Unix system.
import sys as _sys
import unittest as _unittest

unix_only = _unittest.skipIf(_sys.platform == "win32", "uses shell scripts as stand-ins for real programs")
