import ctypes
import os
import signal
import sys
import tkinter as tk

# Make the process DPI-aware BEFORE any window is created.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# Ensure relative paths resolve to the exe/script directory.
if getattr(sys, "frozen", False):
    os.chdir(os.path.dirname(sys.executable))
else:
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

# ── Webview subprocess mode ────────────────────────────────────────────
# Launched by WebWidget as:
#   Desktop Overlay.exe --webview <url> <title> <w> <h>
# This branch runs pywebview on the main thread (no tkinter), then exits.
if "--webview" in sys.argv:
    _i     = sys.argv.index("--webview")
    _url   = sys.argv[_i + 1] if len(sys.argv) > _i + 1 else "about:blank"
    _title = sys.argv[_i + 2] if len(sys.argv) > _i + 2 else "Web"
    _w     = int(sys.argv[_i + 3]) if len(sys.argv) > _i + 3 else 500
    _h     = int(sys.argv[_i + 4]) if len(sys.argv) > _i + 4 else 660
    try:
        import webview
        _win = webview.create_window(
            _title, _url, width=_w, height=_h, resizable=True
        )
        # X button hides the window (keeps session alive) instead of exiting
        def _on_closing():
            _win.hide()
            return False   # cancel the native close
        _win.events.closing += _on_closing
        # Persist cookies / localStorage so AirPortal stays logged in
        # across app restarts.  WebView2 stores its profile in this folder.
        _storage = os.path.join(os.getcwd(), "webview_data")
        os.makedirs(_storage, exist_ok=True)
        webview.start(storage_path=_storage)
    except Exception as exc:
        print(f"WebView error: {exc}")
    sys.exit(0)
# ──────────────────────────────────────────────────────────────────────

from config import Config
from desktop_icons import show_desktop_icons
from hotkey import HotkeyListener
from overlay import OverlayWindow
from tray import TrayIcon
from version import APP_VERSION, check_for_updates


def main():
    config = Config("config.json")
    config.load()

    root = tk.Tk()
    root.withdraw()

    overlay = OverlayWindow(root, config)

    # ── Check for updates in background ──────────────────────────────
    def _on_update_found(new_ver, url):
        import webbrowser
        import tkinter.messagebox as mb
        def _show():
            if mb.askyesno(
                "发现新版本",
                f"Desktop Overlay v{new_ver} 已发布！\n\n"
                f"当前版本：v{APP_VERSION}\n"
                f"最新版本：v{new_ver}\n\n"
                "是否前往下载页面？",
                icon="info",
            ):
                webbrowser.open(url)
        root.after(3000, _show)

    check_for_updates(_on_update_found)

    def exit_app():
        show_desktop_icons()
        config.save()
        root.after(0, root.quit)

    hotkey_str = config.get("hotkey", "alt+`")
    hotkey = HotkeyListener(root, hotkey_str, overlay.toggle)
    hotkey.start()

    tray_icon_path = config.get("tray_icon_path")
    tray = TrayIcon(root, overlay.toggle, exit_app, tray_icon_path)
    tray.start()

    def on_close():
        show_desktop_icons()
        config.save()
        hotkey.stop()
        tray.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    signal.signal(signal.SIGINT, lambda s, f: exit_app())

    try:
        root.mainloop()
    finally:
        show_desktop_icons()
        hotkey.stop()
        tray.stop()


if __name__ == "__main__":
    main()
