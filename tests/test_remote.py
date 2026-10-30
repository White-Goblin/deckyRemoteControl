import asyncio, base64, json, os, socket, struct, sys, types, tempfile, importlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
import audio, netinfo, pointer_sync, store, uinput, wsserver
_REAL_REFRESH = netinfo.refresh   # other tests replace it so they never send real mDNS queries


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
            w.write((f"GET /{token}/ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n"
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
        w2.write(f"GET /{tok}/ HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        assert b'id="pad"' in await r2.read()
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
    main.netinfo.refresh = lambda: None   # no real mDNS queries from tests

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
    assert netinfo.links(8765, "T", "name", ip="10.0.0.5", fqdn="steam-machine.local") == (
        "http://steam-machine.local:8765/T/", "http://10.0.0.5:8765/T/")
    assert netinfo.links(8765, "T", "ip", ip="10.0.0.5", fqdn="steam-machine.local") == (
        "http://10.0.0.5:8765/T/", "http://steam-machine.local:8765/T/")
    assert netinfo.links(8765, "T", "name", ip="10.0.0.5", fqdn=None) == (
        "http://10.0.0.5:8765/T/", None)                                   # no mDNS name: only the IP


def _mdns_response(host, ip, cache_flush=True):
    """A DNS response with one A record, the name written once and compressed in the answer."""
    name = netinfo._encode(host)
    return (struct.pack(">HHHHHH", 0, 0x8400, 1, 1, 0, 0) + name + struct.pack(">HH", 1, 0x8001)
            + b"\xc0\x0c" + struct.pack(">HHIH", 1, 0x8001 if cache_flush else 1, 120, 4) + socket.inet_aton(ip))


def _responder(answer):
    """A tiny mDNS responder on loopback; returns (port, received_queries, stop)."""
    import socket, threading
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); srv.bind(("127.0.0.1", 0)); srv.settimeout(0.1)
    seen, stop = [], threading.Event()
    def loop():
        while not stop.is_set():
            try: data, addr = srv.recvfrom(9000)
            except OSError: continue
            seen.append(data)
            if answer: srv.sendto(answer, addr)
    t = threading.Thread(target=loop, daemon=True); t.start()
    return srv.getsockname()[1], seen, lambda: (stop.set(), t.join(), srv.close())


def test_mdns_probe_asks_the_network_and_checks_name_and_address():
    import socket
    port, seen, stop = _responder(_mdns_response("steam-machine.local", "10.0.0.5"))
    try:
        assert netinfo.probe("steam-machine.local", "10.0.0.5", 0.5, ("127.0.0.1", port))
        q = seen[0]                                                         # the query it sent: type A, "unicast reply" bit set
        assert q[12:].startswith(netinfo._encode("steam-machine.local")) and q[-4:] == b"\x00\x01\x80\x01"
        assert not netinfo.probe("steam-machine.local", "10.0.0.9", 0.3, ("127.0.0.1", port))   # someone else's address
        assert not netinfo.probe("other.local", "10.0.0.5", 0.3, ("127.0.0.1", port))             # a different name
    finally:
        stop()
    port, seen, stop = _responder(None)                                      # nobody answers
    try:
        assert not netinfo.probe("steam-machine.local", "10.0.0.5", 0.2, ("127.0.0.1", port))
    finally:
        stop()
    assert netinfo._a_records(b"\x00" * 5) == [] and netinfo._a_records(b"\x00\x00\x00\x00" + b"\xff" * 8) == []   # junk is ignored


def test_mdns_name_never_blocks_and_is_remembered():
    import time as _t
    saved = netinfo.check, netinfo.refresh, dict(netinfo._cache)
    netinfo.refresh = _REAL_REFRESH
    calls = []
    def slow():
        calls.append(1); _t.sleep(0.3); return "box.local"
    try:
        netinfo.check = slow
        netinfo._cache.update(at=float("-inf"), name=None, busy=False)
        t0 = _t.monotonic()
        assert netinfo.mdns_name() is None and _t.monotonic() - t0 < 0.1    # answers at once, looks it up in the background
        _t.sleep(0.5)
        assert netinfo.mdns_name() == "box.local" and len(calls) == 1       # remembered: no second lookup yet
        netinfo._cache["at"] = float("-inf")
        assert netinfo.mdns_name() == "box.local"                           # stale: still answers at once with the old value
        _t.sleep(0.5)
        assert len(calls) == 2
    finally:
        netinfo.check, netinfo.refresh = saved[0], saved[1]; netinfo._cache.clear(); netinfo._cache.update(saved[2])


def test_links_use_the_ip_by_default_and_the_name_with_mdns_on():
    async def run():
        tmp = tempfile.mkdtemp(); main = _load_main(tmp)
        saved = main.netinfo.mdns_name, main.netinfo.lan_ip
        main.netinfo.mdns_name = lambda: "box-2.local"
        main.netinfo.lan_ip = lambda: "10.1.2.3"
        try:
            p = main.Plugin(); await p._main()
            st = p._state()
            assert main.DEFAULTS["link_host"] == "ip" and "sensitivity" not in p.settings
            assert st["link_host"] == "ip" and ".local" not in st["url"] and st["url"].startswith("http://10.1.2.3:")
            assert st["alt_url"].startswith("http://box-2.local:")
            st = await p.set_link_host("name")
            assert st["url"].startswith("http://box-2.local:") and st["alt_url"].startswith("http://10.1.2.3:")
            assert (await p.set_link_host("bogus"))["link_host"] == "name"     # unknown values are ignored
        finally:
            main.netinfo.mdns_name, main.netinfo.lan_ip = saved
    asyncio.run(run())


def test_manifest_is_relative_so_the_saved_start_url_has_the_key():
    m = json.loads(wsserver.Server.manifest())
    assert m["start_url"] == "." and m["scope"] == "." and m["display"] == "standalone"
    assert {i["src"] for i in m["icons"]} == {"icon-192.png", "icon-512.png"}
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


def test_links_survive_saving_and_bad_links_are_explained():
    """The key is the first part of the path, so a saved bookmark/app keeps it; anything without a
    valid key gets a clear page instead of an app that cannot connect."""
    async def run():
        tmp = tempfile.mkdtemp(); main = _load_main(tmp)
        p = main.Plugin(); await p._main(); p.settings["port"] = 18814
        await p.set_enabled(True)
        k = p.settings["token"]
        async def get(path):
            r, w = await asyncio.open_connection("127.0.0.1", 18814)
            w.write(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode()); data = await r.read(); w.close()
            head, _, body = data.partition(b"\r\n\r\n")
            return head, body
        head, body = await get(f"/{k}/")
        assert b"200" in head and b'id="pad"' in body
        head, body = await get(f"/{k}/index.html")
        assert b"200" in head and b'id="pad"' in body
        head, body = await get(f"/{k}")                                      # no trailing slash: redirect
        assert b"301" in head and f"Location: /{k}/".encode() in head
        head, body = await get(f"/{k}/manifest.webmanifest")
        assert b"manifest+json" in head and json.loads(body)["start_url"] == "."
        head, body = await get(f"/{k}/icon-192.png")
        assert b"image/png" in head and body[:4] == b"\x89PNG" and b"max-age" in head
        assert b"404" in (await get(f"/{k}/nope"))[0]
        assert b"101" in await _ws_status(18814, f"/{k}/ws")
        assert b"101" in await _ws_status(18814, f"/{k}/ws?role=cursor")
        # no valid key (or the old ?t= style): refused with an explanation
        for path in ("/", "/?t=" + k, "/index.html", "/manifest.webmanifest", "/icon-192.png",
                     "/wrong/", "/wrong", "/nope", "/../main.py"):
            head, body = await get(path)
            assert b"403" in head and b"scan the QR code again" in body, path
        assert b"403" in await _ws_status(18814, "/ws")
        assert b"403" in await _ws_status(18814, "/wrong/ws")
        assert b"403" in await _ws_status(18814, "/ws?t=" + k)
        await p._unload()
    asyncio.run(run())


async def _ws_status(port, path):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write((f"GET {path} HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
             "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    line = await asyncio.wait_for(r.readline(), 2)
    w.close()
    return line


def test_launch_diagnostics_are_logged_and_head_works():
    async def run():
        tmp = tempfile.mkdtemp(); main = _load_main(tmp)
        lines = []
        main.decky.logger = types.SimpleNamespace(info=lines.append, error=lines.append, warning=lines.append)
        p = main.Plugin(); await p._main(); p.settings["port"] = 18815
        await p.set_enabled(True)
        k = p.settings["token"]
        ua_app = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
        ua_safari = ua_app + " Version/17.0 Safari/604.1"
        async def req(method, path, ua="curl"):
            r, w = await asyncio.open_connection("127.0.0.1", 18815)
            w.write(f"{method} {path} HTTP/1.1\r\nHost: steam-machine.local:18815\r\nUser-Agent: {ua}\r\n\r\n".encode())
            data = await r.read(); w.close()
            head, _, body = data.partition(b"\r\n\r\n")
            return head, body
        head, body = await req("HEAD", f"/{k}/")
        assert b"200" in head and b"Content-Length: " in head and body == b""      # headers only
        assert not (await req("HEAD", f"/{k}/icon-192.png"))[1]
        await req("GET", f"/{k}/", ua_app)
        await req("GET", f"/{k}/", ua_safari)
        text = "\n".join(map(str, lines))
        assert "(iOS), reached via steam-machine.local:18815" in text and "(other)" in text   # which address it used
        # the socket log says how long after its page the phone connected
        lines.clear()
        assert b"101" in await _ws_status(18815, f"/{k}/ws")
        assert "socket open for 127.0.0.1" in "\n".join(map(str, lines))
        # the phone reports its own timing; it is logged in readable form, junk is ignored
        lines.clear()
        p._on_message({"t": "timing", "dns": 3, "connect": 5, "ttfb": 7, "dom": 120, "wsOpen": 480, "standalone": True})
        p._on_message({"t": "timing", "dns": "x", "wsOpen": None})
        out = "\n".join(map(str, lines))
        assert "name lookup 3, connect 5, first byte 7, page ready 120, socket open 480, home-screen app" in out
        await p._unload()
    asyncio.run(run())


def test_typing_keeps_order_across_helpers_and_keys_and_leaves_no_processes():
    """Accented characters go through xdotool; they must land in order, before a following Enter, and the
    helper must be waited for (no zombies) and aimed at the focused display."""
    async def run():
        tmp = tempfile.mkdtemp(); main = _load_main(tmp)
        log = []
        class Kb:
            def tap(s, code, shift=False, ctrl=False): log.append(("tap", code, shift))
            def close(s): pass
        uinput.Keyboard = Kb
        real_which, real_run = main.shutil.which, main.subprocess.run
        calls = []
        def fake_run(cmd, **kw):
            import time as _t; _t.sleep(0.05)                                 # a slow helper: order must still hold
            calls.append((cmd, kw)); log.append(("xdotool", cmd[-1]))
        main.shutil.which = lambda n: "/usr/bin/xdotool"; main.subprocess.run = fake_run
        try:
            p = main.Plugin(); await p._main(); p.settings["port"] = 18816
            await p.set_enabled(True)
            p.resync = types.SimpleNamespace(live=":1")                       # an app on the second display has focus
            p._on_message({"t": "k", "text": "ca\u00e9\x01 b"})               # control characters are skipped
            p._on_message({"t": "key", "k": "Enter"})                          # sent right after, must not overtake the accent
            await asyncio.sleep(0.4)
        finally:
            main.shutil.which, main.subprocess.run = real_which, real_run
        order = [("x:" + k[1]) if k[0] == "xdotool" else "tap" for k in log]
        assert order == ["tap", "tap", "x:\u00e9", "tap", "tap", "tap"], order   # c, a, accent, space, b, Enter
        cmd, kw = calls[0]
        assert cmd == ["xdotool", "type", "--clearmodifiers", "--", "\u00e9"] and kw["timeout"] == 2
        assert kw["env"]["DISPLAY"] == ":1"
        await p._unload()
    asyncio.run(run())
