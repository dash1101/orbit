"""Entry point for the standalone programs (PyInstaller): the same as `python -m orbit`."""
import sys

from orbit.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
