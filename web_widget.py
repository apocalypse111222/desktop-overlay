"""
AirPortal floating webview panel — subprocess + Windows API show/hide.

pywebview 6.x requires the main thread, which tkinter already owns.
We solve this by spawning a child process (--webview mode) whose main
thread is free for pywebview.

Session is preserved across show/hide cycles: the subprocess is never
killed — only OS window visibility is toggled so the browser keeps its
login cookies and state.
"""
import ctypes
import ctypes.wintypes
import os
import subprocess
import sys
import tkinter as tk

try:
    import webview
    _HAS_WEBVIEW = True
except ImportError:
    _HAS_WEBVIEW = False

_user32 = ctypes.windll.user32

# ShowWindow commands
_SW_HIDE          = 0
_SW_SHOWNOACTIVATE = 4   # show at current pos/size, NO activation flash

# SetWindowPos flags
_SWP_NOSIZE     = 0x0001
_SWP_NOMOVE     = 0x0002
_SWP_NOACTIVATE = 0x0010
_SWP_FLAGS      = _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE


def _find_hwnd_for_pid(pid: int) -> int | None:
    """Return the first visible top-level window belonging to *pid*, or None."""
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool,
                        ctypes.wintypes.HWND,
                        ctypes.wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        proc = ctypes.wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc))
        if proc.value == pid:
            found.append(hwnd)
        return True

    _user32.EnumWindows(_cb, 0)
    return found[0] if found else None


def _get_work_area() -> tuple[int, int, int, int]:
    """Return (left, top, right, bottom) of the primary work area (excludes taskbar)."""
    rc = ctypes.wintypes.RECT()
    _user32.SystemParametersInfoW(48, 0, ctypes.byref(rc), 0)  # SPI_GETWORKAREA
    return rc.left, rc.top, rc.right, rc.bottom


class WebWidget:
    TITLE  = "AirPortal 空投快传"
    URL    = "https://airportal.cn"
    WIDTH  = 600
    HEIGHT = 780

    # Corner-icon clearance: icons are drawn at cy = canvas_h - 28 - 16 = canvas_h - 44
    # Top of icon circle = cy - 28 = canvas_h - 72
    # Leave extra 16 px breathing room below the window
    _ICON_CLEARANCE = 72 + 16   # px from bottom of work area

    def __init__(self, root: tk.Tk, config):
        self._root    = root
        self._config  = config
        self._process: subprocess.Popen | None = None
        self._hwnd:    int | None              = None
        self._visible: bool                    = False
        self._positioned: bool                 = False  # initial position set?

    # ── Helpers ───────────────────────────────────────────────────────

    def _is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _build_cmd(self) -> list[str]:
        args = ["--webview", self.URL, self.TITLE,
                str(self.WIDTH), str(self.HEIGHT)]
        if getattr(sys, "frozen", False):
            return [sys.executable] + args
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "main.py")
        return [sys.executable, script] + args

    def _poll_hwnd(self, attempts: int = 0):
        """Poll until the subprocess window appears, cache its HWND,
        then set the initial bottom-right position on first launch."""
        if not self._is_running():
            return
        hwnd = _find_hwnd_for_pid(self._process.pid)
        if hwnd:
            self._hwnd = hwnd
            if not self._positioned:
                self._positioned = True
                self._set_initial_position()
            return
        if attempts < 30:          # retry for up to ~15 s
            self._root.after(500, lambda: self._poll_hwnd(attempts + 1))

    def _set_initial_position(self):
        """Move AirPortal to bottom-right corner, above the canvas icons."""
        if not self._hwnd:
            return
        l, t, r, b = _get_work_area()
        work_w = r - l
        work_h = b - t

        x = l + work_w - self.WIDTH  - 20               # 20 px from right edge
        y = t + work_h - self.HEIGHT - self._ICON_CLEARANCE

        # Move & resize without activation
        _user32.SetWindowPos(
            self._hwnd,
            0,            # HWND_TOP — bring to front once on first appear
            x, y, self.WIDTH, self.HEIGHT,
            _SWP_NOACTIVATE,
        )

    # ── Public API ────────────────────────────────────────────────────

    def show(self):
        if not _HAS_WEBVIEW:
            import webbrowser
            webbrowser.open(self.URL)
            return

        if not self._is_running():
            # Fresh start — spawn subprocess, wait for its window
            self._hwnd    = None
            self._process = subprocess.Popen(self._build_cmd())
            self._visible = True
            self._config.set("airportal_visible", True)
            self._root.after(500, self._poll_hwnd)
        else:
            # Process alive but window hidden — reveal without flash
            if self._hwnd:
                _user32.ShowWindow(self._hwnd, _SW_SHOWNOACTIVATE)
            self._visible = True
            self._config.set("airportal_visible", True)

    def hide(self):
        """Hide OS window WITHOUT killing subprocess (session preserved)."""
        if self._hwnd and self._is_running():
            _user32.ShowWindow(self._hwnd, _SW_HIDE)
        self._visible = False
        self._config.set("airportal_visible", False)

    def toggle(self):
        if self._visible and self._is_running():
            self.hide()
        else:
            self.show()

    @property
    def visible(self) -> bool:
        return self._visible and self._is_running()

    def lift(self, overlay_hwnd: int = 0):
        """Keep AirPortal above the overlay without becoming globally topmost."""
        if not (self._hwnd and self._is_running() and self._visible):
            return
        if overlay_hwnd:
            _user32.SetWindowPos(overlay_hwnd, self._hwnd, 0, 0, 0, 0, _SWP_FLAGS)
        else:
            _user32.BringWindowToTop(self._hwnd)

    def destroy(self):
        if self._is_running():
            self._process.terminate()
            self._process = None
