"""Download the open-access MIMIC-IV demo from PhysioNet, with checksum verification."""

import hashlib
import urllib.request
from pathlib import Path

DEMO_URL = "https://physionet.org/files/mimic-iv-demo/2.2"


def fetch_demo(dest: Path) -> list[Path]:
    dest = Path(dest)
    with urllib.request.urlopen(f"{DEMO_URL}/SHA256SUMS.txt") as resp:
        sums = [line.split(maxsplit=1) for line in resp.read().decode().splitlines() if line]
    files = []
    for digest, name in sums:
        name = name.strip()
        if not name.endswith(".csv.gz"):
            continue
        path = dest / name
        if not (path.exists() and _sha256(path) == digest):
            path.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(f"{DEMO_URL}/{name}", path)
            if _sha256(path) != digest:
                path.unlink()
                raise ValueError(f"checksum mismatch for {name}")
        files.append(path)
    return files


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
