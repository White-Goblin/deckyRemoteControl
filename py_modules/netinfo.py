"""Where a phone should connect. The name form (`<hostname>.local`) keeps a saved link working when
the device's IP address changes (DHCP lease, wired versus Wi-Fi); the IP form works on phones that
cannot resolve .local names."""
import os
import re
import socket


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


def mdns_available():
    """avahi answers .local queries for this host."""
    return os.path.exists("/run/avahi-daemon/socket")


def links(port, token, mode, ip=None, name=None, mdns=None):
    """Returns (primary_url, alternate_url_or_None). `mode` is "name" or "ip"."""
    ip = ip or lan_ip()
    name = name if name is not None else hostname()
    mdns = mdns_available() if mdns is None else mdns
    by_ip = f"http://{ip}:{port}/{token}/"
    if not (mdns and name):
        return by_ip, None
    by_name = f"http://{name}.local:{port}/{token}/"
    return (by_ip, by_name) if mode == "ip" else (by_name, by_ip)
