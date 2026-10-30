"""Minimal /dev/uinput wrapper (stdlib only): absolute pointer + keyboard."""
import fcntl
import os
import struct
import time

EV_SYN, EV_KEY, EV_REL, EV_ABS = 0x00, 0x01, 0x02, 0x03
SYN_REPORT = 0
REL_X, REL_Y, REL_HWHEEL, REL_WHEEL = 0x00, 0x01, 0x06, 0x08
ABS_X, ABS_Y = 0x00, 0x01
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112

UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
UI_DEV_SETUP = 0x405C5503
UI_ABS_SETUP = 0x401C5504
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_RELBIT = 0x40045566
UI_SET_ABSBIT = 0x40045567

BUS_VIRTUAL = 0x06

# Linux key codes
KEY = {
    "Escape": 1, "Backspace": 14, "Tab": 15, "Enter": 28, "Ctrl": 29, "Shift": 42,
    "Space": 57, "Home": 102, "Up": 103, "PageUp": 104, "Left": 105, "Right": 106,
    "End": 107, "Down": 108, "PageDown": 109, "Delete": 111,
}
_ROW = {
    **{c: 2 + i for i, c in enumerate("1234567890")},
    "-": 12, "=": 13,
    **{c: 16 + i for i, c in enumerate("qwertyuiop")},
    "[": 26, "]": 27,
    **{c: 30 + i for i, c in enumerate("asdfghjkl")},
    ";": 39, "'": 40, "`": 41, "\\": 43,
    **{c: 44 + i for i, c in enumerate("zxcvbnm")},
    ",": 51, ".": 52, "/": 53, " ": 57,
}
_SHIFT = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8",
    "(": "9", ")": "0", "_": "-", "+": "=", "{": "[", "}": "]", ":": ";", '"': "'",
    "~": "`", "|": "\\", "<": ",", ">": ".", "?": "/",
}


def char_to_key(ch):
    """Return (keycode, needs_shift) for an ASCII char, or None."""
    if ch == "\n":
        return KEY["Enter"], False
    if ch == "\t":
        return KEY["Tab"], False
    if ch in _ROW:
        return _ROW[ch], False
    if ch.isascii() and ch.isalpha() and ch.isupper():
        return _ROW[ch.lower()], True
    if ch in _SHIFT:
        return _ROW[_SHIFT[ch]], True
    return None


def pack_event(etype, code, value, ts=None):
    t = time.time() if ts is None else ts
    return struct.pack("llHHi", int(t), int((t % 1) * 1_000_000), etype, code, value)


class Device:
    def __init__(self, name, keys=(), rels=(), abs_axes=None, path="/dev/uinput"):
        self.fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
            for k in keys:
                fcntl.ioctl(self.fd, UI_SET_KEYBIT, k)
            if rels:
                fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_REL)
                for r in rels:
                    fcntl.ioctl(self.fd, UI_SET_RELBIT, r)
            if abs_axes:
                fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_ABS)
                for code in abs_axes:
                    fcntl.ioctl(self.fd, UI_SET_ABSBIT, code)
            setup = struct.pack("HHHH80sI", BUS_VIRTUAL, 0x1209, 0xDE01, 1,
                                name.encode()[:79], 0)
            fcntl.ioctl(self.fd, UI_DEV_SETUP, setup)
            for code, (lo, hi) in (abs_axes or {}).items():
                fcntl.ioctl(self.fd, UI_ABS_SETUP,
                            struct.pack("HHiiiiii", code, 0, 0, lo, hi, 0, 0, 0))
            fcntl.ioctl(self.fd, UI_DEV_CREATE)
        except Exception:
            os.close(self.fd)
            raise

    def emit(self, *events):
        buf = b"".join(pack_event(t, c, v) for t, c, v in events)
        buf += pack_event(EV_SYN, SYN_REPORT, 0)
        try:
            os.write(self.fd, buf)
        except BlockingIOError:
            pass

    def close(self):
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        finally:
            os.close(self.fd)


class Pointer(Device):
    """Standard relative mouse: every compositor treats it as a normal mouse."""

    def __init__(self, **kw):
        super().__init__(
            "Decky Remote Pointer",
            keys=(BTN_LEFT, BTN_RIGHT, BTN_MIDDLE),
            rels=(REL_X, REL_Y, REL_WHEEL, REL_HWHEEL), **kw)

    def move(self, dx, dy):
        ev = []
        if dx:
            ev.append((EV_REL, REL_X, int(dx)))
        if dy:
            ev.append((EV_REL, REL_Y, int(dy)))
        if ev:
            self.emit(*ev)

    def button(self, btn, down):
        self.emit((EV_KEY, btn, 1 if down else 0))

    def scroll(self, v, h=0):
        ev = []
        if v:
            ev.append((EV_REL, REL_WHEEL, int(v)))
        if h:
            ev.append((EV_REL, REL_HWHEEL, int(h)))
        if ev:
            self.emit(*ev)


class Keyboard(Device):
    def __init__(self, **kw):
        keys = set(KEY.values()) | set(_ROW.values())
        super().__init__("Decky Remote Keyboard", keys=sorted(keys), **kw)

    def tap(self, code, shift=False, ctrl=False):
        mods = ([KEY["Ctrl"]] if ctrl else []) + ([KEY["Shift"]] if shift else [])
        for m in mods:
            self.emit((EV_KEY, m, 1))
        self.emit((EV_KEY, code, 1))
        self.emit((EV_KEY, code, 0))
        for m in reversed(mods):
            self.emit((EV_KEY, m, 0))
