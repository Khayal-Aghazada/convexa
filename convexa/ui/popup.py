"""The conversion popup: drop/pick files, choose a format, done."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (QEasingCurve, QPoint, QPropertyAnimation, QRect, QSize, Qt, QThread, QTimer,
                            QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter
from PySide6.QtWidgets import (QAbstractButton, QFileDialog, QFrame, QGraphicsDropShadowEffect, QGridLayout,
                               QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout,
                               QWidget)

from .. import APP_NAME, engine, system
from . import logo, theme

SHADOW = 18
WIDTH = 452
CHIP_COLS = 4


def category_of(ext: str) -> str:
    ext = engine.norm_ext(ext)
    if ext == "pdf":
        return "pdf"
    if ext in engine.FORMATS:
        return engine.FORMATS[ext][1]
    if ext in ("xls",):
        return "spreadsheet"
    if ext in ("svg",):
        return "image"
    return "other"


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


# ------------------------------------------------------------------ widgets
class IconLabel(QLabel):
    def __init__(self, name: str, size: int = 14, color: str | None = None):
        super().__init__(theme.glyph(name))
        self.setFont(theme.icon_font(size))
        if color:
            self.setStyleSheet(f"color: {color};")


class IconButton(QPushButton):
    def __init__(self, name: str, tip: str = "", size: int = 11):
        super().__init__(theme.glyph(name))
        self.setObjectName("icon")
        self.setFont(theme.icon_font(size))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tip)


class Badge(QWidget):
    """Rounded tile with the file extension."""

    def __init__(self, ext: str, size: int = 36):
        super().__init__()
        self.ext = ext.upper()[:4]
        self.color = QColor(theme.CATEGORY_COLORS[category_of(ext)])
        self.setFixedSize(size, size)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(self.color)
        bg.setAlphaF(0.15)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(self.rect(), 9, 9)
        f = QFont(theme.ui_font_family())
        f.setPixelSize(10 if len(self.ext) <= 3 else 9)
        f.setBold(True)
        p.setFont(f)
        p.setPen(self.color)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.ext)


class Toggle(QAbstractButton):
    """Small animated switch."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(34, 20)
        self._pos = 0.0
        self._anim = QVariantAnimation(self, duration=140, easingCurve=QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_pos)
        self.toggled.connect(self._animate)

    def _set_pos(self, v):
        self._pos = float(v)
        self.update()

    def _animate(self, on: bool):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def paintEvent(self, _):
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        off, on = QColor(pal.border), QColor(pal.accent)
        c = QColor(
            int(off.red() + (on.red() - off.red()) * self._pos),
            int(off.green() + (on.green() - off.green()) * self._pos),
            int(off.blue() + (on.blue() - off.blue()) * self._pos))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(self.rect(), 10, 10)
        p.setBrush(QColor("#FFFFFF"))
        x = 3 + self._pos * (self.width() - 20)
        p.drawEllipse(int(x), 3, 14, 14)


