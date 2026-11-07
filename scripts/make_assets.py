"""Generate assets/sentinel.png and assets/sentinel.ico from the code-drawn emblem.

Run:  python scripts/make_assets.py      (needs PySide6; works headless)
The ICO is written by hand (PNG-compressed entries) so no Qt image plugin is required.
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SIZES = (16, 24, 32, 48, 64, 128, 256)


def main() -> int:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication([])
    from sentinel.ui.icons import make_pixmap

    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    pngs = []
    for size in SIZES:
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        make_pixmap(size).save(buf, "PNG")
        buf.close()
        pngs.append((size, bytes(data)))
    (assets / "sentinel.png").write_bytes(dict(pngs)[256])

    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, blobs = b"", b""
    for size, png in pngs:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset + len(blobs))
        blobs += png
    (assets / "sentinel.ico").write_bytes(header + entries + blobs)
    print(f"wrote {assets / 'sentinel.png'} and {assets / 'sentinel.ico'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
