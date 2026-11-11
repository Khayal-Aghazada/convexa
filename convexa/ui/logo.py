"""Logo rendering: SVG -> QIcon / QPixmap, and the .ico used by Windows."""
from __future__ import annotations

import io
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from ..system import ASSETS

SVG = ASSETS / "logo.svg"
ICO = ASSETS / "logo.ico"


def _render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    r = QSvgRenderer(str(SVG))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    r.render(p, QRectF(0, 0, size, size))
    p.end()
    return img


def pixmap(size: int, dpr: float = 1.0) -> QPixmap:
    pm = QPixmap.fromImage(_render(int(size * dpr)))
    pm.setDevicePixelRatio(dpr)
    return pm


def icon() -> QIcon:
    ic = QIcon()
    for s in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        ic.addPixmap(QPixmap.fromImage(_render(s)))
    return ic


def ensure_ico() -> Path:
    if ICO.exists() and ICO.stat().st_mtime >= SVG.stat().st_mtime:
        return ICO
    from PIL import Image
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    _render(256).save(buf, "PNG")
    im = Image.open(io.BytesIO(bytes(buf.data())))
    im.save(str(ICO), "ICO", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    return ICO
