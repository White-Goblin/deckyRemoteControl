import asyncio, base64, json, os, struct, sys, types, tempfile, importlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
import audio, pointer_sync, uinput, wsserver


def test_char_map():
    assert uinput.char_to_key("a") == (30, False)
    assert uinput.char_to_key("A") == (30, True)
    assert uinput.char_to_key("!") == (2, True)
    assert uinput.char_to_key("é") is None
    assert uinput.char_to_key("\n") == (28, False)


def test_event_pack():
    assert len(uinput.pack_event(1, 2, 3)) == 24
    assert struct.calcsize("HHHH80sI") == 92 and struct.calcsize("HHiiiiii") == 28


def _mask(payload):
    m = os.urandom(4)
    return m + bytes(b ^ m[i % 4] for i, b in enumerate(payload))


def test_end_to_end():
    tmp = tempfile.mkdtemp()
    events, calls = [], []
    fake = types.ModuleType("decky")
    fake.DECKY_PLUGIN_DIR = ROOT
    fake.DECKY_PLUGIN_SETTINGS_DIR = tmp
    fake.logger = types.SimpleNamespace(info=print, error=print, warning=print)
    plugin = []
    async def emit(*a):
        events.append(a)
        if a[0] == "rc_view" and not a[1]:  # the frontend would hide its view and confirm
            plugin[0].view_event.set()
    fake.emit = emit
    sys.modules["decky"] = fake

    class FakePointer:
        def __init__(s): pass
        def move(s, dx, dy): calls.append(("move", int(dx), int(dy)))
        def button(s, b, d): calls.append(("btn", b, d))
        def scroll(s, v, h=0): calls.append(("scroll", v, h))
        def close(s): pass
    class FakeKb:
        def tap(s, c, shift=False, ctrl=False): calls.append(("tap", c, shift))
        def close(s): pass
    uinput.Pointer, uinput.Keyboard = FakePointer, FakeKb
    main = importlib.import_module("main")
    main.SETTINGS_FILE = os.path.join(tmp, "s.json")

    async def run():
        p = main.Plugin()
        plugin.append(p)
        await p._main()
        p.settings["port"] = 18765
        st = await p.set_enabled(True)
        assert st["running"]
        tok = p.settings["token"]

        async def client(token):
            r, w = await asyncio.open_connection("127.0.0.1", 18765)
            w.write((f"GET /ws?t={token} HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n"
                     "Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                     "Sec-WebSocket-Version: 13\r\n\r\n").encode())
            return r, w, await r.readline()

        r, w, line = await client("wrong")
        assert b"403" in line
        r, w, line = await client(tok)
        assert b"101" in line
        await r.readuntil(b"\r\n\r\n")
        for m in ({"t": "m", "dx": 10, "dy": 5}, {"t": "c", "b": "l"},
                  {"t": "s", "dy": -2, "dx": 0}, {"t": "k", "text": "Hi"}):
            d = json.dumps(m).encode()
            w.write(bytes([0x81, 0x80 | len(d)]) + _mask(d))
        await w.drain()
        await asyncio.sleep(0.5)
        kinds = [c[0] for c in calls]
        assert "move" in kinds and ("btn", 0x110, True) in calls
        assert ("scroll", -2, 0) in calls
        assert ("tap", 35, True) in calls and ("tap", 23, False) in calls  # H, i
        assert any(e[0] == "rc_activity" for e in events)
        # HTTP page served
        r2, w2 = await asyncio.open_connection("127.0.0.1", 18765)
        w2.write(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        assert b"Remote Control" in await r2.read()
        # the running app is pushed to the phone, and again on request
        async def frame():
            n = (await asyncio.wait_for(r.readexactly(2), 2))[1] & 0x7F
            return json.loads(await r.readexactly(n))
        await p.app_state("Jellyfin")
        assert await frame() == {"t": "app", "name": "Jellyfin"}
        d = json.dumps({"t": "hello"}).encode()
        w.write(bytes([0x81, 0x80 | len(d)]) + _mask(d))
        assert await frame() == {"t": "app", "name": "Jellyfin"}
        await p.app_state("")
        assert await frame() == {"t": "app", "name": ""}
        # disabling while a phone is still connected must not hang
        await asyncio.wait_for(p._unload(), 5)
        w.close()
    asyncio.run(run())


def test_resync_picks_the_live_display():
    r = pointer_sync.Resync(3840, 2160)
    # :0 is stale (center), :1 follows the real pointer and was moved by another device first
    r.begin({":0": (1920, 1080), ":1": (638, 480)})
    real = r.update({":0": (1920, 1080), ":1": (1100, 700)}, (10, 5))   # moved +462,+220 by someone else too
    assert real is None                                             # inconsistent with our own move: not trusted yet
    real = r.update({":0": (1920, 1080), ":1": (1110, 705)}, (10, 5))   # now responds to exactly our move
    assert real == (1110, 705) and r.live == ":1"
    # another device moves it while we are idle-ish: the live display's value is returned
    assert r.update({":0": (3839, 2159), ":1": (1500, 900)}, (0, 0)) == (1500, 900)


def test_resync_ignores_flipping_stale_display_and_drops_dead_live():
    r = pointer_sync.Resync(3840, 2160)
    r.begin({":0": (3839, 2159), ":1": (500, 500)})
    assert r.update({":0": (1920, 1080), ":1": (500, 500)}, (8, 8)) is None   # stale flip is not our move
    assert r.update({":0": (3839, 2159), ":1": (508, 508)}, (8, 8)) == (508, 508)
    for _ in range(3):                                                    # :1 stops following us
        out = r.update({":0": (3839, 2159), ":1": (508, 508)}, (8, 8))
    assert r.live is None and out is None


def _audio(responses):
    calls = []
    def runner(cmd):
        calls.append(cmd)
        for key, out in responses.items():
            if key in " ".join(cmd):
                if isinstance(out, Exception):
                    raise out
                return out
        return ""
    return audio.Audio(runner), calls


def test_audio_cec_when_hdmi_and_active():
    a, calls = _audio({"wpctl inspect": 'node.name = "alsa_output.pci.hdmi-stereo"',
                       "Active": "b true", "AudioLogicalAddress": "y 5"})
    assert a.press("up") == "Volume up"
    assert calls[-1][:5] == ["busctl", "--user", "call", audio.CEC_BUS, audio.CEC_PATH]
    assert calls[-1][-3:] == ["VolumeUp", "y", "5"]
    assert a.press("mute") == "Mute toggled" and calls[-1][-3] == "Mute"


def test_audio_system_volume_and_unmute():
    a, calls = _audio({"wpctl inspect": 'node.name = "alsa_output.usb-dac"', "get-volume": "Volume: 0.65"})
    assert a.press("up") == "Volume 65%"
    assert ["wpctl", "set-volume", "-l", "1.0", audio.SINK, "5%+"] in calls
    a, calls = _audio({"wpctl inspect": "", "get-volume": "Volume: 0.40 [MUTED]"})
    assert a.press("mute") == "Muted"
    assert a.press("down") == "Volume 40%" and ["wpctl", "set-mute", audio.SINK, "0"] in calls


def test_audio_falls_back_to_system_when_cec_call_fails():
    state = {"cec": True}
    calls = []
    def runner(cmd):
        calls.append(cmd)
        text = " ".join(cmd)
        if "wpctl inspect" in text:
            return 'node.name = "alsa_output.hdmi"'
        if "Active" in text:
            return "b true" if state["cec"] else "b false"
        if "AudioLogicalAddress" in text:
            return "y 5"
        if "call" in text:
            state["cec"] = False
            raise RuntimeError("cec gone")
        if "get-volume" in text:
            return "Volume: 0.50"
        return ""
    assert audio.Audio(runner).press("down") == "Volume 50%"
