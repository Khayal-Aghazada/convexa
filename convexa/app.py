"""Tray application: single instance, global hotkey, right-click integration."""
from __future__ import annotations

import ctypes
import getpass
import json
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFont, QKeySequence
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QKeySequenceEdit, QLabel, QMenu,
                               QPushButton, QSystemTrayIcon, QVBoxLayout, QWidget)

from . import APP_ID, APP_NAME, system
from .ui import logo, theme
from .ui.popup import Popup

SERVER_NAME = f"{APP_NAME}-{getpass.getuser()}"
MUTEX_NAME = f"Local\\{SERVER_NAME}-mutex"


# --------------------------------------------------------- single instance
def acquire_mutex() -> bool:
    """True if this process is the first (primary) instance."""
    k = ctypes.windll.kernel32
    acquire_mutex.handle = k.CreateMutexW(None, False, MUTEX_NAME)  # keep handle alive
    return k.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def send_to_primary(payload: dict, attempts: int = 30) -> bool:
    for _ in range(attempts):
        s = QLocalSocket()
        s.connectToServer(SERVER_NAME)
        if s.waitForConnected(200):
            s.write(json.dumps(payload).encode("utf-8"))
            s.flush()
            s.waitForBytesWritten(1000)
            s.disconnectFromServer()
            return True
        QApplication.processEvents()
        ctypes.windll.kernel32.Sleep(100)
    return False


class Bridge(QObject):
    hotkey = Signal(list)


# ------------------------------------------------------------ hotkey dialog
class HotkeyDialog(QDialog):
    def __init__(self, current: str):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(theme.stylesheet(theme.palette()))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QWidget(objectName="card")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)
        lay.addWidget(QLabel("Change shortcut", objectName="title"))
        lay.addWidget(QLabel("Press the new key combination.", objectName="muted"))
        self.edit = QKeySequenceEdit(QKeySequence(current))
        self.edit.setMaximumSequenceLength(1)
        lay.addWidget(self.edit)
        self.err = QLabel("", objectName="status")
        self.err.setProperty("state", "error")
        lay.addWidget(self.err)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Cancel", objectName="link")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Save", objectName="primary")
        ok.clicked.connect(self.check)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        self.setFixedWidth(320)
        self.value = current

    def check(self):
        text = self.edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        text = text.replace("Meta", "Win")
        try:
            mods, _ = system.parse_hotkey(text)
            if not mods:
                raise ValueError("Use at least one modifier (Ctrl, Alt, Shift or Win)")
        except ValueError as e:
            self.err.setText(str(e))
            return
        self.value = text
        self.accept()


