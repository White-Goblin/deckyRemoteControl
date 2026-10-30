"""Settings file that survives hard power-offs: atomic writes, and the previous good copy is kept
so a damaged file can never silently reset the token that saved phone links depend on."""
import json
import os
import shutil


def load(path):
    for candidate in (path, path + ".bak"):
        try:
            with open(candidate) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            continue
    return {}


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())
    if os.path.exists(path):
        try:
            shutil.copyfile(path, path + ".bak")
        except OSError:
            pass
    os.replace(tmp, path)
