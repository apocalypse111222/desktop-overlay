import threading
import keyboard


class HotkeyListener:
    """
    Listens for a global hotkey on a daemon thread.
    Posts toggle_callback to the tkinter event loop via root.after(0, ...).
    """

    def __init__(self, root, hotkey: str, toggle_callback):
        self.root = root
        self.hotkey = hotkey
        self.callback = toggle_callback
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        keyboard.add_hotkey(
            self.hotkey,
            lambda: self.root.after(0, self.callback),
            suppress=False,
        )
        keyboard.wait()

    def stop(self):
        try:
            keyboard.unhook_all()
        except Exception:
            pass
