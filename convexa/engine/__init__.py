"""Conversion engine: which targets a file supports, and converting it.

Everything runs locally with pure-Python libraries (PyMuPDF, python-docx,
openpyxl, python-pptx, Pillow). No Office, LibreOffice or network needed.
"""
from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import docs, images, pdfs, sheets, slides
from .model import doc_to_workbook, workbook_to_doc

# ext -> (label, category)
FORMATS: dict[str, tuple[str, str]] = {
    "pdf": ("Document", "document"),
    "docx": ("Word", "document"),
    "txt": ("Plain text", "document"),
    "md": ("Markdown", "document"),
    "html": ("Web page", "document"),
    "xlsx": ("Excel", "spreadsheet"),
    "csv": ("Comma-separated", "spreadsheet"),
    "tsv": ("Tab-separated", "spreadsheet"),
    "json": ("Data", "spreadsheet"),
    "pptx": ("PowerPoint", "presentation"),
    "png": ("Lossless", "image"),
    "jpg": ("Photo", "image"),
    "webp": ("Web image", "image"),
    "bmp": ("Bitmap", "image"),
    "gif": ("Animation", "image"),
    "tiff": ("Print", "image"),
    "ico": ("Windows icon", "image"),
}
CATEGORIES = ["document", "spreadsheet", "presentation", "image"]

ALIASES = {"jpeg": "jpg", "htm": "html", "markdown": "md", "tif": "tiff", "xlsm": "xlsx", "text": "txt", "log": "txt"}

DOC_SRC = {"docx", "txt", "md", "html", "pdf", "pptx"}
SHEET_SRC = {"xlsx", "xls", "csv", "tsv", "json"}
IMG_SRC = images.RASTER_IN - {"jpeg", "tif"} | {"tiff"}
DOC_OUT = ["pdf", "docx", "txt", "md", "html", "pptx"]
SHEET_OUT = ["xlsx", "csv", "tsv", "json"]
PAGE_IMG_OUT = ["png", "jpg", "webp", "tiff"]


class ConversionError(Exception):
    pass


def norm_ext(path_or_ext: str | Path) -> str:
    s = str(path_or_ext)
    ext = (Path(s).suffix[1:] if "." in s else s).lower()
    return ALIASES.get(ext, ext)


def supported(path: Path) -> bool:
    return bool(targets_for(path))


def targets_for(path: str | Path) -> list[str]:
    s = norm_ext(path)
    t: list[str] = []
    if s in IMG_SRC:
        t = images.RASTER_OUT + ["pdf", "docx", "pptx"]
    elif s in SHEET_SRC:
        t = SHEET_OUT + ["pdf", "docx", "html", "md", "txt"]
    elif s in DOC_SRC:
        t = list(DOC_OUT)
        if s in ("pdf", "docx", "html", "md"):
            t += ["xlsx", "csv"]
        t += PAGE_IMG_OUT
    return [x for x in dict.fromkeys(t) if x != s]


def common_targets(paths: list[Path]) -> list[str]:
    sets = [targets_for(p) for p in paths]
    if not sets:
        return []
    common = [t for t in sets[0] if all(t in s for s in sets[1:])]
    return common


# ------------------------------------------------------------------ helpers
def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    i = 1
    while True:
        cand = p.with_name(f"{p.stem} ({i}){p.suffix}")
        if not cand.exists():
            return cand
        i += 1


def _read_doc(src: Path, s: str):
    if s == "docx":
        return docs.read_docx(src)
    if s == "txt":
        return docs.read_txt(src)
    if s == "md":
        return docs.read_md(src)
    if s == "html":
        return docs.read_html(src)
    if s == "pdf":
        return docs.read_pdf(src)
    if s == "pptx":
        return slides.read_pptx(src)
    if s in SHEET_SRC:
        return workbook_to_doc(_read_wb(src, s), src.stem)
    if s in IMG_SRC:
        from .model import Block, Doc
        doc = Doc(title=src.stem)
        for data, w, _h, ext in images.image_frames_png(src):
            doc.add(Block("image", data=data, ext=ext, width=w * 0.75))
        return doc
    raise ConversionError(f"Can't read .{s} files")


def _read_wb(src: Path, s: str):
    if s == "xlsx":
        return sheets.read_xlsx(src)
    if s == "xls":
        return sheets.read_xls(src)
    if s == "csv":
        return sheets.read_csv(src)
    if s == "tsv":
        return sheets.read_csv(src, "\t")
    if s == "json":
        return sheets.read_json(src)
    if s == "pdf":
        return pdfs.pdf_to_workbook(src)
    return doc_to_workbook(_read_doc(src, s))


def _write_doc(doc, dst: Path, fmt: str) -> None:
    {"pdf": pdfs.write_pdf, "docx": docs.write_docx, "txt": docs.write_txt, "md": docs.write_md,
     "html": docs.write_html, "pptx": slides.write_pptx}[fmt](doc, dst)


def _to_pdf(src: Path, s: str, dst: Path) -> None:
    if s == "pptx":
        slides.pptx_to_pdf(src, dst)
    elif s in IMG_SRC:
        images.image_to_pdf(src, dst)
    else:
        pdfs.write_pdf(_read_doc(src, s), dst)


