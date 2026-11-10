# Tests must never open a browser, window or terminal on the desktop running them.
from orbit import runner as _runner


def _no_launch(self, argv):
    raise AssertionError(f"a test tried to open something for real: {argv}")


_runner.Runner.launch = _no_launch
