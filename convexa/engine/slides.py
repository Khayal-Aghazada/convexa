"""PowerPoint: render pptx -> PDF, pptx -> Doc, Doc -> pptx."""
from __future__ import annotations

import html as htmllib
import io
from pathlib import Path

import pymupdf

from .fonts import FontKit
from .model import Block, Doc, Run

EMU = 12700.0  # EMU per point


# ================================================================ rendering
def _rgb(color) -> str | None:
    try:
        if color is not None and color.type is not None and color.rgb is not None:
            return "#" + str(color.rgb)
    except Exception:
        pass
    return None


def _fill_rgb(fill) -> str | None:
    try:
        from pptx.enum.dml import MSO_FILL
        if fill.type == MSO_FILL.SOLID:
            return _rgb(fill.fore_color)
    except Exception:
        pass
    return None


def _hex_to_tuple(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _luminance(h: str) -> float:
    r, g, b = _hex_to_tuple(h)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _background(slide) -> str | None:
    for owner in (slide, slide.slide_layout, slide.slide_layout.slide_master):
        try:
            bg = owner._element.cSld.bg
            if bg is None:
                continue
            c = _fill_rgb(owner.background.fill)
            if c:
                return c
        except Exception:
            continue
    return None


def _placeholder_type(shape) -> str:
    try:
        if shape.is_placeholder:
            return str(shape.placeholder_format.type).split(".")[-1].split(" ")[0].upper()
    except Exception:
        pass
    return ""


DEFAULT_SIZES = {"CENTER_TITLE": 44, "TITLE": 40, "SUBTITLE": 22, "BODY": 24, "OBJECT": 24}
LEVEL_SIZES = [24, 20, 18, 16, 16]


class _Renderer:
    def __init__(self, prs) -> None:
        self.prs = prs
        self.kit = FontKit()
        self.default_font = self.kit.family("Calibri")
        self.scratch = pymupdf.open()

    def text_html(self, shape, text_color: str, ptype: str) -> tuple[str, str]:
        tf = shape.text_frame
        parts = []
        scale = 1.0
        try:
            na = tf._txBody.bodyPr.find("{http://schemas.openxmlformats.org/drawingml/2006/main}normAutofit")
            if na is not None and na.get("fontScale"):
                scale = int(na.get("fontScale")) / 100000
        except Exception:
            pass
        anchor = "t"
        try:
            anchor = tf._txBody.bodyPr.get("anchor") or ("ctr" if "TITLE" in ptype else "t")
        except Exception:
            pass
        bullets = ptype in ("BODY", "OBJECT")
        for p in tf.paragraphs:
            level = p.level or 0
            base = DEFAULT_SIZES.get(ptype, 18)
            if bullets:
                base = LEVEL_SIZES[min(level, 4)]
            runs = []
            p_size = None
            for r in p.runs:
                f = r.font
                size = (f.size.pt if f.size else None) or base
                p_size = p_size or size
                fam = self.kit.family(f.name) if f.name else self.default_font
                style = [f"font-size:{size * scale:.1f}pt", f"font-family:{fam}"]
                if f.bold or (f.bold is None and "TITLE" in ptype and False):
                    style.append("font-weight:bold")
                if f.italic:
                    style.append("font-style:italic")
                if f.underline:
                    style.append("text-decoration:underline")
                c = _rgb(f.color) if f.color and f.color.type else None
                style.append(f"color:{c or text_color}")
                text = htmllib.escape(r.text).replace("\v", "<br>").replace("\n", "<br>")
                runs.append(f'<span style="{";".join(style)}">{text}</span>')
            align = {"CENTER": "center", "RIGHT": "right", "JUSTIFY": "justify"}.get(
                str(p.alignment).split(".")[-1].split(" ")[0] if p.alignment is not None else "",
                "center" if ptype in ("CENTER_TITLE", "SUBTITLE") else "left")
            size = (p_size or base) * scale
            has_text = any(r.text.strip() for r in p.runs)
            no_bullet = p._p.find(".//{http://schemas.openxmlformats.org/drawingml/2006/main}buNone") is not None
            prefix = ""
            if bullets and has_text and not no_bullet:
                prefix = f'<span style="color:{text_color};font-size:{size:.1f}pt">•&#160;</span>'
            indent = level * 18 if bullets else 0
            inner = "".join(runs) if has_text else f'<span style="font-size:{size:.1f}pt">&#160;</span>'
            parts.append(f'<p style="margin:0 0 {size * 0.25:.1f}pt {indent}pt;text-align:{align};'
                         f'line-height:1.15">{prefix}{inner}</p>')
        return "".join(parts), anchor

    def table_html(self, table, text_color: str) -> str:
        rows = []
        for i, row in enumerate(table.rows):
            cells = []
            for cell in row.cells:
                bg = _fill_rgb(cell.fill) or ("#e8e8ee" if i == 0 else None)
                st = "border:0.5pt solid #b0b0b8;padding:3pt 5pt;font-size:12pt;"
                if bg:
                    st += f"background-color:{bg};"
                    if _luminance(bg) < 0.45:
                        st += "color:#ffffff;"
                if i == 0:
                    st += "font-weight:bold;"
                cells.append(f'<td style="{st}">{htmllib.escape(cell.text)}</td>')
            rows.append("<tr>" + "".join(cells) + "</tr>")
        return (f'<table style="border-collapse:collapse;width:100%;color:{text_color};'
                f'font-family:{self.default_font}">' + "".join(rows) + "</table>")

    def place_html(self, page, rect: pymupdf.Rect, html: str, anchor: str = "t") -> None:
        if rect.is_empty or rect.width < 2 or rect.height < 2:
            return
        css = self.kit.css + f" body{{font-family:{self.default_font};}}"
        try:
            if anchor in ("ctr", "b"):
                tmp = self.scratch.new_page(width=page.rect.width, height=page.rect.height)
                spare, _ = tmp.insert_htmlbox(rect, html, css=css, archive=self.kit.archive)
                self.scratch.delete_page(-1)
                if spare > 0:
                    shift = spare / 2 if anchor == "ctr" else spare
                    rect = pymupdf.Rect(rect.x0, rect.y0 + shift, rect.x1, rect.y1 + shift)
            page.insert_htmlbox(rect, html, css=css, archive=self.kit.archive)
        except Exception:
            pass

    def draw_shape(self, page, shape, xf, text_color: str) -> None:
        from pptx.enum.shapes import MSO_SHAPE_TYPE
        st = None
        try:
            st = shape.shape_type
        except Exception:
            pass
        if st == MSO_SHAPE_TYPE.GROUP:
            try:
                xfrm = shape._element.grpSpPr.xfrm
                ch_off, ch_ext = xfrm.chOff, xfrm.chExt
                sx = (shape.width / ch_ext.cx) if ch_ext.cx else 1
                sy = (shape.height / ch_ext.cy) if ch_ext.cy else 1
                ox, oy = shape.left - ch_off.x * sx, shape.top - ch_off.y * sy

                def child_xf(x, y, w, h, _p=xf):
                    return _p(ox + x * sx, oy + y * sy, w * sx, h * sy)
            except Exception:
                child_xf = xf
            for s in shape.shapes:
                self.draw_shape(page, s, child_xf, text_color)
            return
        try:
            if shape.left is None or shape.width is None:
                return
            rect = xf(shape.left, shape.top, shape.width, shape.height)
        except Exception:
            return
        rect = pymupdf.Rect(rect)
        # Pictures
        image = None
        try:
            if st == MSO_SHAPE_TYPE.PICTURE or (shape.is_placeholder and hasattr(shape, "image")):
                image = shape.image
        except Exception:
            image = None
        if image is not None:
            try:
                data = image.blob
                if image.ext.lower() in ("wmf", "emf", "tif", "tiff", "webp"):
                    from .docs import _image_to_png
                    data = _image_to_png(data)
                page.insert_image(rect, stream=data, keep_proportion=False)
            except Exception:
                pass
            return
        # Filled / outlined auto shapes
        try:
            fill = _fill_rgb(shape.fill) if hasattr(shape, "fill") else None
            line = None
            try:
                if shape.line.fill.type is not None:
                    line = _rgb(shape.line.color)
            except Exception:
                line = None
            if fill or line:
                kind = ""
                try:
                    kind = str(shape.auto_shape_type).upper()
                except Exception:
                    pass
                fc = _hex_to_tuple(fill) if fill else None
                lc = _hex_to_tuple(line) if line else None
                width = max(0.5, (shape.line.width.pt if shape.line.width else 0.75)) if line else 0
                if "OVAL" in kind:
                    page.draw_oval(rect, color=lc, fill=fc, width=width)
                elif "ROUNDED" in kind:
                    page.draw_rect(rect, color=lc, fill=fc, width=width, radius=0.12)
                else:
                    page.draw_rect(rect, color=lc, fill=fc, width=width)
                if fill:
                    text_color = "#ffffff" if _luminance(fill) < 0.45 else "#1d1d1f"
        except Exception:
            pass
        # Tables
        try:
            if getattr(shape, "has_table", False) and shape.has_table:
                self.place_html(page, rect, self.table_html(shape.table, text_color))
                return
        except Exception:
            pass
        # Text
        try:
            if getattr(shape, "has_text_frame", False) and shape.has_text_frame and shape.text_frame.text.strip():
                ptype = _placeholder_type(shape)
                html, anchor = self.text_html(shape, text_color, ptype)
                inset = pymupdf.Rect(rect.x0 + 7.2, rect.y0 + 3.6, rect.x1 - 7.2, rect.y1 - 3.6)
                self.place_html(page, inset, html, anchor)
        except Exception:
            pass

    def render(self) -> pymupdf.Document:
        prs = self.prs
        W, H = prs.slide_width / EMU, prs.slide_height / EMU
        out = pymupdf.open()

        def xf(x, y, w, h):
            return (x / EMU, y / EMU, (x + w) / EMU, (y + h) / EMU)

        for slide in prs.slides:
            page = out.new_page(width=W, height=H)
            bg = _background(slide) or "#ffffff"
            page.draw_rect(page.rect, color=None, fill=_hex_to_tuple(bg), width=0)
            text_color = "#ffffff" if _luminance(bg) < 0.45 else "#1d1d1f"
            # Decorative (non-placeholder) shapes from master and layout first.
            for owner in (slide.slide_layout.slide_master, slide.slide_layout):
                for shape in owner.shapes:
                    try:
                        if shape.is_placeholder:
                            continue
                    except Exception:
                        pass
                    self.draw_shape(page, shape, xf, text_color)
            for shape in slide.shapes:
                self.draw_shape(page, shape, xf, text_color)
        self.scratch.close()
        return out


def render_pptx(src: Path) -> pymupdf.Document:
    from pptx import Presentation
    return _Renderer(Presentation(str(src))).render()


def pptx_to_pdf(src: Path, dst: Path) -> None:
    out = render_pptx(src)
    out.set_metadata({"title": src.stem, "producer": "Convexa"})
    out.save(str(dst), garbage=3, deflate=True)
    out.close()


# ============================================================== pptx -> Doc
def read_pptx(path: Path) -> Doc:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    prs = Presentation(str(path))
    doc = Doc(title=prs.core_properties.title or path.stem)

    def walk(shapes):
        for s in shapes:
            if s.shape_type == MSO_SHAPE_TYPE.GROUP:
                yield from walk(s.shapes)
            else:
                yield s

    for i, slide in enumerate(prs.slides, 1):
        title_shape = slide.shapes.title
        title = title_shape.text_frame.text.strip() if title_shape is not None and title_shape.has_text_frame else ""
        doc.add(Block("heading", [Run(title or f"Slide {i}")], level=2))
        shapes = sorted(walk(slide.shapes), key=lambda s: ((s.top or 0), (s.left or 0)))
        for s in shapes:
            if title_shape is not None and s.shape_id == title_shape.shape_id:
                continue
            if getattr(s, "has_table", False) and s.has_table:
                doc.add(Block("table", rows=[[c.text.strip() for c in r.cells] for r in s.table.rows]))
            elif s.shape_type == MSO_SHAPE_TYPE.PICTURE:
                try:
                    doc.add(Block("image", data=s.image.blob, ext=s.image.ext.lower(), width=min(s.width / EMU, 460)))
                except Exception:
                    pass
            elif getattr(s, "has_text_frame", False) and s.has_text_frame:
                bullets = _placeholder_type(s) in ("BODY", "OBJECT")
                for p in s.text_frame.paragraphs:
                    runs = [Run(r.text, bold=bool(r.font.bold), italic=bool(r.font.italic)) for r in p.runs if r.text]
                    if not runs or not "".join(r.text for r in runs).strip():
                        continue
                    if bullets:
                        doc.add(Block("list", runs, level=p.level or 0))
                    else:
                        doc.add(Block("para", runs))
        try:
            notes = slide.notes_slide.notes_text_frame.text.strip() if slide.has_notes_slide else ""
        except Exception:
            notes = ""
        if notes:
            doc.add(Block("quote", [Run("Notes: " + notes, italic=True)]))
    return doc


# ============================================================== Doc -> pptx
def write_pptx(doc: Doc, dst: Path) -> None:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Emu, Pt

    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(12192000), Emu(6858000)
    SW, SH = prs.slide_width, prs.slide_height
    M = Emu(640080)  # 0.7in
    blank = prs.slide_layouts[6]
    ink, muted, accent = RGBColor(0x1D, 0x1D, 0x1F), RGBColor(0x55, 0x55, 0x5C), RGBColor(0x63, 0x66, 0xF1)

    def new_slide(title: str, cover: bool = False):
        s = prs.slides.add_slide(blank)
        if cover:
            bar = s.shapes.add_shape(1, M, int(SH * 0.42), Emu(91440 * 6), Emu(54864))
            bar.fill.solid()
            bar.fill.fore_color.rgb = accent
            bar.line.fill.background()
        tb = s.shapes.add_textbox(M, int(SH * 0.46) if cover else Emu(457200), SW - 2 * M, Emu(1100000))
        tf = tb.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        r = p.add_run()
        r.text = title
        r.font.size = Pt(44 if cover else 30)
        r.font.bold = True
        r.font.name = "Segoe UI Semibold"
        r.font.color.rgb = ink
        return s

    state = {"slide": None, "body": None, "lines": 0}

    def body():
        if state["body"] is None or state["lines"] >= 9:
            if state["slide"] is None or state["lines"] >= 9:
                title = state.get("title", "") + (" (cont.)" if state["slide"] is not None else "")
                state["slide"] = new_slide(title.strip() or doc.title or "Untitled")
            tb = state["slide"].shapes.add_textbox(M, Emu(1600000), SW - 2 * M, SH - Emu(2100000))
            tb.text_frame.word_wrap = True
            state["body"], state["lines"] = tb.text_frame, 0
        return state["body"]

    def add_text(runs: list[Run], level: int = 0, bullet: bool = False, size: int = 20):
        tf = body()
        p = tf.paragraphs[0] if state["lines"] == 0 and not tf.paragraphs[0].runs else tf.add_paragraph()
        p.level = min(level, 4)
        p.space_after = Pt(8)
        if bullet:
            r0 = p.add_run()
            r0.text = "    " * level + "•  "
            r0.font.color.rgb = accent
            r0.font.size = Pt(size)
        for run in runs:
            r = p.add_run()
            r.text = run.text
            r.font.size = Pt(size)
            r.font.bold = run.bold or None
            r.font.italic = run.italic or None
            r.font.name = "Segoe UI"
            r.font.color.rgb = ink if not bullet or level == 0 else muted
        state["lines"] += 1 + len("".join(x.text for x in runs)) // 90

    first_heading = True
    for b in doc.blocks:
        if b.kind == "heading" and b.level <= 2:
            text = b.plain.strip()
            cover = first_heading and b.level == 1 and not prs.slides
            state.update(slide=new_slide(text, cover=cover), body=None, lines=0, title=text)
            first_heading = False
            if cover:
                state.update(slide=None, body=None)
        elif b.kind == "heading":
            add_text([Run(b.plain, bold=True)], size=22)
        elif b.kind == "list":
            add_text(b.runs, level=b.level, bullet=True)
        elif b.kind in ("para", "quote") and b.plain.strip():
            add_text(b.runs)
        elif b.kind == "code":
            add_text([Run(b.text)], size=14)
        elif b.kind == "table" and b.rows:
            s = new_slide(state.get("title", "") or "Table")
            rows, cols = min(len(b.rows), 14), max(len(r) for r in b.rows)
            shape = s.shapes.add_table(rows, cols, M, Emu(1600000), SW - 2 * M, Emu(370000 * rows))
            for i in range(rows):
                for j in range(cols):
                    cell = shape.table.cell(i, j)
                    cell.text = b.rows[i][j] if j < len(b.rows[i]) else ""
                    for p in cell.text_frame.paragraphs:
                        for r in p.runs:
                            r.font.size = Pt(12)
            state.update(slide=None, body=None, lines=0)
        elif b.kind == "image" and b.data:
            try:
                from .images import Image
                data = b.data
                with Image.open(io.BytesIO(data)) as im:
                    w, h = im.size
                if b.ext not in ("png", "jpg", "jpeg", "gif", "bmp"):
                    from .docs import _image_to_png
                    data = _image_to_png(data)
                s = new_slide(state.get("title", "")) if state.get("title") else prs.slides.add_slide(blank)
                top = Emu(1500000) if state.get("title") else M
                area_w, area_h = SW - 2 * M, SH - top - M
                scale = min(area_w / w, area_h / h)
                pw, ph = int(w * scale), int(h * scale)
                s.shapes.add_picture(io.BytesIO(data), (SW - pw) // 2, top + (area_h - ph) // 2, pw, ph)
                state.update(slide=None, body=None, lines=0)
            except Exception:
                pass
    if not prs.slides:
        new_slide(doc.title or "Untitled", cover=True)
    prs.save(str(dst))
