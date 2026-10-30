import asyncio
import json
import math
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time

import decky

sys.path.insert(0, os.path.join(decky.DECKY_PLUGIN_DIR, "py_modules"))

import uinput  # noqa: E402
from wsserver import Server  # noqa: E402

SETTINGS_FILE = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "remote_control.json")
DEFAULTS = {"port": 8765, "sensitivity": 1.0, "token": ""}
EMIT_INTERVAL = 1 / 60


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Plugin:
    async def _main(self):
        self.loop = asyncio.get_event_loop()
        self.settings = dict(DEFAULTS)
        try:
            with open(SETTINGS_FILE) as f:
                self.settings.update(json.load(f))
        except (OSError, ValueError):
            pass
        if not self.settings["token"]:
            self.settings["token"] = secrets.token_urlsafe(9)
            self._save()
        self.server = None
        self.pointer = self.keyboard = None
        self.width, self.height = 1280, 800
        self.x, self.y = self.width / 2, self.height / 2
        self.speed = 0.0
        self.last_emit = 0.0
        self.clients = 0
        self.error = ""
        decky.logger.info("Remote Control loaded (server off until enabled)")

    def _save(self):
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(self.settings, f)

    # ---- state exposed to the frontend ----
    def _state(self):
        port = self.settings["port"]
        return {
            "running": self.server is not None,
            "url": f"http://{lan_ip()}:{port}/?t={self.settings['token']}",
            "clients": self.clients,
            "sensitivity": self.settings["sensitivity"],
            "error": self.error,
        }

    async def get_state(self):
        return self._state()

    async def set_enabled(self, enabled: bool):
        self.error = ""
        if enabled and self.server is None:
            try:
                self.pointer = uinput.Pointer(self.width, self.height)
                self.keyboard = uinput.Keyboard()
                self.server = Server(
                    os.path.join(decky.DECKY_PLUGIN_DIR, "web", "index.html"),
                    lambda: self.settings["token"], self._on_message, self._on_clients)
                await self.server.start(self.settings["port"])
            except Exception as e:  # noqa: BLE001
                decky.logger.error(f"start failed: {e!r}")
                self.error = f"Could not start: {e}"
                await self._shutdown()
        elif not enabled:
            await self._shutdown()
        return self._state()

    async def regenerate_token(self):
        self.settings["token"] = secrets.token_urlsafe(9)
        self._save()
        if self.server:
            for w in list(self.server.clients):
                w.close()
        return self._state()

    async def set_sensitivity(self, value: float):
        self.settings["sensitivity"] = max(0.3, min(3.0, float(value)))
        self._save()

    async def set_screen(self, width: int, height: int):
        """Frontend reports the real screen size so absolute coordinates map 1:1."""
        if (width, height) == (self.width, self.height) or width < 100 or height < 100:
            return
        self.width, self.height = int(width), int(height)
        self.x = min(self.x, self.width - 1)
        self.y = min(self.y, self.height - 1)
        if self.server is not None:  # recreate pointer with new axis range
            self.pointer.close()
            self.pointer = uinput.Pointer(self.width, self.height)

    # ---- input handling ----
    def _on_clients(self, n):
        self.clients = n
        asyncio.ensure_future(decky.emit("rc_clients", n))

    def _activity(self, down=None, force=False):
        now = time.monotonic()
        if not force and now - self.last_emit < EMIT_INTERVAL:
            return
        self.last_emit = now
        asyncio.ensure_future(decky.emit(
            "rc_activity", round(self.x), round(self.y), round(self.speed, 1),
            self.down if down is None else down))

    down = False

    def _on_message(self, m):
        try:
            self._dispatch(m)
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"input error: {e!r}")

    def _dispatch(self, m):
        t = m.get("t")
        p = self.pointer
        if p is None:
            return
        if t == "m":
            dx, dy = float(m.get("dx", 0)), float(m.get("dy", 0))
            mag = math.hypot(dx, dy)
            gain = self.settings["sensitivity"] * (1 + 0.05 * min(mag, 30))
            self.x = min(max(self.x + dx * gain, 0), self.width - 1)
            self.y = min(max(self.y + dy * gain, 0), self.height - 1)
            self.speed = self.speed * 0.6 + mag * gain * 0.4
            p.move_to(self.x, self.y)
            self._activity()
        elif t == "c":
            btn = uinput.BTN_RIGHT if m.get("b") == "r" else uinput.BTN_LEFT
            p.move_to(self.x, self.y)
            p.button(btn, True)
            p.button(btn, False)
            self.speed = 0
            self._activity(down=True, force=True)
        elif t == "d":
            self.down = bool(m.get("s"))
            p.move_to(self.x, self.y)
            p.button(uinput.BTN_LEFT, self.down)
            self._activity(force=True)
        elif t == "s":
            p.scroll(int(m.get("dy", 0)), int(m.get("dx", 0)))
            self._activity(force=True)
        elif t == "k":
            self._type(str(m.get("text", ""))[:2000])
        elif t == "key":
            code = uinput.KEY.get(m.get("k"))
            if code:
                self.keyboard.tap(code)

    def _type(self, text):
        kb = self.keyboard
        for ch in text:
            km = uinput.char_to_key(ch)
            if km:
                kb.tap(km[0], shift=km[1])
            else:
                self._type_unicode(ch)

    def _type_unicode(self, ch):
        if shutil.which("xdotool"):
            env = dict(os.environ, DISPLAY=os.environ.get("DISPLAY", ":0"))
            subprocess.Popen(["xdotool", "type", "--clearmodifiers", ch], env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        # GTK/IBus style Ctrl+Shift+U hex entry
        kb = self.keyboard
        kb.tap(uinput.char_to_key("u")[0], shift=True, ctrl=True)
        for h in format(ord(ch), "x"):
            kb.tap(uinput.char_to_key(h)[0])
        kb.tap(uinput.KEY["Space"])

    async def _shutdown(self):
        if self.server:
            await self.server.stop()
            self.server = None
        for dev in (self.pointer, self.keyboard):
            if dev:
                try:
                    dev.close()
                except OSError:
                    pass
        self.pointer = self.keyboard = None
        self.clients = 0

    async def _unload(self):
        await self._shutdown()

    async def _uninstall(self):
        await self._shutdown()
