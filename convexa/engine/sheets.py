"""Spreadsheet readers/writers: xlsx, xls, csv, tsv, json <-> Workbook."""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
from pathlib import Path
from typing import Any

from .model import Sheet, Workbook, cell_text

_NUM = re.compile(r"^-?(0|[1-9]\d*)(\.\d+)?([eE][-+]?\d+)?$")


def _coerce(v: str) -> Any:
    s = v.strip()
    if not s:
        return None
    if _NUM.match(s) and len(s) < 16:
        try:
            return int(s) if re.fullmatch(r"-?\d+", s) else float(s)
        except ValueError:
            return v
    if s.upper() in ("TRUE", "FALSE"):
        return s.upper() == "TRUE"
    return v


def _decode(raw: bytes) -> str:
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


# ------------------------------------------------------------------ readers
def read_xlsx(path: Path) -> Workbook:
    import openpyxl
    wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    out = []
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        while rows and all(v is None for v in rows[-1]):
            rows.pop()
        out.append(Sheet(ws.title, rows))
    wb.close()
    return out


def read_xls(path: Path) -> Workbook:
    import xlrd
    book = xlrd.open_workbook(str(path))
    out = []
    for sh in book.sheets():
        rows = []
        for r in range(sh.nrows):
            row = []
            for c in range(sh.ncols):
                cell = sh.cell(r, c)
                v = cell.value
                if cell.ctype == xlrd.XL_CELL_DATE:
                    try:
                        v = xlrd.xldate.xldate_as_datetime(v, book.datemode)
                    except Exception:
                        pass
                elif cell.ctype == xlrd.XL_CELL_EMPTY:
                    v = None
                elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                    v = bool(v)
                row.append(v)
            rows.append(row)
        out.append(Sheet(sh.name, rows))
    return out


def read_csv(path: Path, delimiter: str | None = None) -> Workbook:
    text = _decode(path.read_bytes())
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:20000], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    rows = [[_coerce(c) for c in r] for r in csv.reader(io.StringIO(text), delimiter=delimiter)]
    return [Sheet(path.stem[:31] or "Sheet1", rows)]


def read_json(path: Path) -> Workbook:
    data = json.loads(_decode(path.read_bytes()))

    def table(items: list) -> list[list] | None:
        if items and all(isinstance(i, dict) for i in items):
            keys: list[str] = []
            for i in items:
                for k in i:
                    if k not in keys:
                        keys.append(k)
            return [keys] + [[_scalar(i.get(k)) for k in keys] for i in items]
        if items and all(isinstance(i, list) for i in items):
            return [[_scalar(v) for v in i] for i in items]
        if all(not isinstance(i, (dict, list)) for i in items):
            return [["value"]] + [[i] for i in items]
        return None

    if isinstance(data, list):
        t = table(data)
        if t is not None:
            return [Sheet("Sheet1", t)]
    if isinstance(data, dict):
        sheets = []
        if all(isinstance(v, list) for v in data.values()) and data:
            for k, v in data.items():
                t = table(v)
                if t is None:
                    break
                sheets.append(Sheet(str(k)[:31], t))
            else:
                return sheets
    # Fallback: flatten to key/value pairs.
    rows = [["key", "value"]]
    _flatten(data, "", rows)
    return [Sheet("Sheet1", rows)]


def _scalar(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def _flatten(v: Any, prefix: str, rows: list) -> None:
    if isinstance(v, dict):
        for k, x in v.items():
            _flatten(x, f"{prefix}.{k}" if prefix else str(k), rows)
    elif isinstance(v, list):
        for i, x in enumerate(v):
            _flatten(x, f"{prefix}[{i}]", rows)
    else:
        rows.append([prefix, v])


# ------------------------------------------------------------------ writers
def _safe_title(name: str, used: set[str]) -> str:
    t = re.sub(r"[\[\]:*?/\\]", "_", name or "Sheet").strip("'")[:31] or "Sheet"
    base, i = t, 2
    while t.lower() in used:
        suffix = f" ({i})"
        t = base[: 31 - len(suffix)] + suffix
        i += 1
    used.add(t.lower())
    return t


def write_xlsx(wb: Workbook, dst: Path) -> None:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    book = openpyxl.Workbook()
    book.remove(book.active)
    used: set[str] = set()
    head_font = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="F1F1F4")
    for sheet in wb or [Sheet("Sheet1")]:
        ws = book.create_sheet(_safe_title(sheet.name, used))
        for r in sheet.rows:
            ws.append([_xlsx_value(v) for v in r])
        if sheet.rows and len(sheet.rows) > 1 and all(isinstance(v, str) and v for v in sheet.rows[0]):
            for c in ws[1]:
                c.font, c.fill = head_font, head_fill
                c.alignment = Alignment(vertical="center")
            ws.freeze_panes = "A2"
        widths: dict[int, int] = {}
        for r in sheet.rows[:500]:
            for i, v in enumerate(r):
                widths[i] = max(widths.get(i, 0), min(60, len(cell_text(v))))
        for i, w in widths.items():
            ws.column_dimensions[get_column_letter(i + 1)].width = max(8, w + 2)
    book.save(str(dst))


def _xlsx_value(v: Any) -> Any:
    if isinstance(v, str):
        v = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", v)
        return _coerce(v) if v.strip() else None
    if isinstance(v, (dt.time,)) or v is None or isinstance(v, (int, float, bool, dt.date)):
        return v
    return str(v)


def write_csv(wb: Workbook, dst: Path, delimiter: str = ",") -> list[Path]:
    targets = []
    sheets = [s for s in wb if s.rows] or wb[:1]
    for s in sheets:
        safe = re.sub(r'[<>:"/\\|?*]', "_", s.name)
        p = dst if len(sheets) == 1 else dst.with_name(f"{dst.stem} - {safe}{dst.suffix}")
        with open(p, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=delimiter)
            for r in s.rows:
                w.writerow([cell_text(v) for v in r])
        targets.append(p)
    return targets


def write_json(wb: Workbook, dst: Path) -> None:
    def records(s: Sheet):
        rows = s.rows
        if len(rows) > 1 and all(isinstance(h, str) and h for h in rows[0]) and len(set(rows[0])) == len(rows[0]):
            head = rows[0]
            return [{h: _json_value(r[i] if i < len(r) else None) for i, h in enumerate(head)} for r in rows[1:]]
        return [[_json_value(v) for v in r] for r in rows]

    sheets = [s for s in wb if s.rows] or wb[:1]
    data = records(sheets[0]) if len(sheets) == 1 else {s.name: records(s) for s in sheets}
    dst.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _json_value(v: Any) -> Any:
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v
