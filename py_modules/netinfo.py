"""Where a phone should connect. By default the link uses the device's IP address. With mDNS on, it
uses the name the device advertises on the network, which keeps a saved link working when the IP
changes but is not supported by every router and phone. The name is asked from avahi (SteamOS's mDNS
service) rather than guessed from the host name, because avahi renames itself on a name clash."""
import re
import socket
import subprocess
import time

AVAHI_NAME = ["busctl", "--system", "call", "org.freedesktop.Avahi", "/", "org.freedesktop.Avahi.Server",
              "GetHostNameFqdn"]
NAME_TTL = 30  # seconds: the name only changes on a clash or a host name change
_cache = (0.0, None)


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _ask_avahi():
    try:
        out = subprocess.run(AVAHI_NAME, capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.fullmatch(r'\s*s "([A-Za-z0-9.-]+)"\s*', out)  # busctl prints: s "steam-machine.local"
    return m.group(1) if m else None


def mdns_name(ask=_ask_avahi):
    """The full mDNS name the device advertises (for example "steam-machine.local"), or None when
    avahi is not running or does not answer."""
    global _cache
    now = time.monotonic()
    if ask is _ask_avahi and now - _cache[0] < NAME_TTL:
        return _cache[1]
    name = ask()
    if ask is _ask_avahi:
        _cache = (now, name)
    return name


def links(port, token, mode, ip=None, fqdn=False):
    """Returns (primary_url, alternate_url_or_None). `mode` is "name" or "ip". The name form exists
    only when the device advertises an mDNS name (`fqdn` overrides the lookup, for tests)."""
    ip = ip or lan_ip()
    fqdn = mdns_name() if fqdn is False else fqdn
    by_ip = f"http://{ip}:{port}/{token}/"
    if not fqdn:
        return by_ip, None
    by_name = f"http://{fqdn}:{port}/{token}/"
    return (by_ip, by_name) if mode == "ip" else (by_name, by_ip)