def _pdf_pages_to_images(pdf_path: Path, out_dir: Path, stem: str, fmt: str) -> list[Path]:
    import pymupdf
    with pymupdf.open(str(pdf_path)) as d:
        n = len(d)
        if n == 1:
            dst = unique_path(out_dir / f"{stem}.{fmt}")
            images.save_image(next(pdfs.render_pages(d)), dst, fmt)
            return [dst]
        folder = unique_path(out_dir / stem)
        folder.mkdir(parents=True)
        width = len(str(n))
        for i, im in enumerate(pdfs.render_pages(d), 1):
            images.save_image(im, folder / f"{stem}-{i:0{width}d}.{fmt}", fmt)
        return [folder]


# ------------------------------------------------------------------- public
@dataclass
class Result:
    source: Path
    outputs: list[Path]


def convert(src: str | Path, fmt: str, out_dir: str | Path | None = None) -> Result:
    src = Path(src)
    if not src.is_file():
        raise ConversionError(f"File not found: {src}")
    s = norm_ext(src)
    fmt = norm_ext(fmt)
    if fmt not in targets_for(src):
        raise ConversionError(f".{s} → .{fmt} isn't supported")
    out_dir = Path(out_dir) if out_dir else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = unique_path(out_dir / f"{src.stem}.{fmt}")

    try:
        outputs = _convert(src, s, fmt, dst, out_dir)
    except ConversionError:
        raise
    except PermissionError as e:
        raise ConversionError(f"Permission denied — is the file open in another app? ({e.filename})") from e
    except Exception as e:  # surface a readable message
        if dst.exists() and dst.stat().st_size == 0:
            dst.unlink(missing_ok=True)
        raise ConversionError(f"{type(e).__name__}: {e}") from e
    return Result(src, outputs)


def _convert(src: Path, s: str, fmt: str, dst: Path, out_dir: Path) -> list[Path]:
    # Images -> images
    if s in IMG_SRC and fmt in images.RASTER_OUT:
        images.convert_image(src, dst, fmt)
        return [dst]
    if s in IMG_SRC and fmt == "pptx":
        images.images_to_pptx([src], dst)
        return [dst]
    # Anything -> PDF
    if fmt == "pdf":
        _to_pdf(src, s, dst)
        return [dst]
    # PDF specials
    if s == "pdf" and fmt == "docx":
        pdfs.pdf_to_docx(src, dst)
        return [dst]
    if s == "pdf" and fmt == "txt":
        pdfs.pdf_to_text(src, dst)
        return [dst]
    if s == "pdf" and fmt == "pptx":
        pdfs.pdf_to_pptx(src, dst)
        return [dst]
    # Page images (pdf, docx, pptx, ...) via PDF
    if fmt in PAGE_IMG_OUT:
        if s == "pdf":
            return _pdf_pages_to_images(src, out_dir, src.stem, fmt)
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "pages.pdf"
            _to_pdf(src, s, pdf)
            return _pdf_pages_to_images(pdf, out_dir, src.stem, fmt)
    # Tabular outputs
    if fmt in SHEET_OUT:
        wb = _read_wb(src, s)
        if fmt == "xlsx":
            sheets.write_xlsx(wb, dst)
        elif fmt == "json":
            sheets.write_json(wb, dst)
        else:
            return sheets.write_csv(wb, dst, "\t" if fmt == "tsv" else ",")
        return [dst]
    # Documents
    if fmt in DOC_OUT:
        _write_doc(_read_doc(src, s), dst, fmt)
        outs = [dst]
        extra = dst.with_name(dst.stem + "_files")
        if fmt == "md" and extra.exists():
            outs.append(extra)
        return outs
    raise ConversionError(f".{s} → .{fmt} isn't supported")


def combine(sources: list[Path], fmt: str, out_dir: Path | None = None) -> Result:
    """Convert several files into a single PDF (or PPTX for images)."""
    sources = [Path(p) for p in sources]
    out_dir = Path(out_dir) if out_dir else sources[0].parent
    dst = unique_path(out_dir / f"{sources[0].stem} + {len(sources) - 1} more.{fmt}")
    try:
        if fmt == "pptx":
            images.images_to_pptx(sources, dst)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                parts = []
                for i, src in enumerate(sources):
                    s = norm_ext(src)
                    part = Path(tmp) / f"{i}.pdf"
                    if s == "pdf":
                        shutil.copy(src, part)
                    else:
                        _to_pdf(src, s, part)
                    parts.append(part)
                pdfs.merge_pdfs(parts, dst)
    except Exception as e:
        raise ConversionError(f"{type(e).__name__}: {e}") from e
    return Result(sources[0], [dst])


def can_combine(paths: list[Path], fmt: str) -> bool:
    if len(paths) < 2:
        return False
    if fmt == "pdf":
        return all("pdf" in targets_for(p) or norm_ext(p) == "pdf" for p in paths)
    if fmt == "pptx":
        return all(norm_ext(p) in IMG_SRC for p in paths)
    return False
