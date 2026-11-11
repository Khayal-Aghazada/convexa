"""Command line: python -m convexa.cli FILE [FILE...] --to pdf [--out DIR] [--combine]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import engine


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="convexa", description="Convert files locally.")
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--to", help="target format, e.g. pdf, docx, xlsx, png")
    ap.add_argument("--out", type=Path, help="output folder (default: next to source)")
    ap.add_argument("--combine", action="store_true", help="merge all inputs into one file")
    a = ap.parse_args(argv)
    if not a.to:
        for f in a.files:
            print(f"{f.name}: {', '.join(engine.targets_for(f)) or 'unsupported'}")
        return 0
    if a.combine:
        r = engine.combine(a.files, a.to, a.out)
        print(f"✓ {r.outputs[0]}")
        return 0
    code = 0
    for f in a.files:
        try:
            r = engine.convert(f, a.to, a.out)
            for o in r.outputs:
                print(f"✓ {f.name} → {o}")
        except engine.ConversionError as e:
            print(f"✗ {f.name}: {e}", file=sys.stderr)
            code = 1
    return code


if __name__ == "__main__":
    sys.exit(main())
