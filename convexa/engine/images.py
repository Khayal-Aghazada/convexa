"""Image conversions (Pillow, plus Qt for SVG input)."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageSequence

RASTER_IN = {"png", "jpg", "jpeg", "webp", "bmp", "gif", "tif", "tiff", "ico", "svg"}
RASTER_OUT = ["png", "jpg", "webp", "bmp", "gif", "tiff", "ico"]
_PIL_FORMAT = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP", "bmp": "BMP",
               "gif": "GIF", "tiff": "TIFF", "tif": "TIFF", "ico": "ICO"}


def open_image(path: Path) -> Image.Image:
    if path.suffix.lower() == ".svg":
        return _render_svg(path)
    im = Image.open(str(path))
    if path.suffix.lower() == ".ico":
        try:  # pick the largest icon size
            im.size = max(im.info.get("sizes", {im.size}))
        except Exception:
            pass
    return im


def _render_svg(path: Path, min_side: int = 1024) -> Image.Image:
    from PySide6.QtCore import QByteArray, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    r = QSvgRenderer(QByteArray(path.read_bytes()))
    if not r.isValid():
        raise ValueError("Invalid SVG file")
    size = r.defaultSize()
    w, h = max(size.width(), 1), max(size.height(), 1)
    scale = max(1.0, min_side / max(w, h))
    img = QImage(int(w * scale), int(h * scale), QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r.render(p)
    p.end()
    from PySide6.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).copy()


def _flatten(im: Image.Image, bg=(255, 255, 255)) -> Image.Image:
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        base = Image.new("RGB", im.size, bg)
        base.paste(im, mask=im.split()[-1])
        return base
    return im.convert("RGB") if im.mode != "RGB" else im


def save_image(im: Image.Image, dst: Path, fmt: str) -> None:
    fmt = fmt.lower()
    pil = _PIL_FORMAT[fmt]
    frames = getattr(im, "n_frames", 1)
    if frames > 1 and fmt in ("gif", "webp", "tiff"):
        seq = [f.convert("RGBA") for f in ImageSequence.Iterator(im)]
        kw = {"save_all": True, "append_images": seq[1:], "loop": im.info.get("loop", 0),
              "duration": im.info.get("duration", 100)}
        if fmt == "tiff":
            kw = {"save_all": True, "append_images": seq[1:]}
        seq[0].save(str(dst), pil, **kw)
        return
    if fmt in ("jpg", "jpeg"):
        _flatten(im).save(str(dst), "JPEG", quality=92, optimize=True, subsampling=0)
    elif fmt == "bmp":
        _flatten(im).save(str(dst), "BMP")
    elif fmt == "ico":
        im = im.convert("RGBA")
        side = max(im.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
        if side > 256:
            square = square.resize((256, 256), Image.LANCZOS)
        sizes = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256) if s <= square.width] or [square.size]
        square.save(str(dst), "ICO", sizes=sizes)
    elif fmt == "gif":
        im.convert("RGBA").save(str(dst), "GIF")
    elif fmt == "webp":
        im.convert("RGBA" if "A" in im.mode or im.mode == "P" else "RGB").save(str(dst), "WEBP", quality=90, method=4)
    elif fmt == "png":
        if im.mode not in ("RGB", "RGBA", "L", "LA", "P", "1", "I;16"):
            im = im.convert("RGBA")
        im.save(str(dst), "PNG", optimize=True)
    else:
        if im.mode not in ("RGB", "RGBA", "L", "1", "CMYK"):
            im = im.convert("RGBA")
        im.save(str(dst), pil)


def convert_image(src: Path, dst: Path, fmt: str) -> None:
    with open_image(src) as im:
        save_image(im, dst, fmt)


def image_frames_png(src: Path) -> list[tuple[bytes, int, int, str]]:
    """Return (bytes, width, height, ext) per frame/page, keeping JPEG bytes as-is."""
    ext = src.suffix.lower().lstrip(".")
    if ext in ("jpg", "jpeg"):
        with Image.open(str(src)) as im:
            if getattr(im, "n_frames", 1) == 1:
                from PIL import ImageOps
                if im.getexif().get(0x0112, 1) == 1:
                    return [(src.read_bytes(), im.width, im.height, "jpg")]
                im = ImageOps.exif_transpose(im)
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=92)
                return [(buf.getvalue(), im.width, im.height, "jpg")]
    out = []
    with open_image(src) as im:
        frames = [im] if ext in ("gif", "webp") else ImageSequence.Iterator(im)
        for f in frames:
            f = f.convert("RGBA") if f.mode not in ("RGB", "RGBA", "L") else f
            buf = io.BytesIO()
            f.save(buf, "PNG")
            out.append((buf.getvalue(), f.width, f.height, "png"))
    return out


def image_to_pdf(src: Path, dst: Path) -> None:
    import pymupdf
    out = pymupdf.open()
    for data, w, h, _ in image_frames_png(src):
        page = out.new_page(width=w * 0.75, height=h * 0.75)
        page.insert_image(page.rect, stream=data)
    out.save(str(dst), garbage=3, deflate=True)
    out.close()


def images_to_pptx(sources: list[Path], dst: Path) -> None:
    from pptx import Presentation
    from pptx.util import Emu

    from .pdfs import _place_fit

    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(12192000), Emu(6858000)  # 16:9
    blank = prs.slide_layouts[6]
    for src in sources:
        for data, w, h, _ in image_frames_png(src):
            slide = prs.slides.add_slide(blank)
            _place_fit(slide, io.BytesIO(data), w, h, prs.slide_width, prs.slide_height)
    prs.save(str(dst))
