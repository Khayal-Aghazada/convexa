"""Document readers/writers: docx, html, md, txt, pdf (text) <-> Doc model."""
from __future__ import annotations

import base64
import html as htmllib
import io
import re
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

from .model import Block, Doc, Run

PT_PER_EMU = 1 / 12700


# --------------------------------------------------------------------- helpers
def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("cp1252", errors="replace")


def _image_to_png(data: bytes) -> bytes:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        if im.mode not in ("RGB", "RGBA", "L", "LA"):
            im = im.convert("RGBA")
        out = io.BytesIO()
        im.save(out, "PNG")
        return out.getvalue()


def _image_size_pt(data: bytes) -> tuple[float, float] | None:
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as im:
            dpi = im.info.get("dpi", (96, 96))[0] or 96
            dpi = dpi if 50 <= dpi <= 600 else 96
            return im.width * 72 / dpi, im.height * 72 / dpi
    except Exception:
        return None


# ======================================================================= TXT
def read_txt(path: Path) -> Doc:
    doc = Doc(title=path.stem)
    for line in _read_text(path).splitlines():
        doc.add(Block("para", [Run(line)] if line else []))
    return doc


def write_txt(doc: Doc, dst: Path) -> None:
    out: list[str] = []
    counters: dict[int, int] = {}
    for b in doc.blocks:
        if b.kind != "list":
            counters.clear()
        if b.kind == "heading":
            if out and out[-1] != "":
                out.append("")
            out.append(b.plain.strip())
            out.append("")
        elif b.kind == "list":
            counters[b.level] = counters.get(b.level, 0) + 1
            for lvl in [k for k in counters if k > b.level]:
                del counters[lvl]
            mark = f"{counters[b.level]}." if b.ordered else "•"
            out.append("    " * b.level + f"{mark} {b.plain.strip()}")
        elif b.kind == "table":
            out.extend(_text_table(b.rows or []))
            out.append("")
        elif b.kind == "code":
            out.extend(b.text.splitlines())
        elif b.kind == "quote":
            out.append("> " + b.plain)
        elif b.kind == "pagebreak":
            out.append("\f")
        elif b.kind == "image":
            continue
        else:
            out.append(b.plain)
    dst.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def _text_table(rows: list[list[str]]) -> list[str]:
    if not rows:
        return []
    n = max(len(r) for r in rows)
    rows = [[c.replace("\n", " ") for c in r] + [""] * (n - len(r)) for r in rows]
    widths = [min(40, max(len(r[i]) for r in rows)) for i in range(n)]
    lines = []
    for j, r in enumerate(rows):
        lines.append("  ".join(c[:40].ljust(widths[i]) for i, c in enumerate(r)).rstrip())
        if j == 0 and len(rows) > 1:
            lines.append("  ".join("-" * w for w in widths))
    return lines


# ====================================================================== HTML
BLOCK_TAGS = {"p", "div", "section", "article", "header", "footer", "main", "nav", "aside",
              "figure", "figcaption", "address", "dd", "dt", "center", "form", "fieldset"}
SKIP_TAGS = {"script", "style", "head", "title", "noscript", "template", "svg", "button", "select"}


