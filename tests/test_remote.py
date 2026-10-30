import asyncio, base64, json, os, struct, sys, types, tempfile, importlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
import audio, netinfo, pointer_sync, store, uinput, wsserver


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
    main = _load_main(tmp)
    plugin = []
    async def emit(*a):
        events.append(a)
        if a[0] == "rc_view" and not a[1]:  # the frontend would hide its view and confirm
            plugin[0].view_event.set()
    main.decky.emit = emit

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


# ---- persistence and stable links ----
def _load_main(tmp):
    if "decky" not in sys.modules:
        sys.modules["decky"] = types.ModuleType("decky")
    d = sys.modules["decky"]
    d.DECKY_PLUGIN_DIR, d.DECKY_PLUGIN_SETTINGS_DIR = ROOT, tmp   # main.py reads these when imported
    main = importlib.import_module("main")
    d = main.decky
    d.DECKY_PLUGIN_DIR, d.DECKY_PLUGIN_SETTINGS_DIR = ROOT, tmp
    d.logger = types.SimpleNamespace(info=lambda *a: None, error=lambda *a: None, warning=lambda *a: None)
    async def emit(*a): pass
    d.emit = emit
    main.SETTINGS_FILE = os.path.join(tmp, "s.json")
    main.pointer_sync.XPointers = lambda: (_ for _ in ()).throw(OSError("no X"))

    class Dev:
        def __init__(s, *a, **k): pass
        def move(s, *a): pass
        def button(s, *a): pass
        def scroll(s, *a): pass
        def tap(s, *a, **k): pass
        def close(s): pass
    uinput.Pointer = uinput.Keyboard = Dev
    return main


def test_store_atomic_and_recovers_from_backup():
    tmp = tempfile.mkdtemp(); path = os.path.join(tmp, "x.json")
    store.save(path, {"token": "keepme", "n": 1})
    store.save(path, {"token": "keepme", "n": 2})
    assert sorted(os.listdir(tmp)) == ["x.json", "x.json.bak"]            # no stray temp file
    open(path, "w").write('{"token": "ke')                                 # power cut mid-write
    assert store.load(path)["token"] == "keepme"                           # recovered from the backup
    assert store.load(os.path.join(tmp, "missing.json")) == {}


def test_links_prefer_the_stable_name_with_an_ip_fallback():
    assert netinfo.links(8765, "T", "name", ip="10.0.0.5", name="steam-machine", mdns=True) == (
        "http://steam-machine.local:8765/?t=T", "http://10.0.0.5:8765/?t=T")
    assert netinfo.links(8765, "T", "ip", ip="10.0.0.5", name="steam-machine", mdns=True) == (
        "http://10.0.0.5:8765/?t=T", "http://steam-machine.local:8765/?t=T")
    assert netinfo.links(8765, "T", "name", ip="10.0.0.5", name="steam-machine", mdns=False) == (
        "http://10.0.0.5:8765/?t=T", None)                                 # no mDNS: only the IP


def test_manifest_start_url_carries_the_token():
    srv = wsserver.Server(os.path.join(ROOT, "web", "index.html"), lambda: "T", lambda m: None, lambda n: None)
    m = json.loads(srv.manifest("a b&c"))
    assert m["start_url"] == "/?t=a%20b%26c" and m["display"] == "standalone"
    assert {i["src"] for i in m["icons"]} == {"/icon-192.png", "/icon-512.png"}
    for name in ("icon-192.png", "icon-512.png", "apple-touch-icon.png"):
        assert open(os.path.join(ROOT, "web", name), "rb").read(8) == b"\x89PNG\r\n\x1a\n"


def test_enabled_choice_survives_restarts_and_keeps_the_token():
    async def run():
        tmp = tempfile.mkdtemp(); main = _load_main(tmp)
        p1 = main.Plugin(); await p1._main()
        p1.settings["port"] = 18811; p1._save()
        token = p1.settings["token"]
        assert (await p1.set_enabled(True))["running"] and store.load(main.SETTINGS_FILE)["enabled"] is True
        await p1._unload()                                                  # Decky stopping: a runtime stop only
        assert store.load(main.SETTINGS_FILE)["enabled"] is True
        p2 = main.Plugin(); await p2._main()                                # "reboot": comes back by itself
        await asyncio.sleep(0.3)
        assert p2.server is not None and p2.settings["token"] == token and p2._state()["running"]
        assert (await p2.set_enabled(False))["running"] is False
        await p2._unload()
        p3 = main.Plugin(); await p3._main(); await asyncio.sleep(0.2)
        assert p3.server is None and p3.settings["enabled"] is False        # off stays off
        await p3._unload()
    asyncio.run(run())


def test_autostart_retries_until_the_system_is_ready():
    async def run():
        tmp = tempfile.mkdtemp(); main = _load_main(tmp)
        store.save(main.SETTINGS_FILE, {"enabled": True, "port": 18812, "token": "T"})
        main.RETRY_DELAYS = (0.05,)
        attempts = {"n": 0}
        real = uinput.Pointer
        class Flaky(real):
            def __init__(s, *a, **k):
                attempts["n"] += 1
                if attempts["n"] <= 2:
                    raise PermissionError("/dev/uinput not accessible yet")
        uinput.Pointer = Flaky
        p = main.Plugin(); await p._main()
        await asyncio.sleep(0.05)
        assert p.server is None and p.settings["enabled"] is True and "Could not start" in p.error   # failing, still wanted
        await asyncio.sleep(0.4)
        assert p.server is not None and attempts["n"] == 3 and p.error == ""
        await p._unload()
    asyncio.run(run())


def test_failed_toggle_does_not_stay_enabled():
    async def run():
        tmp = tempfile.mkdtemp(); main = _load_main(tmp)
        class Broken:
            def __init__(s, *a, **k): raise PermissionError("denied")
        uinput.Pointer = Broken
        p = main.Plugin(); await p._main(); p.settings["port"] = 18813
        st = await p.set_enabled(True)
        assert st["running"] is False and st["enabled"] is False and "denied" in st["error"]
        assert store.load(main.SETTINGS_FILE)["enabled"] is False
    asyncio.run(run())


def test_http_routes_manifest_icons_and_404():
    async def run():
        tmp = tempfile.mkdtemp(); main = _load_main(tmp)
        p = main.Plugin(); await p._main(); p.settings["port"] = 18814
        await p.set_enabled(True)
        async def get(path):
            r, w = await asyncio.open_connection("127.0.0.1", 18814)
            w.write(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode()); data = await r.read(); w.close()
            return data.split(b"\r\n\r\n", 1)
        head, body = await get("/manifest.webmanifest?t=abc")
        assert b"200" in head and b"application/manifest+json" in head and json.loads(body)["start_url"] == "/?t=abc"
        head, body = await get("/icon-192.png")
        assert b"image/png" in head and body[:4] == b"\x89PNG" and b"max-age" in head
        assert b"404" in (await get("/nope"))[0]
        assert b"404" in (await get("/../main.py"))[0]
        await p._unload()
    asyncio.run(run())
