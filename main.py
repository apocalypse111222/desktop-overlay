import ctypes
import os
import signal
import sys
import tkinter as tk

# Make the process DPI-aware BEFORE any window is created.
# Without this, Windows renders the app at a lower DPI and scales it up, causing global blurriness.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # Per-monitor DPI aware (Win 8.1+)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()    # System DPI aware (Vista+)
    except Exception:
        pass

# Ensure relative paths (config.json, cache/) resolve to the exe/script directory.
# sys.executable points to the .exe when frozen; __file__ is used during development.
if getattr(sys, "frozen", False):
    os.chdir(os.path.dirname(sys.executable))
else:
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

from config import Config
from desktop_icons import show_desktop_icons
from hotkey import HotkeyListener
from overlay import OverlayWindow
from tray import TrayIcon


def main():
    config = Config("config.json")
    config.load()

    root = tk.Tk()
    root.withdraw()  # start hidden; overlay.show() will deiconify

    overlay = OverlayWindow(root, config)

    def toggle():
        root.after(0, overlay.toggle)

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
