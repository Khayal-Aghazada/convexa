"""Locate installed Windows fonts so PDF output can use real typefaces."""
from __future__ import annotations

import functools
import os
import re
from pathlib import Path

import pymupdf

WIN_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
USER_FONTS = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"

# Theme font aliases used by Office documents.
ALIASES = {
    "+mj-lt": "Calibri Light", "+mn-lt": "Calibri",
    "+mj-ea": "Calibri Light", "+mn-ea": "Calibri",
    "+mj-cs": "Calibri Light", "+mn-cs": "Calibri",
}
FALLBACK_BODY = ["Segoe UI", "Calibri", "Arial"]
FALLBACK_MONO = ["Cascadia Mono", "Consolas", "Courier New"]


@functools.lru_cache(maxsize=1)
def font_table() -> dict[str, Path]:
    """Map lower-case face name (e.g. 'calibri bold') -> font file."""
    table: dict[str, Path] = {}
    try:
        import winreg
    except ImportError:  # pragma: no cover - non-Windows
        return table
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts", WIN_FONTS),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts", USER_FONTS),
    ]
    for hive, sub, base in keys:
        try:
            with winreg.OpenKey(hive, sub) as k:
                i = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    i += 1
                    path = Path(value) if os.path.isabs(value) else base / value
                    if path.suffix.lower() not in (".ttf", ".otf", ".ttc") or not path.exists():
                        continue
                    name = re.sub(r"\s*\((TrueType|OpenType)\)\s*$", "", name, flags=re.I)
                    for part in name.split(" & "):
                        table.setdefault(part.strip().lower(), path)
        except OSError:
            continue
    return table


def _face(family: str, style: str) -> Path | None:
    t = font_table()
    f = family.lower()
    candidates = {
        "regular": [f, f + " regular"],
        "bold": [f + " bold"],
        "italic": [f + " italic"],
        "bolditalic": [f + " bold italic"],
    }[style]
    for c in candidates:
        if c in t:
            return t[c]
    return None


def resolve_family(name: str | None, fallbacks: list[str] = FALLBACK_BODY) -> str | None:
    if name:
        name = ALIASES.get(name, name)
        if _face(name, "regular"):
            return name
    for fb in fallbacks:
        if _face(fb, "regular"):
            return fb
    return None


def css_alias(family: str) -> str:
    return "F_" + re.sub(r"[^A-Za-z0-9]", "_", family)


class FontKit:
    """Collects @font-face rules + an Archive that can serve the font files."""

    def __init__(self) -> None:
        self.archive = pymupdf.Archive()
        self._dirs: set[Path] = set()
        self._families: dict[str, str] = {}
        self.rules: list[str] = []

    def add_dir(self, d: Path) -> None:
        d = Path(d)
        if d not in self._dirs and d.is_dir():
            self.archive.add(str(d))
            self._dirs.add(d)

    def family(self, name: str | None, mono: bool = False) -> str:
        """Return a CSS font-family value for the requested family."""
        fam = resolve_family(name, FALLBACK_MONO if mono else FALLBACK_BODY)
        if not fam:
            return "monospace" if mono else "sans-serif"
        if fam in self._families:
            return self._families[fam]
        alias = css_alias(fam)
        regular = _face(fam, "regular")
        for style, weight, slant in (("regular", "normal", "normal"), ("bold", "bold", "normal"),
                                     ("italic", "normal", "italic"), ("bolditalic", "bold", "italic")):
            path = _face(fam, style) or (regular if style == "regular" else None)
            if not path:
                continue
            self.add_dir(path.parent)
            self.rules.append(
                f"@font-face {{font-family: {alias}; font-weight: {weight}; font-style: {slant}; "
                f"src: url({path.name});}}")
        self._families[fam] = alias
        return alias

    @property
    def css(self) -> str:
        return "\n".join(self.rules)