class _HtmlToDoc(HTMLParser):
    def __init__(self, base_dir: Path | None):
        super().__init__(convert_charrefs=True)
        self.base = base_dir
        self.doc = Doc()
        self.runs: list[Run] = []
        self.ctx: list[tuple[str, Block]] = []   # (tag, template block)
        self.fmt = {"b": 0, "i": 0, "u": 0, "code": 0}
        self.links: list[str | None] = []
        self.lists: list[bool] = []
        self.skip = 0
        self.pre: list[str] | None = None
        self.tables: list[dict] = []
        self.title_parts: list[str] = []
        self.in_title = False

    # ---- structure helpers
    def _template(self) -> Block:
        return self.ctx[-1][1] if self.ctx else Block("para")

    def flush(self) -> None:
        runs = [r for r in self.runs if r.text]
        self.runs = []
        if not runs or not "".join(r.text for r in runs).strip():
            return
        runs[0].text = runs[0].text.lstrip()
        runs[-1].text = runs[-1].text.rstrip()
        t = self._template()
        self.doc.add(Block(t.kind, runs, level=t.level, ordered=t.ordered, align=t.align))

    def push(self, tag: str, block: Block) -> None:
        self.flush()
        self.ctx.append((tag, block))

    def pop(self, tag: str) -> None:
        self.flush()
        for i in range(len(self.ctx) - 1, -1, -1):
            if self.ctx[i][0] == tag:
                del self.ctx[i:]
                return

    # ---- parser callbacks
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag in SKIP_TAGS:
            self.skip += 1
            return
        if self.skip:
            return
        if self.tables and tag in ("td", "th"):
            self.tables[-1]["cell"] = []
            self.tables[-1]["th"] |= tag == "th"
            return
        if self.tables and tag == "tr":
            self.tables[-1]["row"] = []
            return
        if self.tables and self.tables[-1].get("cell") is not None:
            if tag == "br":
                self.tables[-1]["cell"].append("\n")
            return
        style = (a.get("style") or "").lower()
        align = a.get("align") or (m.group(1) if (m := re.search(r"text-align\s*:\s*(\w+)", style)) else None)
        if re.fullmatch(r"h[1-6]", tag):
            self.push(tag, Block("heading", level=int(tag[1]), align=align))
        elif tag in BLOCK_TAGS:
            t = self._template()
            inherit = t.kind if t.kind in ("list", "quote") else "para"
            self.push(tag, Block(inherit, level=t.level, ordered=t.ordered, align=align or t.align))
        elif tag == "blockquote":
            self.push(tag, Block("quote"))
        elif tag in ("ul", "ol"):
            self.flush()
            self.lists.append(tag == "ol")
        elif tag == "li":
            self.push(tag, Block("list", level=max(0, len(self.lists) - 1),
                                 ordered=bool(self.lists and self.lists[-1])))
        elif tag == "pre":
            self.flush()
            self.pre = []
        elif tag == "table":
            self.flush()
            self.tables.append({"rows": [], "row": None, "cell": None, "th": False})
        elif tag == "br":
            if self.pre is not None:
                self.pre.append("\n")
            else:
                self.runs.append(Run("\n"))
        elif tag == "hr":
            self.flush()
        elif tag == "img":
            self.flush()
            self._image(a)
        elif tag in ("b", "strong"):
            self.fmt["b"] += 1
        elif tag in ("i", "em"):
            self.fmt["i"] += 1
        elif tag in ("u", "ins"):
            self.fmt["u"] += 1
        elif tag in ("code", "kbd", "samp", "tt"):
            self.fmt["code"] += 1
        elif tag == "a":
            self.links.append(a.get("href"))

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if self.tables:
            t = self.tables[-1]
            if tag in ("td", "th") and t["cell"] is not None:
                if t["row"] is None:
                    t["row"] = []
                t["row"].append(re.sub(r"[ \t\r\f\v]+", " ", "".join(t["cell"])).strip())
                t["cell"] = None
                return
            if tag == "tr":
                if t["row"]:
                    t["rows"].append(t["row"])
                t["row"] = None
                return
            if tag == "table":
                self.tables.pop()
                if t["rows"]:
                    w = max(len(r) for r in t["rows"])
                    rows = [r + [""] * (w - len(r)) for r in t["rows"]]
                    self.doc.add(Block("table", rows=rows, header=t["th"]))
                return
            if t["cell"] is not None:
                return
        if re.fullmatch(r"h[1-6]", tag) or tag in BLOCK_TAGS or tag in ("blockquote", "li"):
            self.pop(tag)
        elif tag in ("ul", "ol"):
            self.flush()
            if self.lists:
                self.lists.pop()
        elif tag == "pre" and self.pre is not None:
            text = "".join(self.pre).strip("\n")
            self.pre = None
            if text.strip():
                self.doc.add(Block("code", text=text))
        elif tag in ("b", "strong"):
            self.fmt["b"] = max(0, self.fmt["b"] - 1)
        elif tag in ("i", "em"):
            self.fmt["i"] = max(0, self.fmt["i"] - 1)
        elif tag in ("u", "ins"):
            self.fmt["u"] = max(0, self.fmt["u"] - 1)
        elif tag in ("code", "kbd", "samp", "tt"):
            self.fmt["code"] = max(0, self.fmt["code"] - 1)
        elif tag == "a" and self.links:
            self.links.pop()

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        if self.skip:
            return
        if self.tables and self.tables[-1]["cell"] is not None:
            self.tables[-1]["cell"].append(data)
            return
        if self.tables and self.tables[-1]["cell"] is None and not data.strip():
            return
        if self.pre is not None:
            self.pre.append(data)
            return
        text = re.sub(r"\s+", " ", data)
        if not text:
            return
        if text == " " and (not self.runs or self.runs[-1].text.endswith((" ", "\n"))):
            return
        self.runs.append(Run(text, bold=self.fmt["b"] > 0, italic=self.fmt["i"] > 0,
                             underline=self.fmt["u"] > 0, code=self.fmt["code"] > 0,
                             link=self.links[-1] if self.links else None))

    def _image(self, a: dict) -> None:
        src = a.get("src") or ""
        data = ext = None
        try:
            if src.startswith("data:"):
                head, _, payload = src.partition(",")
                ext = head.split("/")[1].split(";")[0] if "/" in head else "png"
                data = base64.b64decode(payload) if ";base64" in head else unquote(payload).encode()
            elif not re.match(r"^[a-z]+://", src, re.I) or src.lower().startswith("file:"):
                p = Path(unquote(urlparse(src).path.lstrip("/"))) if src.lower().startswith("file:") else Path(unquote(src))
                if not p.is_absolute() and self.base:
                    p = self.base / p
                if p.is_file():
                    data, ext = p.read_bytes(), p.suffix.lstrip(".").lower()
        except Exception:
            data = None
        if not data:
            return
        ext = {"jpeg": "jpg", "svg+xml": "svg"}.get(ext or "png", ext or "png")
        width = None
        for key in ("width",):
            if a.get(key, "").rstrip("px").isdigit():
                width = int(a[key].rstrip("px")) * 0.75
        self.doc.add(Block("image", data=data, ext=ext, width=width))

    def close(self):
        super().close()
        self.flush()
        self.doc.title = "".join(self.title_parts).strip()


