import asyncio, base64, json, os, struct, sys, types, tempfile, importlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
import uinput, wsserver


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
    fake.logger = types.SimpleNamespace(info=print, error=print)
    async def emit(*a): events.append(a)
    fake.emit = emit
    sys.modules["decky"] = fake

    class FakePointer:
        def __init__(s, w, h): pass
        def move_to(s, x, y): calls.append(("move", int(x), int(y)))
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
        await asyncio.sleep(0.2)
        kinds = [c[0] for c in calls]
        assert "move" in kinds and ("btn", 0x110, True) in calls
        assert ("scroll", -2, 0) in calls
        assert ("tap", 35, True) in calls and ("tap", 23, False) in calls  # H, i
        assert any(e[0] == "rc_activity" for e in events)
        # HTTP page served
        r2, w2 = await asyncio.open_connection("127.0.0.1", 18765)
        w2.write(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        assert b"Remote Control" in await r2.read()
        w.close()
        await p._unload()
    asyncio.run(run())
