"""Running other programs quietly: on Windows a console program started from a windowed one (like Orbit) flashes a
black console window unless it's told not to. Everything Orbit runs in the background goes through `hidden()`."""
import sys

CREATE_NO_WINDOW = 0x08000000


def hidden():
    """subprocess keyword arguments that keep a console program's window from appearing (Windows only)."""
    return {"creationflags": CREATE_NO_WINDOW} if sys.platform == "win32" else {}
