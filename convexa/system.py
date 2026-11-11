"""Windows integration: settings, global hotkey, Explorer selection,
autostart, right-click menu and Start-menu shortcut."""
from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Callable

from . import APP_NAME

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ENTRY = ROOT / "convexa.pyw"
DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
SETTINGS_FILE = DATA_DIR / "settings.json"

DEFAULTS = {
    "hotkey": "Ctrl+Alt+C",
    "output_dir": "",          # empty = next to the source file
    "open_folder_after": False,
    "first_run_done": False,
}


# ----------------------------------------------------------------- settings
def load_settings() -> dict:
    data = dict(DEFAULTS)
    try:
        data.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return data


def save_settings(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def pythonw() -> str:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def launch_command(*args: str) -> str:
    return " ".join([f'"{pythonw()}"', f'"{ENTRY}"', *args])


# ------------------------------------------------------------------- hotkey
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
MOD = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
MOD_NOREPEAT = 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
SPECIAL_VK = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "insert": 0x2D, "delete": 0x2E,
              "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22}


def parse_hotkey(text: str) -> tuple[int, int]:
    mods, vk = 0, 0
    for part in [p.strip().lower() for p in text.split("+") if p.strip()]:
        if part in MOD:
            mods |= MOD[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x6F + int(part[1:])
        elif part in SPECIAL_VK:
            vk = SPECIAL_VK[part]
        else:
            raise ValueError(f"Unknown key: {part}")
    if not vk:
        raise ValueError("Hotkey needs a key")
    return mods, vk


class GlobalHotkey(threading.Thread):
    """Registers a system-wide hotkey on its own message-loop thread."""

    def __init__(self, combo: str, callback: Callable[[], None]):
        super().__init__(daemon=True, name="hotkey")
        self.combo, self.callback = combo, callback
        self.ok = False
        self._ready = threading.Event()
        self._tid = 0

    def run(self) -> None:
        self._tid = kernel32.GetCurrentThreadId()
        try:
            mods, vk = parse_hotkey(self.combo)
            self.ok = bool(user32.RegisterHotKey(None, 1, mods | MOD_NOREPEAT, vk))
        except ValueError:
            self.ok = False
        self._ready.set()
        if not self.ok:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                try:
                    self.callback()
                except Exception:
                    pass
        user32.UnregisterHotKey(None, 1)

    def start_and_wait(self) -> bool:
        self.start()
        self._ready.wait(2)
        return self.ok

    def stop(self) -> None:
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)


# --------------------------------------------------------- Explorer selection
def explorer_selection() -> list[Path]:
    """Files selected in the foreground File Explorer window (incl. Win11 tabs)."""
    try:
        import pythoncom
        import win32com.client
        import win32gui
    except ImportError:
        return []
    hwnd = user32.GetForegroundWindow()
    try:
        cls = win32gui.GetClassName(hwnd)
    except Exception:
        return []
    if cls not in ("CabinetWClass", "ExploreWClass"):
        return []
    try:
        pythoncom.CoInitialize()
    except Exception:
        pass
    try:
        shell = win32com.client.Dispatch("Shell.Application")
        windows = []
        for w in shell.Windows():
            try:
                if int(w.HWND) == hwnd:
                    windows.append(w)
            except Exception:
                continue
        if not windows:
            return []
        win = windows[0]
        if len(windows) > 1:  # Windows 11 tabs share one top-level HWND
            active_tab = win32gui.FindWindowEx(hwnd, 0, "ShellTabWindowClass", None)
            win = _match_tab(windows, active_tab) or _match_title(windows, win32gui.GetWindowText(hwnd)) or win
        items = win.Document.SelectedItems()
        out = []
        for i in range(items.Count):
            p = Path(items.Item(i).Path)
            if p.is_file():
                out.append(p)
        return out
    except Exception:
        return []


def _match_tab(windows, tab_hwnd: int):
    if not tab_hwnd:
        return None
    try:
        import pythoncom
        from win32com.shell import shell as wshell  # noqa: F401
        SID_STopLevelBrowser = pythoncom.MakeIID("{4C96BE40-915C-11CF-99D3-00AA004AE837}")
        IID_IShellBrowser = pythoncom.MakeIID("{000214E2-0000-0000-C000-000000000046}")
        for w in windows:
            try:
                sp = w._oleobj_.QueryInterface(pythoncom.IID_IServiceProvider)
                browser = sp.QueryService(SID_STopLevelBrowser, IID_IShellBrowser)
                if browser.GetWindow() == tab_hwnd:
                    return w
            except Exception:
                continue
    except Exception:
        pass
    return None


def _match_title(windows, title: str):
    for w in windows:
        try:
            name = w.LocationName
            if name and title.startswith(name):
                return w
        except Exception:
            continue
    return None


# ---------------------------------------------------------------- registry
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MENU_KEY = rf"Software\Classes\*\shell\{APP_NAME}"


def autostart_enabled() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(on: bool) -> None:
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        if on:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, launch_command("--tray"))
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass


def context_menu_enabled() -> bool:
    import winreg
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, MENU_KEY).Close()
        return True
    except OSError:
        return False


def set_context_menu(on: bool) -> None:
    import winreg
    if on:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, MENU_KEY) as k:
            winreg.SetValueEx(k, "MUIVerb", 0, winreg.REG_SZ, f"Convert with {APP_NAME}")
            winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, str(ASSETS / "logo.ico"))
            winreg.SetValueEx(k, "MultiSelectModel", 0, winreg.REG_SZ, "Player")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, MENU_KEY + r"\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, launch_command('"%1"'))
    else:
        for sub in (MENU_KEY + r"\command", MENU_KEY):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
            except OSError:
                pass
    _notify_shell()


def _notify_shell() -> None:
    try:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
    except Exception:
        pass


def start_menu_shortcut() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP_NAME}.lnk"


def create_start_menu_shortcut() -> None:
    try:
        import win32com.client
        lnk = start_menu_shortcut()
        sh = win32com.client.Dispatch("WScript.Shell").CreateShortcut(str(lnk))
        sh.TargetPath = pythonw()
        sh.Arguments = f'"{ENTRY}"'
        sh.WorkingDirectory = str(ROOT)
        sh.IconLocation = str(ASSETS / "logo.ico")
        sh.Description = "Convert files locally"
        sh.Save()
    except Exception:
        pass


def remove_integration() -> None:
    set_autostart(False)
    set_context_menu(False)
    try:
        start_menu_shortcut().unlink(missing_ok=True)
    except Exception:
        pass


def set_app_user_model_id(app_id: str) -> None:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def open_in_explorer(path: Path) -> None:
    import subprocess
    path = Path(path)
    if path.is_file():
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        os.startfile(str(path))  # noqa: S606
