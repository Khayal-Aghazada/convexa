"""PDF output (Doc -> PDF via MuPDF's HTML layout) and PDF-specific conversions."""
from __future__ import annotations

import io
import re
import tempfile
from pathlib import Path

import pymupdf

from .docs import doc_to_html_body
from .fonts import FontKit
from .model import Block, Doc, Sheet, Workbook

MARGIN = 56  # ~2 cm


def pdf_css(body_font: str, mono_font: str) -> str:
    return f"""
body {{ font-family: {body_font}; font-size: 10.5pt; line-height: 1.45; color: #1d1d1f; }}
h1 {{ font-size: 22pt; margin: 0 0 10pt 0; font-weight: bold; }}
h2 {{ font-size: 16pt; margin: 14pt 0 6pt 0; font-weight: bold; }}
h3 {{ font-size: 13pt; margin: 12pt 0 4pt 0; font-weight: bold; }}
h4, h5, h6 {{ font-size: 11pt; margin: 10pt 0 4pt 0; font-weight: bold; }}
p {{ margin: 0 0 6pt 0; }}
ul, ol {{ margin: 0 0 6pt 0; padding-left: 18pt; }}
li {{ margin: 0 0 2pt 0; }}
table {{ border-collapse: collapse; width: 100%; margin: 4pt 0 10pt 0; }}
th, td {{ border: 0.6pt solid #c9c9cf; padding: 3pt 5pt; font-size: 8.5pt; vertical-align: top; }}
th {{ background-color: #f1f1f4; font-weight: bold; }}
pre {{ background-color: #f4f4f6; padding: 6pt; font-family: {mono_font}; font-size: 8.5pt; }}
code {{ font-family: {mono_font}; font-size: 9pt; }}
blockquote {{ margin: 4pt 0 8pt 12pt; color: #555555; }}
a {{ color: #2563eb; }}
"""


def _paper(landscape: bool) -> pymupdf.Rect:
    return pymupdf.paper_rect("a4-l" if landscape else "a4")


def write_pdf(doc: Doc, dst: Path) -> None:
    kit = FontKit()
    body_font = kit.family(doc.font)
    mono_font = kit.family("Consolas", mono=True)
    paper = _paper(doc.landscape)
    usable_w = paper.width - 2 * MARGIN

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        kit.add_dir(tmpdir)

        def image_src(b: Block, i: int) -> str | None:
            data, ext = b.data, b.ext
            if ext not in ("png", "jpg", "jpeg", "gif", "bmp"):
                try:
                    from .docs import _image_to_png
                    data, ext = _image_to_png(data), "png"
                except Exception:
                    return None
            name = f"img{i}.{ext}"
            (tmpdir / name).write_bytes(data)
            if not b.width:
                from .docs import _image_size_pt
                size = _image_size_pt(data)
                b.width = size[0] if size else usable_w
            b.width = min(b.width, usable_w)
            return name

        # Split on page breaks: every segment starts on a fresh page.
        segments: list[Doc] = [Doc()]
        for b in doc.blocks:
            if b.kind == "pagebreak":
                segments.append(Doc())
            else:
                segments[-1].blocks.append(b)
        segments = [s for s in segments if s.blocks] or [Doc(blocks=[Block("para")])]

        css = kit.css + pdf_css(body_font, mono_font)
        writer = pymupdf.DocumentWriter(str(dst))
        where = paper + (MARGIN, MARGIN, -MARGIN, -MARGIN)
        img_counter = [0]

        def counting_src(b: Block, _i: int):
            img_counter[0] += 1
            return image_src(b, img_counter[0])

        for seg in segments:
            html = doc_to_html_body(seg, counting_src)
            story = pymupdf.Story(html=html, user_css=css, archive=kit.archive)
            more = True
            while more:
                dev = writer.begin_page(paper)
                more, _ = story.place(where)
                story.draw(dev)
                writer.end_page()
        writer.close()

    # Add metadata (title) in place.
    try:
        out = pymupdf.open(str(dst))
        out.set_metadata({"title": doc.title or dst.stem, "producer": "Convexa"})
        out.saveIncr()
        out.close()
    except Exception:
        pass


# ------------------------------------------------------------ PDF -> other
def pdf_to_text(src: Path, dst: Path) -> None:
    with pymupdf.open(str(src)) as d:
        parts = [page.get_text("text", sort=True).rstrip() for page in d]
    dst.write_text("\n\n\f".join(parts).strip() + "\n", encoding="utf-8")


def pdf_to_docx(src: Path, dst: Path) -> None:
    try:
        import logging

        from pdf2docx import Converter
        logging.getLogger("pdf2docx").setLevel(logging.ERROR)
        logging.disable(logging.WARNING)
        try:
            cv = Converter(str(src))
            cv.convert(str(dst), multi_processing=False)
            cv.close()
        finally:
            logging.disable(logging.NOTSET)
        if dst.exists() and dst.stat().st_size > 0:
            return
    except Exception:
        pass
    from .docs import read_pdf, write_docx
    write_docx(read_pdf(src), dst)


def render_pages(pdf: pymupdf.Document, dpi: int = 200):
    from PIL import Image
    for page in pdf:
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        yield Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def pdf_to_workbook(src: Path) -> Workbook:
    wb: Workbook = []
    with pymupdf.open(str(src)) as d:
        for pno, page in enumerate(d, 1):
            try:
                tabs = page.find_tables()
            except Exception:
                tabs = []
            for ti, t in enumerate(tabs, 1):
                rows = [[(c or "").strip() for c in r] for r in t.extract()]
                if rows:
                    wb.append(Sheet(f"Page {pno} Table {ti}", rows))
        if not wb:
            rows = []
            for page in d:
                for line in page.get_text("text", sort=True).splitlines():
                    if line.strip():
                        rows.append(re.split(r"\s{2,}|\t", line.strip()))
            wb.append(Sheet("Sheet1", rows))
    return wb


def pdf_to_pptx(src: Path, dst: Path) -> None:
    from pptx import Presentation
    from pptx.util import Emu

    with pymupdf.open(str(src)) as d:
        if not len(d):
            raise ValueError("PDF has no pages")
        first = d[0].rect
        prs = Presentation()
        prs.slide_width = Emu(int(first.width * 12700))
        prs.slide_height = Emu(int(first.height * 12700))
        blank = prs.slide_layouts[6]
        for page in d:
            pix = page.get_pixmap(dpi=200, alpha=False)
            stream = io.BytesIO(pix.tobytes("png"))
            slide = prs.slides.add_slide(blank)
            _place_fit(slide, stream, pix.width, pix.height, prs.slide_width, prs.slide_height)
        prs.save(str(dst))


def _place_fit(slide, stream, w: int, h: int, sw: int, sh: int) -> None:
    scale = min(sw / w, sh / h)
    pw, ph = int(w * scale), int(h * scale)
    slide.shapes.add_picture(stream, (sw - pw) // 2, (sh - ph) // 2, pw, ph)


def merge_pdfs(parts: list[Path], dst: Path) -> None:
    out = pymupdf.open()
    for p in parts:
        with pymupdf.open(str(p)) as d:
            out.insert_pdf(d)
    out.save(str(dst), garbage=3, deflate=True)
    out.close()
