"""Where a phone should connect. By default the link uses the device's IP address. With mDNS on, it
uses the name the device answers to on the network (`<hostname>.local`), which keeps a saved link
working when the IP changes. Not every router or phone supports it, and iPhones can be slow to open it.

Whether the name works is found out by asking the network, not by trusting a service: on SteamOS the
answers come from systemd-resolved (avahi runs with publishing off), but any responder would do."""
import re
import socket
import struct
import threading
import time

MDNS_GROUP = ("224.0.0.251", 5353)
RECHECK = 30  # seconds before the name is looked up again (it changes with the host name or a clash)
_cache = {"at": float("-inf"), "name": None, "busy": False}
_lock = threading.Lock()


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def hostname():
    name = re.sub(r"[^a-z0-9-]", "", socket.gethostname().split(".")[0].lower())
    return name or None


def _encode(name):
    return b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"


def _name_at(d, i):
    labels, end, jumped = [], i, False
    while True:
        n = d[i]
        if n == 0:
            return ".".join(labels), (end if jumped else i + 1)
        if n & 0xC0 == 0xC0:  # compression pointer
            if not jumped:
                end = i + 2
            jumped, i = True, ((n & 0x3F) << 8) | d[i + 1]
            continue
        labels.append(d[i + 1:i + 1 + n].decode("latin1"))
        i += 1 + n


def _a_records(d):
    """(name, ipv4) of every A record in a DNS response packet."""
    if len(d) < 12 or not d[2] & 0x80:
        return []
    qd, an, ns, ar = struct.unpack(">HHHH", d[4:12])
    i, out = 12, []
    try:
        for _ in range(qd):
            _, i = _name_at(d, i)
            i += 4
        for _ in range(an + ns + ar):
            name, i = _name_at(d, i)
            rtype, _, _, rdlen = struct.unpack(">HHIH", d[i:i + 10])
            i += 10
            if rtype == 1 and rdlen == 4:
                out.append((name.lower(), socket.inet_ntoa(d[i:i + 4])))
            i += rdlen
    except (IndexError, struct.error):
        pass
    return out


def probe(host, ip, timeout=0.4, group=MDNS_GROUP, src=None):
    """True when something on the network answers an mDNS query for `host` with `ip`."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        if src:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(src))
        s.bind((src or "", 0))
        # the top bit of the class asks for the answer to be sent straight back to us
        s.sendto(struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0) + _encode(host) + struct.pack(">HH", 1, 0x8001), group)
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            s.settimeout(left)
            try:
                data, _ = s.recvfrom(9000)
            except OSError:
                break
            if (host, ip) in _a_records(data):
                return True
    except OSError:
        pass
    finally:
        s.close()
    return False


def check():
    """The `.local` name this device answers to, or None."""
    host, ip = hostname(), lan_ip()
    name = f"{host}.local" if host else None
    return name if name and probe(name, ip, src=ip) else None


def refresh():
    """Look the name up now (blocking: run it off the main loop) and remember the result."""
    name = check()
    with _lock:
        _cache.update(at=time.monotonic(), name=name, busy=False)
    return name


def mdns_name():
    """The remembered `.local` name (or None). Never blocks: a stale answer starts a background
    lookup, so asking is safe from the event loop."""
    with _lock:
        stale = time.monotonic() - _cache["at"] > RECHECK and not _cache["busy"]
        if stale:
            _cache["busy"] = True
        name = _cache["name"]
    if stale:
        threading.Thread(target=refresh, daemon=True).start()
    return name


def links(port, token, mode, ip=None, fqdn=False):
    """Returns (primary_url, alternate_url_or_None). `mode` is "name" or "ip". The name form exists
    only when the device answers to an mDNS name (`fqdn` overrides the lookup, for tests)."""
    ip = ip or lan_ip()
    fqdn = mdns_name() if fqdn is False else fqdn
    by_ip = f"http://{ip}:{port}/{token}/"
    if not fqdn:
        return by_ip, None
    by_name = f"http://{fqdn}:{port}/{token}/"
    return (by_ip, by_name) if mode == "ip" else (by_name, by_ip)