def html_to_doc(html: str, base_dir: Path | None = None) -> Doc:
    p = _HtmlToDoc(base_dir)
    p.feed(html)
    p.close()
    return p.doc


def read_html(path: Path) -> Doc:
    doc = html_to_doc(_read_text(path), path.parent)
    doc.title = doc.title or path.stem
    return doc


# ================================================================== MARKDOWN
def read_md(path: Path) -> Doc:
    import markdown
    html = markdown.markdown(_read_text(path), extensions=["tables", "fenced_code", "sane_lists"])
    doc = html_to_doc(html, path.parent)
    doc.title = path.stem
    return doc


def _md_inline(runs: list[Run]) -> str:
    out = []
    for r in _merge_runs(runs):
        t = r.text
        if not t:
            continue
        if r.code:
            core = t.strip()
            s = f"`{core}`" if core else t
        else:
            core = re.sub(r"([\\`*_\[\]])", r"\\\1", t.strip())
            s = core
            if core:
                if r.bold and r.italic:
                    s = f"***{core}***"
                elif r.bold:
                    s = f"**{core}**"
                elif r.italic:
                    s = f"*{core}*"
        if r.link and core:
            s = f"[{s}]({r.link})"
        lead = t[: len(t) - len(t.lstrip())]
        trail = t[len(t.rstrip()):]
        out.append(lead + s + trail if core else t)
    return "".join(out).replace("\n", "  \n")


