"""Tiny stdlib asyncio HTTP + WebSocket server."""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import struct
from urllib.parse import parse_qs, urlsplit

_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME = 64 * 1024
ICONS = {"/icon-192.png": "icon-192.png", "/icon-512.png": "icon-512.png",
         "/apple-touch-icon.png": "apple-touch-icon.png"}  # served under /<key>/ like everything else
# Every URL starts with the access key (/<key>/...): unlike a query string, a path survives being
# saved as a bookmark or home-screen app, and the page's relative links keep working underneath it.
KEY_PATH = re.compile(r"^/([A-Za-z0-9_-]+)(/.*)?$")
BAD_LINK = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Remote Control</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0a0e15;color:#e6edf8;
font:16px/1.5 system-ui,sans-serif;text-align:center;padding:24px}h1{font-size:20px;margin:0 0 8px}p{color:#8592a8;max-width:32em;margin:0 auto}</style></head>
<body><div><h1>Remote Control</h1><p>This link is missing its key, or the key has changed (for example after pressing
&ldquo;New link&rdquo;). Open the plugin on the device and scan the QR code again.</p></div></body></html>"""


async def read_frame(reader):
    """Return (opcode, payload) of one (possibly fragmented) message."""
    payload = b""
    first_op = None
    while True:
        b1, b2 = await reader.readexactly(2)
        fin, op = b1 & 0x80, b1 & 0x0F
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack(">H", await reader.readexactly(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", await reader.readexactly(8))[0]
        if n > MAX_FRAME:
            raise ValueError("frame too large")
        mask = await reader.readexactly(4) if b2 & 0x80 else None
        data = await reader.readexactly(n)
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data)) if n < 256 else \
                (int.from_bytes(data, "big") ^ int.from_bytes((mask * (n // 4 + 1))[:n], "big")).to_bytes(n, "big")
        if op != 0:
            first_op = op
        payload += data
        if op >= 8 or fin:
            return (op if op >= 8 else first_op), payload


def make_frame(op, payload=b""):
    n = len(payload)
    head = bytes([0x80 | op])
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack(">H", n)
    else:
        head += bytes([127]) + struct.pack(">Q", n)
    return head + payload


class Server:
    def __init__(self, index_html_path, get_token, on_message, on_clients, log=None):
        self.index_path = index_html_path
        self.get_token = get_token
        self.on_message = on_message
        self.on_clients = on_clients
        self.log = log or (lambda msg: None)
        self.clients = set()
        self.cursors = set()
        self._server = None

    async def start(self, port):
        self._server = await asyncio.start_server(self._handle, "0.0.0.0", port)

    async def stop(self):
        if self._server:
            self._server.close()
            # wait_closed() waits for every open connection, so close them first
            for w in list(self.clients | self.cursors):
                w.close()
            self.clients.clear()
            self.cursors.clear()
            try:
                await asyncio.wait_for(self._server.wait_closed(), 2)
            except asyncio.TimeoutError:
                pass
            self._server = None

    def broadcast_clients(self, text):
        frame = make_frame(1, text.encode())
        for w in list(self.clients):
            try:
                w.write(frame)
            except Exception:
                self.clients.discard(w)

    def broadcast_cursor(self, text):
        frame = make_frame(1, text.encode())
        for w in list(self.cursors):
            try:
                w.write(frame)
            except Exception:
                self.cursors.discard(w)

    async def _http(self, writer, status, body=b"", ctype="text/plain", cache="no-store"):
        charset = "" if ctype.startswith("image/") else "; charset=utf-8"
        writer.write(f"HTTP/1.1 {status}\r\nContent-Type: {ctype}{charset}\r\n"
                     f"Content-Length: {len(body)}\r\nCache-Control: {cache}\r\n"
                     f"Connection: close\r\n\r\n".encode() + body)
        await writer.drain()

    @staticmethod
    def manifest():
        """Web app manifest so a phone can keep the page as an app. Everything is relative to the
        manifest's own URL, so the saved start URL contains the key."""
        return json.dumps({
            "name": "Remote Control", "short_name": "Remote",
            "description": "Touchpad and keyboard for your SteamOS device",
            "start_url": ".", "scope": ".", "display": "standalone",
            "background_color": "#0a0e15", "theme_color": "#0a0e15",
            "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
                      {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
        }).encode()

    def _valid(self, token):
        return hmac.compare_digest(token or "", self.get_token())

    async def _handle(self, reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            lines = head.decode("latin1").split("\r\n")
            method, target, _ = lines[0].split(" ", 2)
            headers = {k.lower(): v.strip() for k, v in
                       (l.split(":", 1) for l in lines[1:] if ":" in l)}
            url = urlsplit(target)
            q = parse_qs(url.query)
            m = KEY_PATH.match(url.path)
            if not (m and self._valid(m.group(1))):
                self.log(f"http: {url.path} has no valid key in it")
                await self._http(writer, "403 Forbidden", BAD_LINK.encode(), "text/html")
                return
            if m.group(2) is None:  # /<key> -> /<key>/ so the page's relative links resolve
                writer.write(f"HTTP/1.1 301 Moved Permanently\r\nLocation: /{m.group(1)}/\r\n"
                             f"Content-Length: 0\r\nConnection: close\r\n\r\n".encode())
                await writer.drain()
                return
            path = m.group(2)
            if path == "/ws" and headers.get("upgrade", "").lower() == "websocket":
                await self._ws(reader, writer, headers, q)
            elif method == "GET" and path in ("/", "/index.html"):
                with open(self.index_path, "rb") as f:
                    await self._http(writer, "200 OK", f.read(), "text/html")
            elif method == "GET" and path == "/manifest.webmanifest":
                await self._http(writer, "200 OK", self.manifest(), "application/manifest+json")
            elif method == "GET" and path in ICONS:
                try:
                    with open(os.path.join(os.path.dirname(self.index_path), ICONS[path]), "rb") as f:
                        await self._http(writer, "200 OK", f.read(), "image/png", "public, max-age=86400")
                except OSError:
                    await self._http(writer, "404 Not Found", b"not found")
            else:
                await self._http(writer, "404 Not Found", b"not found")
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError,
                asyncio.LimitOverrunError, ValueError, OSError):
            pass
        finally:
            self.clients.discard(writer)
            try:
                writer.close()
            except Exception:
                pass

    async def _ws(self, reader, writer, headers, q):
        key = headers.get("sec-websocket-key", "").encode()
        accept = base64.b64encode(hashlib.sha1(key + _GUID).digest()).decode()
        writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                      f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        await writer.drain()
        if q.get("role", [""])[0] == "cursor":
            self.cursors.add(writer)
            try:
                while True:  # receive-only: just drain until the view goes away
                    op, _ = await read_frame(reader)
                    if op == 8:
                        break
            finally:
                self.cursors.discard(writer)
            return
        self.clients.add(writer)
        self.on_clients(len(self.clients))
        try:
            while True:
                op, data = await read_frame(reader)
                if op == 8:
                    break
                if op == 9:
                    writer.write(make_frame(10, data))
                elif op == 1:
                    try:
                        msg = json.loads(data.decode())
                    except ValueError:
                        continue
                    if isinstance(msg, dict):
                        if msg.get("t") == "p":
                            writer.write(make_frame(1, b'{"t":"p"}'))
                        else:
                            self.on_message(msg)
        finally:
            self.clients.discard(writer)
            self.on_clients(len(self.clients))