# -------------------------------------------------------------------- app
class ConvexaApp(QObject):
    def __init__(self, app: QApplication, first_files: list[Path], tray_only: bool):
        super().__init__()
        self.app = app
        self.settings = system.load_settings()
        logo.ensure_ico()

        self.popup = Popup(self.settings)
        self.popup.settings_changed.connect(lambda: system.save_settings(self.settings))

        # IPC
        self.server = QLocalServer(self)
        QLocalServer.removeServer(SERVER_NAME)
        self.server.listen(SERVER_NAME)
        self.server.newConnection.connect(self._on_connection)
        self._batch: list[Path] = []
        self._batch_timer = QTimer(self, singleShot=True, interval=250)
        self._batch_timer.timeout.connect(self._flush_batch)

        # Hotkey
        self.bridge = Bridge()
        self.bridge.hotkey.connect(self.on_hotkey)
        self.hotkey = None
        self.register_hotkey()

        # Tray
        self.tray = QSystemTrayIcon(logo.icon(), self)
        self.tray.activated.connect(self._on_tray)
        self.build_menu()
        self.tray.show()

        # Theme changes
        try:
            app.styleHints().colorSchemeChanged.connect(self.on_theme)
        except Exception:
            pass

        self.first_run()
        if first_files:
            self.popup.set_files(first_files)
            self.popup.show_animated()
        elif not tray_only:
            self.popup.show_animated()

    # ------------------------------------------------------------ setup
    def first_run(self):
        if not self.settings.get("first_run_done"):
            system.set_autostart(True)
            system.set_context_menu(True)
            system.create_start_menu_shortcut()
            self.settings["first_run_done"] = True
            system.save_settings(self.settings)
            QTimer.singleShot(800, lambda: self.tray.showMessage(
                f"{APP_NAME} is ready",
                f"Select files in Explorer and press {self.settings['hotkey']}, "
                f"or right-click → Convert with {APP_NAME}.",
                logo.icon(), 6000))
        else:
            # keep registry commands pointing at this install location
            if system.autostart_enabled():
                system.set_autostart(True)
            if system.context_menu_enabled():
                system.set_context_menu(True)

    def register_hotkey(self) -> bool:
        if self.hotkey:
            self.hotkey.stop()
        combo = self.settings.get("hotkey") or "Ctrl+Alt+C"
        self.hotkey = system.GlobalHotkey(combo, lambda: self.bridge.hotkey.emit(system.explorer_selection()))
        ok = self.hotkey.start_and_wait()
        if not ok and hasattr(self, "tray"):
            self.tray.showMessage(APP_NAME, f"{combo} is used by another app. Pick another shortcut from the tray menu.",
                                  logo.icon(), 5000)
        return ok

    def build_menu(self):
        m = QMenu()
        m.setWindowFlags(m.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        m.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        m.setStyleSheet(theme.stylesheet(theme.palette()))

        open_act = m.addAction(f"Open {APP_NAME}")
        open_act.triggered.connect(self.popup.show_animated)
        f = QFont(open_act.font())
        f.setBold(True)
        open_act.setFont(f)
        m.addAction("Convert files…").triggered.connect(self.pick_files)
        m.addSeparator()

        self.act_auto = QAction("Start with Windows", m, checkable=True)
        self.act_auto.setChecked(system.autostart_enabled())
        self.act_auto.toggled.connect(system.set_autostart)
        m.addAction(self.act_auto)

        self.act_ctx = QAction("Show in right-click menu", m, checkable=True)
        self.act_ctx.setChecked(system.context_menu_enabled())
        self.act_ctx.toggled.connect(system.set_context_menu)
        m.addAction(self.act_ctx)

        self.act_open = QAction("Open folder when done", m, checkable=True)
        self.act_open.setChecked(bool(self.settings.get("open_folder_after")))
        self.act_open.toggled.connect(self._set_open_after)
        m.addAction(self.act_open)

        m.addSeparator()
        self.act_hotkey = m.addAction(f"Shortcut: {self.settings['hotkey']}")
        self.act_hotkey.triggered.connect(self.change_hotkey)
        m.addSeparator()
        m.addAction("Quit").triggered.connect(self.quit)
        self.menu = m
        self.tray.setContextMenu(m)
        self.tray.setToolTip(f"{APP_NAME} · {self.settings['hotkey']}")

    def _set_open_after(self, on: bool):
        self.settings["open_folder_after"] = on
        system.save_settings(self.settings)

    # ---------------------------------------------------------- actions
    def _on_tray(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.popup.toggle()

    def pick_files(self):
        self.popup.show_animated()
        QTimer.singleShot(150, self.popup.browse)

    def on_hotkey(self, paths: list):
        if paths:
            self.popup.set_files([Path(p) for p in paths])
            self.popup.show_animated()
        else:
            self.popup.toggle()

    def change_hotkey(self):
        dlg = HotkeyDialog(self.settings["hotkey"])
        dlg.activateWindow()
        if dlg.exec():
            old = self.settings["hotkey"]
            self.settings["hotkey"] = dlg.value
            if not self.register_hotkey():
                self.settings["hotkey"] = old
                self.register_hotkey()
            system.save_settings(self.settings)
            self.act_hotkey.setText(f"Shortcut: {self.settings['hotkey']}")
            self.tray.setToolTip(f"{APP_NAME} · {self.settings['hotkey']}")
            self.popup.drop.set_hotkey(self.settings["hotkey"])

    def on_theme(self, *_):
        self.popup.apply_theme()
        self.menu.setStyleSheet(theme.stylesheet(theme.palette()))

    def quit(self):
        if self.hotkey:
            self.hotkey.stop()
        self.tray.hide()
        self.app.quit()

    # -------------------------------------------------------------- IPC
    def _on_connection(self):
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            buf = bytearray()

            def read(s=sock, b=buf):
                b.extend(bytes(s.readAll()))

            def finish(s=sock, b=buf):
                b.extend(bytes(s.readAll()))
                s.deleteLater()
                try:
                    self.on_message(json.loads(b.decode("utf-8")))
                except Exception:
                    pass

            sock.readyRead.connect(read)
            sock.disconnected.connect(finish)

    def on_message(self, payload: dict):
        if payload.get("quit"):
            self.quit()
            return
        files = [Path(p) for p in payload.get("files", [])]
        if files:
            if not self._batch_timer.isActive():
                self._batch = []
            self._batch += files
            self._batch_timer.start()
        elif not payload.get("tray"):
            self.popup.show_animated()

    def _flush_batch(self):
        if self._batch:
            self.popup.set_files(self._batch)
            self.popup.show_animated()
        self._batch = []


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    tray_only = "--tray" in argv
    files = [str(Path(a).resolve()) for a in argv if not a.startswith("--")]

    system.set_app_user_model_id(APP_ID)
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    font = QFont(theme.ui_font_family())
    font.setPixelSize(13)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(font)
    app.setWindowIcon(logo.icon())

    if "--uninstall" in argv or "--quit" in argv:
        if "--uninstall" in argv:
            system.remove_integration()
        if not acquire_mutex():
            send_to_primary({"quit": True}, attempts=3)
        return 0

    if not acquire_mutex():
        send_to_primary({"files": files, "tray": tray_only})
        return 0

    _ = ConvexaApp(app, [Path(f) for f in files], tray_only)
    return app.exec()