def write_md(doc: Doc, dst: Path) -> None:
    out: list[str] = []
    img_dir = dst.with_name(dst.stem + "_files")
    n_img = 0
    prev = None
    for b in doc.blocks:
        if prev == "list" and b.kind != "list":
            out.append("")
        if b.kind == "heading":
            out += ["#" * min(max(b.level, 1), 6) + " " + _md_inline(b.runs).strip(), ""]
        elif b.kind == "list":
            mark = "1." if b.ordered else "-"
            out.append("    " * b.level + f"{mark} {_md_inline(b.runs).strip()}")
        elif b.kind == "quote":
            out += ["> " + _md_inline(b.runs), ""]
        elif b.kind == "code":
            out += ["```", b.text, "```", ""]
        elif b.kind == "table" and b.rows:
            rows = [[c.replace("|", "\\|").replace("\n", " ") for c in r] for r in b.rows]
            n = max(len(r) for r in rows)
            rows = [r + [""] * (n - len(r)) for r in rows]
            out.append("| " + " | ".join(rows[0]) + " |")
            out.append("|" + "|".join([" --- "] * n) + "|")
            out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
            out.append("")
        elif b.kind == "image" and b.data:
            n_img += 1
            img_dir.mkdir(exist_ok=True)
            name = f"image{n_img}.{b.ext}"
            (img_dir / name).write_bytes(b.data)
            out += [f"![]({img_dir.name}/{name})", ""]
        elif b.kind == "pagebreak":
            out += ["---", ""]
        elif b.kind == "para":
            text = _md_inline(b.runs)
            if text.strip():
                out += [text, ""]
        prev = b.kind
    dst.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def _merge_runs(runs: list[Run]) -> list[Run]:
    merged: list[Run] = []
    for r in runs:
        if merged and merged[-1].same_format(r):
            merged[-1] = Run(merged[-1].text + r.text, r.bold, r.italic, r.underline, r.code, r.link)
        else:
            merged.append(Run(r.text, r.bold, r.italic, r.underline, r.code, r.link))
    return merged


# --------------------------------------------------------------- HTML writer
HTML_CSS = """
:root { color-scheme: light dark; }
body { font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; max-width: 820px;
       margin: 48px auto; padding: 0 24px; line-height: 1.6; color: #1d1d1f; background: #fff; }
h1, h2, h3, h4 { line-height: 1.25; margin: 1.6em 0 .5em; }
img { max-width: 100%; height: auto; border-radius: 6px; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: .92em; }
th, td { border: 1px solid #e3e3e8; padding: 6px 10px; text-align: left; vertical-align: top; }
th { background: #f5f5f7; font-weight: 600; }
pre { background: #f5f5f7; padding: 14px 16px; border-radius: 8px; overflow: auto; }
code { font-family: 'Cascadia Mono', Consolas, monospace; font-size: .9em; }
blockquote { border-left: 3px solid #d2d2d7; margin: 1em 0; padding: .2em 1em; color: #555; }
hr.page { border: 0; border-top: 1px dashed #d2d2d7; margin: 2em 0; }
@media (prefers-color-scheme: dark) {
  body { background: #161618; color: #f2f2f3; }
  th { background: #232326; } th, td { border-color: #333338; }
  pre { background: #232326; } blockquote { color: #aaa; border-color: #444; }
}
"""


def _html_inline(runs: list[Run]) -> str:
    parts = []
    for r in _merge_runs(runs):
        s = htmllib.escape(r.text).replace("\n", "<br>")
        if r.code:
            s = f"<code>{s}</code>"
        if r.bold:
            s = f"<strong>{s}</strong>"
        if r.italic:
            s = f"<em>{s}</em>"
        if r.underline:
            s = f"<u>{s}</u>"
        if r.link:
            s = f'<a href="{htmllib.escape(r.link, quote=True)}">{s}</a>'
        parts.append(s)
    return "".join(parts)


def doc_to_html_body(doc: Doc, image_src=None) -> str:
    """Render blocks to HTML. image_src(block, index) -> src string."""
    out: list[str] = []
    list_stack: list[str] = []

    def close_lists(to_level: int) -> None:
        while len(list_stack) > to_level:
            out.append(f"</{list_stack.pop()}>")

    n_img = 0
    for b in doc.blocks:
        if b.kind == "list":
            tag = "ol" if b.ordered else "ul"
            close_lists(b.level + 1)
            if len(list_stack) == b.level + 1 and list_stack[-1] != tag:
                close_lists(b.level)
            while len(list_stack) < b.level + 1:
                list_stack.append(tag)
                out.append(f"<{tag}>")
            out.append(f"<li>{_html_inline(b.runs)}</li>")
            continue
        close_lists(0)
        style = f' style="text-align:{b.align}"' if b.align in ("center", "right", "justify") else ""
        if b.kind == "heading":
            lv = min(max(b.level, 1), 6)
            out.append(f"<h{lv}{style}>{_html_inline(b.runs)}</h{lv}>")
        elif b.kind == "quote":
            out.append(f"<blockquote>{_html_inline(b.runs)}</blockquote>")
        elif b.kind == "code":
            out.append(f"<pre><code>{htmllib.escape(b.text)}</code></pre>")
        elif b.kind == "table" and b.rows:
            rows = []
            for i, r in enumerate(b.rows):
                cell = "th" if (i == 0 and b.header) else "td"
                cells = "".join(f"<{cell}>{htmllib.escape(c).replace(chr(10), '<br>')}</{cell}>" for c in r)
                rows.append(f"<tr>{cells}</tr>")
            out.append("<table>" + "".join(rows) + "</table>")
        elif b.kind == "image" and b.data:
            n_img += 1
            src = image_src(b, n_img) if image_src else None
            if src:
                w = f' width="{int(b.width / 0.75)}"' if b.width else ""
                out.append(f'<p{style}><img src="{src}"{w}></p>')
        elif b.kind == "pagebreak":
            out.append('<hr class="page">')
        else:
            inner = _html_inline(b.runs)
            out.append(f"<p{style}>{inner if inner.strip() else '&nbsp;'}</p>")
    close_lists(0)
    return "\n".join(out)


