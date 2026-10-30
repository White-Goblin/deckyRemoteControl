"""Volume and mute: HDMI CEC for a CEC soundbar/TV, otherwise PipeWire (wpctl).

With an HDMI soundbar the real volume lives in the soundbar (PipeWire's own gain stays fixed), so
wpctl would do nothing audible. SteamOS runs `cecd` on the session bus for that case."""
import os
import re
import subprocess
import time

RUNTIME_DIR = f"/run/user/{os.getuid()}" if os.getuid() else "/run/user/1000"
CEC_BUS = "com.steampowered.CecDaemon1"
CEC_PATH = "/com/steampowered/CecDaemon1/Devices/Cec0"
CEC_IFACE = "com.steampowered.CecDaemon1.CecDevice1"
CEC_METHODS = {"up": "VolumeUp", "down": "VolumeDown", "mute": "Mute"}
CEC_MESSAGES = {"up": "Volume up", "down": "Volume down", "mute": "Mute toggled"}
MODE_TTL = 30  # seconds before CEC vs system is detected again (headphones, CEC link coming up)
SINK = "@DEFAULT_AUDIO_SINK@"
_HDMI_SINK = re.compile(r'(?:api\.alsa\.path|node\.name) = "[^"]*hdmi', re.IGNORECASE)


def run(cmd):
    env = {**os.environ, "XDG_RUNTIME_DIR": RUNTIME_DIR, "DBUS_SESSION_BUS_ADDRESS": f"unix:path={RUNTIME_DIR}/bus"}
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=5, env=env)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"{cmd[0]} exited with {r.returncode}")
    return r.stdout.strip()


class Audio:
    def __init__(self, runner=run):
        self._run = runner
        self._mode = None
        self._checked = 0.0

    def _cec_address(self):
        """Logical address of the CEC audio device, or None unless audio plays over HDMI with CEC up."""
        try:
            if not _HDMI_SINK.search(self._run(["wpctl", "inspect", SINK])):
                return None
            busctl = ["busctl", "--user", "get-property", CEC_BUS, CEC_PATH, CEC_IFACE]
            if self._run(busctl + ["Active"]).split()[-1] != "true":
                return None
            address = self._run(busctl + ["AudioLogicalAddress"]).split()[-1]
        except (RuntimeError, OSError, subprocess.SubprocessError, IndexError):
            return None
        return int(address) if address.isdigit() else None

    def _detect(self, force=False):
        if force or self._mode is None or time.monotonic() - self._checked > MODE_TTL:
            address = self._cec_address()
            self._mode = ("cec", address) if address is not None else ("system", None)
            self._checked = time.monotonic()
        return self._mode

    def _system(self, action):
        if action == "mute":
            self._run(["wpctl", "set-mute", SINK, "toggle"])
        else:
            self._run(["wpctl", "set-volume", "-l", "1.0", SINK, "5%+" if action == "up" else "5%-"])
        match = re.match(r"Volume:\s*([\d.]+)(\s*\[MUTED\])?", self._run(["wpctl", "get-volume", SINK]))
        if match and match.group(2) and action != "mute":
            self._run(["wpctl", "set-mute", SINK, "0"])  # changing the volume unmutes, like a phone
            return f"Volume {round(float(match.group(1)) * 100)}%"
        if not match:
            return CEC_MESSAGES[action]
        return "Muted" if match.group(2) else f"Volume {round(float(match.group(1)) * 100)}%"

    def press(self, action):
        """Blocking; returns a short message to show on the phone."""
        if action not in CEC_METHODS:
            raise ValueError(action)
        for attempt in (0, 1):
            mode, address = self._detect(force=attempt == 1)
            try:
                if mode == "cec":
                    self._run(["busctl", "--user", "call", CEC_BUS, CEC_PATH, CEC_IFACE,
                               CEC_METHODS[action], "y", str(address)])
                    return CEC_MESSAGES[action]
                return self._system(action)
            except (RuntimeError, OSError, subprocess.SubprocessError):
                if attempt == 1:
                    raise
        raise RuntimeError("unreachable")
