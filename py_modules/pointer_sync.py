"""Resync the tracked pointer with the real one.

Other devices (a controller trackpad, a mouse) or focus changes can move the real pointer, so dead
reckoning alone drifts. The X server of whichever window has focus follows the real pointer, while
the other server reports a stale value (a corner or the screen center). The live one is the display
whose pointer responds to our own moves."""
import ctypes
import ctypes.util
import glob


class XPointers:
    """Reads the pointer of every X display. Connections are opened per read: Xlib terminates the
    whole process if a held connection dies (an app's Xwayland going away), but a failed open is safe."""

    def __init__(self):
        name = ctypes.util.find_library("X11")
        if not name:
            raise OSError("libX11 not found")
        self.x11 = ctypes.CDLL(name)
        self.x11.XOpenDisplay.restype = ctypes.c_void_p
        self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x11.XDefaultRootWindow.restype = ctypes.c_ulong
        self.x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        self.x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.x11.XQueryPointer.argtypes = [ctypes.c_void_p, ctypes.c_ulong] + [ctypes.c_void_p] * 7
        if not self.read():
            raise OSError("no X display reachable")

    def read(self):
        out = {}
        for sock in sorted(glob.glob("/tmp/.X11-unix/X*")):
            display = ":" + sock.rsplit("X", 1)[1]
            dpy = self.x11.XOpenDisplay(display.encode())
            if not dpy:
                continue
            try:
                root, child = ctypes.c_ulong(), ctypes.c_ulong()
                rx, ry, wx, wy, mask = (ctypes.c_int(), ctypes.c_int(), ctypes.c_int(),
                                        ctypes.c_int(), ctypes.c_uint())
                if self.x11.XQueryPointer(dpy, self.x11.XDefaultRootWindow(dpy), ctypes.byref(root),
                                          ctypes.byref(child), ctypes.byref(rx), ctypes.byref(ry),
                                          ctypes.byref(wx), ctypes.byref(wy), ctypes.byref(mask)):
                    out[display] = (rx.value, ry.value)
            finally:
                self.x11.XCloseDisplay(dpy)
        return out


class Resync:
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.prev = {}
        self.live = None
        self.silent = 0

    def begin(self, readings):
        """Baseline taken right before the first move of a burst."""
        self.prev = readings
        self.live = None
        self.silent = 0

    def _at_edge(self, x, y):
        return x <= 1 or y <= 1 or x >= self.width - 2 or y >= self.height - 2

    def update(self, readings, sent):
        """`sent` is the (dx, dy) in device pixels we injected since the last call. Returns the real
        pointer position when a live display is known, else None."""
        sx, sy = sent
        if abs(sx) + abs(sy) >= 2:
            best = None
            for d, (x, y) in readings.items():
                p = self.prev.get(d)
                if p is None:
                    continue
                dx, dy = x - p[0], y - p[1]
                if abs(dx) + abs(dy) < 2 or dx * sx < 0 or dy * sy < 0:
                    continue
                if abs(dx) > abs(sx) + 4 or abs(dy) > abs(sy) + 4:
                    continue
                err = abs(dx - sx) + abs(dy - sy)
                if best is None or err < best[0]:
                    best = (err, d)
            if best:
                self.live, self.silent = best[1], 0
            elif self.live and abs(sx) + abs(sy) >= 6 and not self._at_edge(*readings.get(self.live, (0, 0))):
                self.silent += 1
                if self.silent >= 3:  # the display stopped following us (focus moved elsewhere)
                    self.live = None
        self.prev = readings
        return readings.get(self.live) if self.live else None