def write_html(doc: Doc, dst: Path) -> None:
    def data_uri(b: Block, _i: int) -> str:
        mime = {"jpg": "jpeg", "svg": "svg+xml", "tif": "tiff"}.get(b.ext, b.ext)
        return f"data:image/{mime};base64," + base64.b64encode(b.data).decode()

    body = doc_to_html_body(doc, data_uri)
    title = htmllib.escape(doc.title or dst.stem)
    dst.write_text(
        f'<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title}</title>\n<style>{HTML_CSS}</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n",
        encoding="utf-8")


# ====================================================================== DOCX
def read_docx(path: Path) -> Doc:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.text.run import Run as DRun
    from docx.text.hyperlink import Hyperlink

    d = docx.Document(str(path))
    doc = Doc(title=(d.core_properties.title or path.stem))
    try:
        doc.font = d.styles["Normal"].font.name
    except Exception:
        pass
    numbering = None
    try:
        numbering = d.part.numbering_part.element
    except Exception:
        pass

    def is_ordered(num_id: str, ilvl: str) -> bool:
        if numbering is None:
            return False
        try:
            abs_ids = numbering.xpath(f'./w:num[@w:numId="{num_id}"]/w:abstractNumId/@w:val')
            if not abs_ids:
                return False
            fmts = numbering.xpath(
                f'./w:abstractNum[@w:abstractNumId="{abs_ids[0]}"]/w:lvl[@w:ilvl="{ilvl}"]/w:numFmt/@w:val')
            return bool(fmts) and fmts[0] not in ("bullet", "none")
        except Exception:
            return False

    align_map = {1: "center", 2: "right", 3: "justify"}

    def run_images(r) -> list[Block]:
        blocks = []
        for blip in r._r.xpath(".//a:blip/@r:embed"):
            try:
                part = d.part.related_parts[blip]
                ext = part.partname.ext.lower()
                width = None
                ext_el = r._r.xpath(".//wp:extent/@cx")
                if ext_el:
                    width = int(ext_el[0]) * PT_PER_EMU
                blocks.append(Block("image", data=part.blob, ext="jpg" if ext == "jpeg" else ext, width=width))
            except Exception:
                continue
        return blocks

    def para(p: Paragraph) -> None:
        style = (p.style.name if p.style is not None else "") or ""
        kind, level, ordered = "para", 0, False
        m = re.match(r"Heading (\d)", style)
        if m:
            kind, level = "heading", int(m.group(1))
        elif style == "Title":
            kind, level = "heading", 1
        elif style == "Subtitle":
            kind, level = "heading", 2
        elif style in ("Quote", "Intense Quote"):
            kind = "quote"
        pPr = p._p.pPr
        numPr = pPr.numPr if pPr is not None else None
        if kind == "para" and (numPr is not None or style.startswith("List")):
            kind = "list"
            if numPr is not None and numPr.ilvl is not None:
                level = int(numPr.ilvl.val)
            elif (m2 := re.search(r"(\d)$", style)):
                level = int(m2.group(1)) - 1
            if numPr is not None and numPr.numId is not None:
                ordered = is_ordered(str(numPr.numId.val), str(level))
            else:
                ordered = "Number" in style
        runs: list[Run] = []
        trailing: list[Block] = []
        page_break = False

        def add_run(r: DRun, link=None):
            nonlocal page_break
            if r._r.xpath('.//w:br[@w:type="page"]'):
                page_break = True
            trailing.extend(run_images(r))
            if r.text:
                runs.append(Run(r.text, bold=bool(r.bold), italic=bool(r.italic),
                                underline=bool(r.underline), link=link,
                                code=(r.font.name or "").lower() in ("consolas", "courier new", "cascadia mono")))

        for item in p.iter_inner_content():
            if isinstance(item, Hyperlink):
                for r in item.runs:
                    add_run(r, item.url or None)
            else:
                add_run(item)
        if kind == "heading" and not "".join(r.text for r in runs).strip():
            kind = "para"
        doc.add(Block(kind, runs, level=level, ordered=ordered,
                      align=align_map.get(int(p.alignment)) if p.alignment is not None else None))
        doc.blocks.extend(trailing)
        if page_break:
            doc.add(Block("pagebreak"))

    for child in d.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para(Paragraph(child, d._body))
        elif tag == "tbl":
            t = Table(child, d._body)
            rows = []
            for row in t.rows:
                try:
                    rows.append([c.text.strip() for c in row.cells])
                except Exception:
                    continue
            if rows:
                doc.add(Block("table", rows=rows, header=True))
    # Drop empty paragraphs at the end
    while doc.blocks and doc.blocks[-1].kind == "para" and not doc.blocks[-1].plain.strip():
        doc.blocks.pop()
    return doc


