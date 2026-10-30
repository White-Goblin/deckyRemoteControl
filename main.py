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

import audio  # noqa: E402
import pointer_sync  # noqa: E402
import uinput  # noqa: E402
from wsserver import Server  # noqa: E402

SETTINGS_FILE = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "remote_control.json")
DEFAULTS = {"port": 8765, "sensitivity": 1.0, "token": ""}
EMIT_INTERVAL = 1 / 60
SYNC_INTERVAL = 0.08  # seconds between real-pointer checks while input flows
SYNC_SNAP = 20  # device px of disagreement before the tracked position is replaced


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
        self.fx = self.fy = 0.0
        self.sent_x = self.sent_y = 0
        self.last_input = 0.0
        self.sync_task = None
        self.resync = None
        self.audio = audio.Audio()
        self.vol_lock = asyncio.Lock()
        self.vol_pending = 0
        self.xpointers = None
        self.view_event = None
        self.press_lock = asyncio.Lock()
        self.scale = 1.0
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
            "cursor_ws": f"ws://127.0.0.1:{port}/ws?t={self.settings['token']}&role=cursor",
            "error": self.error,
        }

    async def get_state(self):
        return self._state()

    async def set_enabled(self, enabled: bool):
        self.error = ""
        if enabled and self.server is None:
            try:
                self.pointer = uinput.Pointer()
                try:
                    self.xpointers = pointer_sync.XPointers()
                except OSError as e:
                    self.xpointers = None
                    decky.logger.warning(f"pointer resync unavailable: {e}")
                self.keyboard = uinput.Keyboard()
                self.server = Server(
                    os.path.join(decky.DECKY_PLUGIN_DIR, "web", "index.html"),
                    lambda: self.settings["token"], self._on_message, self._on_clients)
                await self.server.start(self.settings["port"])
                self.pointer.move(-20000, -20000)  # known corner; tracking restarts at (0, 0)
                self.x = self.y = 0.0
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

    async def set_screen(self, width: int, height: int, scale: float):
        """Frontend reports the screen in CSS px and its device pixel ratio. The real pointer
        moves in device pixels, so tracking is done there and divided by `scale` for drawing."""
        if width < 100 or height < 100 or scale < 0.5:
            return
        self.scale = float(scale)
        self.width, self.height = int(width * scale), int(height * scale)
        self.x = min(self.x, self.width - 1)
        self.y = min(self.y, self.height - 1)

    # ---- input handling ----
    def _on_clients(self, n):
        self.clients = n
        asyncio.ensure_future(decky.emit("rc_clients", n))

    def _activity(self, down=None, force=False):
        now = time.monotonic()
        if not force and now - self.last_emit < EMIT_INTERVAL:
            return
        self.last_emit = now
        d = bool(down)
        x, y, sp = round(self.x / self.scale), round(self.y / self.scale), round(self.speed, 1)
        if self.server:
            self.server.broadcast_cursor(json.dumps({"x": x, "y": y, "s": sp, "d": d}))
        asyncio.ensure_future(decky.emit("rc_activity", d))

    @staticmethod
    def _btn(right):
        return uinput.BTN_RIGHT if right else uinput.BTN_LEFT

    # The cursor overlay is a browser view above Steam's UI and would swallow clicks, so the
    # frontend hides it while a button is down (rc_view) and confirms through view_ready().
    def _click(self, n):
        asyncio.ensure_future(self._click_async(n))

    async def action_result(self, message: str):
        """The frontend reports what an action did; show it on the phone."""
        self._toast(message)

    def _toast(self, message):
        if self.server:
            self.server.broadcast_clients(json.dumps({"t": "toast", "m": str(message)[:80]}))

    async def _volume(self, action):
        try:
            async with self.vol_lock:
                message = await self.loop.run_in_executor(None, self.audio.press, action)
            self._toast(message)
        except Exception as e:  # noqa: BLE001
            decky.logger.warning(f"volume {action} failed: {e!r}")
            self._toast("Volume control unavailable")
        finally:
            self.vol_pending -= 1

    async def view_ready(self):
        if self.view_event:
            self.view_event.set()

    async def _hide_view(self, hidden):
        self.view_event = asyncio.Event()
        await decky.emit("rc_view", not hidden)
        if hidden:
            try:
                await asyncio.wait_for(self.view_event.wait(), 0.3)
            except asyncio.TimeoutError:
                pass

    async def _click_async(self, n):
        async with self.press_lock:
            await self._hide_view(True)
            self.pointer.button(n, True)
            await asyncio.sleep(0.03)
            self.pointer.button(n, False)
            await asyncio.sleep(0.1)
            await self._hide_view(False)
            self._activity(down=True, force=True)

    def _sync_begin(self):
        self.last_input = time.monotonic()
        if self.xpointers is None or (self.sync_task and not self.sync_task.done()):
            return
        self.resync = pointer_sync.Resync(self.width, self.height)
        self.resync.begin(self.xpointers.read())
        self.sent_x = self.sent_y = 0
        self.sync_task = asyncio.ensure_future(self._sync_loop())

    async def _sync_loop(self):
        """While input is flowing, follow the real pointer (moved by other devices or by focus
        changes) so the drawn cursor matches it."""
        try:
            while time.monotonic() - self.last_input < 2.0 and self.server:
                await asyncio.sleep(SYNC_INTERVAL)
                sent, self.sent_x, self.sent_y = (self.sent_x, self.sent_y), 0, 0
                real = self.resync.update(self.xpointers.read(), sent)
                if real and (abs(real[0] - self.x) > SYNC_SNAP or abs(real[1] - self.y) > SYNC_SNAP):
                    decky.logger.info(f"resync: tracked ({self.x:.0f},{self.y:.0f}) -> real {real}")
                    self.x = float(min(max(real[0], 0), self.width - 1))
                    self.y = float(min(max(real[1], 0), self.height - 1))
                    self._activity(force=True)
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"resync error: {e!r}")

    def _rel(self, dx, dy):
        """Relative move with sub-pixel carry so tracking matches what is injected."""
        self.fx += dx
        self.fy += dy
        ix, iy = int(self.fx), int(self.fy)
        self.fx -= ix
        self.fy -= iy
        self.pointer.move(ix, iy)
        self.sent_x += ix
        self.sent_y += iy

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
            self._sync_begin()
            dx, dy = float(m.get("dx", 0)), float(m.get("dy", 0))
            mag = math.hypot(dx, dy)
            gain = self.settings["sensitivity"] * (1 + 0.05 * min(mag, 30)) * self.scale
            self.x = min(max(self.x + dx * gain, 0), self.width - 1)
            self.y = min(max(self.y + dy * gain, 0), self.height - 1)
            self.speed = self.speed * 0.6 + mag * gain / self.scale * 0.4
            self._rel(dx * gain, dy * gain)
            self._activity()
        elif t == "c":
            self._click(self._btn(m.get("b") == "r"))
            self.speed = 0
        elif t == "s":
            p.scroll(int(m.get("dy", 0)), int(m.get("dx", 0)))
            self._activity(force=True)
        elif t == "k":
            self._type(str(m.get("text", ""))[:2000])
        elif t == "vol":
            if m.get("a") in ("up", "down", "mute") and self.vol_pending < 3:
                self.vol_pending += 1
                asyncio.ensure_future(self._volume(m["a"]))
        elif t == "act":
            if m.get("a") in ("steam", "qam", "quit"):
                asyncio.ensure_future(decky.emit("rc_action", m["a"]))
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
        if self.sync_task:
            self.sync_task.cancel()
        self.xpointers = None
        self.clients = 0

    async def _unload(self):
        await self._shutdown()

    async def _uninstall(self):
        await self._shutdown()
