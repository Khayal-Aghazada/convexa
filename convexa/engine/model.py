"""Neutral in-memory models every reader produces and every writer consumes.

Documents are a flat list of blocks (headings, paragraphs, list items, tables,
images...). Spreadsheets are a list of named sheets holding rows of values.
Converting A -> B is always: read A into a model, write the model as B.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Run:
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    code: bool = False
    link: str | None = None

    def same_format(self, other: "Run") -> bool:
        return (self.bold, self.italic, self.underline, self.code, self.link) == (
            other.bold, other.italic, other.underline, other.code, other.link)


@dataclass
class Block:
    # heading | para | quote | list | code | table | image | pagebreak
    kind: str
    runs: list[Run] = field(default_factory=list)
    level: int = 0            # heading level (1-6) or list nesting level (0-based)
    ordered: bool = False     # list items
    align: str | None = None  # left | center | right | justify
    rows: list[list[str]] | None = None  # tables
    header: bool = True                  # tables: first row is a header
    data: bytes | None = None            # images
    ext: str = "png"                     # images
    width: float | None = None           # images, in points
    text: str = ""                       # code blocks

    @property
    def plain(self) -> str:
        if self.kind == "code":
            return self.text
        return "".join(r.text for r in self.runs)


@dataclass
class Doc:
    blocks: list[Block] = field(default_factory=list)
    title: str = ""
    font: str | None = None   # preferred body font family
    landscape: bool = False

    def add(self, block: Block) -> None:
        self.blocks.append(block)


@dataclass
class Sheet:
    name: str
    rows: list[list[Any]] = field(default_factory=list)

    @property
    def width(self) -> int:
        return max((len(r) for r in self.rows), default=0)


Workbook = list[Sheet]


def cell_text(v: Any) -> str:
    """Human-friendly string for a cell value."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        return f"{v:.10g}"
    if isinstance(v, _dt.datetime):
        return v.strftime("%Y-%m-%d %H:%M") if (v.hour or v.minute) else v.strftime("%Y-%m-%d")
    if isinstance(v, (_dt.date, _dt.time)):
        return v.isoformat()
    return str(v)


def workbook_to_doc(wb: Workbook, title: str = "") -> Doc:
    doc = Doc(title=title)
    multi = len(wb) > 1
    for sheet in wb:
        rows = [[cell_text(c) for c in r] for r in sheet.rows]
        rows = _trim(rows)
        if multi:
            doc.add(Block("heading", [Run(sheet.name)], level=2))
        if rows:
            doc.add(Block("table", rows=rows, header=True))
        if max((len(r) for r in rows), default=0) > 6:
            doc.landscape = True
    return doc


def doc_to_workbook(doc: Doc) -> Workbook:
    tables = [b for b in doc.blocks if b.kind == "table" and b.rows]
    if tables:
        return [Sheet(f"Table {i}", [list(r) for r in t.rows]) for i, t in enumerate(tables, 1)]
    rows = [[b.plain] for b in doc.blocks if b.kind not in ("image", "pagebreak") and b.plain.strip()]
    return [Sheet("Sheet1", rows)]


def _trim(rows: list[list[str]]) -> list[list[str]]:
    """Drop fully empty trailing rows/columns and pad rows to equal width."""
    while rows and not any(c.strip() for c in rows[-1]):
        rows.pop()
    width = 0
    for r in rows:
        for i in range(len(r) - 1, -1, -1):
            if r[i].strip():
                width = max(width, i + 1)
                break
    return [(r + [""] * width)[:width] for r in rows]