def write_docx(doc: Doc, dst: Path) -> None:
    import docx
    from docx.enum.section import WD_ORIENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    d = docx.Document()
    normal = d.styles["Normal"]
    normal.font.name = doc.font or "Calibri"
    normal.font.size = Pt(11)
    sec = d.sections[0]
    if doc.landscape:
        sec.orientation = WD_ORIENT.LANDSCAPE
        sec.page_width, sec.page_height = sec.page_height, sec.page_width
    usable = sec.page_width - sec.left_margin - sec.right_margin
    if doc.title:
        d.core_properties.title = doc.title
    aligns = {"center": WD_ALIGN_PARAGRAPH.CENTER, "right": WD_ALIGN_PARAGRAPH.RIGHT,
              "justify": WD_ALIGN_PARAGRAPH.JUSTIFY}

    def add_hyperlink(p, run: Run):
        rid = p.part.relate_to(run.link, docx.opc.constants.RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
        h = OxmlElement("w:hyperlink")
        h.set(qn("r:id"), rid)
        r = OxmlElement("w:r")
        rpr = OxmlElement("w:rPr")
        style = OxmlElement("w:rStyle")
        style.set(qn("w:val"), "Hyperlink")
        rpr.append(style)
        color = OxmlElement("w:color")
        color.set(qn("w:val"), "2563EB")
        rpr.append(color)
        u = OxmlElement("w:u")
        u.set(qn("w:val"), "single")
        rpr.append(u)
        r.append(rpr)
        t = OxmlElement("w:t")
        t.text = run.text
        t.set(qn("xml:space"), "preserve")
        r.append(t)
        h.append(r)
        p._p.append(h)

    def fill(p, runs: list[Run]):
        for run in _merge_runs(runs):
            if run.link and re.match(r"^(https?|mailto):", run.link):
                try:
                    add_hyperlink(p, run)
                    continue
                except Exception:
                    pass
            r = p.add_run(run.text)
            r.bold = run.bold or None
            r.italic = run.italic or None
            r.underline = run.underline or None
            if run.code:
                r.font.name = "Consolas"

    for b in doc.blocks:
        if b.kind == "heading":
            p = d.add_heading(level=min(max(b.level, 1), 9))
            fill(p, b.runs)
        elif b.kind == "list":
            base = "List Number" if b.ordered else "List Bullet"
            style = base if b.level == 0 else f"{base} {min(b.level + 1, 3)}"
            p = d.add_paragraph(style=style)
            fill(p, b.runs)
        elif b.kind == "quote":
            p = d.add_paragraph(style="Quote")
            fill(p, b.runs)
        elif b.kind == "code":
            p = d.add_paragraph()
            r = p.add_run(b.text)
            r.font.name = "Consolas"
            r.font.size = Pt(9.5)
            r.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"), "clear")
            shd.set(qn("w:fill"), "F3F3F5")
            p._p.get_or_add_pPr().append(shd)
        elif b.kind == "table" and b.rows:
            n = max(len(r) for r in b.rows)
            t = d.add_table(rows=len(b.rows), cols=n)
            t.style = "Table Grid"
            for i, row in enumerate(b.rows):
                cells = t.rows[i].cells
                for j in range(n):
                    val = row[j] if j < len(row) else ""
                    cells[j].text = val
                    if i == 0 and b.header and val:
                        for r in cells[j].paragraphs[0].runs:
                            r.bold = True
            d.add_paragraph()
        elif b.kind == "image" and b.data:
            data = b.data
            if b.ext not in ("png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff"):
                try:
                    data = _image_to_png(data)
                except Exception:
                    continue
            try:
                width = int(b.width / PT_PER_EMU) if b.width else None
                if width is None:
                    size = _image_size_pt(data)
                    width = int(size[0] / PT_PER_EMU) if size else usable
                d.add_picture(io.BytesIO(data), width=min(width, usable))
                if b.align in aligns:
                    d.paragraphs[-1].alignment = aligns[b.align]
            except Exception:
                continue
        elif b.kind == "pagebreak":
            d.add_page_break()
        else:
            p = d.add_paragraph()
            fill(p, b.runs)
            if b.align in aligns:
                p.alignment = aligns[b.align]
    d.save(str(dst))


# ======================================================================= PDF
_BULLET = re.compile(r"^\s*([•·▪◦‣●○■□\-–*]|\d{1,3}[.)]|[a-zA-Z][.)])\s+")


def read_pdf(path: Path) -> Doc:
    import collections

    import pymupdf

    src = pymupdf.open(str(path))
    doc = Doc(title=(src.metadata or {}).get("title") or path.stem)
    sizes: collections.Counter = collections.Counter()
    pages = []
    for page in src:
        d = page.get_text("dict", sort=True)
        pages.append(d)
        for b in d["blocks"]:
            for line in b.get("lines", []):
                for s in line["spans"]:
                    sizes[round(s["size"])] += len(s["text"].strip())
    body = sizes.most_common(1)[0][0] if sizes else 11

    for pno, d in enumerate(pages):
        if pno:
            doc.add(Block("pagebreak"))
        for b in d["blocks"]:
            if b["type"] == 1:
                if b.get("image"):
                    x0, _, x1, _ = b["bbox"]
                    doc.add(Block("image", data=b["image"], ext=b.get("ext", "png"), width=x1 - x0))
                continue
            current: list[Run] = []
            kind, ordered = "para", False
            max_size = 0.0

            def emit():
                nonlocal current
                if current and "".join(r.text for r in current).strip():
                    k, lvl = kind, 0
                    if k == "para" and max_size >= body * 1.2 and len("".join(r.text for r in current)) < 200:
                        k = "heading"
                        lvl = 1 if max_size >= body * 1.8 else 2 if max_size >= body * 1.4 else 3
                    if k == "list":
                        current[0].text = _BULLET.sub("", current[0].text, count=1)
                    doc.add(Block(k, current, level=lvl, ordered=ordered))
                current = []

            for line in b.get("lines", []):
                spans = [s for s in line["spans"] if s["text"]]
                if not spans:
                    continue
                line_text = "".join(s["text"] for s in spans)
                m = _BULLET.match(line_text)
                if m and current:
                    emit()
                    max_size = 0.0
                if m and not current:
                    kind, ordered = "list", m.group(1)[0].isalnum()
                elif not current:
                    kind, ordered = "para", False
                if current:
                    last = current[-1]
                    if last.text.endswith("-") and not last.text.endswith(" -"):
                        last.text = last.text[:-1]
                    elif not last.text.endswith(" "):
                        last.text += " "
                for s in spans:
                    max_size = max(max_size, s["size"])
                    bold = bool(s["flags"] & 16) or "bold" in s["font"].lower()
                    italic = bool(s["flags"] & 2) or "italic" in s["font"].lower()
                    current.append(Run(s["text"], bold=bold, italic=italic,
                                       code=bool(s["flags"] & 8)))
            emit()
    return doc