class ProgressLine(QWidget):
    """Thin progress bar with an indeterminate mode."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(3)
        self.value = 0.0
        self.busy = False
        self._phase = 0.0
        self._anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=1100, loopCount=-1)
        self._anim.valueChanged.connect(self._tick)

    def _tick(self, v):
        self._phase = float(v)
        self.update()

    def set_busy(self, busy: bool):
        self.busy = busy
        (self._anim.start if busy else self._anim.stop)()
        self.update()

    def set_value(self, v: float):
        self.value = max(0.0, min(1.0, v))
        self.update()

    def paintEvent(self, _):
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(pal.surface))
        p.drawRoundedRect(self.rect(), 1.5, 1.5)
        p.setBrush(QColor(pal.accent))
        w = self.width()
        if self.busy and self.value <= 0:
            seg = w * 0.3
            x = -seg + (w + seg) * self._phase
            p.drawRoundedRect(QRect(int(x), 0, int(seg), 3), 1.5, 1.5)
        elif self.value > 0:
            p.drawRoundedRect(QRect(0, 0, int(w * self.value), 3), 1.5, 1.5)


class FormatChip(QPushButton):
    def __init__(self, ext: str):
        super().__init__()
        self.ext = ext
        self.setObjectName("chip")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(54)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        label = engine.FORMATS[ext][0]
        self.setToolTip(f"Convert to {label} (.{ext})")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 10, 8)
        lay.setSpacing(1)
        top = QHBoxLayout()
        top.setSpacing(6)
        dot = QLabel()
        dot.setFixedSize(6, 6)
        dot.setStyleSheet(f"background:{theme.CATEGORY_COLORS[category_of(ext)]}; border-radius:3px;")
        name = QLabel(ext.upper())
        f = name.font()
        f.setPixelSize(13)
        f.setWeight(QFont.Weight.DemiBold)
        name.setFont(f)
        top.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(name)
        top.addStretch()
        sub = QLabel(label)
        sub.setObjectName("sub")
        sub.setStyleSheet("font-size: 11px;")
        for w in (name, sub, dot):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addLayout(top)
        lay.addWidget(sub)


class FileRow(QFrame):
    removed = Signal(Path)

    def __init__(self, path: Path):
        super().__init__()
        self.path = path
        self.setObjectName("fileRow")
        self.setFixedHeight(48)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(10)
        lay.addWidget(Badge(path.suffix.lstrip(".") or "?"))
        col = QVBoxLayout()
        col.setSpacing(0)
        name = QLabel()
        f = name.font()
        f.setPixelSize(13)
        f.setWeight(QFont.Weight.Medium)
        name.setFont(f)
        name.setText(QFontMetrics(f).elidedText(path.name, Qt.TextElideMode.ElideMiddle, WIDTH - 150))
        name.setToolTip(str(path))
        try:
            size = human_size(path.stat().st_size)
        except OSError:
            size = ""
        meta = QLabel(f"{size}  ·  {path.parent.name or path.anchor}")
        meta.setObjectName("sub")
        col.addWidget(name)
        col.addWidget(meta)
        lay.addLayout(col, 1)
        self.rm = IconButton("close", "Remove")
        self.rm.setVisible(False)
        self.rm.clicked.connect(lambda: self.removed.emit(self.path))
        lay.addWidget(self.rm)

    def enterEvent(self, e):
        self.rm.setVisible(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.rm.setVisible(False)
        super().leaveEvent(e)


class DropZone(QFrame):
    clicked = Signal()

    def __init__(self, hotkey: str):
        super().__init__()
        self.setObjectName("drop")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(168)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(6)
        pal = theme.palette()
        ic = IconLabel("upload", 22, pal.accent)
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t = QLabel("Drop files here")
        t.setObjectName("dropTitle")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint = QLabel()
        self.hint.setObjectName("muted")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_hotkey(hotkey)
        lay.addWidget(ic)
        lay.addSpacing(2)
        lay.addWidget(t)
        lay.addWidget(self.hint)

    def set_hotkey(self, hotkey: str):
        self.hint.setText(f"or click to browse\nSelect files in Explorer and press {hotkey}")

    def set_hover(self, on: bool):
        self.setProperty("hover", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


# ------------------------------------------------------------------- worker
class Worker(QThread):
    progress = Signal(int, int, str)
    done = Signal(list, list)  # outputs: list[Path], errors: list[tuple[str, str]]

    def __init__(self, files: list[Path], fmt: str, out_dir: Path | None, combine: bool):
        super().__init__()
        self.files, self.fmt, self.out_dir, self.combine = files, fmt, out_dir, combine

    def run(self):
        outputs, errors = [], []
        if self.combine:
            self.progress.emit(0, 1, f"{len(self.files)} files")
            try:
                outputs += engine.combine(self.files, self.fmt, self.out_dir).outputs
            except Exception as e:
                errors.append(("Combine", str(e)))
        else:
            total = len(self.files)
            for i, f in enumerate(self.files):
                self.progress.emit(i, total, f.name)
                try:
                    outputs += engine.convert(f, self.fmt, self.out_dir).outputs
                except Exception as e:
                    errors.append((f.name, str(e)))
        self.done.emit(outputs, errors)


# -------------------------------------------------------------------- popup
class Popup(QWidget):
    settings_changed = Signal()

    def __init__(self, settings: dict):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.settings = settings
        self.files: list[Path] = []
        self.worker: Worker | None = None
        self.last_outputs: list[Path] = []
        self._drag_pos: QPoint | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAcceptDrops(True)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(logo.icon())
        self.setFixedWidth(WIDTH + 2 * SHADOW)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW, SHADOW - 6, SHADOW, SHADOW + 6)
        self.card = QWidget(objectName="card")
        self.card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        outer.addWidget(self.card)
        self.shadow = QGraphicsDropShadowEffect(self.card, blurRadius=36, offset=QPoint(0, 8))
        self.card.setGraphicsEffect(self.shadow)

        self.body = QVBoxLayout(self.card)
        self.body.setContentsMargins(18, 14, 18, 16)
        self.body.setSpacing(12)

        # Header
        self.header = QWidget()
        h = QHBoxLayout(self.header)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.logo_lbl = QLabel()
        self.logo_lbl.setPixmap(logo.pixmap(20, self.devicePixelRatioF()))
        title = QLabel(APP_NAME, objectName="title")
        h.addWidget(self.logo_lbl)
        h.addWidget(title)
        h.addStretch()
        self.add_btn = IconButton("add", "Add files")
        self.add_btn.clicked.connect(self.browse)
        self.close_btn = IconButton("close", "Close (Esc)")
        self.close_btn.clicked.connect(self.hide_animated)
        h.addWidget(self.add_btn)
        h.addWidget(self.close_btn)
        self.body.addWidget(self.header)

        # Drop zone / file list
        self.drop = DropZone(settings.get("hotkey", ""))
        self.drop.clicked.connect(self.browse)
        self.body.addWidget(self.drop)

        self.list_area = QScrollArea()
        self.list_area.setWidgetResizable(True)
        self.list_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_host = QWidget()
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(2)
        self.list_area.setWidget(self.list_host)
        self.body.addWidget(self.list_area)

        # Formats
        self.section = QLabel("CONVERT TO", objectName="section")
        self.body.addWidget(self.section)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        self.body.addWidget(self.grid_host)
        self.empty_note = QLabel("These files have no common output format.", objectName="muted")
        self.body.addWidget(self.empty_note)

        # Options row
        self.options = QWidget()
        o = QHBoxLayout(self.options)
        o.setContentsMargins(0, 2, 0, 0)
        o.setSpacing(8)
        self.combine = Toggle()
        self.combine_lbl = QLabel("Combine into one file", objectName="muted")
        o.addWidget(self.combine)
        o.addWidget(self.combine_lbl)
        o.addStretch()
        self.dest_btn = QPushButton(objectName="link")
        self.dest_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dest_btn.clicked.connect(self.dest_menu)
        o.addWidget(self.dest_btn)
        self.body.addWidget(self.options)

        # Status
        self.progress = ProgressLine()
        self.body.addWidget(self.progress)
        st = QHBoxLayout()
        st.setSpacing(6)
        self.status_icon = QLabel()
        self.status_icon.setFont(theme.icon_font(12))
        self.status = QLabel(objectName="status")
        self.status.setWordWrap(True)
        self.open_btn = QPushButton("Show in folder", objectName="primary")
        self.open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_btn.clicked.connect(self.reveal)
        st.addWidget(self.status_icon, 0, Qt.AlignmentFlag.AlignTop)
        st.addWidget(self.status, 1)
        st.addWidget(self.open_btn)
        self.status_row = QWidget()
        self.status_row.setLayout(st)
        st.setContentsMargins(0, 0, 0, 0)
        self.body.addWidget(self.status_row)

        self.fade = QPropertyAnimation(self, b"windowOpacity", duration=160)
        self.slide = QPropertyAnimation(self, b"pos", duration=200,
                                        easingCurve=QEasingCurve.Type.OutCubic)
        self.apply_theme()
        self.clear_status()
        self.refresh()

    # ---------------------------------------------------------------- theme
    def apply_theme(self):
        pal = theme.palette()
        self.setStyleSheet(theme.stylesheet(pal))
        self.shadow.setColor(QColor(0, 0, 0, pal.shadow))
        self.update_dest_label()
        # rebuild pieces that captured colors
        old = self.drop
        self.drop = DropZone(self.settings.get("hotkey", ""))
        self.drop.clicked.connect(self.browse)
        self.body.replaceWidget(old, self.drop)
        old.deleteLater()
        if hasattr(self, "grid"):
            self.refresh()

    # ---------------------------------------------------------------- files
    def set_files(self, paths: list[Path], replace: bool = True):
        if self.busy:
            return
        if replace:
            self.files = []
        skipped = 0
        for p in self._expand(paths):
            if p in self.files:
                continue
            if engine.targets_for(p):
                self.files.append(p)
            else:
                skipped += 1
        self.clear_status()
        self.refresh()
        if skipped:
            self.set_status("error", f"Skipped {skipped} unsupported file{'s' if skipped > 1 else ''}.")

    @staticmethod
    def _expand(paths: list[Path]) -> list[Path]:
        out = []
        for p in paths:
            p = Path(p)
            if p.is_dir():
                out += sorted(x for x in p.iterdir() if x.is_file())
            elif p.is_file():
                out.append(p)
        return out

    def remove_file(self, path: Path):
        if self.busy:
            return
        self.files = [f for f in self.files if f != path]
        self.clear_status()
        self.refresh()

    def browse(self):
        if self.busy:
            return
        start = str(self.files[0].parent) if self.files else str(Path.home())
        exts = sorted(set(engine.FORMATS) | {"xls", "xlsm", "htm", "jpeg", "tif", "svg", "markdown"})
        filt = "Supported files (" + " ".join(f"*.{e}" for e in exts) + ");;All files (*)"
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        self.show()
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose files to convert", start, filt)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.show()
        self.activate()
        if paths:
            self.set_files([Path(p) for p in paths], replace=False)

    # -------------------------------------------------------------- refresh
    @property
    def busy(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def targets(self) -> list[str]:
        t = engine.common_targets(self.files)
        if len(self.files) > 1 and "pdf" not in t and engine.can_combine(self.files, "pdf"):
            t.insert(0, "pdf")  # e.g. several PDFs -> merge
        return t

    def refresh(self):
        has = bool(self.files)
        self.drop.setVisible(not has)
        self.list_area.setVisible(has)
        self.add_btn.setVisible(has)

        while self.list_lay.count():
            w = self.list_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        for f in self.files:
            row = FileRow(f)
            row.removed.connect(self.remove_file)
            self.list_lay.addWidget(row)
        self.list_lay.addStretch()
        rows = min(len(self.files), 4)
        self.list_area.setFixedHeight(rows * 48 + max(0, rows - 1) * 2)

        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        targets = self.targets() if has else []
        order = {c: i for i, c in enumerate(["pdf", "document", "spreadsheet", "presentation", "image"])}
        targets.sort(key=lambda e: (order.get(category_of(e), 9), list(engine.FORMATS).index(e)))
        for i, ext in enumerate(targets):
            chip = FormatChip(ext)
            chip.clicked.connect(lambda _=False, e=ext: self.start(e))
            self.grid.addWidget(chip, i // CHIP_COLS, i % CHIP_COLS)
        for c in range(CHIP_COLS):
            self.grid.setColumnStretch(c, 1)
        self.section.setVisible(has and bool(targets))
        self.grid_host.setVisible(has and bool(targets))
        self.empty_note.setVisible(has and not targets)

        combinable = len(self.files) > 1 and (engine.can_combine(self.files, "pdf")
                                                 or engine.can_combine(self.files, "pptx"))
        self.combine.setVisible(combinable)
        self.combine_lbl.setVisible(combinable)
        self.combine_lbl.setText("Combine into one PDF" if not engine.can_combine(self.files, "pptx")
                                 else "Combine into one PDF / PowerPoint")
        self.options.setVisible(has)
        self.update_dest_label()
        self.card.adjustSize()
        self.adjustSize()

    # ---------------------------------------------------------- destination
    def update_dest_label(self):
        d = self.settings.get("output_dir") or ""
        name = (Path(d).name or d) if d else "same folder"
        self.dest_btn.setText(f"Save to {name}  ▾")
        self.dest_btn.setToolTip(d or "Next to each original file")

    def dest_menu(self):
        m = QMenu(self)
        m.setWindowFlags(m.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        m.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        same = m.addAction("Same folder as original")
        same.setCheckable(True)
        same.setChecked(not self.settings.get("output_dir"))
        custom = self.settings.get("output_dir")
        if custom:
            cur = m.addAction(Path(custom).name or custom)
            cur.setCheckable(True)
            cur.setChecked(True)
            cur.setEnabled(False)
        m.addSeparator()
        choose = m.addAction("Choose folder…")
        act = m.exec(self.dest_btn.mapToGlobal(QPoint(0, self.dest_btn.height() + 4)))
        if act == same:
            self.settings["output_dir"] = ""
        elif act == choose:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
            self.show()
            d = QFileDialog.getExistingDirectory(self, "Save converted files to", custom or str(Path.home()))
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            self.show()
            self.activate()
            if d:
                self.settings["output_dir"] = d
        else:
            return
        self.settings_changed.emit()
        self.update_dest_label()

    # ----------------------------------------------------------- conversion
    def start(self, fmt: str):
        if self.busy or not self.files:
            return
        combine = self.combine.isVisible() and self.combine.isChecked() and engine.can_combine(self.files, fmt)
        files = list(self.files)
        if fmt == "pdf" and not combine and any(engine.norm_ext(f) == "pdf" for f in files):
            if len(files) > 1 and engine.can_combine(files, "pdf") and "pdf" not in engine.common_targets(files):
                combine = True  # merging PDFs is the only meaningful action
        out_dir = Path(self.settings["output_dir"]) if self.settings.get("output_dir") else None
        self.worker = Worker(files, fmt, out_dir, combine)
        self.worker.progress.connect(self.on_progress)
        self.worker.done.connect(self.on_done)
        for i in range(self.grid.count()):
            self.grid.itemAt(i).widget().setEnabled(False)
        self.progress.set_value(0)
        self.progress.set_busy(True)
        self.set_status("", f"Converting to {fmt.upper()}…")
        self.worker.start()

    def on_progress(self, i: int, total: int, name: str):
        if total > 1:
            self.progress.set_value(i / total if i else 0)
            self.set_status("", f"Converting {i + 1} of {total} · {name}")
        else:
            self.set_status("", f"Converting {name}…")

    def on_done(self, outputs: list, errors: list):
        self.progress.set_busy(False)
        self.progress.set_value(0)
        for i in range(self.grid.count()):
            self.grid.itemAt(i).widget().setEnabled(True)
        self.last_outputs = outputs
        if errors and not outputs:
            name, msg = errors[0]
            more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
            self.set_status("error", f"{name}: {msg}{more}")
            self.status.setToolTip("\n".join(f"{n}: {m}" for n, m in errors))
        elif errors:
            self.set_status("error", f"Converted {len(outputs)} · {len(errors)} failed")
            self.status.setToolTip("\n".join(f"{n}: {m}" for n, m in errors))
            self.open_btn.setVisible(True)
        else:
            what = outputs[0].name if len(outputs) == 1 else f"{len(outputs)} files"
            self.set_status("ok", f"Saved {what}")
            self.status.setToolTip("\n".join(str(o) for o in outputs))
            self.open_btn.setVisible(True)
            if self.settings.get("open_folder_after") and outputs:
                self.reveal()
        if self.worker is not None:
            self.worker.wait(5000)  # run() is returning; never drop a live QThread
        self.worker = None
        self.adjustSize()

    def set_status(self, state: str, text: str):
        pal = theme.palette()
        self.status_row.setVisible(True)
        self.status.setText(text)
        self.status.setToolTip("")
        self.status.setProperty("state", state)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        icon = {"ok": ("check", pal.success), "error": ("error", pal.danger)}.get(state)
        self.status_icon.setVisible(bool(icon))
        if icon:
            self.status_icon.setText(theme.glyph(icon[0]))
            self.status_icon.setStyleSheet(f"color:{icon[1]}; padding-top: 1px;")
        self.open_btn.setVisible(False)
        self.progress.setVisible(state == "")
        self.adjustSize()

    def clear_status(self):
        self.status_row.setVisible(False)
        self.progress.setVisible(False)
        self.last_outputs = []

    def reveal(self):
        if self.last_outputs:
            system.open_in_explorer(self.last_outputs[0])

    # --------------------------------------------------------- show / hide
    def show_animated(self):
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        self.adjustSize()
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.y() + int(geo.height() * 0.28) - SHADOW
        target = QPoint(x, y)
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.move(target + QPoint(0, 10))
            self.show()
            self.fade.stop()
            self.fade.setStartValue(0.0)
            self.fade.setEndValue(1.0)
            self.fade.start()
            self.slide.stop()
            self.slide.setStartValue(target + QPoint(0, 10))
            self.slide.setEndValue(target)
            self.slide.start()
        self.activate()

    def activate(self):
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(60, self._force_foreground)

    def _force_foreground(self):
        try:
            import ctypes
            u = ctypes.windll.user32
            hwnd = int(self.winId())
            if u.GetForegroundWindow() != hwnd:
                fg = u.GetForegroundWindow()
                cur = ctypes.windll.kernel32.GetCurrentThreadId()
                other = u.GetWindowThreadProcessId(fg, None)
                u.AttachThreadInput(other, cur, True)
                u.SetForegroundWindow(hwnd)
                u.AttachThreadInput(other, cur, False)
        except Exception:
            pass

    def hide_animated(self):
        if not self.isVisible():
            return
        self.fade.stop()
        self.fade.setStartValue(self.windowOpacity())
        self.fade.setEndValue(0.0)
        try:
            self.fade.finished.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.fade.finished.connect(self._after_fade)
        self.fade.start()

    def _after_fade(self):
        try:
            self.fade.finished.disconnect()
        except (RuntimeError, TypeError):
            pass
        if self.windowOpacity() < 0.05:
            self.hide()
            if not self.busy and self.status.property("state") in ("ok",):
                # start fresh next time after a successful run
                self.files = []
                self.clear_status()
                self.refresh()

    def toggle(self):
        if self.isVisible() and self.windowOpacity() > 0.5:
            self.hide_animated()
        else:
            self.show_animated()

    # -------------------------------------------------------------- events
    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.hide_animated()
        elif e.key() == Qt.Key.Key_O and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.browse()
        else:
            super().keyPressEvent(e)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and not self.busy:
            e.acceptProposedAction()
            self.drop.set_hover(True)

    def dragLeaveEvent(self, e):
        self.drop.set_hover(False)

    def dropEvent(self, e):
        self.drop.set_hover(False)
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.set_files(paths, replace=False)
            self.activate()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < SHADOW + 50:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    def sizeHint(self):
        return QSize(WIDTH + 2 * SHADOW, super().sizeHint().height())
